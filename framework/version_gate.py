#!/usr/bin/env python3
"""版本门禁 + 变更归因的双信号源（plan §4.1 / §10.2）。

信号 1：versionCode / versionName 变化 → **APK 替换**
信号 2：元素集合指纹（rid 集合 hash）变化 → **服务端变更 / A/B 切换**

**为什么必须双信号**：versionCode 只能捕获 APK 替换；而运营弹窗是服务端下发、
文案微调可能是 A/B 测试 —— **都不随版本号走**，且这类漂移的发生频率远高于 OTA
（§4.1「信号源修正」）。只盯版本号会大面积漏报，这正是"用例今天过、明天不过"
最常见的那类原因。

门禁定位：**只打 WARN、不废缓存**。它的价值不是预测，是挂的时候消灭歧义 ——
把"排查半天"变成"一眼归因"。

归因码（§10.2 决策表，判定按表序、**先命中先归因**）：

| 码 | 含义 |
|---|---|
| `no_baseline` | 首次运行、无可比基线（**跳过对比，不标"未归因"**） |
| `script` | 用例脚本自己改过 —— 最常见，必须先排除它 |
| `apk` | APK 替换（版本信号变） |
| `config` | 版本/脚本都没变但界面变了 → 服务端/配置（A/B、运营下发） |
| `unchanged` | 与上次一致（正常情况，不告警） |

`script` 与 `unchanged` **都不打 WARN**：前者不是产品变更（SKILL.md 原话：
失败历史多为"改脚本改出来的中间态"），给它 WARN 会稀释真信号。
"""
import os
import re

# 知识卡头部「验证版本」（versionName 口径）。卡里形如：
#   - **app**: `com.zui.calendar`｜**验证版本**: 9.0.0.83｜**最近验证**: ...
CARD_VERSION_RE = re.compile(r"验证版本[\*\s]*[:：]\s*([0-9][0-9A-Za-z._\-]*)")

# `dumpsys package <pkg>` 里的版本字段
_DUMPSYS_NAME_RE = re.compile(r"versionName=(\S+)")
_DUMPSYS_CODE_RE = re.compile(r"versionCode=(\d+)")

# 只看卡片头部多少字符：正文里出现的版本号不是"验证版本"字段
_CARD_HEAD_CHARS = 2000


def parse_version(text):
    """从 `dumpsys package <pkg>` 输出取 (versionName, versionCode)。

    两者都取**第一次**出现：dumpsys 的 included 段可能把依赖包再列一遍，
    主包那一段才是被测 App 自己的。
    解析不到 → None（**不猜**，门禁宁可不判也不误判）。
    """
    text = text or ""
    m = _DUMPSYS_NAME_RE.search(text)
    name = m.group(1) if m else None
    m = _DUMPSYS_CODE_RE.search(text)
    code = m.group(1) if m else None
    return name, code


def read_card_version(knowledge_dirs, pkg):
    """知识卡头部「验证版本」（versionName 口径）。

    找不到卡 / 卡里没该字段 → None。**缺失 ≠ 失配**：不少卡根本没有这个字段
    （§4.1 实测：5 张卡里只有 1 张有），把它当"版本变了"会造成大面积误报。
    """
    if not pkg:
        return None
    for d in knowledge_dirs or ():
        try:
            with open(os.path.join(d, f"{pkg}.md"), encoding="utf-8") as f:
                head = f.read(_CARD_HEAD_CHARS)
        except (OSError, UnicodeDecodeError):
            continue
        m = CARD_VERSION_RE.search(head)
        if m:
            return m.group(1)
    return None


def norm_version(v):
    """归一化版本名：丢掉构建后缀。

    `9.0.0.83-2026.07.22-release` → `9.0.0.83`。

    **为什么必须归一化**（2026-09-16 真机实测）：知识卡里的「验证版本」是人手写
    的、常写简写 —— 实测 `com.zui.calendar.md` 写 `9.0.0.83`，而真机 versionName
    是 `9.0.0.83-2026.07.22-release`。逐字符比会在**每次运行**都报"版本失配"，
    而**假 WARN 比漏报更糟**：它会训练人忽略这条告警，等真失配时也就没人看了。
    """
    return (v or "").strip().split("-")[0].split("+")[0]


