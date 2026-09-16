#!/usr/bin/env python3
"""联想日历_185 用例：全部课程表列表页 + 多选态置灰逻辑
前提: 1.日历已允许各项权限 2.设备中已添加多个课程表
步骤: 课程表页面→右上角菜单→加号→长按列表→勾选按钮→3 种勾选组合
验证: 1.打开全部课程表页面，显示列表，当前课程表名称后显示"当前"，
         名称右侧显示"设置"，右上角显示勾选按钮和加号按钮
      2.进入新建课程表页面
      3-4.列表名称前显示多选按钮
      5.仅勾选当前课程表：设为当前置灰，显示删除按钮；点删除弹框提示
      6.勾选多个课程表：设为当前置灰，显示删除按钮
      7.仅勾选非当前课程表：显示设为当前和删除两个按钮

【探查结论（2026-09-11 真机实测）】
- 进课程表落在**周视图** TimetableActivity；到列表页要点顶栏
  action_curriculum_table_settings（文案"课程表设置"，**不是**"菜单"）。
- 列表页 = TimetableListActivity，标题「全部课程表」，顶栏两图标：
  action_schedule（文案「编辑」，即用例说的"勾选按钮"）+ action_add_schedule
  （文案「添加课程表」，即"加号"）。
- 点 action_schedule → 多选态：出现 checkbox_select + bottom_bar +
  btn_set_default(设为当前课程表) / btn_delete(删除课程表)。
- **置灰规则**（实测 3 种组合）：勾选集合**包含当前课程表** → 设为当前置灰
  (enabled=false)；**不包含** → 可用(enabled=true)。删除按钮始终可用。
- 新建的课表**不自动抢占"当前"**（知识卡 L162，本次复验成立）。
- 删除弹框文案：「确定删除此课程表吗？」+ 取消/确定。
"""
import os
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_185
前提
1.日历已允许各项权限
2.设备中已添加多个课程表
操作
1.课程表页面点击右上角菜单按钮
2.点击右上角加号按钮
3.长按课程表列表
4.点击右上角勾选按钮
5.仅勾选当前课程表时
6.勾选多个课程表时
7.仅勾选当前课程表以外的任一课程表时
验证
1.点击后打开全部课程表页面，显示课程表列表，当前课程表名称后方显示”当前“提示；课程表名称右侧显示“设置”；右上角显示勾选按钮和加号按钮
2.点击后进入新建课程表页面
3-4.课程表列表名称前方显示多选按钮
5-6.设为当前课程表置灰，显示删除课程表按钮；点击删除弹框提示是否确定删除选择的课程表
7.显示设为当前课程表和删除课程表两个按钮；"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase            # noqa: E402
from _flow import tap_more_menu, ensure_two_tables, goto_home    # noqa: E402

PKG = "com.zui.calendar"
# `_flow.ensure_two_tables` 造的表名前缀 —— 用来区分"备数据表"与"当前课表"
SEED_PREFIX = "备数据表"


# ── 辅助 ──────────────────────────────────────────────────
def _on_list_page(t):
    return "全部课程表" in " ".join(t.screen_text())


def _to_list_page(t):
    """周视图 → 全部课程表列表页（点顶栏"课程表设置"）。"""
    if _on_list_page(t):
        return True
    if t.el_bounds(rid="com.zui.calendar:id/action_curriculum_table_settings"):
        t.tap_rid("com.zui.calendar:id/action_curriculum_table_settings", silent=True)
        time.sleep(2.5)
    return _on_list_page(t)


def _btn(t, short_rid):
    """读底部按钮状态：返回 (是否存在, enabled 字符串)。"""
    nodes = t.find_nodes(rid_re=short_rid)
    if not nodes:
        return (False, None)
    return (True, nodes[0].get("enabled"))


def _checked(t):
    """各行多选按钮的 checked 列表（按屏幕顺序）。"""
    return [n.get("checked") for n in t.find_nodes(rid_re="checkbox_select")]


def _tap_row_checkbox(t, name):
    """点指定课表名那一行的多选按钮（按 y 坐标就近匹配）。"""
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


def _ensure_two_tables(t):
    """前提 2：确保有 ≥2 个课程表。实现见 `_flow.ensure_two_tables`。

    为什么下沉到 _flow：原实现只会点**列表页顶栏加号** `action_add_schedule`，
    而套件里前序用例 `pm_clear` 过 → 进课程表落在**引导式空状态页**（那页没有
    这个加号）→ 直接 FAIL+BLOCKED（实测 20 秒就结束）。共享版处理三种落点
    （空状态页 `btnCreateManually` / 列表页加号 / 周视图先回列表页）。
    """
    return ensure_two_tables(t)


