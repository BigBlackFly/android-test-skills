#!/usr/bin/env python3
"""用例执行器：python run_case.py com.zui.calendar/172.py

用例目录单一数据源 = <skill包>/cases（与 knowledge 平级，随版本同步团队共享）。
用例按被测 App 包名分目录存放：cases/<包名>/<编号>.py，
与该 App 的知识卡 knowledge/<包名>.md、探查缓存 storage/probes/<包名>/ 同键。

解析顺序：
  1) 绝对路径                        直接使用
  2) 带子目录的相对路径               cases/com.zui.calendar/172.py
  3) 裸文件名递归精确匹配             172.py
  4) 子串模糊匹配（唯一命中才接受）    172
查找目录：
  1) <workspace>/cases/        （用户工作区，setup 首次复制后只动这里）
  2) <skill包>/cases/          （skill 包兜底）
  3) <framework>/cases/        （旧布局兼容）
环境变量 DSH_WORKSPACE_CASES 可覆盖用例目录。
"""
import ast
import importlib.util
import logging
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _case_dirs():
    """用例查找目录，按优先级返回。

    工作区优先（setup 首次复制后用户只改工作区），skill 包兜底。
    环境变量 DSH_WORKSPACE_CASES 可覆盖（优先级最高）。
    """
    dirs = []
    env = os.environ.get("DSH_WORKSPACE_CASES")
    if env and env.strip():
        dirs.append(os.path.abspath(os.path.expanduser(env.strip())))
    # 工作区 cases/（setup 首次复制，后续只动工作区）
    try:
        from db import default_test_dir
        dirs.append(os.path.join(default_test_dir(), "cases"))
    except Exception:
        pass
    dirs += [
        os.path.join(os.path.dirname(HERE), "cases"),   # skill 包 cases/
        os.path.join(HERE, "cases"),                    # 旧布局兼容
    ]
    skill_root = os.environ.get("DSH_SKILL_DIR") or os.path.join(
        os.path.expanduser("~"), ".agents", "skills", "android-test-skills")
    dirs.append(os.path.join(skill_root, "cases"))      # skill 包锚点
    seen, out = set(), []
    for d in dirs:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


CASE_DIRS = _case_dirs()

# 用例内 `from test_framework import ...` 依赖 test_framework.py 所在目录
# （framework/），无论从哪个 cwd 启动执行器都把它放进 sys.path。
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _iter_case_files():
    """递归产出所有用例文件（绝对路径）。排除 _ 开头的共享模块与目录（_flow.py、_lib/ 等）。"""
    for d in CASE_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs
                       if x not in ("__pycache__",)
                       and not x.startswith((".", "_"))]
            for f in files:
                if f.endswith(".py") and not f.startswith("_"):
                    yield os.path.join(root, f)


def resolve_case(name: str) -> str | None:
    """把用例名解析为实际文件路径；找不到返回 None。

    支持：绝对路径 / 带子目录的相对路径 / 裸文件名 / 唯一子串模糊匹配。
    裸名或模糊匹配命中多个时打印候选并退出（由调用方处理 None）。"""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    n = name if name.lower().endswith(".py") else name + ".py"
    # 带子目录的相对路径（com.zui.calendar/172.py，正反斜杠均可）
    if "/" in name or "\\" in name:
        rel = name.replace("/", os.sep).replace("\\", os.sep)
        if not rel.lower().endswith(".py"):
            rel += ".py"
        for d in CASE_DIRS:
            candidate = os.path.join(d, rel)
            if os.path.isfile(candidate):
                return candidate
        return None
    # 裸文件名：递归精确匹配
    hits = [p for p in _iter_case_files() if os.path.basename(p) == n]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"用例名 '{name}' 匹配到多个文件，请写带子目录的路径:")
        for p in hits:
            print(f"  {p}")
        return None
    # 子串模糊匹配（唯一命中才接受）
    hits = [p for p in _iter_case_files() if n[:-3] in os.path.basename(p)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"用例名 '{name}' 模糊匹配到多个文件，请写更完整的名字:")
        for p in hits:
            print(f"  {p}")
    return None


