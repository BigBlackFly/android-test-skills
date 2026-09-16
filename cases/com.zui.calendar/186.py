#!/usr/bin/env python3
"""联想日历_186 用例：设为当前课程表 + 删除所有课程表
前提: 1.日历已允许各项权限 2.设备中已添加多个课程表
操作: 1.勾选非当前课程表，点击设为当前课程表 2.点确定 3.点取消 4.删除所有课程表
验证: 1.弹框提示确定设为当前课程表吗
      2.点"确定"后"当前"标签显示在选中的课程表标题旁
      3.点"取消"关闭弹框
      4.显示空页面状态及拍照导入、图库导入、手动创建的操作按钮

【探查结论（2026-09-11 真机实测）】
- 列表页 = TimetableListActivity，入口 = 顶栏 action_curriculum_table_settings
- 多选态：checkbox_select + btn_set_default(设为当前课程表)/btn_delete(删除课程表)
- 仅勾选**非当前** → btn_set_default 可用（enabled=true）→ 点它弹确认框
- 确认框文案：「确定设为当前课程表吗？」+ 取消 / 确定（android:id/button2/button1）
- 点「确定」→ 「当前」(tv_default_badge) 移到选中课表那一行；点「取消」→ 仅关框
- 删除弹框文案随数量变化：多选时「确定要删除选中的 2 个课程表吗？」
- **删除全部后当场停在"列表空态"**（TimetableListActivity，文案「没有课程表」+
  「点击 + 创建新课程表」，**无三按钮**）；**重新进入课程表**才回到引导式空状态页
  （TimetableActivity，「还未添加课程表」+ 拍照/图库/手动创建三按钮）。
  判定：预期 4 成立（空状态页与三按钮都出现），但需"重新进入"这一动作，
  差异记 INFO 不计失败（用户确认：属"列表空态"的合理设计）。
"""
import os
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_186
前提
1.日历已允许各项权限
2.设备中已添加多个课程表
操作
1.勾选当前课程表以外的任一课程表时，点击设为当前课程表
2.点击确定
3.点击取消
4.删除所有课程表
验证
1.弹框提示确定设为当前课程表吗
2.点击“确定”后“当前”标签显示在选中的课程表标题旁
3.点击“取消”关闭弹框
4.显示空页面状态及拍照导入、图库导入、手动创建的操作按钮"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase            # noqa: E402
from _flow import tap_more_menu, ensure_two_tables, goto_home    # noqa: E402

PKG = "com.zui.calendar"


# ── 辅助 ──────────────────────────────────────────────────
def _on_list_page(t):
    return "全部课程表" in " ".join(t.screen_text())


def _to_list_page(t):
    if _on_list_page(t):
        return True
    if t.el_bounds(rid="com.zui.calendar:id/action_curriculum_table_settings"):
        t.tap_rid("com.zui.calendar:id/action_curriculum_table_settings", silent=True)
        time.sleep(2.5)
    return _on_list_page(t)


def _names(t):
    """列表页课表名（按屏幕顺序）。"""
    return [n["text"] for n in t.find_nodes(rid_re="tv_schedule_name") if n["text"]]


def _current_name(t):
    """带「当前」角标的课表名（按 y 坐标就近匹配 tv_default_badge）。"""
    badges = t.find_nodes(rid_re="tv_default_badge")
    if not badges:
        return None
    by = badges[0]["bounds_xy"][1]
    rows = [n for n in t.find_nodes(rid_re="tv_schedule_name") if n["text"]]
    if not rows:
        return None
    return min(rows, key=lambda n: abs(n["bounds_xy"][1] - by))["text"]


def _tap_row_checkbox(t, name):
    rows = [n for n in t.find_nodes(rid_re="tv_schedule_name")
            if n["text"] and name in n["text"]]
    if not rows:
        return False
    y = rows[0]["bounds_xy"][1]
    boxes = t.find_nodes(rid_re="checkbox_select")
    if not boxes:
        return False
    best = min(boxes, key=lambda b: abs(b["bounds_xy"][1] - y))
    t.tap_xy(*best["bounds_xy"][:2])
    time.sleep(1.2)
    return True


