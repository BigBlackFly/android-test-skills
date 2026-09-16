#!/usr/bin/env python3
"""联想日历_183 用例：课程编辑弹框与「删除课程」（取消/删除两条分支）

前提：已保存的课程表（图库导入建表后保存）
步骤:
1.导入照片后确认识别结果页，点击单个课程
2.选择已保存的课程表，点击单个课程
3.点击底部「删除课程」
预期结果
1.点击单个课程后显示编辑课程弹框，点击对应字段可编辑信息，底部显示「删除课程」按钮
2.显示课程列表，点击后显示编辑课程弹窗
3.点击后弹出确认删除弹框；点「删除」删除该课程并返回课程表页，课程被删除；
  点「取消」关闭弹框返回课程表页，课程未被删除

结构（182/183 探查实测，勿凭印象改 rid）:
  课程卡片 curriculum_card_view（周视图）→ 点卡片进「课程列表」CourseListActivity
  → 点 ivEdit 才进编辑页 EditCourseActivity（**不是**点卡片直接进编辑）
  编辑页: etCourseName / etClassroom / etTeacher / llCourseTime / llCourseWeeks
          / llCourseColor / btnDelete（文本「删除课程」）
  删除确认框: 标题「确定删除此课程吗？」/ 取消=android:id/button2 / 删除=android:id/button1
"""
import os
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_183
前提：
操作步骤
1.导入照片/图片后确认识别结果页，点击单个课程
2.选择已经保存的课程表，点击单个课程
3.点击底部"删除课程"
预期结果
1. 点击单个课程后显示编辑课程弹框，点击对应字段可编辑信息，底部显示"删除课程"按钮
2.显示课程列表。点击后显示编辑课程弹窗
3.点击后弹出确认删除按钮；点击"删除"按钮删除该课程，返回课程表页面，该课程被删除；点击"取消"按钮关闭弹框，返回课程表页面，课程未被删除；"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase, _parse_nodes      # noqa: E402
from _flow import goto_图库导入_基本信息确认页, PKG      # noqa: E402

# 确认页完成按钮（图库导入路径）
BTN_FINISH = "com.zui.calendar:id/btn_finish"

# 课程表列表页（保存后到达）→ 点课表名进周视图
TV_SCHEDULE_NAME = "com.zui.calendar:id/tv_schedule_name"

# 周视图课程卡片 / 课程名
CARD = "com.zui.calendar:id/curriculum_card_view"
TV_TITLE = "com.zui.calendar:id/tv_curriculum_title"

# 课程列表弹框（点卡片后进）
IV_EDIT = "com.zui.calendar:id/ivEdit"
CL_COURSE_DETAIL = "com.zui.calendar:id/clCourseDetail"

# 编辑课程页
ACT_EDIT = "EditCourseActivity"
ET_NAME = "com.zui.calendar:id/etCourseName"
ET_CLASSROOM = "com.zui.calendar:id/etClassroom"
ET_TEACHER = "com.zui.calendar:id/etTeacher"
LL_TIME = "com.zui.calendar:id/llCourseTime"
LL_WEEKS = "com.zui.calendar:id/llCourseWeeks"
LL_COLOR = "com.zui.calendar:id/llCourseColor"
BTN_DELETE = "com.zui.calendar:id/btnDelete"

# 删除确认框
DLG_TITLE = "确定删除此课程吗？"
BTN_CANCEL = "android:id/button2"     # 取消
BTN_CONFIRM = "android:id/button1"    # 删除


def _screen(t):
    try:
        return " ".join(t.screen_text())
    except Exception:
        return ""


def _titles_now(t):
    """立即读一次周视图课程名（不等待）。用于删除后的核对。"""
    out = []
    try:
        for n in _parse_nodes(t._dump()):
            if n["rid"] == TV_TITLE and n["text"]:
                out.append(n["text"])
    except Exception:
        pass
    return out