# setup 会把 framework/ 复制到工作区，改动靠 sync_skill.ps1 双向搬运；
# 忘了同步就会出现"改的代码不生效"。启动时对比两份拷贝的关键文件哈希。
_DRIFT_KEY_FILES = ("test_framework.py", "states.py", "run_case.py", "db.py",
                    "vision.py", "ocr_screen.py", "webui.py", "VERSION")


def _file_digest(path):
    import hashlib
    h = hashlib.md5()  # 只做一致性对比，非安全用途
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def warn_if_framework_drift(run_fw=None):
    """framework 多副本哈希比对：运行副本 / 工作区备份 / skill 包安装副本。

    本机可能同时存在三份以上 framework（开发仓、工作区备份、Agent 安装的
    skill 副本）。旧实现只比对后两份：从开发仓直接运行时，"安装副本过期"
    检测不到，且告警文案指向的 sync 脚本修不到真正在跑的代码。
    现在把运行副本纳入比对，告警标出每份路径与"正在运行的是哪份"。
    run_fw 供单测注入；默认 = 本文件所在 framework 目录。
    """
    run_fw = run_fw or HERE
    try:
        from db import default_test_dir
        ws_fw = os.path.join(default_test_dir(), "framework")
    except Exception:
        ws_fw = None
    skill_root = os.environ.get("DSH_SKILL_DIR") or os.path.join(
        os.path.expanduser("~"), ".agents", "skills", "android-test-skills")
    skill_fw = os.path.join(skill_root, "framework")

    dirs = [("运行副本", run_fw), ("工作区备份", ws_fw), ("skill包", skill_fw)]
    seen, uniq = set(), []
    for label, d in dirs:
        if not d or not os.path.isdir(d):
            continue
        k = os.path.normcase(os.path.abspath(d))
        if k in seen:
            continue
        seen.add(k)
        uniq.append((label, d))
    if len(uniq) < 2:
        return

    diffs = []
    for f in _DRIFT_KEY_FILES:
        by_hash = {}                       # hash/缺失 -> [标签]
        for label, d in uniq:
            fp = os.path.join(d, f)
            if not os.path.isfile(fp):
                by_hash.setdefault("缺失", []).append(label)
                continue
            by_hash.setdefault(_file_digest(fp), []).append(label)
        if len(by_hash) > 1:
            detail = "；".join(
                f"{','.join(labels)}={state if state == '缺失' else state[:8]}"
                for state, labels in by_hash.items())
            diffs.append(f"{f}（{detail}）")
    if diffs:
        print("⚠️ " * 8)
        print(f"⚠️  framework 多副本不一致，正在运行: [{uniq[0][0]}] {uniq[0][1]}")
        for label, d in uniq:
            print(f"⚠️    [{label}] {d}")
        for d in diffs:
            print(f"⚠️    {d}")
        print("⚠️  本次执行的代码就是「运行副本」这份；其余副本过期只会误导其它入口，")
        print("⚠️  请把要生效的那份同步到其它副本（如 Agent 安装的 skill 目录）。")
        print("⚠️ " * 8)


# 用例最终结论 → 进程退出码（CI/其他 Agent 据此机器消费结果）
EXIT_CODES = {"PASS": 0, "WARN": 0, "FAIL": 1, "BLOCKED": 2, "ERROR": 3}


def exit_code_for(status):
    """最终结论映射退出码；未知/缺失一律按 ERROR(3)，绝不默认 0 放行。"""
    return EXIT_CODES.get(status, 3)


def _last_case():
    """本进程内最近一次 finish() 的 TestCase（test_framework.LAST_CASE）。"""
    tf = sys.modules.get("test_framework")
    return getattr(tf, "LAST_CASE", None) if tf else None


