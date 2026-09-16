"""联想日历 175：课程时间设置页字段与弹框可用性

探查记录（2026-09-03，TB323FU / 日历 9.0.0.83）：
- 页面 Activity = com.zui.calendar/.timetable.management.TimeSlotSettingsActivity
- 默认值: 上课时长 30分钟 / 课间休息 10分钟 / 上午 4节 / 下午 5节 / 晚上 0节
- 节数值都有独立 rid，可精确断言，无需 OCR
- 「上午课程/下午课程/晚上课程」是分组标题（clickable=false），点不动属正常设计，
  用例只要求"查看入口"，故断言其存在与节数显示
- 两个时长弹框的内容区是 Canvas 自绘（customPanel 下无 dump 节点），当前值读不到，
  故只断言弹框正常打开；数值用 OCR 补读并记 INFO（含背景干扰，不作断言）
- 「课程提醒时间」入口在**确认页**，不在课程时间设置页 —— 需先返回确认页再点
"""
import os
import re
import sys
import time

USER_INPUT = """测试用例 联想日历_175
前提：
已通过图库导入图片并完成解析，进入课程表基本信息确认页
操作步骤
1. 点击"课程时间设置"
2. 查看课程时间设置页字段
2.1. 打开每节课上课时长弹框
2.2. 打开课间休息时长弹框
2,3. 查看上午/下午/晚上课程节数入口
6. 打开课程提醒时间设置入口
预期结果
1. 正常进入课程时间设置页
2. 默认值和设置项正常展示
3. 各弹框或入口可正常打开"""

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase          # noqa: E402
from _flow import goto_图库导入_基本信息确认页   # noqa: E402

PKG = os.path.basename(_HERE)

PAGE_TIME = "课程时间设置"
ACT_TIME = "TimeSlotSettingsActivity"
PAGE_CONFIRM = "确认课程表基本信息"

# 默认值控件（探查所得 rid，全部可精确读值）
RID_LESSON = "com.zui.calendar:id/tv_lesson_duration"
RID_BREAK = "com.zui.calendar:id/tv_break_duration"
RID_MORNING = "com.zui.calendar:id/tv_morning_slot_count"
RID_AFTERNOON = "com.zui.calendar:id/tv_afternoon_slot_count"
RID_EVENING = "com.zui.calendar:id/tv_evening_slot_count"

EXPECT = [
    ("每节课上课时长", RID_LESSON, "30分钟"),
    ("课间休息时长", RID_BREAK, "10分钟"),
    ("上午课程节数", RID_MORNING, "4节"),
    ("下午课程节数", RID_AFTERNOON, "5节"),
    ("晚上课程节数", RID_EVENING, "0节"),
]

REMIND_OPTS = ["不提醒", "任务发生时", "5分钟前", "15分钟前", "30分钟前"]

# 弹框内容区（customPanel）的屏幕坐标，用于 OCR 补读当前值。
# 这是**已知待清除**的设备绑定值：按 smart-skill-roadmap v5.0 的 A2.3，应改为
#   从弹框容器 bounds 现场推导内容区（横竖屏/换设备都成立）。在改完之前用
#   noqa 显式留痕，而不是把 lint 规则整体关掉。
# noqa: pixel_const
DLG_TOP, DLG_BOTTOM = 1350, 1870


def _back(t):
    """返回上一页（BACK 键，绑定用例 serial）。"""
    t.adb_shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.3)


