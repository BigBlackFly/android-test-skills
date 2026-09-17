#!/usr/bin/env python3
"""知识索引生成器 + 卡头部审计（plan §6.1 / §6.2 / §3.4）。

**为什么是脚本而不是手写**：索引腐烂是"元数据路由"的经典死穴 —— 手编的索引
第一次是对的，第三次就开始骗人。生成式索引根治这件事：索引永远是卡头的投影。

用法：
    python scripts/knowledge_index.py            # 生成/刷新 knowledge/_index.md
    python scripts/knowledge_index.py --check    # 校验索引与卡头一致（漂移 → 退出码 1）
    python scripts/knowledge_index.py --audit    # 输出「缓存回流清单」（§3.4）

**头部契约**（§6.1：只限定路由必需的字段，正文仍自由 Markdown）：

    - **app**: `com.zui.calendar`
    - **name**: ZUI 日历（联想平板）
    - **验证版本**: 9.0.0.83          # versionName，过渡期口径（P0 双采用）
    - **versionCode**: 83             # 整数，OTA 里会动；未采集时写 `待采集`
    - **最近验证**: 2026-09-14（178 真机 31/31）

**触发词单一来源**（2026-09-16 人确认）：触发词**只写在「检索索引」节**，头部不重复
（两处都写必然漂移，而漂移的触发词会把索引指向错的小节）。下面的解析函数会自动抽。

解析**必须容错**（现状卡格式并不统一，硬要求会立刻失效）：
  · `- **k**: v` 列表式
  · `**k**: v｜**k2**: v2` 单行合并式（`com.zui.calendar.md` 现状就是这样）
  · 全角/半角冒号、反引号包裹
触发词缺失时回退到「检索索引」表 / 平铺词列表抽取（§6.2 明确要求解析两种格式）。

**CI 边界**（§6.2）：CI 跑在仓库上只能看到种子副本，而运行时 Agent 读的是工作区
那份 —— 所以 `_index.md` **只在本地 setup / 探查后刷新**，CI 不校验其内容
（只报数不设闸门）。`--check` 是本地工具，不要接进 CI。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(ROOT, "knowledge")
INDEX_NAME = "_index.md"

# 头部字段：`**k**: v`（列表式）与 `...｜**k2**: v2`（单行合并式）都能吃
_FIELD_RE = re.compile(r"\*\*(?P<k>[^*\n]+?)\*\*\s*[:：]\s*(?P<v>[^｜|\n]+)")
# 触发词的回退来源：检索索引表体首列
_TABLE_ROW_RE = re.compile(r"^\|(?P<cells>[^|]+)\|")
_INDEX_HEADING_RE = re.compile(r"^#{1,6}\s*.*检索索引")
# 「待采集」标记：versionCode 尚未从真机落盘（版本门禁首次跑后回填）
PENDING = "待采集"

# §3.4 缓存回流审计：卡正文里"换个设备/方向就失效"的实测值
_AUDIT_PATTERNS = (
    (re.compile(r"bounds\s*[≈=~]\s*\("), "bounds 实测值"),
    (re.compile(r"\[\s*TB\d{3}[A-Z]{0,3}\s*\]"), "机型戳（如 [TB323FU]）"),
    (re.compile(r"\b\d{3,4}\s*[×x]\s*\d{3,4}\b"), "屏幕尺寸实测值"),
    (re.compile(r"(?:^|[\s（(])(?:x|y|w|h)\s*[:=]\s*\d{3,}"), "像素实测值"),
)


def _clean(s):
    """去掉反引号 / 空白 / 结尾的说明性括号外空白。"""
    s = (s or "").strip().strip("`").strip()
    return re.sub(r"\s+", " ", s)


def _split_values(v):
    return [x for x in re.split(r"[,，、/;；]", _clean(v)) if x]


def _lead_token(value, pattern):
    """取字段值的**首个有效 token**，丢掉后面的解释性括号。

    现实里字段值常带说明：`9.0.0.83（versionName，过渡期口径）`、
    `待采集（首次真机运行由 §4.1 落盘）`。整串塞进索引会让单元格变成一句话，
    而 `待采集` 判据也会因为多了尾巴而失效（**告警被静默吞掉**）。
    """
    m = re.search(pattern, _clean(value))
    return m.group(0) if m else _clean(value)


_VERSION_TOKEN_RE = r"[0-9][0-9A-Za-z._\-]*"
_CODE_TOKEN_RE = r"(?:\d+|" + PENDING + r")"


def parse_card(path):
    """解析单张 App 卡的头部。返回 dict（无 app 字段 → None，不是 App 卡）。

    `head` 只取文件前 HEAD_CHARS 个字符：正文里出现的 `**xx**: yy` 不算头部字段
    （否则正文随便一句加粗说明就会被当成路由元数据）。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    # 头部 = 文件开头连续的非标题正文；取前 30 行足够覆盖所有现状卡
    head = "\n".join(lines[:30])
    fields = {}
    for m in _FIELD_RE.finditer(head):
        k = _clean(m.group("k"))
        if k and k not in fields:
            fields[k] = _clean(m.group("v"))
    app = fields.get("app")
    if not app:
        return None
    app = app.split()[0]            # `com.x（必填，...）` 这类注释尾巴切掉
    if app.startswith("_"):
        return None                 # `_system` 兜底卡 / `_template` 不进索引
    triggers = _split_values(fields.get("触发词", ""))
    if not triggers:
        triggers = _triggers_from_index_section(lines)
    return {
        "app": app,
        "name": fields.get("name", ""),
        "version_name": _lead_token(fields.get("验证版本", ""), _VERSION_TOKEN_RE),
        "version_code": _lead_token(fields.get("versionCode", ""), _CODE_TOKEN_RE),
        "last_verified": fields.get("最近验证", ""),
        "triggers": triggers,
        "lines": len(lines),
        "path": path,
    }