def _parse_stop_after(raw):
    """`--stop-after` 取值校验：必须是**正整数**。

    0/负数无意义（"一步都不跑"），非法值直接报错退出 —— **不静默忽略**：
    调试开关被静默忽略的后果是"以为只跑了前 5 步、其实跑完了整个用例"，
    比报错难查得多。
    """
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        n = 0
    if n < 1:
        print(f"--stop-after 需要正整数，收到: {raw!r}")
        sys.exit(3)
    return n


def _parse_args(argv):
    """解析命令行参数，返回 (name, device, stop_after)。

    支持: python run_case.py [--device SERIAL] [--stop-after N] <用例名>
    - device=None → TestCase 自动选唯一授权设备
    - stop_after=N → 只跑前 N 步（局部执行：退出码 0、不入库；见
      test_framework.PartialRun）。目的是把 edit-run 循环从分钟级压到秒级。
    `--stop-after=5` 与 `--stop-after 5` 两种写法都接受。
    """
    args = list(argv[1:])
    device = None
    stop_after = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--device" and i + 1 < len(args):
            device = args[i + 1]
            args[i:i + 2] = []
        elif a == "--stop-after" and i + 1 < len(args):
            stop_after = _parse_stop_after(args[i + 1])
            args[i:i + 2] = []
        elif a.startswith("--stop-after="):
            stop_after = _parse_stop_after(a.split("=", 1)[1])
            args[i:i + 1] = []
        else:
            i += 1
    if not args:
        print("用法: python run_case.py [--device SERIAL] [--stop-after N] "
              "<用例文件名或 com.zui.calendar/172.py>")
        sys.exit(3)
    return args[0], device, stop_after


# ── P0a 执行时守门（plan/mechanism-over-prose-plan.md §八 P0a）──────────
# 在**执行前**跑静态检查，级别定义：
#   lint_case   → ERROR **拒跑**（退出码 3 = ERROR，与 EXIT_CODES 同构）；
#                 HINT 仅打印（噪音型规则折叠，见 lint_case._QUIET_HINT_RULES）
#   check_facts → **只作 WARN，永不拒跑**：它的语料是 probes/traces，而 probes
#                 30 分钟即被 cleanup_probes 清掉 → "无语料"是常态，拿它拒跑
#                 会把干净用例全拦下（§八 P0a 明确的 ⚠️）。
# 辅助脚本（`_` 前缀）整个跳过：它们本就无 USER_INPUT，规则 1 不适用。
# 逃生口：DSH_SKIP_GATES=1（仅调试用；正式回归不设）。
GATE_EXIT_CODE = 3

# 本次守门计数（供 RunMetrics 落库，§7.1「守门」维度）。
# 模块级而非返回值：run_gates 的返回值是"放行/拒跑"的布尔语义，把计数塞进
# 返回值会让每个调用点都得拆元组；这里与 test_framework.LAST_CASE 同一套路。
LAST_GATE_STATS = {"lint_errors": None, "check_facts_suspects": None,
                   "checked_words": None}


def _reset_gate_stats():
    for k in LAST_GATE_STATS:
        LAST_GATE_STATS[k] = None


def _storage_dir():
    """工作区 storage/（守门读语料用；取不到时退回框架相对位置）。"""
    try:
        from db import default_test_dir
        return os.path.join(default_test_dir(), "storage")
    except Exception:
        return os.path.join(os.path.dirname(HERE), "storage")


def _pkg_from_case_path(path):
    """从用例路径反推被测包名：cases/<包名>/x.py → <包名>。

    目录名不像包名（不含 '.'）时返回 None —— 只影响"去哪找语料"，
    猜错最多导致 check_facts 不校验，不会误判。
    """
    d = os.path.basename(os.path.dirname(os.path.abspath(path or "")))
    return d if "." in d else None


def _dir_has_entries(d):
    try:
        return os.path.isdir(d) and next(os.scandir(d), None) is not None
    except OSError:
        return False


