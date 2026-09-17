#!/usr/bin/env python3
"""交叉比对 lint：case 字面量 × probes 落盘数值（plan §3.3）。

**检测什么**：用例里的整型字面量如果和探查期缓存的数值（OCR 坐标/置信度、
dump bounds、meta 里的尺寸）相同，说明这个值很可能是**缓存值回流**——把"当时
那台设备上量到的数"当成了常量写进用例。换设备/换方向即失效。

**为什么是独立脚本、且不进 CI 门禁**（§3.3 明确要求）：
- 数据源是 `storage/probes/`，而 `run_case` 启动即清理 30 分钟未访问项
  → CI（无设备）上 probes 必然不存在 → 接进 CI 只会永远"未校验"或误杀。
- 本工具**定位为本地探索期工具**：无语料时明确打印"未校验"，既不判通过也不判违规。

**两个设计参数先量化再定级别**（§3.3，勿凭感觉定）：
- 容差 `--tol`：默认 ±10。像素微调（±1~3）在换 DPI 时同样失效，但容差过大会
  把 `timeout=60` 这类语义常量误判成坐标值。
- 误报率：本工具**每跑一次就打印分母**（检查了多少个字面量、命中多少），
  先看两周真实数据再决定升不升 ERROR。

用法：
    python evals/lint_probes.py cases/com.zui.calendar/178.py
    python evals/lint_probes.py --package com.zui.calendar --all
    python evals/lint_probes.py <case> --probes <自定义 probes 目录> --tol 5
"""
import argparse
import ast
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# 复用主 lint 的"像素参数位"判据（§3.3：不做全 AST 扫描，只收窄到像素参数位）
from lint_case import _PIXEL_PARAMS, _PIXEL_OFFSET_PARAMS  # noqa: E402

DEFAULT_TOL = 10
# 数值来源：只在"几何/置信度"字段里取，避免把语义常量当坐标
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def default_probes_dir():
    ws = os.environ.get("DSH_WORKSPACE_DIR") or os.path.join(
        os.path.expanduser("~"), "android-test-skills-data")
    return os.path.join(ws, "storage", "probes")


def collect_probe_numbers(probes_dir, pkg=None):
    """收集 probes 落盘数值集合。

    只取**几何/置信度**相关字段：dump.xml 的 bounds、ocr.json 的坐标与 conf、
    meta.json 的尺寸类字段。不无脑抓全文数字 —— 那会把版本号、计数、时间戳
    一起吞进来，误报率会高到没法用（§3.3 的误报风险正是指这个）。
    """
    nums = set()
    if not os.path.isdir(probes_dir):
        return nums
    root = os.path.join(probes_dir, pkg) if pkg else probes_dir
    if not os.path.isdir(root):
        return nums
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            path = os.path.join(dirpath, fn)
            try:
                with io.open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            low = fn.lower()
            if low.endswith(".xml"):
                # dump.xml：只取 bounds 属性里的 4 个数
                for b in re.findall(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                                    text):
                    nums.update(int(x) for x in b)
            elif low.endswith(".json"):
                nums.update(_json_geometry_numbers(text))
    return nums


def _json_geometry_numbers(text):
    """从 JSON 文本里取几何/置信度字段的数字（键名白名单）。"""
    out = set()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return out
    keys = ("x", "y", "cx", "cy", "x_min", "x_max", "y_min", "y_max",
            "left", "top", "right", "bottom", "width", "height", "w", "h",
            "conf", "confidence", "score", "bounds", "region")

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    if isinstance(v, (int, float)):
                        out.add(int(v))
                    elif isinstance(v, (list, tuple)):
                        out.update(int(i) for i in v
                                   if isinstance(i, (int, float)))
                    elif isinstance(v, str):
                        out.update(int(float(x)) for x in _NUM_RE.findall(v))
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(data)
    return out


