#!/usr/bin/env python3
"""联想日历_177 用例：手动创建课程表 → 课程表基本信息编辑页字段校验
前提：手动创建课程表后进入课程表基本信息编辑页（EditTimetableActivity）
步骤:
1.查看课程表名称  2.查看学期开始时间  3.查看当前周数  4.点击学期总周数
5.查看周末有课开关  6.点击显示非本周课程  7.清空名称/学期开始时间后看完成按钮
预期:
1.名称为必填、可点击弹键盘编辑、最多20字符
2.点击调用系统日期弹框
3.当前周数默认按当前日期计算、格式正确、可查看
4.默认20周、点击可选择总周数
5.周末有课开关默认关闭、可点击开启
6.显示非本周课程开关默认关闭、可点击开启
7.必填为空时完成按钮置灰，两项都有内容才可点击
"""
import os
import re
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_177
前提：
手动创建课程表后进入课程表基本信息编辑页
操作步骤
1.查看课程表名称
2.查看学期开始时间
3.查看当前周数
4.点击学期总周数
5.查看周末有课开关
6.点击显示非本周课程
7.课程表名称及学期开始时间不填写内容时查看右上角完成按钮
预期结果
1.课程表名称为必填项，可点击名称弹出键盘进行编辑，最多20字符
2.点击后调用系统的日期弹框
3.默认根据当前时间计算开学日期，当前周数默认为当前日期所在周数，点击可修改
4.默认20周，点击可选择学期总共多少周
5.周末有课开关默认关闭，可点击开启
6.显示非本周课程开关默认关闭，可点击开启
7.完成按钮置灰，两个选项都有内容时才可点击完成"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import goto_手动创建课程表

PKG = "com.zui.calendar"
P = f"{PKG}:id/"
NAME = P + "et_schedule_name"
FINISH = P + "save_view"          # 手动创建页完成按钮是 save_view（非图库导入的 btn_finish）
LAYOUT_SEM_START = P + "layout_semester_start_date"
TV_SEM_START = P + "tv_semester_start_date"
LAYOUT_CUR_WEEK = P + "layout_current_week"
TV_CUR_WEEK = P + "tv_current_week"
LAYOUT_TOTAL = P + "layout_total_weeks"
TV_TOTAL = P + "tv_total_weeks"
SW_WEEKEND = P + "switch_weekend_classes"
SW_NONCUR = P + "switch_show_non_current_week"


def _text(t, rid):
    info = t.read_rid(rid)
    return (info or {}).get("text", "") if isinstance(info, dict) else (info or "")


def _checked(t, rid):
    info = t.read_rid(rid)
    if not isinstance(info, dict):
        return None
    v = info.get("checked")
    if isinstance(v, str):
        return v == "true"
    return bool(v)


def _hide_ime(t):
    """收起输入法（ESC 键只收键盘不退页面，比 BACK 安全）。"""
    t.adb_shell("input", "keyevent", "111")
    time.sleep(0.8)


def _ime_shown(t):
    """探测输入法是否弹出（dumpsys input_method 末行 mInputShown）。"""
    out = t.adb_shell("dumpsys", "input_method")
    lines = [ln for ln in (out or "").splitlines() if "mInputShown" in ln]
    if not lines:
        return None
    return "true" in lines[-1].lower()


def _toggle_switch(t, rid, label):
    """读开关初态 → 点按 → 验证翻转 → 点回 → 验证还原。"""
    before = _checked(t, rid)
    if before is None:
        t.record("WARN", f"{label}: 未能读到开关初始状态（dump 无 checked 属性）")
        return
    t.record("INFO", f"{label} 初始状态: {'开' if before else '关'}")
    if not t.tap_rid(rid, silent=True):
        t.record("FAIL", f"{label}: 开关点按失败（rid 未找到）")
        return
    time.sleep(1)
    mid = _checked(t, rid)
    flipped = (mid is not None and mid != before)
    t.record("PASS" if flipped else "FAIL",
             f"{label} 点按后: {'开' if mid else '关'}（{'翻转成功' if flipped else '未翻转'}）")
    if not flipped:
        return
    t.tap_rid(rid, silent=True)          # 点回还原，不留设备副作用
    time.sleep(1)
    back = _checked(t, rid)
    restored = (back == before)
    t.record("PASS" if restored else "WARN",
             f"{label} 还原后: {'开' if back else '关'}（{'还原成功' if restored else '还原失败'}）")