def _case_has_corpus(pkg, case_stem):
    """该用例是否有"当次语料"（check_facts 的输入）。

    判据刻意是 **per-case**，而不是"storage 目录是否存在"（P0a 设计点）：
    probes 被清空后 storage/probes/ 目录可能还在、里面却是别的页面；traces 是
    采集会话档案（按用例名分目录）。任一有内容才算"可校验"。
    """
    storage = _storage_dir()
    candidates = []
    if pkg:
        candidates.append(os.path.join(storage, "probes", pkg))
    candidates.append(os.path.join(storage, "traces", case_stem))
    return any(_dir_has_entries(d) for d in candidates)


def run_gates(path, pkg=None):
    """执行前静态守门。返回 True=放行，False=拒跑（原因已打印）。"""
    _reset_gate_stats()
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.startswith("_"):
        print(f"[守门] {stem} 是辅助脚本（_ 前缀、无 USER_INPUT）→ 跳过静态守门")
        return True
    if os.environ.get("DSH_SKIP_GATES") == "1":
        print("[守门] DSH_SKIP_GATES=1 → 跳过静态守门（调试用）")
        return True

    evals_dir = os.path.join(os.path.dirname(HERE), "evals")
    if os.path.isdir(evals_dir) and evals_dir not in sys.path:
        sys.path.insert(0, evals_dir)

    # ① lint_case：ERROR 拒跑，HINT 只提示
    try:
        import lint_case
        errors, hints = lint_case.lint_file(path)
        quiet = getattr(lint_case, "_QUIET_HINT_RULES", frozenset())
    except Exception as e:
        print(f"⚠️ [守门] lint 不可用（跳过）: {e}")
        return True

    if errors:
        LAST_GATE_STATS["lint_errors"] = len(errors)
        print(f"❌ [守门] lint 发现 {len(errors)} 处违规 → 拒跑（先改用例再跑）:")
        for line, rule, msg in errors:
            print(f"     {stem}.py:{line} [{rule}] {msg}")
        print("     确需原样执行（仅调试）：设 DSH_SKIP_GATES=1")
        return False
    shown = [h for h in hints if h[1] not in quiet]
    folded = f"，另有 {len(hints) - len(shown)} 条已折叠" if len(shown) != len(hints) else ""
    print(f"[守门] lint 通过（{len(hints)} 条提示{folded}）")
    for line, rule, msg in shown:
        print(f"     💡 {stem}.py:{line} [{rule}] {msg}")

    # ② check_facts：只告警（无当次语料时明确打"未校验"，不判违规）
    if not _case_has_corpus(pkg, stem):
        print("[守门] check_facts 未校验 —— 该用例无当次语料"
              "（probes 30 分钟即清 / 无采集档案）")
        return True
    try:
        import check_facts
        storage = _storage_dir()
        suspects, checked = check_facts.check_facts_file(
            path, [os.path.join(storage, "probes"),
                   os.path.join(storage, "traces")])
    except Exception as e:
        print(f"⚠️ [守门] check_facts 不可用（跳过）: {e}")
        return True
    if suspects:
        print(f"⚠️ [守门] check_facts 疑似编造文案 {len(suspects)} 处"
              f"（共检查 {checked} 个断言词）—— 仅告警，不拒跑：")
        for line, word in suspects:
            print(f"     {stem}.py:{line} {word!r}（语料中无出处 → 回探索补采）")
    else:
        print(f"[守门] check_facts 通过（{checked} 个断言词均有出处）")
    LAST_GATE_STATS["check_facts_suspects"] = len(suspects)
    LAST_GATE_STATS["checked_words"] = checked
    return True


def _knowledge_dirs():
    """知识卡目录（工作区优先、skill 包兜底）——与 cases/ 的搜索顺序同构。"""
    dirs = []
    try:
        from db import default_test_dir
        dirs.append(os.path.join(default_test_dir(), "knowledge"))
    except Exception:
        pass
    for d in CASE_DIRS:
        dirs.append(os.path.join(os.path.dirname(d), "knowledge"))
    return [d for d in dirs if os.path.isdir(d)]


