#!/usr/bin/env python3
"""等待合理性审计：**不是"等得久不久"，而是"等得有没有道理"**（人确认 2026-09-16）。

为什么不做"总时长"指标：用例本来就有长有短，执行久是**可接受**的。真正要抓的是
**不合理的等待** —— 那些既浪费时间、又掩盖真实时序、还会制造假 FAIL 的写法。

判据（看 `time.sleep(...)` 之后**紧邻的那条语句**）：

| 类别 | 后续语句 | 判断 | 为什么 |
|---|---|---|---|
| **多余** | 自身会轮询的调用（`wait_*` / `tap_*` / `dismiss_*` / `observe_dialogs` / `launch_app`） | ❌ | 那个调用本来就会等 —— 前面的固定 sleep 是**纯浪费**，且会让后面的超时语义失真（"到底是谁在等？"） |
| **可疑** | 单次查询后判真假（`el_bounds` / `screen_text` / `current_activity` 出现在 `if`/`while` 条件里） | ⚠️ | 这是"睡一觉再看一眼"的碰运气写法：界面慢一点就**假 FAIL**、快一点就白等。可用 `wait_*` 精确表达 |
| **合理** | 读值 / 截图 / 断言 / 循环体（拨档后读值、惯性滚动、页面转场） | ✅ | 等的是**值变化或物理现象**，不是"元素出现" —— `wait_*` 表达不了 |

用法：
    python evals/audit_waits.py cases/                     # 全部用例
    python evals/audit_waits.py cases/com.zui.calendar/178.py
    python evals/audit_waits.py cases/ --only-problems     # 只列 多余/可疑
"""
import argparse
import ast
import io
import os
import sys

# 「睡一觉再去找同一个东西」= 确定的多余：后续调用自己就在轮询**同一目标**
# （元素/Activity）。sleep 在这里是**同一件事做两遍**，且会让后面的超时语义失真。
POLLING_FINDER = {
    "wait_rid", "wait_text", "wait_activity", "wait_gone", "wait_text_contains",
    "tap_rid", "tap_text", "tap_el", "tap_desc", "tap_text_re", "tap_vision",
    "scroll_to_rid", "back",
}
# 自带等待、但**等的可能是另一个条件**：不能判"多余"，只能交人判。
# 例（实测）：`force_stop → sleep(1) → launch_app` 里的 sleep 是等**进程退出**，
# 而 launch_app 等的是"应用起来" —— 两者无关，删掉 sleep 会真的出问题。
SELF_WAITING_OTHER = {
    "dismiss_first_use_dialogs", "observe_dialogs", "launch_app", "force_stop",
    "dismiss_image_hint", "tap_more_menu", "goto_",
}
# 单次查询（不轮询）：sleep 后紧跟它们判真假 = "睡一觉看一眼"。
# ⚠️ 这三类的共同点是**能用 wait_* 表达**：等某个元素/文字/Activity 出现。
SINGLE_SHOT = {"el_bounds", "screen_text", "current_activity", "locate",
               "ime_shown"}
# 读值/留证：这些后面的 sleep 属**合理** settle。
# `read_rid` 刻意放这里（不放 SINGLE_SHOT）：读值前等待是必需的（值落定需要时间），
# 而 `wait_*` 只认"出现/消失"，**表达不了"值变了"** —— 判成可疑是误报。
READ_LIKE = {"screenshot", "record", "assert_true", "assert_false", "tap_xy",
             "read_rid"}

BODY_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.For,
              ast.While, ast.If, ast.With, ast.Try)


def _call_name(node):
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _is_sleep(stmt):
    """是不是 `time.sleep(...)` 表达式语句。"""
    if not isinstance(stmt, ast.Expr):
        return False
    c = stmt.value
    if not isinstance(c, ast.Call):
        return False
    f = c.func
    return (isinstance(f, ast.Attribute) and f.attr == "sleep"
            and isinstance(f.value, ast.Name) and f.value.id == "time")


def _sleep_seconds(stmt):
    if stmt.value.args and isinstance(stmt.value.args[0], ast.Constant):
        v = stmt.value.args[0].value
        return v if isinstance(v, (int, float)) else None
    return None


def _names_in(node):
    """收集节点里出现的调用名（用于判断后续语句在做什么）。"""
    out = set()
    for n in ast.walk(node):
        nm = _call_name(n)
        if nm:
            out.add(nm)
    return out


def _iter_bodies(tree):
    """产出所有语句块。

    ⚠️ **必须同时走 `orelse` / `finalbody` / `handlers`**（2026-09-16 实测踩到）：
    最初只取 `node.body`，于是**所有 `else:` 分支里的 sleep 全被漏掉**
    （`_flow.py:669/:695` 就是权限被拒分支的 settle）—— 一个"只扫一半代码"的
    审计工具比没有更糟：它会给出"已检查、没问题"的结论。
    """
    for node in ast.walk(tree):
        blocks = []
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            blocks = [getattr(node, "body", None)]
        elif isinstance(node, (ast.If, ast.For, ast.While)):
            blocks = [getattr(node, "body", None),
                      getattr(node, "orelse", None)]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            blocks = [getattr(node, "body", None)]
        elif isinstance(node, ast.Try):
            blocks = [node.body, node.orelse, node.finalbody]
            blocks += [h.body for h in node.handlers]
        for b in blocks:
            if b:
                yield node, b


