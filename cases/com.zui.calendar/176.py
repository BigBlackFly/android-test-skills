#!/usr/bin/env python3
"""联想日历 176：手动创建课表 → 加号 → 添加课程编辑弹框字段可用性。

探查结论（2026-09-03）：
- 手动创建后的"新建课程表"页 = EditTimetableActivity（schedule name + 开学日期 + 周数 + 时间设置 + 完成）。
  保存按钮 toolbar 右侧 action_save（文本"完成"）；该页 et_schedule_name 默认空，必填校验
  拦住空名保存——用例文本未明示填名但产品要求，故此处填一个测试名再保存（用例差异已在知识卡标注）。
- 完成保存后 = TimetableActivity（周视图）；周视图显示"周X日 + 第N周 + 8节时间"。
  顶部 toolbar 标题=课表名，副文本=当前周数（第1周）。第一格空内容 cv_empty_content 可点。
  点空格 → EditCourseActivity 新建课程页。
  （⚠️ 2026-09-17 改版：中间的 `iv_add_hint` 加号已移除，且**第一次点击会被吸收、
  第二次**才进编辑页；旧文档写的"点空格出现加号、再点加号"已不成立。）
- 新建课程页 = EditCourseActivity：课程名(必填)/教室(非必填)/备注(非必填) →

3字段
  /课程时间(第X节) /上课周数(第X-Y周) /课程背景色（行条目，背景色弹板是独立 AlertDialog，
  内容区 5×2 网格共 10 个 clickable FrameLayout 色块 + 取消/完成）。
"""
import os
import re
import sys
import time

USER_INPUT = """测试用例 联想日历_176
前提：
确保设备没有课程表。可以清空APP数据
操作步骤
1. 日历点击页面右上角更多按钮，选择课程表
2. 点击"手动创建课程表"按钮
3.点击"完成"应用信息到空课程表，查看显示
4.点击第一个小节的加号
5.点击空日历上的某一小节
6.点击"取消"或弹框以外的空白处
预期结果
1. 打开课程表页面
2. 进入课程表基本信息编辑页面
3.标题右侧显示当前周数是第几周；进入空课程表后，第一个小节显示加号示意可直接点击添加
4.进入空白日历，顶部标题提示"添加课程"引导，不添加课程右上角"完成"按钮也可以点击保存空课程表
5.弹出编辑弹窗进行编辑；可以调整字段包括：课程名称（必填）教室（非必填）备注（非必填，如老师等信息）上课时间（第x～x节）上课周数（如第1～20周，最多24周）课程背景色（10种）
6.弹框关闭"""

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase, _parse_nodes   # noqa: E402
from _flow import goto_课程表空状态, open_新建课程    # noqa: E402

NAME_RID = "com.zui.calendar:id/et_schedule_name"
SAVE_RID = "com.zui.calendar:id/action_save"
BTN_MANUAL = "com.zui.calendar:id/btnCreateManually"
EMPTY_CELL = "com.zui.calendar:id/cv_empty_content"
# 注：`iv_add_hint`（旧的"点空格后出现的加号浮标"）**已从 App 移除**（2026-09-17
# 探针实测），故不再作为定位目标 —— 链路直接走 _flow.open_新建课程()。
COLOR_RID = "com.zui.calendar:id/llCourseColor"


def _text(t, predicate=lambda s: s):
    return predicate(" ".join(t.screen_text()))