def run():
    t = TestCase("联想日历_177")

    # ── 前置：手动创建课程表 → 基本信息编辑页（EditTimetableActivity）────
    t.step("前置 手动创建课程表进入基本信息编辑页")
    if not goto_手动创建课程表(t, pm_clear=True):
        return t.finish()

    # ── Step1: 课程表名称（必填 + 可编辑 + ≤20 字符 + 弹键盘）──────────
    t.step("Step1 查看课程表名称：必填、可点击弹键盘编辑、最多20字符")
    pre = _text(t, NAME)
    t.record("INFO", f"名称预设（占位/值）: {pre!r}")
    # 点击名称 → 应弹出键盘进入编辑
    if not t.tap_rid(NAME, silent=True):
        t.record("FAIL", "未找到课程表名称输入框")
        return t.finish()
    time.sleep(0.8)
    kb = _ime_shown(t)
    t.record("INFO", f"点击名称后键盘弹出: {'是' if kb else ('否' if kb is False else '未能确认')}")
    # 输入 25 字符验证截断到 20
    LONG = "一二三四五六七八九十一二三四五六七八九十一二三四五"   # 25 个汉字
    if not t.input_text(NAME, LONG, silent=True):
        t.record("FAIL", "名称输入框 input_text 失败（rid 未找到）")
        return t.finish()
    time.sleep(0.8)
    _hide_ime(t)
    after = _text(t, NAME)
    ok_len = 0 < len(after) <= 20
    t.record("PASS" if ok_len else "FAIL",
             f"输入 25 字符后实际保留 {len(after)} 字符: {after!r}"
             f"（{'≤20 截断生效' if ok_len else '超出 20 字符上限，截断未生效'}）")
    ok_edit = (after != pre) and len(after) > 0
    t.record("PASS" if ok_edit else "FAIL",
             f"名称可编辑: 输入后值已变化（{pre!r} → {after!r}）")

    # ── Step2: 学期开始时间 → 系统日期弹框 ──────────────────────────────
    t.step("Step2 点击学期开始时间调起系统日期弹框")
    orig_date = _text(t, TV_SEM_START)
    t.record("INFO", f"当前学期开始时间: {orig_date!r}")
    if not t.tap_rid(LAYOUT_SEM_START, silent=True):
        t.record("FAIL", f"未找到学期开始时间入口 {LAYOUT_SEM_START}，屏幕={t.screen_text()[:8]}")
        return t.finish()
    has_picker = False
    for _ in range(6):                    # 条件等待 DatePicker 类窗口
        time.sleep(1)
        try:
            if t.d(className="android.widget.DatePicker").exists:
                has_picker = True
                break
        except Exception:
            pass
    t.screenshot("02_日期弹框")
    if has_picker:
        t.record("PASS", "系统日期弹框（DatePicker）已调起")
    else:
        tx = t.screen_text()
        has_like = any(x in " ".join(tx) for x in ("年", "月", "今天"))
        t.record("PASS" if has_like else "FAIL",
                 f"未识别到 DatePicker 类，屏幕文本含年月字样={has_like}，屏幕={tx[:8]}")
    # 关闭弹框：优先「取消」，避免改动日期
    if not t.tap_text("取消", wait=3, silent=True):
        t.back()   # 无取消键则 BACK 关弹框
        time.sleep(1)
    time.sleep(0.8)
    now_date = _text(t, TV_SEM_START)
    t.record("INFO" if now_date == orig_date else "WARN",
             f"关闭弹框后学期开始时间: {now_date!r}（{'未变化' if now_date == orig_date else '发生变化！'}）")

    # ── Step3: 当前周数展示（默认按当前日期计算）──────────────────────
    t.step("Step3 查看当前周数（默认按当前日期计算，点击可修改）")
    cur = _text(t, TV_CUR_WEEK)
    ok_cur = bool(re.fullmatch(r"第\d+周", cur))
    t.record("PASS" if ok_cur else "FAIL", f"当前周数展示: {cur!r}（{'格式正确' if ok_cur else '不符合 第N周 格式'}）")
    # 规格称「点击可修改」：点击应弹出当前周数选择控件（含 取消/确定）
    t.tap_rid(LAYOUT_CUR_WEEK, silent=True)
    time.sleep(1)
    t.screenshot("03_当前周数点击")
    txt3 = " ".join(t.screen_text())
    opened = ("当前周数" in txt3) and any(x in txt3 for x in ("取消", "确定"))
    if opened:
        t.record("PASS", "当前周数可点击修改（点击弹出选择控件，含 取消/确定）")
    else:
        t.record("WARN", f"点击当前周数后未检测到选择控件，屏幕={txt3[:10]}")
    # 关键：关闭选择控件，否则会盖住编辑页导致后续步骤读不到开关/总周数（177 首跑级联 WARN/FAIL 根因）
    if not t.tap_text("取消", wait=3, silent=True):
        t.back()
        time.sleep(1)
    time.sleep(0.8)

    # ── Step4: 点击学期总周数 → 弹框选择 ────────────────────────────────
    t.step("Step4 点击学期总周数并验证可选择")
    total = _text(t, TV_TOTAL)
    m_total = re.search(r"\d+", total)
    if not m_total:
        t.record("WARN", "总周数未取到数值，跳过选择验证")
    elif not t.tap_rid(LAYOUT_TOTAL, silent=True):
        t.record("FAIL", f"未找到总周数入口 {LAYOUT_TOTAL}")
        return t.finish()
    else:
        time.sleep(1.5)
        t.screenshot("04_总周数弹框")
        # 弹框是滚轮选择器（知识卡）：当前值居中高亮，上下各露出 ±1；
        # 数字为 Canvas 绘制 dump 读不到 → OCR 定位数字坐标点选
        cur_n = int(m_total.group())
        target = cur_n + 1                     # 滚轮上必然露出当前值+1
        hits = [(x, y) for x, y, c, tx in t.ocr()
                if tx.strip() == str(target)]
        if not hits:
            t.record("WARN", f"滚轮上未 OCR 到 {target}（附截图人工核对）")
            t.tap_text("取消", wait=2, silent=True) or t.back()
            time.sleep(0.8)
        else:
            t.record("INFO", f"滚轮候选含 {target}，OCR 定位 {hits[0]} 点选")
            t.tap_xy(*hits[0])
            time.sleep(1)
            t.tap_text("确定", wait=3, silent=True)
            time.sleep(1)
            new_total = _text(t, TV_TOTAL)
            changed = str(target) in new_total
            if not changed:
                # 兜底：部分滚轮不支持点选，改用上滑一格再确定
                t.record("INFO", f"点选未生效（展示 {new_total!r}），改用滑动滚轮重试")
                if t.tap_rid(LAYOUT_TOTAL, silent=True):
                    time.sleep(1.5)
                    b = t.el_bounds(rid=LAYOUT_TOTAL)
                    cx = (b[0] + b[2]) // 2 if b else 540
                    cy = (b[1] + b[3]) // 2 if b else 700
                    t.adb_shell("input", "swipe", str(cx), str(cy + 90),
                                str(cx), str(cy - 60), "300")
                    time.sleep(0.8)
                    t.tap_text("确定", wait=3, silent=True)
                    time.sleep(1)
                    new_total = _text(t, TV_TOTAL)
                    changed = str(target) in new_total
            t.record("PASS" if changed else "FAIL",
                     f"总周数选择 {target}: {total!r} → {new_total!r}（{'选择生效' if changed else '选择未生效'}）")
            # 还原：重开滚轮选回原值
            if changed and str(cur_n) not in new_total and t.tap_rid(LAYOUT_TOTAL, silent=True):
                time.sleep(1.5)
                hits0 = [(x, y) for x, y, c, tx in t.ocr()
                         if tx.strip() == str(cur_n)]
                if hits0:
                    t.tap_xy(*hits0[0])
                    time.sleep(1)
                t.tap_text("确定", wait=3, silent=True)
                time.sleep(1)
                t.record("INFO", f"还原总周数: {_text(t, TV_TOTAL)!r}")

    # ── Step5: 周末有课开关（默认关闭 + 可开启）────────────────────────
    t.step("Step5 周末有课开关：默认关闭、可点击开启")
    wk = _checked(t, SW_WEEKEND)
    t.record("PASS" if wk is False else "FAIL" if wk is True else "WARN",
             f"周末有课开关默认状态: {'关闭' if wk is False else '开启' if wk is True else '未知'}（规格预期：关闭）")
    _toggle_switch(t, SW_WEEKEND, "周末有课开关")

    # ── Step6: 显示非本周课程开关（默认关闭 + 可开启）──────────────────
    t.step("Step6 显示非本周课程开关：默认关闭、可点击开启")
    nc = _checked(t, SW_NONCUR)
    t.record("PASS" if nc is False else "FAIL" if nc is True else "WARN",
             f"显示非本周课程开关默认状态: {'关闭' if nc is False else '开启' if nc is True else '未知'}（规格预期：关闭）")
    _toggle_switch(t, SW_NONCUR, "显示非本周课程开关")

    # ── Step7: 清空必填项 → 完成按钮置灰/恢复 ──────────────────────────
    t.step("Step7 清空课程表名称后查看完成按钮状态")
    t.clear_text(NAME, silent=True)
    time.sleep(0.8)
    _hide_ime(t)
    name_now = _text(t, NAME)
    t.record("INFO", f"名称已清空（当前值 {name_now!r}）")
    # 学期开始时间为日期选择行：默认已有值，且点开日期弹框后**无法选择空值**
    # （必须选中某个日期才能确认），因此该字段客观上无法置空 —— 记 WARN：
    # 用例文本要求「两项都不能为空」，但其中一项连置空手段都不存在。
    t.record("WARN",
             "学期开始时间无法置空：默认已有值，点击后只能选择具体日期、UI 无清空/取消为空的入口，"
             "故该必填项不存在『为空』状态可供验证")

    # 置灰判定改用 UI 树的 enabled 属性（视觉模型对「置灰/可点击」判定不稳定：
    # 实测同一按钮在名称为空/已填两种状态下都回答「置灰」）。
    # 失败点落在「课程表名称为空时完成按钮仍可点击」—— 与预期『两项都有内容才可
    # 点击』不符，记 FAIL（这是用例真正要验的校验缺失）。
    fin_empty = t.read_rid(FINISH)
    en_empty = (fin_empty or {}).get("enabled", "") if isinstance(fin_empty, dict) else ""
    clk_empty = (fin_empty or {}).get("clickable", "") if isinstance(fin_empty, dict) else ""
    if en_empty == "false":
        t.record("PASS", f"课程表名称为空时完成按钮置灰（enabled 属性）: enabled={en_empty}")
        grayed_ok = True
    else:
        t.record("FAIL",
                 f"课程表名称为空时完成按钮未置灰（需求要求置灰）: "
                 f"enabled={en_empty or '未知'}, clickable={clk_empty or '未知'}"
                 " —— 与需求『名称/时间都填了才可点完成』的**表现形式**不符；"
                 "是否存在点击级校验请看下一条证据")
        grayed_ok = False
    if not grayed_ok:
        # 与规格『置灰』不符 → 补行为证据：空名点「完成」是否被校验拦截
        fin = t.read_rid(FINISH)
        enabled = (fin or {}).get("enabled", "") if isinstance(fin, dict) else ""
        t.record("INFO", f"完成按钮 dump 状态: enabled={enabled or '未知'}")
        # 截图先行：tap(observe=False) → 立即 capture_toast，抓拦截提示
        if t.tap_rid(FINISH, observe=False, silent=True):
            texts, _shot = t.capture_toast()
            hit = [s for s in texts if s.strip()]
            # settle 属"合理"：等的是**页面有没有离开**（否定条件）——"某个东西
            # 不出现"没法用 wait_* 表达（wait_rid 会立刻命中已存在的 NAME）。
            time.sleep(1)
            on_page = bool(t.el_bounds(rid=NAME)) and "新建课程表" in " ".join(t.screen_text())
            t.record("INFO" if on_page else "FAIL",
                     f"空名点完成: {'停留编辑页（点击被校验拦截）' if on_page else '页面离开编辑页（未被拦截！）'}"
                     f"，toast={hit or '无'}")
            if not on_page:
                return t.finish()
    # 恢复名称 → 完成按钮应恢复可点击
    t.input_text(NAME, "测试课表177", silent=True)
    time.sleep(0.8)
    _hide_ime(t)
    t.screenshot("07_名称恢复")
    fin_full = t.read_rid(FINISH)
    en_full = (fin_full or {}).get("enabled", "") if isinstance(fin_full, dict) else ""
    clk_full = (fin_full or {}).get("clickable", "") if isinstance(fin_full, dict) else ""
    t.record("PASS" if en_full == "true" else "FAIL",
             f"名称恢复后完成按钮可点击（enabled 属性）: enabled={en_full or '未知'}, "
             f"clickable={clk_full or '未知'}")

    # 不点「完成」（会创建课程表），BACK 离开编辑页收尾
    t.back()
    return t.finish()


if __name__ == "__main__":
    run()