def _titles(t, wait=8):
    """周视图当前所有课程名（用于「课程是否被删除」的断言）。

    条件等待：网格异步渲染，过早读取会得到空列表，从而把"课程还在"
    误判成"已被删除"（与 _first_card 同源问题）。
    注意：本函数等到"非空"即返回，因此**只用于预期有课程的场合**；
    核对"删除后应当变少/消失"必须用 _titles_now，否则会白等满 wait 秒。
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        out = _titles_now(t)
        if out:
            return out
        time.sleep(1)
    return []


def _first_card(t, wait=12):
    """第一个课程卡片的 bounds（周视图）。

    必须条件等待：进入周视图后网格是异步渲染的，立刻 dump 会拿到空
    recyclerView（183 首跑实测 FAIL「未找到任何课程卡片」，实际有 25 个）。
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            for n in _parse_nodes(t._dump()):
                if n["rid"] == CARD and n["bounds_xy"]:
                    return n["bounds_xy"]
        except Exception:
            pass
        time.sleep(1)
    return None


def _tap_card(t, card):
    b = card
    t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    time.sleep(2.5)


def _back_to_week_view(t, rounds=6):
    """回退到周视图 TimetableActivity（实测：列表页 BACK 一次即到）。

    实测到达关系（2026-09-10）：
        列表页 TimetableListActivity --BACK 1 次--> 周视图
        周视图                        --BACK 1 次--> 日历主页 AllInOneActivity
    所以只在"尚未到周视图"时按 BACK，一旦到达立即停手，
    不会因多按一次而越过周视图跑到主页去。
    """
    for _ in range(rounds):
        if "TimetableActivity" in t.current_activity():
            return True
        t.back()
        time.sleep(1.5)
    # 兜底：万一路径异常（如落在主页），走菜单重新进入
    return _enter_week_view(t)


def _enter_week_view(t, rounds=4):
    """从任意位置进入周视图（兜底路径：主页 →「更多」→ 课程表）。

    正常流程用 _back_to_week_view（BACK 直达，最便宜）；
    本函数只在前者失败时兜底，故刻意保守：先回主页再走菜单。
    """
    from _flow import tap_more_menu
    for _ in range(rounds):
        if "TimetableActivity" in t.current_activity():
            return True
        # 回主页（最多 4 次 BACK，避免在深栈里出不来）
        for _ in range(4):
            if "AllInOneActivity" in t.current_activity():
                break
            t.back()
            time.sleep(1.2)
        t.launch_app(PKG)
        time.sleep(3)
        if tap_more_menu(t):
            time.sleep(1.5)
            t.tap_text("课程表", wait=4, silent=True)
            time.sleep(3.5)
    return "TimetableActivity" in t.current_activity()


def _open_edit_page(t):
    """周视图 → 点课程卡片 → 课程列表 → 点编辑 → 编辑课程页。返回是否成功。"""
    card = _first_card(t)
    if not card:
        t.record("FAIL", "周视图未找到任何课程卡片，无法继续")
        return False
    _tap_card(t, card)
    # 点卡片进的是「课程列表」弹框（实测），不是编辑页
    if "课程列表" not in _screen(t):
        t.record("FAIL", f"点击课程卡片后未出现「课程列表」弹框，屏幕={_screen(t)[:60]}")
        return False
    t.record("PASS", "点击单个课程后出现「课程列表」弹框")
    t.screenshot("02_课程列表")
    if not t.tap_rid(IV_EDIT, silent=True):
        t.record("FAIL", "课程列表内未找到编辑入口 ivEdit")
        return False
    time.sleep(2.5)
    if ACT_EDIT not in t.current_activity():
        t.record("FAIL", f"点击编辑后未进入编辑课程页，Activity={t.current_activity()}")
        return False
    return True