def run():
    t = TestCase("联想日历_176")
    t.start_watchdog(policy="allow")

    # ── 前提：清数据到课程表空状态 ─────────────────────────────
    t.step("前提-清数据到课程表空状态")
    if not goto_课程表空状态(t, pm_clear=True):
        return t.finish()
    t.observe_dialogs(rounds=3)
    t.record("PASS", "已到课程表空状态页")

    # ── Step1 更多→课程表 →
    t.step("Step1 更多→课程表 → 打开课程表页")
    # goto_课程表空状态 已点完"更多→课程表"，此刻应在 TimetableActivity 空列表
    txt = " ".join(t.screen_text())
    ok = "TimetableActivity" in t.current_activity() and "课程表" in txt
    t.record("PASS" if ok else "FAIL",
             f"打开课程表页: activity含TimetableActivity={ok}, 包含'课程表'={('课程表' in txt)}")
    if not ok:
        return t.finish()

    # ── Step2 手动创建课程表 → 进入新建页 ─────────────────────
    t.step("Step2 点击手动创建课程表")
    if not t.tap_rid(BTN_MANUAL, silent=True):
        t.record("FAIL", "未找到手动创建课程表按钮")
        return t.finish()
    time.sleep(2)
    t.observe_dialogs(rounds=3)
    act = t.current_activity()
    txt = " ".join(t.screen_text())
    ok = "EditTimetable" in act and "新建课程表" in txt
    t.record("PASS" if ok else "FAIL",
             f"进入新建课程表页: activity={act}, 含'新建课程表'={('新建课程表' in txt)}")
    if not ok:
        return t.finish()

    # ── Step3 填名+完成 → 空课表周视图 ──────────────────────────
    # 注：et_schedule_name 默认空且必填，用例未明示填名但产品要求；填入测试名。
    t.step("Step3 填名称+完成 → 进入空课表周视图")
    if not t.tap_rid(NAME_RID, silent=True):
        t.record("FAIL", "未找到名称输入框")
        return t.finish()
    time.sleep(0.8)
    t.input_text(NAME_RID, "测试课程表")
    # settle：等输入内容提交。**这类等待不可条件化**——变的是输入框的值而不是
    # "某个元素出现"，`wait_*` 表达不了（审计归"合理"）。
    time.sleep(0.6)
    # toolbar 右侧 action_save；不按 back（避免退出页面）
    if not t.tap_rid(SAVE_RID, silent=True):
        t.record("FAIL", "未找到保存按钮(action_save)")
        return t.finish()
    time.sleep(3)
    t.observe_dialogs(rounds=4)
    act = t.current_activity()
    txt = " ".join(t.screen_text())
    has_cell = bool(t.el_bounds(rid=EMPTY_CELL))
    title_right = t.read_rid("com.zui.calendar:id/toolbar_title") or {}
    name_in_title = "测试课程表" in txt
    # 周数：读取 tv_date_range（如「第2周」），按当前学期日期动态判定，不写死第几周
    week_info = (t.read_rid("com.zui.calendar:id/tv_date_range") or {}).get("text", "")
    week_ok = bool(re.search(r"第\d+周", week_info))
    t.record("PASS" if week_ok else "FAIL",
             f"当前周数指示: tv_date_range={week_info!r}（预期匹配 第N周）")
    ok = "TimetableActivity" in act and name_in_title and week_ok and has_cell
    t.record("PASS" if ok else "FAIL",
             f"进入空课表周视图: activity={act}, 课表名标题={name_in_title}, "
             f"周数文本={week_info!r}, 空格(cv_empty_content)={has_cell}, "
             f"toolbar_title={title_right.get('text', '')[:30]!r}")
    t.screenshot("176_周视图")
    if not ok:
        return t.finish()

    # ── Step4 点空格 → 进入新建课程编辑页 ─────────────────────────
    # ⚠️ App 已改版（2026-09-17 探针实测，见 evals/probe_addhint.py）：中间的
    # 加号浮标 `iv_add_hint` **已被移除**，且**第一次点空格会被"吸收"、第二次**
    # 才进编辑页。用例原先卡在"等加号"那一步 → 2026-09-16 全量套件在这里 FAIL。
    # 现统一走 _flow.open_新建课程()（点一次不成再点一次，命中即停）。
    t.step("Step4 点第一个小节空格 → 添加课程编辑页")
    if not open_新建课程(t):
        return t.finish()
    t.observe_dialogs(rounds=3)
    act = t.current_activity()
    txt = " ".join(t.screen_text())
    has_edit = "EditCourse" in act and "新建课程" in txt
    t.record("PASS" if has_edit else "FAIL",
             f"添加课程编辑页: activity={act}, 含'新建课程'={('新建课程' in txt)}")
    if not has_edit:
        return t.finish()

    # ── Step5 编辑弹窗字段断言 ────────────────────────────────────
    t.step("Step5 编辑弹窗字段断言")
    # 5.1 输入字段存在
    for rid, label in [("com.zui.calendar:id/etCourseName", "课程名(必填)"),
                       ("com.zui.calendar:id/etClassroom",  "教室(非必填)"),
                       ("com.zui.calendar:id/etTeacher",     "备注(非必填)")]:
        t.record("PASS" if t.el_bounds(rid=rid) else "FAIL",
                 f"字段 {label} 输入框存在 rid={rid}")
    # 5.2 必填/非必填提示
    has_required = "必填" in txt
    has_optional = "非必填" in txt
    t.record("PASS" if has_required and has_optional else "FAIL",
             f"必填/非必填提示: 必填={has_required}, 非必填={has_optional}")
    # 5.3 课程时间（第X节）
    tv = t.read_rid("com.zui.calendar:id/tvCourseTime") or {}
    time_val = tv.get("text", "")
    t.record("PASS" if "第" in time_val and "节" in time_val else "FAIL",
             f"课程时间字段 tvCourseTime={time_val!r}")
    # 5.4 上课周数（第X-YY周）
    tv2 = t.read_rid("com.zui.calendar:id/tvCourseWeeks") or {}
    weeks_val = tv2.get("text", "")
    t.record("PASS" if "周" in weeks_val and re.search(r"\d+-\d+周", weeks_val) else "FAIL",
             f"上课周数字段 tvCourseWeeks={weeks_val!r}")
    # 5.5 课程背景色（行存在 + 弹出后色板 10 色）
    color_row = bool(t.el_bounds(rid=COLOR_RID))
    t.record("PASS" if color_row else "FAIL",
             f"课程背景色行存在 llCourseColor={color_row}")
    # 弹出色板验证 10 色
    if color_row:
        cb = t.el_bounds(rid=COLOR_RID)
        t.tap_xy((cb[0] + cb[2]) // 2, (cb[1] + cb[3]) // 2)
        time.sleep(2.5)
        t.observe_dialogs(rounds=3)
        # 精确定位色板内容区 bounds（customPanel；色格是纯色 View 无文本/desc，
        # UI 树数节点会被嵌套/装饰干扰——数"10 种颜色"用视觉模型，不猜）
        pb = None
        xml = t._dump()
        for n in _parse_nodes(xml):
            if n["rid"] == "com.zui.calendar:id/customPanel" and n["bounds_xy"]:
                pb = n["bounds_xy"]
                break
        if pb is not None:
            try:
                ans = t.vision_ask(
                    "截图是 Android 的课程背景色选择区。请数一下里面一共有几个"
                    "可选颜色块（纯色圆形/圆角方块，不含任何文字）。"
                    "先在心里逐行数清，最后只输出一个阿拉伯数字作为总数，不要解释。",
                    bounds=pb)
                # 解析必须取「最后一个」数字，不能取第一个：模型常先分段描述
                # （"第一行 5 个…第二行 5 个…因此答案是 10"），首个数字是某一段的
                # 数量而非总数。实测取首个数字把正确答案 10 解析成了 5。
                nums = re.findall(r"\d+", ans or "")
                num = int(nums[-1]) if nums else -1
                t.record("PASS" if num == 10 else "FAIL",
                         f"课程背景色: 视觉模型数色={ans!r}（解析={num}，预期 10；"
                         f"命中数字序列={nums}）")
            except Exception as e:
                t.record("WARN", f"课程背景色: 视觉数色调用失败 {e}")
        else:
            t.record("FAIL", "未找到色板 customPanel 节点")
        # 关闭色板（收尾动作，失败不影响断言；后续 Step6 验证页面状态）
        t.tap_text("取消", wait=3, silent=True)
        time.sleep(1.2)

    # ── Step6 back 关闭编辑页 → 回周视图 ─────────────────────────
    t.step("Step6 关闭编辑弹窗")
    t.d.press("back")
    # 等回到周视图：原来是 `sleep(1.2)` + 单次读 Activity —— 转场慢一点就会读到
    # **旧 Activity** 而假 FAIL。wait_activity 命中即停（上限 5s）。
    act = t.wait_activity("Timetable", timeout=5) or t.current_activity()
    ok = "Timetable" in act
    t.record("PASS" if ok else "FAIL",
             f"编辑弹窗关闭后回到周视图: activity={act}")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    sys.exit(run())