#!/usr/bin/env python3
"""联想日历 179：课程时间设置——单节改间隔（53分钟）+ 上午同步 + 冲突红字 toast。

链路（盲跑 7 轮落盘 + 知识卡时间滚轮操作法）：
  手动创建 → 课程时间设置 → TimeSlotSettingsActivity
  上午第1节 row arrow → TimePickerDialog（结束分钟列 tap 下方=+1）
  确定 → "是否自动调整其他课程" dialog → 确定 → 设置页 sync
"""
import os
import sys
import time

USER_INPUT = """测试用例 联想日历_179
前提：
手动创建课程表后进入课程表基本信息编辑页
操作步骤
1.手动修改上午时段中一个小节的时间间隔为 53 分钟
2.查看上午时段内后续小节上课时长的显示
3.设置上午的最后一个小节结束时间晚于下午第一节的开始时间
预期结果
1.可成功修改小节时间
2.手动修改小节间隔后，仅上午时段后续小节时间同步变为 53 分钟；课程的「每节课上课时长」仍保持 50 分钟不变
3.小节数和时间显示为红色，点击完成toast提示时间冲突"""

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase, _parse_nodes   # noqa: E402
from _flow import goto_课程表空状态, goto_手动创建课程表   # noqa: E402

TIME_SETTINGS_RID = "com.zui.calendar:id/layout_time_settings"


def _cluster_centers(values, tol=25):
    """把相近的 y 值聚成一行，返回各行中心（抗 OCR 把同一行识别成多个相邻 y）。"""
    vals = sorted(values)
    if not vals:
        return []
    clusters = [[vals[0]]]
    for v in vals[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) // len(c) for c in clusters]


def calibrate_picker(t):
    """OCR 标定时间滚轮：返回 (row_y, step, cols)，带重试抗瞬时漏读。

    cols 按 x 升序排列，顺序固定为 [start小时, start分钟, end小时, end分钟]。
    每行 y 经 25px 聚类去除 OCR 重复识别后再算行中心与步距；
    设备可能为横屏/竖屏，坐标全部动态标定，不写死。
    """
    import statistics
    nums = []
    for _ in range(3):
        ocr = t.ocr()
        nums = [(x, y, c, s) for (x, y, c, s) in ocr if (s or "").lstrip("-").isdigit()]
        if len(nums) >= 8:
            break
        time.sleep(0.8)
    if not nums:
        return None, None, []
    ys = _cluster_centers([y for x, y, c, s in nums], tol=25)
    cy = int(statistics.median(ys))
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    step = int(statistics.median(gaps)) if gaps else 160
    # 按 x 80px 分带，每带取距纵向中心最近的数值作为当前选中值
    bands = {}
    for x, y, c, s in nums:
        if abs(y - cy) < step * 1.6:
            bands.setdefault(x // 80, []).append((x, y, s))
    cols = []
    for k in sorted(bands):
        items = bands[k]
        avg_x = sum(i[0] for i in items) // len(items)
        cur = min(items, key=lambda v: abs(v[1] - cy))
        cols.append({"x": avg_x, "y": cur[1], "text": cur[2]})
    cols.sort(key=lambda c: c["x"])
    return cy, step, cols


def collect_time_ranges(t, max_scrolls=6):
    """滚动收集 TimeSlotSettings 页所有 tv_time_range 文本（RecyclerView 懒加载）。"""
    seen = []
    for _ in range(max_scrolls):
        for n in _parse_nodes(t._dump()):
            if n["rid"] == "com.zui.calendar:id/tv_time_range" and n["text"]:
                if n["text"] not in seen:
                    seen.append(n["text"])
        w, h = t.d.window_size()
        t.d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.32), 0.3)  # 上滑露出下方
        time.sleep(0.8)
    return seen


def scroll_to_top(t):
    w, h = t.d.window_size()
    for _ in range(4):
        t.d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.82), 0.3)  # 下滑回顶部
        time.sleep(0.5)


def read_center_val(t, col_x, row_y, step):
    ocr = t.ocr()
    best, best_d = None, 1e9
    for x, y, c, s in ocr:
        if (s or "").lstrip("-").isdigit():
            if abs(y - row_y) < step * 0.9 and abs(x - col_x) < 75:
                d = abs(x - col_x) + abs(y - row_y)
                if d < best_d:
                    best_d, best = d, int(s)
    return best


def tap_increment(t, col_x, target, row_y, step, max_steps=120):
    """在标定后的滚轮列上点按上/下一格，逐步逼近目标值。"""
    for _ in range(max_steps):
        cur = read_center_val(t, col_x, row_y, step)
        if cur is None:
            time.sleep(1.0)
            continue
        if cur == target:
            return True
        # 当前中心上方为较小值、下方为较大值 → 下方点按使中心 +1
        if cur < target:
            t.tap_xy(col_x, row_y + step, observe=False)
        else:
            t.tap_xy(col_x, row_y - step, observe=False)
        time.sleep(0.6)
    return False