def _is_int_literal(node):
    """整型字面量（含 `-5` 这种 UnaryOp 形态）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return True
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, int)
            and not isinstance(node.operand.value, bool))


def _int_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        return -node.operand.value
    return None


def collect_case_literals(path):
    """取用例里**值得交叉比对**的整型字面量。

    范围刻意收窄（§3.3）：① 模块级 `CONST = 123` 常量；② 像素参数位上的字面量
    （复用 lint 规则 5/6/7 的判据）。不做全 AST 扫描 —— 那样 `range(10)`、
    `timeout=60` 全进来了，误报率没法看。
    返回 [(lineno, 值, 来源说明)]。
    """
    with io.open(path, encoding="utf-8-sig") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    out = []

    # ① 模块级常量
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not _is_int_literal(node.value):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not names:
            continue
        out.append((node.lineno, _int_value(node.value),
                    f"模块级常量 {names[0]}"))

    # ② 像素参数位
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = ""
        if isinstance(node.func, ast.Attribute):
            fn = node.func.attr
        elif isinstance(node.func, ast.Name):
            fn = node.func.id
        spec = _PIXEL_PARAMS.get(fn) or _PIXEL_OFFSET_PARAMS.get(fn)
        if not spec:
            continue
        pos_idx, kw_names = spec
        cands = [(node.lineno, a) for i, a in enumerate(node.args)
                 if i in pos_idx]
        cands += [(node.lineno, kw.value) for kw in node.keywords
                  if kw.arg in kw_names or kw.arg == "bounds"]
        for lineno, c in cands:
            for sub in ast.walk(c):
                if _is_int_literal(sub):
                    out.append((lineno, _int_value(sub), f"{fn}() 像素参数位"))
    return out


def cross_check(case_path, probe_nums, tol=DEFAULT_TOL):
    """返回 (hits, checked_count)。hits = [(lineno, 值, 来源, 命中的 probes 值)]。

    `checked_count` = 参与比对（且值够大、够像坐标）的字面量个数 —— 它就是
    **误报率的分母**，必须打印出来，否则"0 命中"无法区分"真干净"和"没检查"。
    """
    hits = []
    checked = 0
    for lineno, val, src in collect_case_literals(case_path):
        # 只比"像坐标/尺寸"的值：<10 的值（0/1/2/3）到处都是，比了全是噪音
        if val is None or abs(val) < 10:
            continue
        checked += 1
        for p in probe_nums:
            if abs(p - val) <= tol:
                hits.append((lineno, val, src, p))
                break
    return hits, checked


def _iter_cases(root, package=None):
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith("_") and
                   d not in ("__pycache__",)]
        for fn in sorted(files):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if package and not rel.startswith(package + "/"):
                continue
            out.append(p)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="交叉比对 lint（本地探索期工具）")
    ap.add_argument("case", nargs="?", help="用例文件路径")
    ap.add_argument("--package", help="按包名扫全部用例")
    ap.add_argument("--all", action="store_true", help="扫 cases/ 下全部用例")
    ap.add_argument("--probes", default=None, help="probes 目录（默认取工作区）")
    ap.add_argument("--tol", type=int, default=DEFAULT_TOL, help="容差（默认 ±10）")
    args = ap.parse_args()

    probes_dir = args.probes or default_probes_dir()
    if args.package or args.all:
        targets = _iter_cases(os.path.join(ROOT, "cases"), args.package)
    elif args.case:
        targets = [args.case]
    else:
        ap.error("需要 <case> 或 --package / --all")

    total_hits, total_checked, no_corpus = 0, 0, 0
    for path in targets:
        pkg = None
        parts = os.path.abspath(path).replace("\\", "/").split("/")
        if "cases" in parts:
            i = parts.index("cases")
            if i + 1 < len(parts) - 1:
                pkg = parts[i + 1]
        nums = collect_probe_numbers(probes_dir, pkg)
        rel = os.path.relpath(path, ROOT)
        if not nums:
            # §3.3：无语料明确说"未校验"——既不判通过也不判违规
            print(f"  ⚠️ {rel}: 未校验（{pkg or '?'} 的 probes 不存在或已过期）")
            no_corpus += 1
            continue
        hits, checked = cross_check(path, nums, args.tol)
        total_hits += len(hits)
        total_checked += checked
        if hits:
            for lineno, val, src, p in hits:
                print(f"  💡 {rel}:{lineno} [{src}] 字面量 {val} ≈ probes 里的 {p}"
                      f"（容差 ±{args.tol}）——疑似缓存值回流，改用运行时派生")
        else:
            print(f"  ✅ {rel}: 无命中（检查了 {checked} 个候选字面量）")

    print()
    if no_corpus == len(targets):
        print(f"结论：未校验（{no_corpus} 个文件都无语料）。"
              "probes 被 30 分钟规则清空后属正常，不是失败。")
        return 0
    print(f"结论：检查 {total_checked} 个候选字面量，命中 {total_hits} 个"
          f"（容差 ±{args.tol}）。")
    print("级别：**HINT**（本地工具，不进 CI 门禁）。"
          "误报率 = 命中数 / 检查数，先攒两周真实数据再决定是否升 ERROR（§3.3）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
