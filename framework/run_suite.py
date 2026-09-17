#!/usr/bin/env python3
"""套件级 runner：批量执行用例，子进程隔离，聚合报告。

用法：
  python run_suite.py [--package com.zui.calendar] [--device SERIAL|all]
                      [--repeat N] [--retry-fail N] [--jobs 1] [--timeout SECS]

每个用例 = 独立子进程（sys.executable run_case.py <用例>），天然隔离全局态/设备连接/异常。
子进程退出码直接复用现有语义（0/1/2/3），套件退出码与之同构：
  任一 FAIL→1；无 FAIL 有 ERROR→3；仅 BLOCKED→2；全 PASS/WARN→0。

**P3a 新增**：
- `--repeat N`：同一批用例连跑 N 轮（M11 同设备一致率的数据源）
- `--device all`：自动发现全部在线设备，**串行**逐台跑完整批（M12 双设备一致率的
  数据源；不做并行 —— 并行会让两台设备抢同一份用例/截图目录，产物互相覆盖）

**P5 新增**：
- `--retry-fail N`（默认 1）：FAIL 的用例自动复跑 N 次。首跑 FAIL 复跑 PASS →
  标记 `FLAKY`（**疑似抖动**），与 `FAIL`（确定性结论）在报告里分开统计。
  这是**机制自动复跑**（Agent 无感知），与 SKILL.md 原则 8 禁止的"Agent 自主
  另起一轮独立复核"不同性质（§五 已就此同步措辞）。

设备断连熔断：子进程返回 3（ERROR）时先 adb devices 检查设备在线，
不在线 → 立即终止套件（不浪费时间跑注定全部超时的后续用例）。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# run_case 模块的 CASE_DIRS / _iter_case_files 复用于用例发现
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from run_case import CASE_DIRS, _iter_case_files  # noqa: E402


def _collect_cases(package=None):
    """收集要执行的用例文件列表。

    package 过滤：只返回 cases/<package>/ 下的用例。None = 全量。
    返回 [(绝对路径, 相对路径), ...]，相对路径 = 去掉 CASE_DIRS 前缀后的部分
    （传给子进程的 run_case.py 参数）。

    ⚠️ **按「相对路径」去重，不是绝对路径**（2026-09-16 真机实测踩到）：
    `CASE_DIRS` 同时含「工作区 cases」与「skill 包 cases」，同一个用例在两处
    各有一份 —— **绝对路径不同、相对路径相同**。旧实现按绝对路径去重 ⇒ 同一
    用例被枚举两次；而子进程拿到的是相对路径、`run_case.py` 又是**工作区优先**
    解析 ⇒ **同一份文件真的跑了两遍**。
    后果：套件用例数翻倍（实测 32 vs 实际的 16）、耗时翻倍，且人为重复的结果会
    污染 M11 一致率（"两次都 PASS"变成自证）。
    `rel` 才是"实际会被执行的那个身份"，去重必须用它。
    """
    cases = []
    seen = set()
    for abs_path in _iter_case_files():
        # 计算相对路径（去掉 CASE_DIRS 中的某个前缀）
        rel = None
        for d in CASE_DIRS:
            d_norm = os.path.normcase(os.path.abspath(d))
            p_norm = os.path.normcase(abs_path)
            if p_norm.startswith(d_norm + os.sep):
                rel = os.path.relpath(abs_path, d)
                break
        if rel is None:
            rel = os.path.basename(abs_path)
        # package 过滤：相对路径的第一段目录 = 包名
        if package:
            parts = rel.replace("\\", "/").split("/")
            if len(parts) < 2 or parts[0] != package:
                continue
        key = os.path.normcase(rel.replace("/", os.sep))
        if key in seen:
            continue
        seen.add(key)
        cases.append((abs_path, rel))
    return cases


def list_devices():
    """已授权在线设备的 serial 列表（`adb devices` 的 device 态）。"""
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    for ln in (r.stdout or "").splitlines()[1:]:
        parts = ln.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def _device_online(serial=None):
    """检查设备是否仍在线。serial=None 时检查是否有任何授权设备。"""
    if serial in (None, "all"):
        return bool(list_devices())
    return serial in list_devices()


def _resolve_devices(arg):
    """把 `--device` 的值解析成要跑的 serial 列表。

    · None            → [None]（交给 TestCase 自己选唯一设备，保持旧行为）
    · "all"           → 全部在线设备（M12 双设备门禁；零台则报错退出）
    · "S1,S2"         → 显式多台（逗号分隔）
    · "S1"            → 单台（旧行为，原样透传）
    """
    if not arg:
        return [None]
    if arg == "all":
        devs = list_devices()
        if not devs:
            print("❌ --device all：未发现任何在线设备")
            sys.exit(3)
        return devs
    return [d.strip() for d in arg.split(",") if d.strip()]


def _suite_exit_code(counts):
    """套件退出码：与单用例语义同构，CI 可直接消费。

    ⚠️ **`flaky` 计入阻断**（2026-09-16 人确认："优先保证不能假 PASS"）。

    首跑失败**就是失败**。复跑成功的价值是**归因**（区分确定性缺陷 vs 环境抖动），
    **不是豁免** —— 把 flaky 当通过，正是"假 PASS"最典型的形态：报告全绿、缺陷
    留在原地，而且比普通 FAIL 更难察觉（因为它看起来像"已经处理过了"）。
    """
    if counts["fail"] > 0 or counts.get("flaky", 0) > 0:
        return 1
    if counts["error"] > 0:
        return 3
    if counts["blocked"] > 0:
        return 2
    return 0


def _fail_reason(stdout, limit=2):
    """从子进程输出里提取失败原因（报告**必须给原因**，不能只说"失败了"）。

    取最后几条 `❌/⛔/💥` 行 —— 那是 `record()` 打印的断言详情，是唯一能说明
    "为什么挂"的信息。只写 "FAIL" 的报告没法定位，等于让人重跑一遍。

    这条在 FLAKY 场景下尤其重要：首跑失败、复跑通过时，**首跑原因必须留在报告里**，
    否则"疑似抖动"就退化成一个无法复查的标签。
    """
    if not stdout:
        return ""
    picks = []
    for ln in stdout.splitlines():
        s = ln.strip()
        if s[:1] in ("❌", "⛔", "💥"):
            picks.append(s[1:].strip())
    return " ｜ ".join(picks[-limit:])


def _run_one(rel_path, device, timeout, env):
    """跑一个用例（子进程隔离），返回结果 dict。

    返回 {"status", "elapsed", "code", "report", "stdout", "stderr",
          "timeout", "reason"}。

    ⚠️ 显式 `encoding="utf-8"`：子进程输出含中文/emoji，用默认的 locale 编码
    （Windows 下常是 GBK）解码会花屏，而花屏的原因会被原样写进报告与失败原因列
    —— 那等于把"被截断的失败信息"当成失败信息。
    """
    cmd = [sys.executable, os.path.join(HERE, "run_case.py")]
    if device:
        cmd += ["--device", device]
    cmd.append(rel_path)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8",
                              errors="replace")
        elapsed = time.time() - t0
        code = proc.returncode
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        timed_out = False
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "elapsed": time.time() - t0, "code": 3,
                "report": "", "stdout": "", "stderr": "", "timeout": True,
                "reason": f"超时（>{timeout}s）"}
    except Exception as e:
        return {"status": "ERROR", "elapsed": time.time() - t0, "code": 3,
                "report": "", "stdout": "", "stderr": str(e), "timeout": False,
                "reason": f"子进程异常: {e}"}

    status = {0: "PASS", 1: "FAIL", 2: "BLOCKED", 3: "ERROR"}.get(code, "ERROR")
    report = ""
    m = re.search(r"报告已生成[：:]\s*(.+\.md)", stdout)
    if m:
        rp = m.group(1).strip()
        try:
            report = os.path.relpath(rp)
        except ValueError:
            report = rp
    return {"status": status, "elapsed": elapsed, "code": code,
            "report": report, "stdout": stdout, "stderr": stderr,
            "timeout": timed_out,
            "reason": "" if status == "PASS" else _fail_reason(stdout)}


def _consistency(records):
    """按用例统计跨轮次结论一致率（M11/M12 的数据源）。

    records: {"rel_path": ["PASS", "PASS", "FAIL", ...]}
    返回 [(rel, total, 一致次数, 一致率, 结论集合)]，只含跑过 ≥2 次的用例。
    """
    out = []
    for rel, sts in records.items():
        if len(sts) < 2:
            continue
        # 一致 = 与**首轮**相同（后轮都是"相对基线是否稳定"的判据）
        same = sum(1 for s in sts[1:] if s == sts[0])
        total = len(sts) - 1
        out.append((rel, len(sts), same, (same / total) if total else 1.0,
                    sorted(set(sts))))
    return sorted(out, key=lambda x: x[3])


def _generate_report(cases_results, counts, started_at, suite_filter, report_dir,
                     consistency=None, devices=None, repeat=1):
    """生成套件聚合报告 Markdown。返回报告文件路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"suite_{ts}_报告"
    path = os.path.join(report_dir, name + ".md")
    dur = time.time() - started_at

    status_emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "FLAKY": "🎲",
                    "BLOCKED": "⛔", "ERROR": "💥"}
    lines = [
        f"# 套件报告 {name}",
        "",
        f"- **执行时间**: {datetime.fromtimestamp(started_at).isoformat(timespec='seconds')}",
        f"- **总耗时**: {dur:.1f}s",
        f"- **筛选条件**: {suite_filter or '全量'}",
        f"- **设备**: {', '.join(devices) if devices else '自动选择'}",
        f"- **轮次**: {repeat}",
        f"- **总计**: {len(cases_results)} 个用例",
        f"  - ✅ PASS: {counts['pass']}",
        f"  - ⚠️ WARN: {counts['warn']}",
        f"  - ❌ FAIL: {counts['fail']}（**确定性**，复跑仍失败）",
        f"  - 🎲 FLAKY: {counts.get('flaky', 0)}（首跑失败、复跑通过 → 疑似抖动，"
        f"**仍计入失败**）",
        f"  - ⛔ BLOCKED: {counts['blocked']}",
        f"  - 💥 ERROR: {counts['error']}",
        f"  - ⏭️ 未执行: {counts.get('skipped', 0)}",
        "",
        "## 用例明细",
        "",
        "| # | 用例 | 设备 | 结论 | 复跑 | 耗时 | 退出码 | 报告 |",
        "|---|------|------|------|------|------|--------|------|",
    ]
    for i, result in enumerate(cases_results, 1):
        rel, status, elapsed, code = result[:4]
        report_rel = result[4] if len(result) > 4 else ""
        dev = result[5] if len(result) > 5 else ""
        retry = result[6] if len(result) > 6 else ""
        emoji = status_emoji.get(status, "?")
        report_link = f"[`报告`]({report_rel})" if report_rel else "—"
        lines.append(f"| {i} | `{rel}` | {dev or '—'} | {emoji} {status} | "
                     f"{retry or '—'} | {elapsed:.1f}s | {code} | {report_link} |")
    if counts.get("skipped", 0) > 0:
        lines.append(f"| - | *剩余 {counts['skipped']} 个用例未执行* | - | ⏭️ | - | - | - | — |")

    # ── 失败原因（含疑似抖动）────────────────────────────────────
    # 只写"FAIL"的报告没法定位，等于让人重跑一遍。FLAKY 的首跑原因尤其必须留：
    # 复跑通过会让后续查看者以为"没事了"，而首跑原因才是可复查的线索。
    reasons = [(x[0], x[1], x[7] if len(x) > 7 else "")
               for x in cases_results
               if x[1] in ("FAIL", "FLAKY", "BLOCKED", "ERROR")]
    if reasons:
        lines += ["", "## 失败原因", ""]
        for rel, status, why in reasons:
            tag = "🎲 疑似抖动" if status == "FLAKY" else status
            lines.append(f"- `{rel}`（{tag}）："
                         f"{why or '（输出里没有 ❌/⛔ 行，详见该用例报告）'}")
        if any(s == "FLAKY" for _r, s, _w in reasons):
            lines += [
                "",
                "> **FLAKY = 首跑失败、复跑通过，仍计入失败。** 复跑用于**归因**"
                "（环境抖动 vs 确定性缺陷），不是豁免 —— 首跑失败就是失败，"
                "把它当通过就是假 PASS（2026-09-16 人确认）。",
            ]

    if consistency:
        worst = min(c[3] for c in consistency)
        lines += [
            "",
            "## 结论一致率（M11/M12）",
            "",
            f"- 样本：{len(consistency)} 个用例，每例 ≥2 次",
            f"- **最低一致率**: {worst:.0%}（目标 ≥90%，flaky 标记步骤除外）",
            "",
            "| 用例 | 次数 | 与首轮一致 | 一致率 | 出现过的结论 |",
            "|---|---|---|---|---|",
        ]
        for rel, n, same, rate, sts in consistency:
            lines.append(f"| `{rel}` | {n} | {same} | {rate:.0%} | "
                         f"{'/'.join(sts)} |")
        lines += [
            "",
            "> 说明：一致率把「同设备连跑 N 次结论是否稳定」变成数字。",
            "> 100% 不现实（OCR 偶发 None、渲染竞态、网络超时是业界公认问题），",
            "> 故目标定 ≥90% 而非 100%（§7.2 M11）。",
        ]

    os.makedirs(report_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="套件级 runner：批量执行用例")
    parser.add_argument("--package", help="只跑指定包名的用例（如 com.zui.calendar）")
    parser.add_argument("--device",
                        help="绑定设备：SERIAL / 'all'（全部在线设备）/ 'S1,S2'")
    parser.add_argument("--repeat", type=int, default=1,
                        help="同一批用例连跑 N 轮（M11 一致率数据源，默认 1）")
    parser.add_argument("--retry-fail", type=int, default=1,
                        help="FAIL 用例复跑次数（默认 1；0=关闭）。复跑通过 → "
                             "标为 FLAKY（疑似抖动）而非确定性 FAIL（P5）")
    parser.add_argument("--jobs", type=int, default=1,
                        help="并行数（P2 预留，当前仅支持 1）")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单个用例超时秒数（默认 600）")
    args = parser.parse_args()

    if args.jobs > 1:
        print("⚠️  --jobs > 1 尚未实现（P2），回退为串行执行")
        args.jobs = 1
    if args.repeat < 1:
        print("❌ --repeat 需要 ≥1")
        sys.exit(3)

    devices = _resolve_devices(args.device)

    # ── 收集用例 ──────────────────────────────────────────────────────
    cases = _collect_cases(package=args.package)
    suite_filter = f"package={args.package}" if args.package else "全量"
    if not cases:
        print(f"⚠️  无匹配用例（{suite_filter}）")
        print(f"  已查找目录: {[d for d in CASE_DIRS if os.path.isdir(d)]}")
        sys.exit(3)

    print(f"🏁 套件执行：{len(cases)} 个用例（{suite_filter}）")
    print(f"   设备: {', '.join(devices) if devices != [None] else '自动选择'}"
          f"｜轮次: {args.repeat}｜FAIL 复跑: {args.retry_fail}")
    print(f"   退出码语义: PASS/WARN=0  FAIL=1  BLOCKED=2  ERROR=3")
    print()

    # ── 初始化 DB ─────────────────────────────────────────────────────
    suite_id = None
    try:
        from db import get_db, default_test_dir
        db = get_db()
        suite_id = db.start_suite(suite_filter, len(cases) * len(devices) * args.repeat)
        report_dir = os.path.join(default_test_dir(), "storage", "reports")
    except Exception as e:
        print(f"⚠️ [db] 套件记录初始化失败（不影响执行）: {e}")
        db = None
        report_dir = os.path.join(
            os.environ.get("DSH_WORKSPACE_DIR",
                           os.path.join(os.path.expanduser("~"), "android-test-skills-data")),
            "storage", "reports")

    # ── 逐用例执行（设备 × 轮次 × 用例）───────────────────────────────
    started_at = time.time()
    results = []          # [(rel, status, elapsed, code, report?, device?, retry?)]
    records = {}          # rel → [status, ...]（M11/M12 一致率）
    counts = {"pass": 0, "warn": 0, "fail": 0, "flaky": 0, "blocked": 0,
              "error": 0, "skipped": 0}
    aborted = False
    total_slots = len(cases) * len(devices) * args.repeat
    done = 0

    for dev in devices:
        if aborted:
            break
        for rep in range(1, args.repeat + 1):
            if aborted:
                break
            dev_tag = dev or "auto"
            if len(devices) > 1 or args.repeat > 1:
                print(f"\n── 设备 {dev_tag}｜第 {rep}/{args.repeat} 轮 ──")
            for idx, (abs_path, rel_path) in enumerate(cases, 1):
                env = os.environ.copy()
                if suite_id is not None:
                    env["DSH_SUITE_ID"] = str(suite_id)
                tag = f"[{done + 1}/{total_slots}]"
                print(f"  {tag} {rel_path} ...", end=" ", flush=True)

                r = _run_one(rel_path, dev, args.timeout, env)
                done += 1
                status = r["status"]
                retry_note = ""
                # 首跑失败原因：FLAKY 时**必须**留在报告里 —— 否则"疑似抖动"
                # 退化成一个无法复查的标签（人确认 2026-09-16）
                first_reason = r["reason"]

                # ── P5：FAIL 复跑 → **归因**（确定性 vs 抖动），不是豁免 ──
                # 首跑 FAIL、复跑 PASS ⇒ 标 FLAKY（疑似抖动），但**结论仍记失败**：
                # "优先保证不能假 PASS"（人确认 2026-09-16）。复跑让报告能说
                # "可能只是环境抖动"，而不是让缺陷消失。
                # 复跑是**机制自动**做的（Agent 无感知），与 SKILL.md 原则 8 禁止的
                # "Agent 自主另起一轮复核"不同性质（§五）。
                if status == "FAIL" and args.retry_fail > 0:
                    print("🔁复跑...", end=" ", flush=True)
                    for _ in range(args.retry_fail):
                        r2 = _run_one(rel_path, dev, args.timeout, env)
                        done += 1
                        if r2["status"] == "PASS":
                            status = "FLAKY"
                            retry_note = "复跑 PASS（疑似抖动）"
                            break
                        retry_note = f"复跑 {r2['status']}"

                if r["timeout"]:
                    print(f"💥 TIMEOUT ({r['elapsed']:.1f}s)")

                # 失败/错误时回显子进程输出（保留排查线索）
                if r["code"] != 0:
                    for ln in (r["stdout"] or "").strip().splitlines()[-10:]:
                        print(f"      │ {ln}")
                    for ln in (r["stderr"] or "").strip().splitlines()[-5:]:
                        print(f"      │ {ln}")

                emoji_map = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌",
                             "FLAKY": "🎲", "BLOCKED": "⛔", "ERROR": "💥"}
                extra = f" → {status}" if status != r["status"] else ""
                print(f"{emoji_map.get(status, '?')} {status}{extra} "
                      f"({r['elapsed']:.1f}s)")

                results.append((rel_path, status, r["elapsed"], r["code"],
                                r["report"], dev or "", retry_note,
                                first_reason))
                records.setdefault(rel_path, []).append(status)
                counts[status.lower()] = counts.get(status.lower(), 0) + 1

                # ── 设备断连熔断 ──────────────────────────────────────
                if r["code"] == 3 and not _device_online(dev):
                    remaining = total_slots - done
                    counts["skipped"] = remaining
                    print(f"\n🔌 设备断连（{dev_tag}）！剩余 {remaining} 个用例未执行")
                    aborted = True
                    break

    # ── 聚合报告 ──────────────────────────────────────────────────────
    consistency = _consistency(records) if (args.repeat > 1 or len(devices) > 1) else None
    report_path = _generate_report(results, counts, started_at, suite_filter,
                                   report_dir, consistency,
                                   [d or "auto" for d in devices], args.repeat)
    total_dur = time.time() - started_at
    suite_code = _suite_exit_code(counts)

    print()
    print(f"{'=' * 60}")
    print(f"🏁 套件完成: {len(results)}/{total_slots} 次执行  耗时 {total_dur:.1f}s")
    print(f"   ✅{counts['pass']}  ⚠️{counts['warn']}  ❌{counts['fail']}"
          f"  🎲{counts.get('flaky', 0)}  ⛔{counts['blocked']}  💥{counts['error']}"
          + (f"  ⏭️{counts['skipped']}" if counts['skipped'] else ""))
    if counts.get("flaky"):
        print(f"   🎲 {counts['flaky']} 个用例首跑失败、复跑通过 → 疑似抖动"
              "（**仍计入失败**，原因见报告「失败原因」节）")
    if consistency:
        worst = min(c[3] for c in consistency)
        print(f"   📊 最低结论一致率 {worst:.0%}（目标 ≥90%，样本 {len(consistency)} 例）")
    if aborted:
        print(f"   ⚠️  套件因设备断连中止")
    print(f"   📄 报告: {report_path}")
    print(f"   退出码: {suite_code}")

    # ── DB 收尾 ────────────────────────────────────────────────────────
    if db and suite_id:
        try:
            db.finish_suite(suite_id,
                            pass_n=counts["pass"],  # WARN 单独计数
                            fail_n=counts["fail"], blocked_n=counts["blocked"],
                            error_n=counts["error"], warn_n=counts["warn"],
                            report_path=report_path)
        except Exception as e:
            print(f"⚠️ [db] 套件记录收尾失败: {e}")

    sys.exit(suite_code)


if __name__ == "__main__":
    main()
