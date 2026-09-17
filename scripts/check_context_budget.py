#!/usr/bin/env python3
"""上下文预算门禁：分层检查文档体积（§6.4）。

设计目的
────────
知识卡/文档膨胀是 AI 上下文成本的隐形杀手。行数为主指标（稳定、可读），
token 估算仅作参考。超预算 → exit 1；达 80% → 警告但不红。

**分层（§6.4）**——两层成本结构完全不同，混在一起只有行数一个量纲：

| 层 | 内容 | 何时进上下文 | 判据 |
|---|---|---|---|
| **L1** | SKILL.md frontmatter 的 `description` | **每次调用 skill 都进**（最贵） | token（行数管不住：一行长 description 比十行短 description 更贵） |
| **L2** | SKILL.md / docs / 内置卡 / 场景卡 | 按需读 | 行数 |

用法
────
    # 检查仓库根目录下所有预算文件
    python scripts/check_context_budget.py [仓库根]

    # CI 集成
    python scripts/check_context_budget.py

预算规则
────────
- 精确匹配：文件名完全匹配（如 SKILL.md）
- glob 匹配：模式中含 * 的（如 knowledge/*.md 匹配该目录下所有 .md）
- 同一文件被多条规则匹配时，取最严格（最小预算）的一条

退出码
------
- 0：全部通过（含警告）
- 1：至少一个文件超限（L1 或 L2）

⚠️ **L2 目标不是闸门**：§7.2 M5 的「SKILL.md ≤190 行（−30%）」是**条件目标** ——
它只能靠 P4b（删规则）达成，而 P4b 依赖 P3b 的对抗 eval 验证。在前提未达成前
把它设成闸门，结果就是"永远红"，而红灯指不出任何当下可动的对象（与 App 卡体积
为什么不设闸门是同一个道理，见 BUDGETS 上方注释）。所以目标只报进度。
"""
import glob
import os
import re
import sys

# ── 预算配置 ─────────────────────────────────────────────────────
# (模式, 行数上限)
# 模式说明：
#   无 * → 精确匹配（相对仓库根）
#   含 * → glob 匹配（相对仓库根）
#
# ⚠️ **App 卡（`knowledge/<包名>.md`）刻意不在此列**（2026-09-14 人确认）：
#   它们是**工作区资产** —— `resolve_knowledge_dir()` 工作区优先，setup 时从 skill 包
#   复制一次，之后运行时用的**永远是工作区那份**。而 CI 跑在仓库上、看不到工作区，
#   只能查到这份"种子副本" → **闸门卡住的是一份运行时不生效的文件，红灯指错对象**
#   （实测：卡被改到 518 行时，红的是种子副本，真正生效的工作区副本无任何度量）。
#   App 卡体积改为**可见指标**：见 `report_app_cards()`（只报数、不设闸门）。
BUDGETS = [
    ("SKILL.md",                    280),
    ("knowledge/_system.md",        200),
    ("knowledge/_*.md",             400),   # 内置卡（_system / _template）
    ("knowledge/scenarios/*.md",    80),
    ("docs/*.md",                   400),
]

WARN_RATIO = 0.8   # 达预算 80% → 警告但不红

# ── L1：frontmatter description 的 token 预算（§6.4）──────────────
# description 是**最贵的一层**：每次 skill 被调用都会进上下文，与任务实际需要
# 什么无关。所以它必须比 L2 更早报警。当前实测 ~107 tokens，预算 150 留 ~40%
# 余量 —— 既容得下正常的描述调整，又能在"往 description 里塞功能清单"时拦住。
L1_DESCRIPTION_BUDGET = 150
L1_FILE = "SKILL.md"

# ── L2 目标（**只报进度、不设闸门**）────────────────────────────
# §7.2 M5：SKILL.md ≤190 行（−30%）。条件目标 —— 依赖 P4b 删规则，P4b 依赖 P3b
# 对抗 eval 验证。前提未达成时设成闸门 = 永远红且指不出可动对象（同 App 卡）。
L2_TARGETS = {"SKILL.md": 190}


def _line_count(path):
    """文件行数（UTF-8，容错）。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except (OSError, IOError):
        return None


def _token_estimate(path):
    """粗略 token 估算：中文字符 ×1 + 英文词 ×1.3。仅参考。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except (OSError, IOError):
        return 0
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    en = len(re.findall(r'[a-zA-Z]+', text))
    return int(cn + en * 1.3)


def _resolve_budgets(root):
    """将预算规则展开为 {相对路径: 行数上限}，同文件取最小预算。"""
    result = {}
    for pattern, limit in BUDGETS:
        if "*" in pattern:
            matches = glob.glob(os.path.join(root, pattern))
            for fp in matches:
                rel = os.path.relpath(fp, root).replace("\\", "/")
                if rel not in result or limit < result[rel]:
                    result[rel] = limit
        else:
            fp = os.path.join(root, pattern)
            if os.path.isfile(fp):
                rel = os.path.relpath(fp, root).replace("\\", "/")
                if rel not in result or limit < result[rel]:
                    result[rel] = limit
    return result