def _classify(nxt, in_loop):
    """返回 (类别, 说明)。nxt 为 None 表示 sleep 是语句块最后一条。"""
    if nxt is None:
        return "合理", "语句块末尾（多为 settle）"
    names = _names_in(nxt)
    if names & POLLING_FINDER:
        hit = sorted(names & POLLING_FINDER)[0]
        # 区分"纯等待"与"动作"：后者删 sleep 有风险（见 _flow.py:115 的点空教训）
        if hit.startswith("wait_"):
            return "多余", (f"紧跟着 `{hit}()`：后面那次 wait 本来就覆盖这段等待，"
                            f"前面的 sleep 可直接删（或把 timeout 并到后者）")
        return "多余", (f"紧跟着 `{hit}()`：它自己会轮询同一目标 ⇒ 等待是重复的。"
                        f"**但不要只删 sleep** —— 若该元素在入场动画中就存在，"
                        f"点会落空；正解是改成 `wait_*` + 点后验证（见 `_flow.py` 的点空教训）")
    if names & SELF_WAITING_OTHER:
        hit = sorted(names & SELF_WAITING_OTHER)[0]
        return "需人判", (f"紧跟着 `{hit}()`：它自带等待，但等的**可能是另一个条件**"
                          f"（或这个 sleep 是为**前一个动作**让路）—— 删前先确认")
    if isinstance(nxt, (ast.If, ast.While)):
        cond = _names_in(nxt.test)
        single = cond & SINGLE_SHOT
        if single:
            hit = sorted(single)[0]
            return "可疑", (f"醒后单次 `{hit}()` 判真假 —— 界面慢一点即假 FAIL，"
                            f"可改 wait_*/wait_text_contains")
    if names & SINGLE_SHOT:
        return "可疑", "醒后单次查询 —— 可改条件等待"
    if names & READ_LIKE:
        return "合理", "后续是读值/留证（值变化类 settle）"
    if in_loop:
        return "合理", "循环体内（逐档点按/滚动，物理节奏）"
    return "需人判", f"后续: {type(nxt).__name__}"


def audit_file(path):
    with io.open(path, encoding="utf-8-sig") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []
    lines = src.splitlines()
    out = []
    for _owner, body in _iter_bodies(tree):
        in_loop = isinstance(_owner, (ast.For, ast.While))
        for i, st in enumerate(body):
            if not _is_sleep(st):
                continue
            nxt = body[i + 1] if i + 1 < len(body) else None
            kind, why = _classify(nxt, in_loop)
            # 同行/上一行有说明性注释 → 视为"已声明理由"（人写过原因就别再催）
            ln = st.lineno
            near = "\n".join(lines[max(0, ln - 2):ln])
            has_note = ("#" in (lines[ln - 1] if ln - 1 < len(lines) else "")) \
                       or ("#" in near)
            out.append({"line": ln, "sec": _sleep_seconds(st), "kind": kind,
                        "why": why, "noted": has_note,
                        "code": (lines[ln - 1].strip()[:80]
                                 if ln - 1 < len(lines) else "")})
    return out


def _iter_targets(root, package=None):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if package and not rel.startswith(package + "/"):
                continue
            yield p


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="等待合理性审计")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--package", help="只扫该包名子目录")
    ap.add_argument("--only-problems", action="store_true",
                    help="只列 多余/可疑（默认全列）")
    args = ap.parse_args()

    files = ([args.target] if os.path.isfile(args.target)
             else list(_iter_targets(args.target, args.package)))
    total = {"多余": 0, "可疑": 0, "需人判": 0, "合理": 0}
    problems = []
    for p in files:
        rows = audit_file(p)
        if not rows:
            continue
        rel = os.path.relpath(p)
        shown = [r for r in rows if r["kind"] != "合理"] or rows
        if args.only_problems:
            shown = [r for r in rows if r["kind"] in ("多余", "可疑")]
        if not shown:
            continue
        print(f"\n{rel}")
        for r in rows:
            total[r["kind"]] = total.get(r["kind"], 0) + 1
        for r in shown:
            icon = {"多余": "❌", "可疑": "⚠️", "需人判": "❓",
                    "合理": "✅"}[r["kind"]]
            note = "" if not r["noted"] else "（已有注释说明）"
            print(f"  {icon} :{r['line']} sleep({r['sec']}) [{r['kind']}]{note}")
            print(f"        {r['why']}")
        for r in rows:
            if r["kind"] in ("多余", "可疑"):
                problems.append((rel, r))

    print("\n" + "=" * 60)
    print("合计：多余 %s ｜ 可疑 %s ｜ 需人判 %s ｜ 合理 %s（共 %s 处）"
          % (total["多余"], total["可疑"], total["需人判"], total["合理"],
             sum(total.values())))
    if problems:
        print("\n❌/⚠️ 明细（按文件）:")
        for rel, r in problems:
            print(f"  {rel}:{r['line']}  [{r['kind']}] {r['why'][:70]}")
    else:
        print("\n✅ 未发现「多余/可疑」的等待")
    return 0


if __name__ == "__main__":
    sys.exit(main())