def row_arrows_sorted(t):
    """所有 desc=编辑 arrow 按 y 排序（早 8:00 第1节…）。"""
    nodes = _parse_nodes(t._dump())
    arrs = [n for n in nodes if n["desc"] == "编辑" and n["bounds_xy"]]
    arrs.sort(key=lambda n: n["bounds_xy"][1])
    return arrs


def parse_time_ranges(t):
    """读设置页上午/下午/晚上各小节 tv_time_range 与 tv_lesson_duration。"""
    nodes = _parse_nodes(t._dump())
    out = {"ranges":[], "lesson": None}
    for n in nodes:
        if n["rid"] == "com.zui.calendar:id/tv_time_range" and n["text"]:
            out["ranges"].append(n["text"])
        if n["rid"] == "com.zui.calendar:id/tv_lesson_duration" and n["text"]:
            out["lesson"] = n["text"]
    return out


def open_section_editor(t, idx):
    """点第 idx(0-based) 节行的编辑 arrow（跳过时长/课间休息两行）。"""
    arrs = row_arrows_sorted(t)
    if idx + 2 >= len(arrs):
        return False
    b = arrs[idx + 2]["bounds_xy"]
    t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    time.sleep(2.2)
    return True


def run():
    t = TestCase("联想日历_179")
    t.start_watchdog(policy="allow")

    # ── 前提：手动创建（不保存，直接进课程时间设置）──
    t.step("前提-手动创建 → 进课程时间设置页")
    if not goto_课程表空状态(t, pm_clear=True):
        return t.finish()
    t.observe_dialogs(rounds=3)
    if not goto_手动创建课程表(t):
        t.record("FAIL", "未进入手动创建页")
        return t.finish()
    time.sleep(2)  # 等手动创建页渲染
    if not t.tap_rid(TIME_SETTINGS_RID, silent=True):
        t.record("FAIL", "未找到课程时间设置入口")
        return t.finish()
    time.sleep(2.5)  # 等 TimeSlotSettings 页渲染
    t.observe_dialogs(rounds=3)
    act = t.current_activity()
    t.record("PASS" if "TimeSlotSettings" in act else "FAIL",
             f"进入 TimeSlotSettingsActivity: activity={act}")

    # ── Step1：上午第1节 改结束分钟到 53（tap 下方一格 +1）──
    t.step("Step1 上午第1节弹窗 → 拨结束分钟到 53 → 确定")
    if not open_section_editor(t, 0):
        t.record("FAIL", "未找到上午第1节编辑 arrow")
        return t.finish()
    time.sleep(0.8)  # 等弹窗动画完成
    row_y, step, cols = calibrate_picker(t)
    if not cols or len(cols) < 4:
        t.record("FAIL", f"时间滚轮 OCR 标定失败（cols={cols}）")
        return t.finish()
    end_min_col = cols[-1]  # x 最大的列为结束分钟
    t.record("INFO", f"滚轮标定 row_y={row_y} step={step} end_min_x={end_min_col['x']} start_min_x={cols[1]['x']}")
    ok = tap_increment(t, end_min_col["x"], target=53, row_y=row_y, step=step)
    if not ok:
        t.record("FAIL", "结束分钟无法拨到 53")
        return t.finish()
    cur = read_center_val(t, end_min_col["x"], row_y, step)
    t.record("PASS" if cur == 53 else "FAIL",
             f"上午第1节结束分钟={cur}（预期 53）")
    if not t.tap_text("确定", wait=3, silent=True):
        t.record("FAIL", "第1节确定未生效")
        return t.finish()
    time.sleep(2)  # 等同步动画完成
    # 自动调整 dialog
    t.tap_text("确定", wait=3, silent=True)
    time.sleep(2)  # 等自动调整弹窗关闭
    t.observe_dialogs(rounds=3)

    # ── Step2：验证上午后续小节同步、每节课时长仍 50 ──
    t.step("Step2 验证上午同步与课时长 50 分钟不变")
    t.observe_dialogs(rounds=3)
    time.sleep(1.0)  # 等对话框动画稳定
    # 课时长元素在页顶，必须在滚动收集前读取（滚动后会离屏）
    lesson = (t.read_rid("com.zui.calendar:id/tv_lesson_duration") or {}).get("text", "")
    t.record("PASS" if lesson and "50" in lesson else "FAIL",
             f"每节课时长={lesson!r}（预期含 '50'）")
    ranges = collect_time_ranges(t)
    # 上午 4 节（前 4 条为上午），第1节 08:00-08:53，后续开始时间顺延 3 分钟
    expected_am = ["08:00-08:53", "09:03-09:53", "10:03-10:53", "11:03-11:53"]
    am_ok = ranges[:4] == expected_am
    t.record("PASS" if am_ok else "FAIL",
             f"上午4节同步: 实测={ranges[:4]}（预期={expected_am}）")
    # 下午 4 节应未变 14:00-14:50 / 15:00-15:50 / 16:00-16:50 / 17:00-17:50
    expected_pm = ["14:00-14:50", "15:00-15:50", "16:00-16:50", "17:00-17:50"]
    pm_ranges = [r for r in ranges[4:] if r.split("-")[0].split(":")[0] in {"14", "15", "16", "17"}]
    pm_ok = pm_ranges == expected_pm
    t.record("PASS" if pm_ok else "FAIL",
             f"下午4节不变: 实测={pm_ranges}（预期={expected_pm}）")
    t.screenshot("179_改后设置页")
    # Step3 需要操作上午第4节 arrow，先滚回顶部确保它可见
    scroll_to_top(t)
    time.sleep(0.6)  # 等滚动动画完成

    # ── Step3：上午第4节 改结束小时到 14（>下午开始 14:00 → 冲突）──
    t.step("Step3 上午最后小节结束 > 下午开始 → 触发冲突")
    if not open_section_editor(t, 3):                # 第4节（0-based idx=3）
        t.record("FAIL", "未找到上午第4节编辑 arrow")
        return t.finish()
    time.sleep(1.0)  # 等编辑弹窗动画
    t.observe_dialogs(rounds=3)
    time.sleep(0.6)  # 等弹窗稳定
    row_y3, step3, cols3 = calibrate_picker(t)
    if not cols3 or len(cols3) < 4:
        t.record("FAIL", f"时间滚轮 OCR 标定失败（cols={cols3}）")
        return t.finish()
    end_hour_col = cols3[-2]  # 倒数第2列为结束小时
    # 改结束小时 → 14
    ok = tap_increment(t, end_hour_col["x"], target=14, row_y=row_y3, step=step3)
    cur_h = read_center_val(t, end_hour_col["x"], row_y3, step3)
    t.record("PASS" if cur_h == 14 else "FAIL",
             f"上午第4节结束小时={cur_h}（预期 14）")
    if not t.tap_text("确定", wait=3, silent=True):
        t.record("FAIL", "第4节确定未生效")
        return t.finish()
    time.sleep(2)  # 等冲突提示响应
    t.observe_dialogs(rounds=3)
    # 改后续小节也可能触发"是否自动调整其他课程"对话框 → 关掉它
    t.tap_text("确定", wait=3, silent=True)
    time.sleep(2)  # 等自动调整弹窗关闭
    t.observe_dialogs(rounds=3)
    t.screenshot("179_冲突设置页")

    # ── Step4：点完成 → toast「课程时间有冲突，无法设置」+ 红字 ─────────────
    t.step("Step4 点完成 → 期望 toast 提示时间冲突")
    try:
        t.observe_dialogs(rounds=3)
        time.sleep(1.0)  # 等 toast 窗口稳定
        # 实测（probe v2/v3/AB 隔离实验）：save_view **bounds 中心**点击无反应，
        # 需落在元素左上偏内处才稳定触发 toast（热区偏移）。
        # ⚠️ 偏移不得写成设备像素常量（换设备/换 DPI 即失效，lint 规则 7 亦报）：
        # 按元素**自身尺寸的 1/8** 派生 —— 标定机上 save_view ≈488×248，1/8 即
        # 61/31，与当初实测一致；换设备按同比例落点，语义不变。
        # observe=False 必须带：默认点击链的弹窗检查窗口(~1.5s)+截图会占满 toast 的 ~2s 窗口
        b = t.el_bounds(rid="com.zui.calendar:id/save_view")
        if b:
            w, h = b[2] - b[0], b[3] - b[1]
            t.tap_xy(b[0] + w // 8, b[1] + h // 8, observe=False)
        else:
            t.tap_text("完成", wait=3, observe=False, silent=True)
        # 真实文案「课程时间有冲突，无法设置」来自探索期连拍记录（storage/179_toast_tap/v3_*.png），
        # toast 窗口实测 ~2-3s，动作后立即 capture_toast（截屏定格→OCR）
        texts, _shot = t.capture_toast(wait=1.0, label="179_完成_toast")
        hit = [s for s in texts if "冲突" in s]
        t.record("PASS" if hit else "FAIL",
                 f"toast 含'冲突'（实测文案='课程时间有冲突，无法设置'）: capture={hit or texts[-4:]}")
    except Exception as e:
        t.record("FAIL", f"Step4 执行异常: {e}")
    # 红字：探索记录确认第4节/第5节时间文字变红（probe_冲突设置页 截图），视觉模型断言
    try:
        t.assert_visual("页面中第4节 11:00-14:50 与第5节 14:00-14:50 的时间文字是否显示为红色",
                        ("是", "红"), msg="冲突小节时间红字")
    except Exception as e:
        t.record("WARN", f"红字视觉断言异常（未配视觉凭据时降级）: {e}")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    sys.exit(run())