# ── 主流程 ────────────────────────────────────────────────
def run():
    t = TestCase("联想日历_185")
    t.add_prop_restore("accelerometer_rotation")
    t.start_watchdog(policy="allow")

    # ── 前置：权限已允许 + 进课程表 ─────────────────────────
    t.step("前置条件-进入课程表列表页")
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

    # 前提 2 必须在**找列表页之前**满足：落点是空状态页时根本没有列表页顶栏，
    # 先去 _to_list_page 必然失败（见 _ensure_two_tables docstring）
    _ensure_two_tables(t)

    # ── 预期 1：全部课程表页面 ──────────────────────────────
    t.step("Step1 打开全部课程表页面")
    if not _to_list_page(t):
        t.record("FAIL", "未到达「全部课程表」页面（顶栏课程表设置未生效）")
        t.blocked("无法验证列表页")
        return t.finish()
    t.screenshot("01_全部课程表列表页")
    texts = t.screen_text()
    t.record("PASS" if "全部课程表" in " ".join(texts) else "FAIL",
             f"打开全部课程表页面（标题={[x for x in texts if '课程表' in x][:4]}）",
             rid="com.zui.calendar:id/toolbar_title")

    # 前提 2：多个课程表
    names = _ensure_two_tables(t)
    if not _on_list_page(t):
        _to_list_page(t)
    t.record("PASS" if len(names) >= 2 else "FAIL",
             f"设备存在多个课程表（{len(names)} 个：{names}）")

    # 当前课程表后有「当前」标签
    t.record("PASS" if "当前" in texts or any("当前" in x for x in t.screen_text())
             else "FAIL", "当前课程表名称后方显示「当前」提示",
             rid="com.zui.calendar:id/tv_default_badge")
    # 名称右侧「设置」
    t.record("PASS" if "设置" in " ".join(t.screen_text()) else "FAIL",
             "课程表名称右侧显示「设置」",
             rid="com.zui.calendar:id/tv_schedule_info")
    # 右上角勾选按钮 + 加号按钮
    has_check = bool(t.el_bounds(rid="com.zui.calendar:id/action_schedule"))
    has_add = bool(t.el_bounds(rid="com.zui.calendar:id/action_add_schedule"))
    t.record("PASS" if (has_check and has_add) else "FAIL",
             f"右上角显示勾选按钮和加号按钮"
             f"（勾选/编辑={'✓' if has_check else '✗'}"
             f"，加号/添加={'✓' if has_add else '✗'}）")

    # ── 预期 2：加号 → 新建课程表页 ─────────────────────────
    t.step("Step2 点击右上角加号")
    t.tap_rid("com.zui.calendar:id/action_add_schedule", silent=True)
    time.sleep(3)
    shot = t.screenshot("02_新建课程表页")
    on_new = "新建课程表" in " ".join(t.screen_text())
    t.record("PASS" if on_new else "FAIL",
             f"进入新建课程表页面"
             f"（Activity={t.current_activity().split('/')[-1]}）")
    # 退回列表页（新建页直接 BACK；若在确认页则 BACK 到列表）
    for _ in range(4):
        t.back()
        time.sleep(1.5)
        if _on_list_page(t):
            break

    # ── 预期 3-4：长按 / 勾选按钮 → 多选态 ──────────────────
    t.step("Step3-4 进入多选态，名称前方显示多选按钮")
    t.tap_rid("com.zui.calendar:id/action_schedule", silent=True)
    time.sleep(2)
    ck = _checked(t)
    t.record("PASS" if ck else "FAIL",
             f"课程表列表名称前方显示多选按钮（{len(ck)} 个）",
             rid="com.zui.calendar:id/checkbox_select")
    multi = bool(t.el_bounds(rid="com.zui.calendar:id/bottom_bar"))
    t.record("PASS" if multi else "FAIL", "进入多选态（出现底部操作栏）",
             rid="com.zui.calendar:id/bottom_bar")
    # 找出当前课程表：用产品自己的「当前」角标 `tv_default_badge` 按 y 就近匹配
    # ——不能靠"名字不等于备数据表"：pm_clear 后两张表都是备数据表，那样会得到
    #   None → 后面 ⑤⑥⑦ 的勾选全落空（`勾选态=[]`）连挂 7 条（实测踩过）
    rows = [n for n in t.find_nodes(rid_re="tv_schedule_name") if n["text"]]
    badges = t.find_nodes(rid_re="tv_default_badge")
    cur_name = None
    if badges and rows:
        by = badges[0]["bounds_xy"][1]
        cur_name = min(rows, key=lambda r: abs(r["bounds_xy"][1] - by))["text"]
    if not cur_name and rows:
        cur_name = next((r["text"] for r in rows
                         if not r["text"].startswith(SEED_PREFIX)), rows[0]["text"])
    others = [n["text"] for n in rows if n["text"] != cur_name]
    print(f"   当前课表={cur_name}  其他={others}")

    # ── 预期 5：仅勾选当前课程表 ────────────────────────────
    t.step("Step5 仅勾选当前课程表")
    if cur_name:
        _tap_row_checkbox(t, cur_name)
    time.sleep(1)
    ok5, en5 = _btn(t, "btn_set_default")
    okd5, end5 = _btn(t, "btn_delete")
    t.record("PASS" if (ok5 and en5 == "false") else "FAIL",
             f"⑤仅勾选当前课程表：设为当前课程表**置灰**"
             f"（enabled={en5}，勾选态={_checked(t)}）",
             rid="com.zui.calendar:id/btn_set_default")
    t.record("PASS" if (okd5 and end5 == "true") else "FAIL",
             f"⑤显示删除课程表按钮（enabled={end5}）",
             rid="com.zui.calendar:id/btn_delete")
    t.screenshot("05_仅勾选当前")

    # 点删除 → 弹框
    if okd5:
        t.tap_rid("com.zui.calendar:id/btn_delete", silent=True)
        time.sleep(2)
        dtexts = t.screen_text()
        hit = any("确定删除" in x for x in dtexts)
        t.record("PASS" if hit else "FAIL",
                 f"点击删除弹框提示是否确定删除（文案={[x for x in dtexts if '删除' in x][:4]}）")
        t.screenshot("05_删除弹框")
        # 取消，保留数据
        for txt in ("取消", "取消 "):
            if t.tap_text(txt, wait=2, silent=True):
                break
        else:
            t.back()
        time.sleep(1.5)

    # ── 预期 6：勾选多个 ────────────────────────────────────
    t.step("Step6 勾选多个课程表")
    for nm in others:
        _tap_row_checkbox(t, nm)
    time.sleep(1)
    ok6, en6 = _btn(t, "btn_set_default")
    okd6, end6 = _btn(t, "btn_delete")
    t.record("PASS" if (ok6 and en6 == "false") else "FAIL",
             f"⑥勾选多个课程表：设为当前课程表**置灰**"
             f"（enabled={en6}，勾选态={_checked(t)}）",
             rid="com.zui.calendar:id/btn_set_default")
    t.record("PASS" if (okd6 and end6 == "true") else "FAIL",
             f"⑥显示删除课程表按钮（enabled={end6}）",
             rid="com.zui.calendar:id/btn_delete")
    t.screenshot("06_勾选多个")

    # ── 预期 7：仅勾选非当前 ────────────────────────────────
    t.step("Step7 仅勾选当前课程表以外的课程表")
    if cur_name:
        _tap_row_checkbox(t, cur_name)     # 取消当前，只留非当前
    time.sleep(1)
    ok7, en7 = _btn(t, "btn_set_default")
    okd7, end7 = _btn(t, "btn_delete")
    t.record("PASS" if (ok7 and en7 == "true") else "FAIL",
             f"⑦仅勾选非当前课程表：显示**设为当前课程表**且可用"
             f"（enabled={en7}，勾选态={_checked(t)}）",
             rid="com.zui.calendar:id/btn_set_default")
    t.record("PASS" if (okd7 and end7 == "true") else "FAIL",
             f"⑦显示删除课程表按钮（enabled={end7}）",
             rid="com.zui.calendar:id/btn_delete")
    t.record("PASS" if (ok7 and okd7) else "FAIL",
             "⑦同时显示「设为当前课程表」和「删除课程表」两个按钮")
    t.screenshot("07_仅勾选非当前")

    # 清理：退出多选态
    if t.el_bounds(rid="com.zui.calendar:id/action_schedule"):
        t.tap_rid("com.zui.calendar:id/action_schedule", silent=True)
        time.sleep(1.5)

    return t.finish()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