def _swipe_up(t):
    """内容上滚一屏（列表向下翻页）。坐标按当前窗口动态推导，横竖屏通用。"""
    w, h = t.d.window_size()
    t.adb_shell("input", "swipe", str(w // 2), str(int(h * 0.8)),
                str(w // 2), str(int(h * 0.25)), "400")
    time.sleep(1.2)


def _swipe_down(t):
    """内容下滚一屏（列表向上翻页，用于滚过头后回顶部）。"""
    w, h = t.d.window_size()
    t.adb_shell("input", "swipe", str(w // 2), str(int(h * 0.25)),
                str(w // 2), str(int(h * 0.8)), "400")
    time.sleep(1.2)


def _ocr_numbers(t, tag):
    """弹框数值是 Canvas 自绘，UI 树读不到 —— OCR 补读，只作 INFO 参考。"""
    try:
        items = t.ocr(DLG_TOP, DLG_BOTTOM)
    except Exception as e:                    # OCR 不可用不影响结论
        t.record("INFO", f"{tag} OCR 不可用: {e}")
        return
    nums = [it[3] for it in items if it[3].isdigit()] if items else []
    t.record("INFO", f"{tag} 弹框区 OCR 数值（半透明弹框会混入背景，仅供参考）: {nums}")


def _open_dialog(t, label, entry_text):
    """打开一个时长弹框并验证其正常打开，返回是否成功。"""
    if not t.tap_text(entry_text, wait=4, silent=True):
        t.record("FAIL", f"未找到'{entry_text}'入口")
        return False
    time.sleep(1.8)
    t.observe_dialogs(rounds=3)
    tx = t.screen_text()
    has_title = any(label in x for x in tx)
    has_btns = ("取消" in tx) and ("确定" in tx)
    ok = has_title and has_btns
    t.record("PASS" if ok else "FAIL",
             f"'{label}'弹框打开: 标题={has_title}, 取消/确定={has_btns}, 屏幕={tx[:8]}")
    t.screenshot(f"{label}弹框")
    _ocr_numbers(t, label)
    # 用「取消」关闭，顺带验证按钮可用
    if not t.tap_text("取消", wait=3, silent=True):
        _back(t)
    time.sleep(1.2)
    return ok


def run():
    t = TestCase("联想日历_175")
    t.start_watchdog(policy="allow")

    # ── 前提：图库导入 → 确认页（链路内部会按原因记 BLOCKED，不额外记 FAIL）──
    t.step("前提-图库导入并进入基本信息确认页")
    if not goto_图库导入_基本信息确认页(t, pm_clear=True, timeout=60,
                                    skip_if_ready=True):
        return t.finish()
    t.observe_dialogs(rounds=4)
    if not t.wait_text(PAGE_CONFIRM, timeout=8):
        t.record("BLOCKED", f"未进入确认页，屏幕={t.screen_text()[:6]}")
        return t.finish()
    t.record("PASS", "已进入'确认课程表基本信息'页")
    t.screenshot("00_确认页")

    # ── Step1: 点击「课程时间设置」─────────────────────────────────────
    t.step("Step1 点击课程时间设置")
    if not t.tap_text("课程时间设置", wait=4, silent=True):
        t.record("FAIL", f"未找到'课程时间设置'入口，屏幕={t.screen_text()[:8]}")
        return t.finish()
    time.sleep(2)  # 等课程时间设置页渲染
    t.observe_dialogs(rounds=3)
    act = t.current_activity()
    tx = t.screen_text()
    ok_page = (PAGE_TIME in " ".join(tx)) and (ACT_TIME in act)
    t.record("PASS" if ok_page else "FAIL",
             f"进入课程时间设置页: 标题={PAGE_TIME in ' '.join(tx)}, "
             f"Activity={act}（期望含 {ACT_TIME}）")
    t.screenshot("01_课程时间设置页")
    if not ok_page:
        return t.finish()

    # ── Step2: 默认值与设置项展示 ─────────────────────────────────────
    # 横屏一屏放不下 9 节（上午4+下午5+晚上0）：上午/下午在首屏，
    # 「晚上课程」组与后续节次在折叠线以下（RecyclerView 懒加载：
    # off-screen 的 rid 定位得到但 text 为空——首跑实测）。统一滚动收集。
    t.step("Step2 查看课程时间设置页字段默认值")
    for name, rid, exp in EXPECT[:4]:            # 首屏四项
        info = t.read_rid(rid)
        val = (info or {}).get("text", "")
        t.record("PASS" if val == exp else "FAIL",
                 f"{name}={val!r}（期望 {exp!r}）")
    # 节次明细滚动收集：标签「第N节」与时间段是两个独立文本节点，分别匹配去重。
    # 注意：首屏内容必须在任何滑动之前先采——先滑后采会漏掉第1/第2节和
    # 「上午课程」组头（二轮验证实测 7/9、缺上午组的根因）
    tx = t.screen_text()
    labels = {x for x in tx if re.match(r"^第\d+节$", x)}
    spans = set(re.findall(r"\d{2}:\d{2}-\d{2}:\d{2}", " ".join(tx)))
    groups = {g for g in ("上午课程", "下午课程", "晚上课程")
              if any(g in x and len(x) <= 8 for x in tx)}
    _swipe_up(t)                                  # 露出「晚上课程」组
    info = t.read_rid(RID_EVENING)
    val = (info or {}).get("text", "")
    t.record("PASS" if val == "0节" else "FAIL",
             f"晚上课程节数={val!r}（期望 '0节'）")
    for _ in range(4):
        tx = t.screen_text()
        labels |= {x for x in tx if re.match(r"^第\d+节$", x)}
        spans |= set(re.findall(r"\d{2}:\d{2}-\d{2}:\d{2}", " ".join(tx)))
        groups |= {g for g in ("上午课程", "下午课程", "晚上课程")
                   if any(x == g or (g in x and len(x) <= 8) for x in tx)}
        if len(labels) >= 9 and len(spans) >= 9:
            break
        _swipe_up(t)
    ok = len(labels) >= 9 and len(spans) >= 9
    t.record("PASS" if ok else "FAIL",
             f"课程节次明细: {len(labels)} 节课 / {len(spans)} 个时间段，"
             f"示例={list(zip(sorted(labels), sorted(spans)))[:3]}")
    t.screenshot("02_字段默认值")
    # Step2.1/2.2 的时长入口在页面顶部，滚回去（条件等待，防滚过头）
    for _ in range(5):
        if t.wait_text("每节课上课时长", timeout=2):
            break
        _swipe_down(t)

    # ── Step2.1 / 2.2: 两个时长弹框 ───────────────────────────────────
    t.step("Step2.1 打开每节课上课时长弹框")
    _open_dialog(t, "每节课上课时长（分钟）", "每节课上课时长")

    t.step("Step2.2 打开课间休息时长弹框")
    _open_dialog(t, "课间休息时长（分钟）", "课间休息时长")

    # ── Step2.3: 上午/下午/晚上节数入口 ───────────────────────────────
    # 这三个是分组标题（clickable=false），用例只要求"查看"，故断言存在与节数；
    # 分组散布在整页（晚上组在折叠线以下），用 Step2 滚动收集到的 groups 断言
    t.step("Step2.3 查看上午/下午/晚上课程节数入口")
    for kw in ("上午课程", "下午课程", "晚上课程"):
        ok = kw in groups
        t.record("PASS" if ok else "FAIL",
                 f"分组入口'{kw}'存在: {ok}（滚动收集={sorted(groups)}）")
    t.screenshot("03_节数入口")

    # ── Step6: 课程提醒时间（入口在确认页，先返回）─────────────────────
    t.step("Step6 打开课程提醒时间设置入口")
    _back(t)
    if not t.wait_text(PAGE_CONFIRM, timeout=8):
        t.record("FAIL", f"未返回确认页，屏幕={t.screen_text()[:8]}")
        return t.finish()
    t.record("INFO", "已从课程时间设置页返回确认页")

    # 「课程提醒时间」入口在确认页下部（横屏首屏外），滚动查找再点
    found = False
    for _ in range(4):
        if t.tap_text("课程提醒时间", wait=3, silent=True):
            found = True
            break
        _swipe_up(t)
    if not found:
        t.record("FAIL", f"滚动 4 屏仍未找到'课程提醒时间'入口，"
                         f"屏幕={t.screen_text()[:8]}")
        return t.finish()
    time.sleep(1.6)  # 等提醒时间选项渲染
    t.observe_dialogs(rounds=3)
    tx = t.screen_text()
    miss = [o for o in REMIND_OPTS if not any(o in x for x in tx)]
    shown = [x for x in tx if x in REMIND_OPTS]
    t.record("PASS" if not miss else "FAIL",
             f"课程提醒时间入口打开，选项={shown}，缺失={miss}")
    t.screenshot("04_课程提醒时间选项")
    if not t.tap_text("取消", wait=3, silent=True):
        _back(t)

    t.stop_watchdog()
    return t.finish()