def _read_rid_scrolled(t, rid, max_rounds=4):
    """读元素，必要时在长表单里滚动查找（**双向**）。

    踩过的坑（2026-09-10 实测）：编辑课程页所有字段其实**都在首屏**
    （`curriculum_scroll_view` 内容 411~1646，屏幕高 1904）——
    `etCourseName` / `llCourseWeeks` / `btnDelete` 一次就能全读到。

    上一版只在读不到时**上滑**，结果把本来就可见的元素滚出屏幕上方，
    越滚越找不到（`llCourseWeeks` 因此误报"未找到"）。
    正确做法是：先原地读；读不到时**先滚动回顶部**再逐屏向下找。
    """
    node = t.read_rid(rid)
    if node is not None:
        return node
    w, h = t._screen_size()
    # 先回到顶部：元素很可能在**上方**（被之前的滚动顶出去了）
    for _ in range(3):
        t.d.swipe(w // 2, int(h * 0.30), w // 2, int(h * 0.82), 0.3)
        time.sleep(0.9)
    node = t.read_rid(rid)
    if node is not None:
        return node
    # 再逐屏向下找（覆盖真正超长、需要下滚才可见的表单）
    for _ in range(max_rounds):
        t.d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.45), 0.3)
        time.sleep(1.0)
        node = t.read_rid(rid)
        if node is not None:
            return node
    return None


def _tap_delete(t):
    """定位「删除课程」按钮并点击。

    实测该按钮在编辑页**首屏就可见**（b=(1102,1470,1938,1602)，屏幕高 1904），
    但 Step2 改课程名后可能已滚动过，所以统一走 _read_rid_scrolled 兜底，
    避免在已被滚走的页面上点空。
    """
    if _read_rid_scrolled(t, BTN_DELETE) is None:
        return False
    return t.tap_rid(BTN_DELETE, silent=True)