def read_description(root=None):
    """读 SKILL.md frontmatter 的 description（L1）。取不到返回 ""。"""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fp = os.path.join(root, L1_FILE)
    try:
        with open(fp, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return ""
    m2 = re.search(r"^description:\s*(.+)$", m.group(1), re.M)
    return m2.group(1).strip() if m2 else ""


def description_tokens(text):
    """description 的 token 估算（与 _token_estimate 同口径）。"""
    cn = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    en = len(re.findall(r"[a-zA-Z]+", text or ""))
    return int(cn + en * 1.3)


def check_l1(root=None):
    """L1 检查：description token 是否超预算。返回 (errors, warnings)。"""
    desc = read_description(root)
    if not desc:
        return [], []          # 没 frontmatter 就不判（不是本门禁的职责）
    n = description_tokens(desc)
    ratio = n / L1_DESCRIPTION_BUDGET if L1_DESCRIPTION_BUDGET else 0
    if n > L1_DESCRIPTION_BUDGET:
        return [f"  ❌ L1 {L1_FILE} description: {n}/{L1_DESCRIPTION_BUDGET} tokens"
                f"（{ratio:.0%}）— 超限！description 每次调用都进上下文，"
                f"功能清单请写进正文而不是这里"], []
    if ratio >= WARN_RATIO:
        return [], [f"  ⚠️ L1 {L1_FILE} description: "
                    f"{n}/{L1_DESCRIPTION_BUDGET} tokens（{ratio:.0%}）— 接近上限"]
    return [], []


def report_l2_targets(root=None):
    """L2 目标进度（只报数、不设闸门，见模块 docstring 的说明）。"""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for rel, target in L2_TARGETS.items():
        fp = os.path.join(root, rel.replace("/", os.sep))
        cur = _line_count(fp)
        if cur is None:
            continue
        gap = cur - target
        rows.append((rel, cur, target, gap))
    return rows


def check(root=None):
    """执行预算检查。返回 (errors, warnings, 规则数)。"""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    budgets = _resolve_budgets(root)
    errors = []
    warnings = []
    # L1 先查（最贵的一层，超限时优先暴露）
    l1e, l1w = check_l1(root)
    errors += l1e
    warnings += l1w
    for rel, limit in sorted(budgets.items()):
        fp = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(fp):
            continue
        lines = _line_count(fp)
        if lines is None:
            continue
        tokens = _token_estimate(fp)
        ratio = lines / limit if limit > 0 else 0
        status = "OK"
        if lines > limit:
            status = "OVER"
            errors.append(
                f"  ❌ {rel}: {lines}/{limit} 行 "
                f"({ratio:.0%}, ~{tokens} tokens) — 超限！"
            )
        elif ratio >= WARN_RATIO:
            status = "WARN"
            warnings.append(
                f"  ⚠️ {rel}: {lines}/{limit} 行 "
                f"({ratio:.0%}, ~{tokens} tokens) — 接近上限"
            )
        else:
            # 只输出摘要，不全量打印 OK 文件
            pass
    return errors, warnings, len(budgets)


def report_app_cards(root=None):
    """App 卡体积：**只报数、不设闸门**（2026-09-14 人确认）。

    为什么不做成闸门：CI 看到的是"种子副本"，运行时用的是工作区那份
    （见 BUDGETS 上方注释）→ 做成闸门只会红灯指错对象。
    但每张 App 卡都是 Agent 每次任务可能要读的东西（400 行 ≈ 6.5k tokens），
    体积值得盯 —— 所以报数，让它可见。
    """
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for fp in sorted(glob.glob(os.path.join(root, "knowledge", "*.md"))):
        base = os.path.basename(fp)
        if base.startswith("_"):
            continue                      # 内置卡（_system / _template）走 BUDGETS
        rows.append((base, _line_count(fp) or 0, _token_estimate(fp)))
    if not rows:
        return
    print("\n📄 App 卡体积（**种子副本**，仅参考；运行时用的是工作区那份）：")
    for base, lines, tokens in rows:
        print(f"   {base}: {lines} 行 (~{tokens} tokens)")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = sys.argv[1] if len(sys.argv) > 1 else None
    errors, warnings, total = check(root)
    print(f"📏 上下文预算门禁（L1 1 条 + L2 {total} 条规则）\n")
    for w in warnings:
        print(w)
    for e in errors:
        print(e)
    # L2 条件目标进度（不是闸门：§7.2 M5 依赖 P4b→P3b）
    for rel, cur, target, gap in report_l2_targets(root):
        if gap <= 0:
            print(f"  🎯 {rel} 已达成条件目标 {target} 行（当前 {cur}）")
        else:
            print(f"  🎯 {rel} 条件目标 {target} 行：当前 {cur}，还差 {gap} 行"
                  "（M5 是**条件目标**，须由 P4b 删规则补，前提是 P3b 对抗验证通过）")
    report_app_cards(root)
    if warnings:
        print(f"\n⚠️ {len(warnings)} 个文件接近预算上限")
    if errors:
        print(f"\n❌ {len(errors)} 个文件超限！请瘦身或调整预算。")
        sys.exit(1)
    else:
        print(f"\n✅ 全部通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
