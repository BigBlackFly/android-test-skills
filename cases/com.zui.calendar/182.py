#!/usr/bin/env python3
"""联想日历_182 用例：空课程表→新建课程，验证建课弹窗字段与各可编辑项、周数占用保护
前提：创建空课程表，关闭「周末是否有课」开关
步骤:
1.进入空课程表后，点空白小节→出现+→再点+（进新建课程）
2.查看可调整字段
3.点击上课时间（节段范围选择器）
4.点击上课周数（全选/单周/双周 + 1~N 周多选，空选 toast）
5.点击课程背景色（10 色块）
6.建第一节(8:00-8:50)课程、选 1/3/5 周；再建一门第一节课程，点上课周数验证 1/3/5 被占用
"""
import os
import re
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_182
前提：创建空课程表，关闭周末有课开关
操作步骤
1.进入空课程表后，点击空白日历上的某一小节。出现+号再次点击
2.查看可调整字段
3.点击上课时间
4.点击上课周数
5.点击课程背景色
6.上课周数设置为20周，创建一个第一节（8:00-8:50）的课程，选择1，3，5周，再次创建一个第一节的课程，点击上课周数
预期结果
1.弹出新建课程编辑弹窗
2.课程名称显示（必填），教室，备注（如老师）显示（非必填）；还有上课时间、周数、课程背景色可编辑
3.x到х节（1～12到1～12节自由选择）
4.弹出多选弹框，默认选中设定的全部周数；顶部有3个单选-全部、单选、双选；下面为1～24周多选，根据课程表周数，可自由勾选，如果选中的既不符合全部、单选，也不是双选，则自动不选中任何顶部单选项；如果一个周都不选时，点击确定，则toast提示：请选择上课周数；选择“确定”按钮则展示已选周数的课程表在列表中；选择“取消：按钮直接关闭弹框；
5.点击课程颜色，在10个颜色中任选切换，具体默认颜色、顺序由UI定义；
6.新创建的课程周数不可选1，3，5周，可根据设定的上课周数进行选择其他周数"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import goto_手动创建课程表, open_新建课程, PKG

WEEKEND_SW = "com.zui.calendar:id/switch_weekend_classes"
NAME_RID = "com.zui.calendar:id/et_schedule_name"
SAVE_RID = "com.zui.calendar:id/action_save"
EMPTY_RID = "com.zui.calendar:id/cv_empty_content"
# 注：`iv_add_hint`（旧的"点空格后出现的加号浮标"）**已从 App 移除**（2026-09-17
# 探针实测），链路直接走 _flow.open_新建课程()，不再需要该定位目标。

ET_COURSE_NAME = "com.zui.calendar:id/etCourseName"
LL_TIME = "com.zui.calendar:id/llCourseTime"
TV_TIME = "com.zui.calendar:id/tvCourseTime"
LL_WEEKS = "com.zui.calendar:id/llCourseWeeks"
TV_WEEKS = "com.zui.calendar:id/tvCourseWeeks"
LL_COLOR = "com.zui.calendar:id/llCourseColor"
COLOR_IND = "com.zui.calendar:id/viewColorIndicator"

WEEK_GRID = "com.zui.calendar:id/weeks_chip_group"
QUICK_GRID = "com.zui.calendar:id/quick_select_chip_group"


def _switch_checked(t, rid):
    try:
        return bool(t.d(resourceId=rid).info.get("checked"))
    except Exception:
        return None


def _chip_sel(t, w):
    """读周次 chip 的选中态（selected/checked）。"""
    try:
        info = t.d(resourceId=WEEK_GRID).child(text=str(w)).info
        return bool(info.get("selected") or info.get("checked"))
    except Exception:
        return None


def _set_weeks(t, want, total=20):
    """把周数多选设置为 want 集合。先读实际态再逐一切换；读不到则尽力而为。"""
    states = {w: _chip_sel(t, w) for w in range(1, total + 1)}
    if any(v is not None for v in states.values()):
        for w in range(1, total + 1):
            s = states.get(w)
            if s is None:
                continue
            if (w in want) != s:
                try:
                    t.d(resourceId=WEEK_GRID).child(text=str(w)).click()
                    time.sleep(0.12)
                except Exception:
                    pass
    else:
        # 读不到选中态：假定默认全选，逐个取消不想要的
        for w in range(1, total + 1):
            if w not in want:
                try:
                    t.d(resourceId=WEEK_GRID).child(text=str(w)).click()
                    time.sleep(0.1)
                except Exception:
                    pass
    time.sleep(0.3)