def _is_index_heading(ln):
    """是否是「检索索引」节标题。

    必须限定形态，否则会误命中**正文里提到这个词**的句子（如 `_template.md`
    的说明文字"命中词补进「检索索引」"）。
    """
    s = ln.strip()
    if "检索索引" not in s:
        return False
    return s.startswith(("#", ">", "**"))


def _triggers_from_index_section(lines):
    """回退：从「检索索引」节里抽触发词。

    现状卡有**三种**格式（§6.2 要求脚本都能解析，实测确认）：

    | 形态 | 实例 |
    |---|---|
    | 表格 | `_template.md`：`\\| 滚轮、时间选择器 \\|「时间选择器」\\|` |
    | 平铺词列表 | `com.android.settings.md` / `com.zui.launcher.md`：换行逗号串 |
    | 引用块散文 | `com.zui.calendar.md`：`> 「导入 / 弹窗 / 图库」→「标准链路」` |

    判定顺序：**先找 `「词」→「节」` 映射式**（有就用它，散文式里词被引号包着、
    最好抽）；没有再去啃平铺列表（跳过解释性句子）。
    """
    start = None
    for i, ln in enumerate(lines):
        if _is_index_heading(ln):
            start = i + 1
            break
    if start is None:
        return []
    body = []
    for ln in lines[start:start + 40]:
        if ln.strip().startswith("#"):      # 下一节 → 停
            break
        body.append(ln)

    # ① 映射式：只取「→」**左边**的词（右边是小节名，不是触发词）
    quoted = []
    for ln in body:
        left = ln.split("→")[0]
        for g in re.findall(r"「([^」]+)」", left):
            quoted += _split_values(g)
    if len(quoted) >= 2:
        return _dedup(quoted)[:12]

    # ② 表格式：表体首列
    words = []
    for ln in body:
        m = _TABLE_ROW_RE.match(ln.strip())
        if not m:
            continue
        cell = m.group("cells").split("|")[0].strip()
        if not cell or set(cell) <= set("-: ") or "触发词" in cell:
            continue
        words += _split_values(cell)

    # ③ 平铺列表式：跳过解释性句子（含 ** 或句末句号的长句）
    if not words:
        for ln in body:
            s = ln.strip()
            if not s or "**" in s or s.endswith("。"):
                continue
            s = s.lstrip("-*•> ").strip()
            if not s or s.startswith("|"):
                continue
            s = re.sub(r"^触发词\s*[:：]\s*", "", s)
            items = _split_values(s)
            if len(items) >= 2:
                words += items
    return _dedup(words)[:12]