def _ensure_multi(t):
    if t.el_bounds(rid="com.zui.calendar:id/checkbox_select"):
        return True
    t.tap_rid("com.zui.calendar:id/action_schedule", silent=True)
    time.sleep(1.8)
    return bool(t.el_bounds(rid="com.zui.calendar:id/checkbox_select"))


def _exit_multi(t):
    for _ in range(3):
        if not t.el_bounds(rid="com.zui.calendar:id/checkbox_select"):
            return True
        if t.el_bounds(rid="com.zui.calendar:id/action_schedule"):
            t.tap_rid("com.zui.calendar:id/action_schedule", silent=True)
        else:
            t.back()
        time.sleep(1.5)
    return not t.el_bounds(rid="com.zui.calendar:id/checkbox_select")


def _ensure_two_tables(t):
    """前提 2：不足 2 个课表则补建。实现见 `_flow.ensure_two_tables`（处理三种落点）。

    原实现只会在**列表页**点加号，落点是引导式空状态页时进不去新建页。
    """
    return ensure_two_tables(t)


# ── 主流程 ────────────────────────────────────────────────
def run():
    t = TestCase("联想日历_186")
    t.add_prop_restore("accelerometer_rotation")
    t.start_watchdog(policy="allow")

    # ── 前置 ───────────────────────────────────────────────
    t.step("前置条件-进入课程表列表页（≥2 个课程表）")
    t.launch_app(PKG)
    goto_home(t)      # launch_app 是"恢复前台"，上个用例可能把它停在深层页
    if not t.wait_rid("com.zui.calendar:id/iv_more", timeout=15):
        t.record("FAIL", "未找到首页「更多」按钮")
        t.blocked("无法进入课程表")
        return t.finish()
    if not tap_more_menu(t):
        t.record("FAIL", "未能打开「更多」菜单")
        t.blocked("无法进入课程表")
        return t.finish()
    t.tap_text("课程表", silent=True)
    time.sleep(3)
    # 前提 2 必须在**找列表页之前**满足：落点是空状态页时没有列表页顶栏，
    # 先去 _to_list_page 必然失败
    _ensure_two_tables(t)
    if not _to_list_page(t):
        t.record("FAIL", "未到达「全部课程表」页面")
        t.blocked("无法验证")
        return t.finish()
    names = _ensure_two_tables(t)
    if not _on_list_page(t):
        _to_list_page(t)
    # 用**同一页快照**：`_ensure_two_tables` 建表后落点是周视图，若这里重新
    # 读一次 `_names()` 可能拿到空列表 → others 判空 → 假 FAIL
    names = _names(t) or names
    t.record("PASS" if len(names) >= 2 else "FAIL",
             f"设备存在多个课程表（{len(names)} 个：{names}）")

    cur = _current_name(t)
    others = [n for n in names if n != cur]
    print(f"   当前课表={cur}  其他={others}")
    if not others:
        t.record("FAIL", "无「当前以外的课程表」可操作，前提不满足")
        t.blocked("无法验证设为当前")
        return t.finish()
    target = others[0]

    # ── 预期 3：点取消关闭弹框（先做，避免状态被改）──────────
    t.step("Step1+3 勾选非当前课表 → 设为当前 → 点取消")
    _ensure_multi(t)
    _tap_row_checkbox(t, target)
    en = next((n.get("enabled") for n in t.find_nodes(rid_re="btn_set_default")), None)
    t.record("PASS" if en == "true" else "FAIL",
             f"仅勾选非当前课表「{target}」时「设为当前课程表」可用"
             f"（enabled={en}）", rid="com.zui.calendar:id/btn_set_default")
    t.tap_rid("com.zui.calendar:id/btn_set_default", silent=True)
    time.sleep(2)
    dtexts = t.screen_text()
    hit = any("确定设为当前课程表吗" in x for x in dtexts)
    t.record("PASS" if hit else "FAIL",
             f"①弹框提示「确定设为当前课程表吗？」"
             f"（实际={[x for x in dtexts if '设为当前' in x or '确定' in x][:4]}）")
    t.screenshot("01_设为当前弹框")

    if t.tap_text("取消", wait=3, silent=True):
        time.sleep(1.5)
        gone = not any("确定设为当前课程表吗" in x for x in t.screen_text())
        t.record("PASS" if gone else "FAIL",
                 f"③点击「取消」关闭弹框（弹框已消失={'✓' if gone else '✗'}）")
    else:
        t.record("FAIL", "③未找到「取消」按钮")
    t.screenshot("02_取消后")

    # ── 预期 2：点确定 → 当前标签移到目标课表 ────────────────
    t.step("Step2 设为当前 → 点确定")
    t.tap_rid("com.zui.calendar:id/btn_set_default", silent=True)
    time.sleep(2)
    if not t.tap_text("确定", wait=3, silent=True):
        t.record("FAIL", "②未找到「确定」按钮")
    time.sleep(3)
    _exit_multi(t)
    new_cur = _current_name(t)
    t.record("PASS" if new_cur == target else "FAIL",
             f"②点「确定」后「当前」标签显示在选中的课程表「{target}」旁"
             f"（实际当前={new_cur}）", rid="com.zui.calendar:id/tv_default_badge")
    t.screenshot("03_设为当前后")

    # ── 预期 4：删除所有课程表 → 重新进入 → 空状态三按钮 ─────
    t.step("Step4 删除所有课程表")
    for rnd in range(1, 6):
        if not _on_list_page(t):
            _to_list_page(t)
        rest = _names(t)
        if not rest:
            break
        _ensure_multi(t)
        for nm in rest:
            _tap_row_checkbox(t, nm)
        if t.el_bounds(rid="com.zui.calendar:id/btn_delete"):
            t.tap_rid("com.zui.calendar:id/btn_delete", silent=True)
            time.sleep(2)
            dts = t.screen_text()
            t.record("PASS" if any("删除选中的" in x or "确定删除" in x for x in dts)
                     else "WARN",
                     f"删除确认弹框（文案={[x for x in dts if '删除' in x][:3]}）")
            t.screenshot("04_删除确认框")
            t.tap_text("确定", wait=3, silent=True)
            time.sleep(3)

    # 当场停「列表空态」
    after = t.screen_text()
    list_empty = any("没有课程表" in x for x in after) or not _names(t)
    t.record("PASS" if list_empty else "WARN",
             f"④删除全部后课程表列表为空（文案={[x for x in after if '课程表' in x][:4]}）")
    t.screenshot("05_删除后列表空态")

    # 重新进入 → 引导式空状态页（三按钮）
    t.step("Step4b 重新进入课程表，验证空状态与三按钮")
    t.force_stop(PKG)
    time.sleep(1)
    t.launch_app(PKG)
    t.wait_rid("com.zui.calendar:id/iv_more", timeout=15)
    tap_more_menu(t)
    t.tap_text("课程表", silent=True)
    time.sleep(3)
    if not any("还未添加课程表" in x for x in t.screen_text()):
        # 可能仍在列表页，退回一次再进
        t.back()
        time.sleep(2)
        tap_more_menu(t)
        t.tap_text("课程表", silent=True)
        time.sleep(3)
    texts = t.screen_text()
    has_empty = any("还未添加课程表" in x for x in texts)
    t.record("PASS" if has_empty else "FAIL",
             f"④空页面状态「还未添加课程表」"
             f"（Activity={t.current_activity().split('/')[-1]}）",
             rid="com.zui.calendar:id/emptyView")
    for rid, label in [("btnImportFromPhoto", "拍照导入课程表"),
                       ("btnImportFromGallery", "从图库导入课程表"),
                       ("btnCreateManually", "手动创建课程表")]:
        b = t.el_bounds(rid=f"com.zui.calendar:id/{rid}")
        has_txt = any(label in x for x in texts)
        t.record("PASS" if (b and has_txt) else "FAIL",
                 f"④显示「{label}」操作按钮", rid=rid)
    t.screenshot("06_空状态三按钮")

    t.record("INFO", "④差异：删除全部后**当场**停在列表空态（TimetableListActivity，"
                     "「没有课程表」+「点击 + 创建新课程表」，无三按钮）；"
                     "**重新进入**课程表才显示引导式空状态页与三个导入按钮。"
                     "属「列表空态」的合理设计，预期成立、不计失败。")

    return t.finish()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