def _enter_add_course(t):
    """从空课表点空格 → 进新建课程页。返回是否成功。

    ⚠️ App 已改版（2026-09-17 探针实测，见 evals/probe_addhint.py）：中间的加号
    浮标 `iv_add_hint` **已被移除**，且**第一次点空格会被"吸收"**、第二次才进编辑页
    —— 本函数原先卡在"等加号"，所以 2026-09-16 全量套件在这里 FAIL。
    现统一走 `_flow.open_新建课程()`（点一次不成再点一次、命中即停）。
    """
    return open_新建课程(t)


def run():
    t = TestCase("联想日历_182")

    # ── 前置：建空课程表 + 关闭周末开关 ──────────────────────────
    t.step("前置-建空课程表并关闭周末开关")
    t.start_watchdog(policy="allow")
    if not goto_手动创建课程表(t, pm_clear=True):
        t.record("FAIL", "无法进入手动创建课程表页")
        t.blocked("前置未达成")
        return t.finish()
    t.input_text(NAME_RID, "自动化测试课表")
    time.sleep(0.8)
    st = _switch_checked(t, WEEKEND_SW)
    if st is True:
        t.tap_rid(WEEKEND_SW, silent=True)
        time.sleep(0.6)
        t.record("PASS", "周末开关原=开，已切换为关")
    else:
        t.record("PASS", "周末开关已为关（或初始即关），保持关闭")
    t.tap_rid(SAVE_RID, silent=True)
    # 原有的 `sleep(3)` 已删：后面的 wait_rid(EMPTY_RID, 10) 覆盖这段等待
    # （原来最坏 sleep3 + wait10 = 13s，现在最坏 10s 且命中即停）。
    if not t.wait_rid(EMPTY_RID, timeout=10):
        t.record("FAIL", "保存后未到达空课表（无 cv_empty_content）")
        t.blocked("前置未达成")
        return t.finish()
    t.record("PASS", "已建空课程表并抵达空课表页（周末开关已关）")
    t.screenshot("00_空课表")
    t.stop_watchdog()  # 进入精确交互阶段，停用看门狗避免误触

    # ── Step1：点空格→+→进新建课程弹窗 ────────────────────────
    t.step("Step1 进新建课程弹窗")
    if not _enter_add_course(t):
        return t.finish()
    t.record("PASS", "弹出新建课程编辑弹窗（EditCourseActivity，含 etCourseName）")
    t.screenshot("01_新建课程弹窗")

    # ── Step2：查看可调整字段 ────────────────────────────────
    t.step("Step2 字段完整性（必填/非必填/可编辑）")
    texts = t.screen_text()
    name_ok = t.el_bounds(rid=ET_COURSE_NAME) is not None
    # 必填提示
    name_hint = ""
    try:
        name_hint = t.read_rid(ET_COURSE_NAME).get("text", "")
    except Exception:
        name_hint = ""
    room_ok = any("教室" in x for x in texts)
    note_ok = any(("备注" in x) or ("老师" in x) for x in texts)
    time_ok = t.el_bounds(rid=LL_TIME) is not None
    weeks_ok = t.el_bounds(rid=LL_WEEKS) is not None
    color_ok = t.el_bounds(rid=LL_COLOR) is not None
    t.record("PASS" if name_ok else "FAIL",
             f"课程名输入存在(必填)；当前提示='{name_hint}'")
    t.record("PASS" if (room_ok and note_ok) else "WARN",
             f"教室={room_ok}、备注(老师)={note_ok} 字段存在（标注非必填）")
    t.record("PASS" if (time_ok and weeks_ok and color_ok) else "FAIL",
             f"上课时间/周数/背景色 入口均存在且可点击(time={time_ok},weeks={weeks_ok},color={color_ok})")
    t.screenshot("02_字段")

    # ── Step3：点击上课时间 ──────────────────────────────────
    t.step("Step3 上课时间（节段范围选择器）")
    t.tap_rid(LL_TIME, silent=True)
    time.sleep(1.0)
    title = ""
    try:
        title = t.read_rid("com.zui.calendar:id/npTitle").get("text", "")
    except Exception:
        pass
    has_cancel = t.el_bounds(rid="android:id/button2") is not None
    has_ok = t.el_bounds(rid="android:id/button1") is not None
    m = re.match(r"^\d+到\d+节$", title or "")
    t.record("PASS" if m else "FAIL",
             f"上课时间弹框标题='{title}'（期望 x到x节 格式，匹配节段范围选择器）")
    t.record("PASS" if (has_cancel and has_ok) else "WARN",
             f"弹框含取消/确定按钮(cancel={has_cancel},ok={has_ok})")
    t.screenshot("03_上课时间弹框")
    t.tap_text("取消", silent=True)
    time.sleep(0.6)

    # ── Step4：点击上课周数 ──────────────────────────────────
    t.step("Step4 上课周数（多选弹框）")
    t.tap_rid(LL_WEEKS, silent=True)
    # 等弹框标题出现（原来是 sleep(1.2) + 单次读屏：弹框慢一点即假 FAIL）
    t.wait_text_contains("上课周数", timeout=1.2)
    texts = t.screen_text()
    title_ok = "上课周数" in texts
    quick = [k for k in ("全选", "单周", "双周") if k in texts]
    # 真机顶部三选项为 全选/单周/双周；规格写的是 全部/单选/双选（措辞差异，记录说明）
    weeks_present = all(str(w) in texts for w in range(1, 21))
    t.record("PASS" if title_ok else "FAIL", f"弹出『上课周数』多选弹框(title={title_ok})")
    t.record("PASS" if len(quick) == 3 else "WARN",
             f"顶部快捷选项={quick}（真机为 全选/单周/双周；规格措辞为 全部/单选/双选）")
    t.record("PASS" if weeks_present else "WARN",
             f"周次 1~20 多选项齐全(依据课表总周数=20；规格写 1~24)")
    # 默认选中全部周数
    sel = [_chip_sel(t, w) for w in range(1, 21)]
    all_sel = sum(1 for s in sel if s is True)
    t.record("PASS" if all_sel == 20 else "WARN",
             f"默认选中周数={all_sel}/20（期望=全部已选）")
    t.screenshot("04_周数弹框")

    # 空选→确定→toast 校验
    for w in range(1, 21):
        try:
            if _chip_sel(t, w):
                t.d(resourceId=WEEK_GRID).child(text=str(w)).click()
                time.sleep(0.1)
        except Exception:
            pass
    time.sleep(0.3)
    before_sel = sum(1 for w in range(1, 21) if _chip_sel(t, w) is True)
    # toast 断言 = 截图先行（2026-09-07 讨论定稿）：toast 窗口 4s/7s 档
    # （AOSP ToastPresenter），旧写法 tap_text 默认 observe=True（点击后弹窗
    # 检查链 ~1.5s）+ sleep 0.8 + OCR 冷加载，轮到读屏时 toast 已到窗口边缘
    # ——首跑 WARN 很可能是采集漏了而非产品未弹。tap 用 observe=False 跳过
    # 点击后链路，点完立即 capture_toast 定格同帧。
    t.tap_text("完成", silent=True, observe=False)
    toast_texts, toast_shot = t.capture_toast()
    toast_texts = toast_texts or []
    hit = any("请选择上课周数" in s for s in toast_texts)
    if not hit:
        # 定帧 OCR 混背景读不准半透明 toast → 视觉模型对定格截图兜底
        try:
            ans = t.vision_ask(
                "屏幕上是否有 toast 提示？若有只回答 toast 完整文本，没有回答'无'")
            hit = "请选择上课周数" in (ans or "")
        except Exception:
            pass
    if before_sel == 0:
        # 确实空选了；若没弹 toast，则与规格不符
        t.record("PASS" if hit else "WARN",
                 f"空选(0项)后点完成：toast{'命中' if hit else '未命中'}'请选择上课周数' "
                 f"(ocr={toast_texts[:4]}, 定格截图={toast_shot})；"
                 f"规格要求空选时 toast 提示，截图先行复核仍未弹则记 WARN（产品差异）")
    else:
        t.record("WARN",
                 f"空选校验被跳过：关闭弹框前仍有 {before_sel} 个周被选中，无法确认纯空选行为")
    # 若 toast 后弹框仍开着（产品可能不自动关闭），先关掉防级联：
    # 直接 reopen 会让下面的周数设置落在旧弹框上产生连锁误判
    if t.el_bounds(rid=WEEK_GRID) is not None:
        if not t.tap_text("取消", wait=2, silent=True):
            t.tap_text("完成", wait=2, silent=True)
        time.sleep(0.5)

    # 重新打开并设为 1,3,5（供 Step6 占用校验）
    t.tap_rid(LL_WEEKS, silent=True)
    time.sleep(1.0)
    _set_weeks(t, {1, 3, 5}, total=20)
    t.tap_text("完成", silent=True)
    time.sleep(0.5)
    wk = ""
    try:
        wk = t.read_rid(TV_WEEKS).get("text", "")
    except Exception:
        pass
    t.record("PASS" if ("3" in wk and "5" in wk and "20" not in wk) else "WARN",
             f"周数选择结果 tvCourseWeeks='{wk}'（期望仅含 1/3/5 周）")
    t.screenshot("04b_周数已选135")

    # ── Step5：点击课程背景色 ────────────────────────────────
    t.step("Step5 课程背景色（10 色块）")
    t.tap_rid(LL_COLOR, silent=True)
    # 同上：等弹框标题出现再判（原来是 sleep(1.2) + 单次读屏）
    t.wait_text_contains("课程背景色", timeout=1.2)
    color_title = "课程背景色" in " ".join(t.screen_text())
    panel = t.el_bounds(rid="com.zui.calendar:id/customPanel")
    n_colors = None
    if panel:
        # 直接问总数会抖动（19:50 实测数成 9）；改"逐个列出颜色再给总数"，
        # 列举强制模型逐块扫描，计数显著更稳
        try:
            ans = t.vision_ask(
                "图中是颜色选择面板。请先逐个列出每个颜色方块的颜色名称"
                "（一行一个，不要遗漏贴边/半遮挡的），最后单独一行回答："
                "总数=N。", bounds=panel)
            m2 = re.search(r"总数\s*=\s*(\d+)", str(ans)) or re.search(r"\d+", str(ans))
            n_colors = int(m2.group(1)) if m2 else None
        except Exception as e:
            t.record("WARN", f"视觉数色块失败: {e}")
    t.record("PASS" if color_title else "FAIL", f"弹出『课程背景色』弹框(title={color_title})")
    t.record("PASS" if (n_colors == 10) else "WARN",
             f"颜色方块数量={n_colors}（期望 10；视觉计数）")
    t.screenshot("05_背景色弹框")
    t.tap_text("取消", silent=True)
    time.sleep(0.5)

    # ── Step6：建课 + 周数占用保护 ───────────────────────────
    t.step("Step6 建第一节(1/3/5周)课程 + 占用校验")
    # 填课程名（必填），默认第1节；周数已设为 1/3/5
    t.input_text(ET_COURSE_NAME, "测试课程A")
    # settle：等输入内容提交（变的是输入框的**值**，`wait_*` 表达不了 → 审计归"合理"）
    time.sleep(0.5)
    t.tap_text("完成", silent=True)  # 保存课程，回空课表
    # 原有的 `sleep(2.5)` 已删：下一行 wait_rid(EMPTY_RID, 10) 覆盖这段等待
    back_ok = t.wait_rid(EMPTY_RID, timeout=10) or ("测试课程A" in " ".join(t.screen_text()))
    t.record("PASS" if back_ok else "WARN",
             f"课程A(第1节,1/3/5周)已保存并回到课表(看到课程A={back_ok})")
    t.screenshot("06_课程A已建")

    # 再建一门第一节课程
    if not _enter_add_course(t):
        return t.finish()
    t.input_text(ET_COURSE_NAME, "测试课程B")
    # settle：同上，等输入提交（值变化类，不可条件化）
    time.sleep(0.5)
    # 打开周数弹框，验证 1/3/5 被占用（未默认选中 + 点不动）
    t.tap_rid(LL_WEEKS, silent=True)
    time.sleep(1.2)
    sel_after = {w: _chip_sel(t, w) for w in range(1, 21)}
    occupied_unsel = all(sel_after.get(w) is False for w in (1, 3, 5))
    others_sel = all(sel_after.get(w) is True for w in (2, 4, 6))
    # 更严格：2、4、6 都应默认被选中，而 1、3、5 不应选中
    t.record("PASS" if occupied_unsel else "FAIL",
             f"课程A占用第1节1/3/5周后，新建课程B同节次弹框中 1/3/5 未默认选中={occupied_unsel}, "
             f"各周选中态={ {1:sel_after.get(1),3:sel_after.get(3),5:sel_after.get(5)} }")
    t.record("PASS" if others_sel else "WARN",
             f"非占用周（2/4/6）默认已选中={others_sel}")

    # 额外点按测试：尝试点 1，看它是否仍不选中（点不动）
    tap_changed = False
    try:
        t.d(resourceId=WEEK_GRID).child(text="1").click()
        time.sleep(0.4)
        tap_changed = _chip_sel(t, 1) is True
    except Exception as e:
        t.record("WARN", f"尝试点按周1失败: {e}")
    t.record("PASS" if not tap_changed else "WARN",
             f"尝试点按已被占用的周1：{'仍可被选中（异常）' if tap_changed else '仍保持未选中（点不动，符合保护逻辑）'}")
    t.screenshot("06b_占用校验")
    t.tap_text("取消", silent=True)
    time.sleep(0.4)

    return t.finish()


if __name__ == "__main__":
    run()