def _single_device_serial():
    """唯一已授权设备的 serial；零台/多台 → None（**宁可不查也不猜**）。

    与 test_framework._resolve_serial 同一口径：多台设备时"猜一台"会让版本
    门禁报出另一台设备的版本，比不报更糟。
    """
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
    except Exception as e:
        # ⚠️ 不许静默：这里原本是 `except Exception: return None`，把
        # `NameError: subprocess 未导入` 吞成了"没有唯一设备"→ 版本门禁**一次都没
        # 采集过**版本号（DB 里 app_version_* 恒 NULL），全程无任何报错。
        # 2026-09-16 真机实测踩到；同类"缺导入 + 宽 except"是静默失效的典型形态。
        print(f"⚠️ [版本门禁] 查设备失败，跳过版本采集: {e}")
        return None
    devs = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs[0] if len(devs) == 1 else None


def collect_app_version(serial, pkg):
    """取被测 App 的 (versionName, versionCode)；任何失败 → (None, None)。

    门禁绝不能因为取不到版本而阻断用例执行。
    """
    if not serial or not pkg:
        return None, None
    try:
        import version_gate
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "package", pkg],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"⚠️ [版本门禁] dumpsys package 返回 {r.returncode}，版本未采集")
            return None, None
        return version_gate.parse_version(r.stdout)
    except Exception as e:
        # 同上：不静默（缺导入/解析异常都要看得见）
        print(f"⚠️ [版本门禁] 版本采集失败（不阻断执行）: {e}")
        return None, None


def _dump_static_metrics(path, device=None):
    """收集静态指标（含版本门禁的信号 1）并写入环境变量。

    **隐藏前提**（§4.1）：run_case 启动时既无设备 serial、也未定被测包名 ——
    包名从用例路径反推（`_pkg_from_case_path`），serial 只认显式 `--device`
    或"唯一已授权设备"。取不到就只落 None，不阻断执行。
    """
    try:
        import run_metrics as _rm
        import version_gate as _vg
    except Exception:
        return
    metrics = _rm.collect_static(path, LAST_GATE_STATS)
    pkg = _pkg_from_case_path(path)
    vname = vcode = card_v = None
    if pkg:
        serial = device or _single_device_serial()
        vname, vcode = collect_app_version(serial, pkg)
        card_v = _vg.read_card_version(_knowledge_dirs(), pkg)
        if vname or vcode:
            print(f"[版本门禁] {pkg}: versionName={vname} versionCode={vcode}")
            # ⚠️ 用 version_gate 的**同一个**判断函数：这里原本自己写了一份精确
            # 比较，与 finish() 那份漂移（2026-09-16 实测：归一化只改了 finish，
            # 启动时照样报假 WARN）。同一逻辑只许有一份实现。
            _line = _vg.card_mismatch_line(card_v, vname)
            if _line:
                print(f"⚠️ [版本门禁] {_line} —— 只告警，不废缓存")
    # 双采（§4.1）：versionName 与现存卡比对、versionCode 落盘存档。
    # P2 卡头部改造完成后再切换为 versionCode 单一比对。
    metrics["app_version_name"] = vname
    metrics["app_version_code"] = vcode
    # card_version **不是** case_metrics 的列，只是传给 TestCase 的载体
    # （报告「本次与历史的差异」节要用它；落库时被 sanitize_extra 自动丢弃）。
    metrics["card_version"] = card_v
    _rm.dump_static(metrics)


