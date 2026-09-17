#!/usr/bin/env python3
"""探针：空课表周视图「点空格 → 加号」的真实交互（`iv_add_hint` 是否还在）。

## 为什么要这个探针

2026-09-16 全量套件里 **176 / 182 双双 FAIL**，失败点一模一样：
`点空格后(加号浮标) iv_add_hint 未出现`；且同一轮框架的指纹检测器报出
`[WARN] 版本/界面漂移（config）：版本未变但界面已变` —— 指向 App 侧 UI 变了
（服务端下发 / A-B 切换），用例依赖的 rid 可能已经失效。

先把「等 1.2s 太短」的时序假设证伪了（改成 `wait_rid(5s)` 后仍不出现），
所以必须**实地看一眼点空格之后 UI 树里到底有什么**，而不是继续猜。

## 回答三个问题

1. 点空格后 UI 树里出现了什么（有没有新的 rid / 文本 / clickable 节点）？
2. 加号是换了 rid、换了容器，还是整体交互变了（如长按、点别处）？
3. 空课表第一格现在还能不能点（`cv_empty_content` 是否仍在）？

## 用法

    python evals/probe_addhint.py            # 全流程（会清数据重建成空课表）

只读不改，不写用例、不动知识卡；结论人工确认后再回去改 176/182。
"""
import io
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "cases", "com.zui.calendar"))
sys.path.insert(0, os.path.join(_ROOT, "framework"))

from test_framework import TestCase, _parse_nodes  # noqa: E402
from _flow import goto_课程表空状态                  # noqa: E402

PKG = "com.zui.calendar"
BTN_MANUAL = PKG + ":id/btnCreateManually"
NAME_RID = PKG + ":id/et_schedule_name"
SAVE_RID = PKG + ":id/action_save"
EMPTY_CELL = PKG + ":id/cv_empty_content"
ADD_HINT = PKG + ":id/iv_add_hint"

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "probe_addhint")
os.makedirs(OUT, exist_ok=True)


def _interesting(nodes):
    """挑出"值得看"的节点：有 rid 的，或 clickable 的（其余是排版容器噪声）。"""
    out = []
    for n in nodes:
        if n.get("rid") or n.get("clickable") == "true":
            out.append(n)
    return out


def _dump_and_report(t, label, prev_rids=None):
    """dump 一次并打印：节点数、'值得看'的节点、与上一次相比新增/消失的 rid。"""
    xml = t._dump()
    t._run_dialog_watchers(xml)
    nodes = _parse_nodes(xml)
    inter = _interesting(nodes)
    rids = {n["rid"] for n in nodes if n.get("rid")}

    print("\n" + "=" * 78)
    print("[%s] 节点 %d 个（其中带 rid %d / clickable %d）"
          % (label, len(nodes), len(rids), sum(1 for n in inter if n.get("clickable") == "true")))
    if prev_rids is not None:
        new = sorted(rids - prev_rids)
        gone = sorted(prev_rids - rids)
        print("  ➕ 新增 rid: %s" % (new or "（无）"))
        print("  ➖ 消失 rid: %s" % (gone or "（无）"))
    print("  -- 值得看的节点 --")
    for n in inter:
        rid = (n.get("rid") or "").replace(PKG + ":id/", "…/")[:34]
        txt = (n.get("text") or n.get("desc") or "").replace("\n", " ")[:22]
        print("    %-34s %-24s clk=%-5s bnd=%s"
              % (rid, txt or "-", n.get("clickable", "-"), n.get("bounds", "")))
    with io.open(os.path.join(OUT, label.replace(" ", "_") + ".xml"),
                 "w", encoding="utf-8") as f:
        f.write(xml)
    shot = t.screenshot("probe_" + label.replace(" ", "_"))
    print("  截图: %s" % shot)
    return rids


def main():
    t = TestCase("探针_加号入口")
    t.start_watchdog(policy="allow")

    print("── 1) 建一张空课程表并进入周视图 ──")
    if not goto_课程表空状态(t):
        print("!! 前置失败：没到课程表空状态")
        return t.finish()
    if not t.tap_rid(BTN_MANUAL, silent=True):
        print("!! 未找到'手动创建课程表'")
        return t.finish()
    if not t.wait_rid(NAME_RID, timeout=8):
        print("!! 未进入新建课程表页")
        return t.finish()
    t.input_text(NAME_RID, "探针课表")
    time.sleep(0.6)
    t.tap_rid(SAVE_RID, silent=True)
    if not t.wait_rid(EMPTY_CELL, timeout=15):
        print("!! 保存后没到空课表（无 cv_empty_content）")
        return t.finish()
    print("   已到空课表周视图 ✓")

    base = _dump_and_report(t, "0_点空格之前")

    cell = t.el_bounds(rid=EMPTY_CELL)
    if not cell:
        print("!! 拿不到 cv_empty_content 的 bounds")
        return t.finish()
    cx, cy = (cell[0] + cell[2]) // 2, (cell[1] + cell[3]) // 2
    print("\n── 2) 点第一格空格 (中心 %d,%d)，多个时刻分别看 ──" % (cx, cy))
    t.tap_xy(cx, cy)

    prev = base
    cum = 0.0
    for wait in (0.3, 0.7, 1.5, 3.0):
        time.sleep(wait)
        cum += wait
        prev = _dump_and_report(t, "1_点空格后%.1fs" % cum, prev)
        if ADD_HINT in prev:
            print("   ✅ iv_add_hint 出现了！→ 说明只是**时序**问题")
            break
    else:
        print("   ❌ 累计等待 %.1fs 后 iv_add_hint 仍未出现" % cum)

    print("\n── 3) 再点一次空格（有些实现要二次点）──")
    t.tap_xy(cx, cy)
    time.sleep(1.2)
    r3 = _dump_and_report(t, "2_再点一次空格", prev)
    print("   iv_add_hint 在树里: %s" % (ADD_HINT in r3))

    print("\n── 4) 长按空格（可能是长按才出加号）──")
    try:
        t.d.long_click(cx, cy, 0.9)
    except Exception as e:
        print("   long_click 调用失败: %s" % e)
    time.sleep(1.5)
    r4 = _dump_and_report(t, "3_长按空格", r3)
    print("   iv_add_hint 在树里: %s" % (ADD_HINT in r4))

    print("\n" + "=" * 78)
    print("结论要素：")
    print("  · iv_add_hint 是否曾出现 / 换到什么新 rid → 见各段'新增 rid'")
    print("  · 完整 XML 已落盘: %s" % OUT)
    print("  · 截图见各段输出路径（人工看图确认是否出现加号图形）")
    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    main()