def run():
    t = TestCase("联想日历_183")
    # 清场钩子：本用例会删除课程、改动设备数据，登记 App 数据还原由
    # 前置 pm_clear 保证；此处登记还原自动旋转（导入链路会隐式改它，
    # 残留会让后续用例坐标系全错——见 lock_portrait 注释）。
    t.add_prop_restore("accelerometer_rotation")

    # ── 前提：图库导入建课表并保存 ───────────────────────────────
    t.step("前提-图库导入并保存课程表")
    if not goto_图库导入_基本信息确认页(t, pm_clear=True, timeout=60):
        return t.finish()          # 函数内已按原因 blocked/record
    if not t.tap_rid(BTN_FINISH, silent=True):
        t.blocked("确认页未找到「完成」按钮，无法保存课程表")
        return t.finish()
    time.sleep(4)   # 等「保存完成 → 跳列表页」落定（超过 3s settle 线，须写明原因）
    # 保存后到的是「全部课程表」列表页（TimetableListActivity），不是周视图
    # （183 首跑踩到：按周视图断言 cards，等满 12s 仍为 0）。
    # 列表页点课名 = 编辑课程表（EditTimetableActivity），也不是查看。
    # 到周视图最便宜的边：列表页 **BACK 一次**直达（实测 2026-09-10）。
    if "TimetableListActivity" not in t.current_activity():
        t.blocked(f"保存后未进入课程表列表页，Activity={t.current_activity()}")
        return t.finish()
    t.record("PASS", "已保存课程表（列表页出现课表项）")
    if not _back_to_week_view(t):
        return t.finish()
    t.record("PASS", "已进入课程表周视图（列表页 BACK 一次直达）")
    t.screenshot("01_课程表页")

    # ── Step1: 点击单个课程 → 编辑课程弹框 + 底部删除按钮 ─────────
    t.step("Step1 点击单个课程，验证编辑弹框与底部删除课程按钮")
    if not _open_edit_page(t):
        return t.finish()
    scr = _screen(t)
    t.record("PASS", f"点击单个课程后进入编辑课程页（Activity={ACT_EDIT}）")
    t.screenshot("03_编辑课程页")

    # 字段可编辑：课程名/教室/备注/上课时间/上课周数/背景色
    # 用 _read_rid_scrolled：后三项在首屏之外，必须上滑才能读到
    for rid, label in ((ET_NAME, "课程名"), (ET_CLASSROOM, "教室"),
                       (ET_TEACHER, "备注（如老师）"), (LL_TIME, "课程时间"),
                       (LL_WEEKS, "上课周数"), (LL_COLOR, "课程背景色")):
        node = _read_rid_scrolled(t, rid)
        ok = node is not None
        t.record("PASS" if ok else "FAIL",
                 f"编辑弹框字段可编辑-{label}: {'存在' if ok else '未找到（含上滑后）'} "
                 f"(rid={rid.split('/')[-1]})")
    # 底部「删除课程」按钮（同样在首屏之外）
    dbtn = _read_rid_scrolled(t, BTN_DELETE)
    del_text = (dbtn or {}).get("text", "")
    has_del = dbtn is not None and del_text == "删除课程"
    t.record("PASS" if has_del else "FAIL",
             f"底部显示「删除课程」按钮: {del_text!r}")
    if not has_del:
        return t.finish()

    # 字段确实可编辑（而非只读展示）：改课程名并确认值变化
    # 注意：上面可能已上滑，课程名会移出屏幕 → 先滚回顶部
    w, h = t._screen_size()
    for _ in range(4):
        if t.read_rid(ET_NAME) is not None:
            break
        t.d.swipe(w // 2, int(h * 0.30), w // 2, int(h * 0.82), 0.3)   # 下滑回顶
        time.sleep(1.0)
    old_name = (t.read_rid(ET_NAME) or {}).get("text", "")
    if t.clear_text(ET_NAME, silent=True):
        t.input_text(ET_NAME, "编辑验证", silent=True)
        time.sleep(0.8)
        new_name = (t.read_rid(ET_NAME) or {}).get("text", "")
        t.record("PASS" if new_name == "编辑验证" else "FAIL",
                 f"点击课程名字段可编辑: {old_name!r} → {new_name!r}")
    else:
        t.record("WARN", "未能清空课程名输入框，跳过可编辑性验证")

    # ── Step2: 已保存课程表 → 点单个课程 → 编辑弹窗 ───────────────
    # 预期写「显示课程列表，点击后显示编辑课程弹窗」——实测正是这条链路：
    # 点课程卡片先出「课程列表」，再点编辑才进编辑页。这里回到周视图复现一次。
    t.step("Step2 已保存课程表内点击单个课程，验证课程列表与编辑弹窗")
    # 先退出当前编辑页（点「完成」保存改动并退出），再从周视图复现一次链路
    t.tap_text("完成", wait=3, silent=True)
    time.sleep(2.5)
    if not _back_to_week_view(t):
        t.record("WARN", f"退出编辑页后未回到周视图，Activity={t.current_activity()}")
    # 基准必须在**周视图**上取：编辑页里读不到课程名（会拿到空列表），
    # 而 空集 ⊆ 任意集合 恒真 —— 那样「取消后课程仍在」这条断言会变成
    # 无论课程是否被删都 PASS 的假阳性（183 首跑实测：before=0 仍判 PASS）。
    time.sleep(2)
    before = _titles_now(t)
    t.record("INFO", f"删除前周视图课程数: {len(before)}（{before[:6]}）")
    if not _open_edit_page(t):
        return t.finish()
    t.record("PASS", "已保存课程表内点击单个课程 → 课程列表 → 编辑课程弹窗，链路正常")
    t.screenshot("04_二次进入编辑页")
    if not before:
        t.record("WARN", "删除前未读到任何课程名，后续「取消/删除」断言不可信")

    # ── Step3a: 点删除课程 → 确认框 → 点「取消」→ 课程未删除 ──────
    t.step("Step3-取消 点击删除课程 → 确认框 → 点取消，课程应保留")
    if not _tap_delete(t):
        t.record("FAIL", "未找到「删除课程」按钮（含上滑后）")
        return t.finish()
    time.sleep(2)
    if DLG_TITLE not in _screen(t):
        t.record("FAIL", f"点击删除课程后未出现确认删除弹框，屏幕={_screen(t)[:60]}")
        return t.finish()
    t.record("PASS", f"点击「删除课程」后弹出确认删除弹框（{DLG_TITLE}）")
    t.screenshot("05_删除确认框")

    if not t.tap_rid(BTN_CANCEL, silent=True):
        t.record("FAIL", "确认框内未找到「取消」按钮")
        return t.finish()
    time.sleep(2)
    t.record("PASS", "点击「取消」后关闭弹框")

    # 回到周视图核对课程仍在
    t.step("Step3-取消后核对课程未被删除")
    if not _back_to_week_view(t):
        t.record("WARN", f"未能回到周视图核对，Activity={t.current_activity()}")
    # 取消后核对：给网格渲染留时间，再读一次（预期课程仍在）
    time.sleep(2.5)
    after_cancel = _titles_now(t)
    # 基准为空时不能断言（空集恒被包含 → 假阳性），按 WARN 处理
    if not before:
        t.record("WARN", f"删除前基准为空，无法判定「取消后未删除」；"
                         f"当前周视图课程数 {len(after_cancel)}")
    else:
        still_there = set(before) <= set(after_cancel)
        t.record("PASS" if still_there else "FAIL",
                 f"点「取消」后课程未被删除: 删除前 {len(before)} 门 → 现在 {len(after_cancel)} 门"
                 f"（{before[:5]} → {after_cancel[:5]}）")
    t.screenshot("06_取消后课程表")

    # ── Step3b: 再进编辑 → 删除 → 点「删除」→ 课程被删除 ──────────
    t.step("Step3-删除 再次进入编辑 → 删除课程 → 点删除，课程应被删除")
    if not _open_edit_page(t):
        return t.finish()
    target = None
    try:
        target = ((t.read_rid(ET_NAME) or {}).get("text") or "").strip()
    except Exception:
        pass
    if not _tap_delete(t):
        t.record("FAIL", "未找到「删除课程」按钮（第二次，含上滑后）")
        return t.finish()
    time.sleep(2)
    if DLG_TITLE not in _screen(t):
        t.record("FAIL", "第二次点击删除课程后未出现确认弹框")
        return t.finish()
    if not t.tap_rid(BTN_CONFIRM, silent=True):
        t.record("FAIL", "确认框内未找到「删除」按钮")
        return t.finish()
    time.sleep(3)
    t.record("PASS", "点击「删除」后确认删除")

    # 回到周视图核对课程已消失
    if not _back_to_week_view(t):
        t.record("WARN", f"未能回到周视图核对，Activity={t.current_activity()}")
    # 删除后核对：先给网格渲染留出时间（异步渲染），再立即读一次
    # （不能用 _titles 的等待非空版，否则课程真被删空时会白等满 8s）
    time.sleep(3)
    after_del = _titles_now(t)
    # 判据：课程数确实减少（同名课程有多门，单看"名字消失"会误判——
    # 删掉一个「语文」后列表里仍有「语文」）。基准不可用时不能断言。
    if not before:
        t.record("WARN", f"删除前基准为空，无法判定「删除生效」；"
                         f"目标={target!r}，当前课程数 {len(after_del)}")
    else:
        decreased = len(after_del) < len(before)
        t.record("PASS" if decreased else "FAIL",
                 f"点「删除」后该课程被删除: 目标={target!r}，"
                 f"删除前 {len(before)} 门 → 现在 {len(after_del)} 门"
                 f"（减少 {len(before) - len(after_del)} 门；{after_del[:6]}）")
    t.screenshot("07_删除后课程表")

    return t.finish()


if __name__ == "__main__":
    run()
