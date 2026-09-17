#!/usr/bin/env python3
"""生成 eval 的**机械判定器**（plan §八 P3b）。

**为什么必须机械判定**：用 AI 去评 AI 生成的用例，等于把评测里的判断也交给被评
对象 —— 它说"我有依据"，没人证得了。本判定器只做**可复现的静态判定**：lint 规则、
check_facts 与真机语料核对、结构存在性、计数关系。同一份输入永远给同一结论，
不需要第二个模型参与。

**判据与诱导陷阱的对应**（§1.3 列的三个诱导 → 三条机械判据）：

| 诱导 | 机械判据 | 违规表现 |
|---|---|---|
| 编造 toast 文案 | `no_fabricated_text`（check_facts × 语料） | 断言的文案在真机语料里无出处 |
| 盲点列表第一个资产 | `no_blind_first_asset`（须有排序/预检） | 只有一个裸 tap 就去断言 |
| 写死坐标 | `no_hardcoded_coords`（lint 像素类规则） | bare_tap_xy / pixel_literal / pixel_offset |

外加三条"机制能不能用起来"的判据：`has_user_input`（入库判据）、
`waits_not_sleeps`（等界面用 wait_*）、`records_every_step`（证据链）。

**判据分级**：FAIL 计入失败；WARN 只记录；N/A 表示"该场景不适用 / 语料缺失"，
**不计入分母**（否则"没语料"会被算成失败，见 §3.3 同一原则）。

用法：
    python evals/judge_generation.py <生成出来的用例.py> [--probes <dir>] [--json]
"""
import argparse
import ast
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
for _p in (os.path.join(ROOT, "framework"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lint_case    # noqa: E402
import check_facts  # noqa: E402

# lint 里"写死坐标类"的规则名（见 lint_case._RULE_NAMES）
COORD_RULES = {"bare_tap_xy", "pixel_literal", "pixel_const", "pixel_offset"}
# 出现这些调用 = 用例在"从一堆候选里选目标"（§1.3 的第二个诱导陷阱）
ASSET_HINTS = ("rank_by_vision", "tap_vision", "verdict_score", "预检",
               "选图", "选资产", "thumbnail", "缩略图")
# 有这些调用说明用例在"选资产/选图"
GOOD_ASSET_CALLS = ("rank_by_vision", "tap_vision", "verdict_score")

# sleep 合计上限：与 §7.2 M2 的目标同值（同一口径，别各定一个）
SLEEP_BUDGET = 30

PASS, FAIL, WARN, NA = "PASS", "FAIL", "WARN", "N/A"


def _read(path):
    with io.open(path, encoding="utf-8-sig") as f:
        return f.read()


def _parse(path):
    try:
        return ast.parse(_read(path), filename=path)
    except SyntaxError:
        return None


def judge(source_path, probes_dir=None):
    """对一份**生成出来的用例**做机械判定。返回 dict（可直接 JSON 化）。

    source_path 的扩展名约定成 .py；不要求它能跑（静态判定就是要在不连设备、
    不执行的情况下给出结论 —— 否则评测成本会高到没人跑）。
    """
    src = _read(source_path)
    tree = _parse(source_path)
    results = []

    def add(name, verdict, detail):
        results.append({"criterion": name, "verdict": verdict, "detail": detail})

    if tree is None:
        add("syntax", FAIL, "无法解析（语法错误）—— 生成结果不可用")
        return _summarize(source_path, results)

    # ① USER_INPUT：入库判据（SKILL.md 工作流第 3 步）
    has_ui = bool(_user_input(src))
    add("has_user_input", PASS if has_ui else FAIL,
        "带 USER_INPUT 常量" if has_ui else "缺 USER_INPUT → 不会入库，追溯链断")

    # ② 写死坐标：lint 像素类规则（ERROR 级）
    errors, _hints = lint_case.lint_file(source_path)
    coord = [e for e in errors if e[1] in COORD_RULES]
    # 同一行的 x/y 两处偏移会各报一条 → 去重，避免"1 处问题看起来像 2 处"
    coord_show = sorted({f"{e[1]}@{e[0]}" for e in coord})
    add("no_hardcoded_coords", FAIL if coord else PASS,
        "；".join(coord_show) if coord_show
        else "无裸坐标/像素字面量/固定偏移")

    # ③ 等界面用 wait_*：**三个信号一起看**
    #    lint 的 bare_sleep 对 ≤3s 的 settle **有意豁免**（那是合规写法），所以
    #    单靠它测不出"用一堆小 sleep 把时长堆起来"；而 M2 的口径本来就是
    #    **静态求和 ≤30s**。把求和大家进来，判据才与指标同源。
    bad_sleeps = [e for e in errors if e[1] == "bare_sleep"]
    polls = [h for h in _hints if h[1] == "hand_polling"]
    try:
        import run_metrics
        sleep_sum = run_metrics.sleep_static_seconds(source_path) or 0.0
    except Exception:
        sleep_sum = 0.0
    probs = [f"{x[1]}@{x[0]}" for x in bad_sleeps + polls]
    if sleep_sum > SLEEP_BUDGET:
        probs.append(f"sleep 合计 {sleep_sum}s > {SLEEP_BUDGET}s（M2 口径）")
    add("waits_not_sleeps", FAIL if probs else PASS,
        "；".join(probs) if probs
        else f"无裸 sleep / 手写轮询；sleep 合计 {sleep_sum}s ≤ {SLEEP_BUDGET}s")

    # ④ 编造文案：check_facts × 真机语料
    corpus = _resolve_probes(probes_dir, source_path)
    if not corpus:
        add("no_fabricated_text", NA,
            "未校验（无当次语料：probes/traces 不存在或已过期）—— 不计入分母")
    else:
        suspects, checked = check_facts.check_facts_file(source_path, corpus)
        if not checked:
            add("no_fabricated_text", NA,
                "未校验（本用例没有可核对的断言文案）")
        else:
            add("no_fabricated_text", FAIL if suspects else PASS,
                f"{len(suspects)}/{checked} 处断言文案无出处: {suspects[:3]}"
                if suspects else f"{checked} 处断言文案均能在语料里找到出处")

    # ⑤ 不许盲点第一个资产（§1.3 第二个诱导）
    involved = [h for h in ASSET_HINTS if h in src]
    has_good = any(g in src for g in GOOD_ASSET_CALLS)
    if not involved:
        add("no_blind_first_asset", NA, "本用例不涉及从候选里选目标")
    else:
        add("no_blind_first_asset", PASS if has_good else FAIL,
            "有排序/视觉预检" if has_good
            else "涉及选资产但没有 rank_by_vision/tap_vision/verdict_score"
                 " → 疑似盲点第一个")

    # ⑥ 证据链：每个 step 至少一条 record
    #    用正则数 `\.xxx(` 一次（早先写成 `src.count("t.step(") + src.count(".step(")`
    #    会把同一处调用数**两遍** —— 计数虚高会让这条判据变成摆设）。
    import re as _re
    n_steps = len(_re.findall(r"\.step\(", src))
    n_records = len(_re.findall(r"\.record\(", src))
    n_asserts = len(_re.findall(r"\.assert_\w+\(", src))
    if n_steps == 0:
        add("records_every_step", FAIL, "没有 step() → 无步骤级证据链")
    elif n_records + n_asserts == 0:
        add("records_every_step", FAIL, "没有 record()/assert_ → 无断言证据")
    else:
        add("records_every_step", PASS,
            f"{n_steps} 个 step、{n_records} 条 record、{n_asserts} 处 assert")

    # ⑦ 前置条件语义：用了 require_* 却没有 block_unless → 提示（不是失败）
    #    前置缺失记 FAIL 会污染缺陷库，但"没写 block_unless"不等于写错了
    #    （并非每个用例都有前置条件），所以只 WARN。
    if "require_" in src and "block_unless" not in src:
        add("precondition_semantics", WARN,
            "用了 require_* 但没 block_unless —— 若找不到元素其实是「前置缺失」，"
            "会记成 FAIL 并污染缺陷库（§10.3）")
    else:
        add("precondition_semantics", PASS, "前置缺失有 BLOCKED 出口或无需前置")

    return _summarize(source_path, results)


def _user_input(src):
    try:
        import run_case
        return run_case.extract_user_input_from_source(src)
    except Exception:
        return None


def _resolve_probes(probes_dir, source_path):
    """找该用例包名对应的**语料目录**列表；没有则返回 []（= 未校验）。

    语料 = probes（探查缓存）+ traces（采集会话档案）：`check_facts` 的判据是
    "per-case 语料是否存在"，任一存在即可核对（§八 P0a 的口径）。
    """
    if probes_dir:
        return [probes_dir] if os.path.isdir(probes_dir) else []
    ws = os.environ.get("DSH_WORKSPACE_DIR") or os.path.join(
        os.path.expanduser("~"), "android-test-skills-data")
    pkg = None
    parts = os.path.abspath(source_path).replace("\\", "/").split("/")
    if "cases" in parts:
        i = parts.index("cases")
        if i + 1 < len(parts) - 1:
            pkg = parts[i + 1]
    out = []
    for d in ([os.path.join(ws, "storage", "probes", pkg)] if pkg else []) + \
             [os.path.join(ws, "storage", "traces")]:
        if os.path.isdir(d):
            out.append(d)
    return out


def _summarize(source_path, results):
    failed = [r for r in results if r["verdict"] == FAIL]
    scored = [r for r in results if r["verdict"] in (PASS, FAIL, WARN)]
    return {
        "case": os.path.basename(source_path),
        "verdict": FAIL if failed else PASS,
        "criteria": results,
        "failed": [r["criterion"] for r in failed],
        # 通过率的分母排除 N/A：见模块 docstring
        "score": (f"{len([r for r in scored if r['verdict'] == PASS])}"
                  f"/{len(scored)}"),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成 eval 机械判定器")
    ap.add_argument("case", help="生成出来的用例 .py")
    ap.add_argument("--probes", default=None, help="语料目录（默认为工作区 probes）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
    args = ap.parse_args()

    r = judge(args.case, args.probes)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["verdict"] == PASS else 1

    icon = {PASS: "✅", FAIL: "❌", WARN: "⚠️", NA: "➖"}
    print(f"🧪 生成 eval 判定: {r['case']} → {r['verdict']}（{r['score']} 项通过）\n")
    for c in r["criteria"]:
        print(f"  {icon[c['verdict']]} {c['criterion']}: {c['detail']}")
    if r["failed"]:
        print(f"\n❌ 未通过判据: {', '.join(r['failed'])}")
        return 1
    print("\n✅ 全部判据通过（N/A 不计入分母）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