def _setup_logging():
    """结构化日志：每次执行落 storage/logs/run_<ts>.log，print 保留控制台。
    日志含时间戳+级别，便于回溯问题；print 输出不受影响。
    失败静默（日志目录不可写不阻断执行）。
    """
    try:
        from db import default_test_dir
        log_dir = os.path.join(default_test_dir(), "storage", "logs")
    except Exception:
        log_dir = os.path.join(HERE, "..", "storage", "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"run_{ts}.log")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    logging.info("日志文件: %s", log_path)
    return log_path


def main():
    # 控制台编码兜底（2026-09-16 实测踩到）：Windows 默认控制台是 GBK
    # （cp936），无法编码 emoji（❌ U+274C / 💡 U+1F4A1 等）→ `print` 直接抛
    # UnicodeEncodeError **中断用例执行**（实测：守门打印 ❌ 时崩，退出码 1，
    # 既没拒跑也没跑用例）。errors="replace" 只把不可编码字符降级成 "?"，
    # 不改变中文本身的编码行为，也不会再中断。
    # 与 evals/lint_case.py main() 的同类处理一致；不设 encoding 是为了不把
    # GBK 控制台的中文输出变成乱码。
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    name, device, stop_after = _parse_args(sys.argv)
    # 结构化日志：每次执行落 storage/logs/run_<ts>.log
    try:
        _setup_logging()
    except Exception:
        pass  # 日志失败不阻断执行
    # 套件 runner 传 --device SERIAL 时，注入环境变量供 TestCase 读取
    if device:
        os.environ["DSH_DEVICE_ID"] = device
    # --stop-after N：局部执行（edit-run 调试循环）。注入环境变量供 TestCase
    # 在 step() 处判断（§八 P1a：退出码 0、不入库、不计入 flaky）。
    if stop_after is not None:
        os.environ["DSH_STOP_AFTER"] = str(stop_after)
        print(f"[局部执行] --stop-after {stop_after}：只跑前 {stop_after} 步"
              "（退出码 0、不入库）")
    try:
        warn_if_framework_drift()
    except Exception:
        pass                        # 检测失败不影响用例执行
    path = resolve_case(name)
    if path is None:
        print(f"用例文件不存在或匹配不唯一: {name}")
        for d in CASE_DIRS:
            print(f"  已查找: {d}/")
        sys.exit(3)

    # 输出版本号（便于确认运行的是哪份框架代码）
    try:
        ver_path = os.path.join(HERE, "VERSION")
        with open(ver_path, encoding="utf-8") as _vf:
            _ver = _vf.read().strip()
        print(f"[版本] {_ver}")
    except Exception:
        pass

    # 输出最终生效的关键路径（排查"跑的代码/用例不是我以为的那份"）
    try:
        from db import default_test_dir
        print(f"[路径] 工作区: {default_test_dir()}")
        print(f"[路径] 用例目录: {[d for d in CASE_DIRS if os.path.isdir(d)]}")
        print(f"[路径] 用例脚本: {path}")
    except Exception:
        pass

    # 通过环境变量把「用户原始输入 + 用例脚本路径」传给 TestCase 入库
    # （TestCase.__init__ 读取；AI 生成用例时把用户口述写进 USER_INPUT 常量）
    os.environ["DSH_CASE_SCRIPT_PATH"] = path
    user_input = extract_user_input(path)
    if user_input is not None:
        os.environ["DSH_CASE_USER_INPUT"] = user_input
    else:
        # 无 USER_INPUT = 辅助脚本（探查/补采/备数据/补验/框架自测）——
        # 按用户 2026-09-11 定的规则**完全不入库**（B1）。这里明说一句，
        # 免得"跑了但没记录"被当成 bug 查半天。
        # 正式用例漏写 USER_INPUT 也会走到这里 → 记录库里就查不到，所以下面
        # 用 _looks_like_formal_case 给一个显眼提醒（不阻断：探查脚本本就该无）。
        _warn_missing_user_input(path)

    # ── P0a 执行时守门 ────────────────────────────────────────────────
    # 位置刻意在探查缓存维护**之前**：check_facts 的语料就是 probes/traces，
    # 先跑维护会把"本次本可校验"的语料删掉，让守门永远报"未校验"。
    if not run_gates(path, _pkg_from_case_path(path)):
        sys.exit(GATE_EXIT_CODE)
    # RunMetrics 静态指标（脚本 hash / sleep 静态和 / 守门计数 / 版本双采）→
    # 环境变量，由 TestCase.finish() 合并落库（§7.1）。失败不阻断执行。
    _dump_static_metrics(path, device)

    # 探查缓存维护（2026-09-10 讨论定稿）：探查产物用完即弃——
    # 超过 30 分钟未访问的 storage/probes/ 目录由**代码**自动删除，
    # 不靠人/AI 记得清理（"一个自觉弥补另一个自觉"）。
    # 每次跑用例都做一次，等于把清理挂在最频繁的入口上。
    # 阈值可由环境变量 DSH_PROBES_MAXIDLE_MIN 覆盖（0=关闭清理，见 cleanup_probes）；
    # 调试期放宽它可保住同一批 probes 供离线预检（inventory.py verify）复用。
    try:
        from test_framework import TestCase as _TC
        _removed, _kept = _TC.cleanup_probes()
        if _removed:
            print(f"[探查缓存] 已清理 {_removed} 份超时未访问，保留 {_kept} 份")
        _stale = _TC.list_probes()
        if _stale:
            print(f"[探查缓存] 现有 {len(_stale)} 份（还在 30 分钟窗口内，本次不删；"
                  f"最新 X 分钟前访问）".replace("X", str(_stale[0]["idle_min"])))
            for _s in _stale[:5]:
                print(f"           {_s['pkg']}/{_s['label']}  {_s['idle_min']} 分钟前访问")
            if len(_stale) > 5:
                print(f"           …另有 {len(_stale) - 5} 份")
    except Exception as _e:
        print(f"[探查缓存] 维护跳过: {_e}")

    spec = importlib.util.spec_from_file_location("testcase", path)
    mod = importlib.util.module_from_spec(spec)
    import traceback
    from test_framework import PartialRun      # 局部执行（--stop-after）
    try:
        spec.loader.exec_module(mod)
        if not hasattr(mod, "run"):
            print("用例文件需要定义 run() 函数")
            sys.exit(3)
        report = mod.run()
    except KeyboardInterrupt:
        raise
    except PartialRun as e:
        # --stop-after：主动收尾，**不是失败**。刻意不走下面那条异常分支：
        # 那里会标 _fatal_error（结论被压成 ERROR）、退出码 3 —— 与"局部执行"
        # 的语义完全相反。这里也不记 FAIL（否则"跑一半"污染缺陷库）。
        print(f"\n⏸️  {e} → 局部执行完成（退出码 0，不入库）")
        tc = _last_case()
        if tc is not None:
            try:
                tc.finish()
            except Exception:
                pass
        sys.exit(0)
    except Exception as e:
        # 脚本/框架/设备异常：留完整堆栈，尽量生成已有证据的报告。
        # ⚠️ 必须先标记 _fatal_error 再 finish：用例没跑完，断言统计不可信，
        # 不标记的话报告结论会按已跑部分算（可能 PASS）与退出码 3 ERROR 矛盾。
        # CaseAbort 例外：require_* 已记 FAIL，结论就是 FAIL（退出码 1），
        # 标 ERROR 反而与退出码矛盾。
        traceback.print_exc()
        from test_framework import CaseAbort
        is_abort = isinstance(e, CaseAbort)
        tc = _last_case()
        # 收尾异常判定：finish() 已跑过 = 用例本体已完成并产出 verdict，
        # 后续收尾代码（finally 恢复、finish 后的清理）再抛异常，不该把结论
        # 压成 ERROR——否则报告/DB 显示 PASS、退出码却是 3，追溯链两端打架。
        # 此时退出码沿用 final_status 映射（测试本体已完成，收尾异常不算失败）。
        already_finished = bool(tc is not None and getattr(tc, "_finished", False))
        if tc is not None:
            try:
                if not is_abort and not already_finished:
                    tc._fatal_error = e
                tc.finish()
            except Exception:
                pass
        if is_abort:
            # 结论由 final_status 映射：CaseBlocked 记的是 BLOCKED → 2，普通
            # CaseAbort 记的是 FAIL → 1。**不再硬编码 1** —— 硬编码会把 BLOCKED
            # 写成 FAIL 的退出码，让"前置条件缺失"和"App 真缺陷"在 CI 侧无法区分
            # （§2.2 点名的退出码陷阱）。
            code = exit_code_for(getattr(tc, "final_status", None))
            if code == 3:      # 拿不到结论 → 退回旧行为（1），不假装知道
                code = 1
            tail = "必需操作失败（前置条件不满足则记 BLOCKED）"
        elif already_finished:
            code = exit_code_for(getattr(tc, "final_status", None))
            tail = f"收尾异常（用例已完成，结论 {getattr(tc, 'final_status', None)}）"
        else:
            code = 3
            tail = "执行异常"
        print(f"\n💥 用例异常终止（{tail}），退出码 {code}")
        sys.exit(code)

    status = getattr(_last_case(), "final_status", None)
    code = exit_code_for(status)
    if status is None:
        print("\n⚠️ 用例未调用 t.finish()，无最终结论，按 ERROR 处理")
    print(f"\n🎉 用例执行完成，报告: {report}（最终结论: {status}，退出码 {code}）")
    sys.exit(code)


def extract_user_input_from_source(source: str) -> str | None:
    """从用例源码提取 USER_INPUT 字符串常量（ast 解析，不吃引号/转义陷阱）。

    只认模块顶层的 `USER_INPUT = <字符串字面量>`；找不到或源码无法解析返回 None。
    """
    # 兜底剥离 BOM：调用方可能直接传字符串（不经 extract_user_input 的文件
    # 读取路径）。与那里的 utf-8-sig 属同一件事的两道防线（有 BOM 就剥掉）。
    source = source.lstrip("\ufeff")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "USER_INPUT"
                   for t in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value.strip() or None
    return None


def extract_user_input(path: str) -> str | None:
    """从用例脚本文件提取 USER_INPUT 常量（AI 生成用例时写入用户原始描述）。

    ⚠️ **必须用 utf-8-sig 读**（不是 utf-8）：用例 .py 由外部工具产出，可能带
    UTF-8 BOM。带 BOM 时 `utf-8` 解出的首字符是 U+FEFF，`ast.parse()` 抛
    SyntaxError → 被下面的 except 吞掉 → **静默返回 None**。

    后果是三重静默（2026-09-14 实测确认）：
      1. `cases.user_input` 写空；
      2. `_should_record()` 判 False → **该用例不入库**；
      3. `_guard_exec_cache()` 用同一个判据 → **执行期缓存守卫静默失效**
         （拿过期数据当结论不再被拦 = 假 PASS 风险）。
    而 `importlib` 加载同一文件是正常的（Python 自己按 utf-8-sig 处理），
    所以用例照跑、PASS、退出码 0 —— 全程没有任何提示。
    `utf-8-sig` 读**无 BOM** 文件与 `utf-8` 行为完全一致，零回归风险。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            source = f.read()
    except OSError:
        return None
    return extract_user_input_from_source(source)


def _warn_missing_user_input(path: str) -> None:
    """无 USER_INPUT 时提示一句（不阻断执行）。

    判定"疑似正式用例"用**文件名是否纯数字**：正式用例约定命名 = `<用例号>.py`
    （119.py / 185.py），辅助脚本是描述性名字（`_collect_185.py`、
    「探查_186_设为当前与清空」）。纯数字名却没有 USER_INPUT → 多半是漏写，
    点出来（否则它跑完静默不入库，人查记录时以为丢数据）。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.isdigit():
        print(f"⚠️ [记录] 正式用例 {stem}.py 缺少 USER_INPUT 常量 → "
              f"本次执行不会写入记录库。\n"
              f"         请在脚本顶部补：USER_INPUT = \"\"\"<用户原始口述用例>\"\"\"")
    else:
        print(f"[记录] {stem} 无 USER_INPUT → 按辅助脚本处理，不入记录库"
              f"（报告/截图/trace 照常生成）")


if __name__ == "__main__":
    main()