def _dedup(words):
    out, seen = [], set()
    for w in words:
        w = _clean(w).strip("、,，;；/")
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def scan_cards(kdir=KNOWLEDGE_DIR):
    """扫描目录下所有 App 卡（跳过 `_` 前缀），按包名排序。"""
    if not os.path.isdir(kdir):
        return []
    cards = []
    for name in sorted(os.listdir(kdir)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        c = parse_card(os.path.join(kdir, name))
        if c:
            cards.append(c)
    return cards


def _cell(s):
    """表格单元格转义（竖线会破坏表格结构）。"""
    return _clean(s).replace("|", "/") or "—"


def render_index(cards, generated_at=None):
    """生成 `_index.md` 内容。"""
    from datetime import datetime
    ts = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "# 知识索引（自动生成，**禁止手编**）",
        "",
        f"> 由 `scripts/knowledge_index.py` 于 {ts} 生成。",
        "> 手编会立刻漂移：改了卡头就跑一次脚本，别改本文件。",
        "> 它同时是 §4.1 版本门禁的比对数据源（versionCode 列）。",
        "",
        "| 包名 | versionCode | 验证版本 | 卡片行数 | 最近验证 | 触发词 |",
        "|---|---|---|---|---|---|",
    ]
    for c in cards:
        vc = c["version_code"] or PENDING
        out.append(
            f"| `{c['app']}` | {_cell(vc)} | {_cell(c['version_name'])} | "
            f"{c['lines']} | {_cell(c['last_verified'])} | "
            f"{_cell('、'.join(c['triggers']))} |")
    pending = [c["app"] for c in cards if not c["version_code"]
               or c["version_code"] == PENDING]
    out += [
        "",
        f"共 {len(cards)} 张 App 卡。",
    ]
    if pending:
        out += [
            "",
            f"⚠️ **{len(pending)} 张卡尚未采集 versionCode**："
            + "、".join(f"`{p}`" for p in pending),
            "",
            "versionCode 由版本门禁（§4.1）在**首次真机运行**时采集落盘，",
            "回填卡头后再跑本脚本刷新本行即可。P2 卡头部改造完成前，",
            "版本比对仍走 versionName（双采过渡期）。",
        ]
    return "\n".join(out) + "\n"


def write_index(kdir=KNOWLEDGE_DIR, cards=None):
    cards = scan_cards(kdir) if cards is None else cards
    path = os.path.join(kdir, INDEX_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_index(cards))
    return path, cards


def check_index(kdir=KNOWLEDGE_DIR):
    """校验现有索引与卡头一致。返回 (ok, detail)。

    比对时**忽略生成时间戳行**：时间每次都变，拿它当漂移会把校验变成噪音。
    """
    path = os.path.join(kdir, INDEX_NAME)
    if not os.path.isfile(path):
        return False, f"{INDEX_NAME} 不存在（先跑一次生成）"
    with open(path, encoding="utf-8") as f:
        cur = f.read()
    cards = scan_cards(kdir)
    want = render_index(cards)
    norm = lambda t: [ln for ln in t.splitlines() if "由 `scripts/" not in ln]
    if norm(cur) == norm(want):
        return True, f"{len(cards)} 张卡与索引一致"
    # 给出差异行，便于直接定位
    cs, ws = set(norm(cur)), set(norm(want))
    diff = [ln for ln in norm(want) if ln not in cs][:5]
    return False, "索引与卡头不一致（卡头改了但没刷新索引）:\n    " + \
                  "\n    ".join(diff or ["（内容相同、仅顺序/空白差异）"])


def audit(kdir=KNOWLEDGE_DIR):
    """§3.4 缓存回流清单：卡正文里的设备实测值 / 机型戳。

    这是**清单**不是门禁：有些实测值是解释性的（如"本机型常在横屏 3040×1904
    下跑完"用于说明方向问题），判成违规会把真相淹掉。清单交给人逐条判断：
    能换成运行时派生就换，换不了打 `# noqa` 留痕（§3.4 第 2 步）。
    """
    hits = []
    if not os.path.isdir(kdir):
        return hits
    for name in sorted(os.listdir(kdir)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = os.path.join(kdir, name)
        try:
            with open(path, encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, ln in enumerate(lines, 1):
            for rx, why in _AUDIT_PATTERNS:
                if rx.search(ln):
                    hits.append((name, i, why, ln.strip()[:110]))
                    break
    return hits


def _main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = argv[1:]
    if "--check" in args:
        ok, detail = check_index()
        print(("✅ " if ok else "❌ ") + detail)
        return 0 if ok else 1
    if "--audit" in args:
        hits = audit()
        if not hits:
            print("✅ 未发现设备实测值 / 机型戳")
            return 0
        print(f"§3.4 缓存回流清单：{len(hits)} 处（清单交人判断，非门禁）\n")
        for name, ln, why, text in hits:
            print(f"  {name}:{ln}  [{why}]  {text}")
        print("\n处置：能换成 region_of / anchor 派生的就换；"
              "换不了的打 `# noqa` 留痕并进提案队列（§3.4）。")
        return 0
    path, cards = write_index()
    print(f"✅ 已生成 {os.path.relpath(path)}（{len(cards)} 张 App 卡）")
    pending = [c["app"] for c in cards
               if not c["version_code"] or c["version_code"] == PENDING]
    if pending:
        print(f"   ⚠️ {len(pending)} 张卡 versionCode 待采集（首次真机运行后回填）")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