def card_mismatch_line(card_version, app_version_name):
    """知识卡「验证版本」与真机版本名失配 → 告警文案；一致（或无从判断）→ None。

    **单一来源**（2026-09-16 真机实测教训）：这个判断有两处调用方 ——
    `run_case`（启动时立即打印，让人一眼看到）和 `TestCase.finish`（进结论）。
    两处各写一份"精确比较"的副本必然漂移：当时只给 finish 那条加了归一化，
    结果启动时照样报假 WARN。抽成函数后只有一个口径。
    """
    if not card_version or not app_version_name:
        return None
    if norm_version(card_version) == norm_version(app_version_name):
        return None
    return (f"知识卡「验证版本」={card_version}，当前 App={app_version_name}"
            "（缓存可能是旧版本时期的）")


def _ver_changed(prev, cur):
    for k in ("app_version_code", "app_version_name"):
        a, b = prev.get(k), cur.get(k)
        if a and b and a != b:
            return True
    return False


def classify(prev, cur):
    """按 §10.2 决策表返回 (归因码, 人话说明)。**先命中先归因**。

    判定顺序（对 §10.2 表序的一处有意细化）：
      no_baseline → script → apk → **layout（换设备）** → config → unchanged

    `layout` 刻意排在 `config` **之前**：换设备时 rid 集合天然会不同，若让
    `config` 先命中，每次换机都会误报"服务端配置变更"（假信号比漏报更糟 ——
    它会稀释真信号）。§10.2 只规定"复跑/换设备仅在脚本与版本均未变时参与判定"，
    未规定它与元素集合的先后，这里按"避免误报"来定。
    """
    if not prev:
        return "no_baseline", "首次运行（无可比基线）"
    if not prev.get("script_hash") and not prev.get("rid_set_hash"):
        # 有历史记录、但**没有可比字段**（RunMetrics 上线前的旧记录，全为 NULL）。
        # 不这么判的话会落进 "unchanged" —— 而 "unchanged" 是"比过了、没变"的
        # 结论，与"压根没法比"完全不同（实测 2026-09-16：168 首跑就报 unchanged）。
        return "no_baseline", "历史记录缺少可比字段（旧记录）→ 跳过对比"
    pk, ck = prev.get("script_hash"), cur.get("script_hash")
    if pk and ck and pk != ck:
        return "script", f"用例脚本已改（{pk}→{ck}）"
    if _ver_changed(prev, cur):
        pv = prev.get("app_version_name") or prev.get("app_version_code")
        cv = cur.get("app_version_name") or cur.get("app_version_code")
        return "apk", f"App 版本变化（{pv}→{cv} → APK/OTA）"
    pd, cd = prev.get("device"), cur.get("device")
    if pd and cd and pd != cd:
        return "layout", f"换设备（{pd}→{cd}）→ 布局变体，非产品变更"
    pr, cr = prev.get("rid_set_hash"), cur.get("rid_set_hash")
    if pr and cr and pr != cr:
        return "config", "版本未变但界面已变 → 服务端下发 / A-B 切换（配置类）"
    return "unchanged", "与上次一致"


def gate_warn_lines(prev, cur, card_version=None):
    """门禁要打 WARN 的行（返回 (lines, 归因码)；lines 为空 = 不告警）。

    - 与知识卡「验证版本」失配 → WARN（缓存可能是旧版本时期的）
    - `apk`（版本信号变）→ WARN
    - `config`（仅信号 2 变）→ WARN，文案明确"版本未变但界面已变"
    仅 `script` / `unchanged` **不告警**（见模块 docstring 的理由）。
    """
    code, detail = classify(prev, cur)
    lines = []
    # 与 run_case 启动时那条共用同一函数（单一来源，见 card_mismatch_line）
    m = card_mismatch_line(card_version, cur.get("app_version_name"))
    if m:
        lines.append(m)
    if code in ("apk", "config"):
        lines.append(detail)
    return lines, code
