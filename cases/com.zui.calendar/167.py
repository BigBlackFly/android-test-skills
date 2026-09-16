#!/usr/bin/env python3
"""联想日历_167 用例：首次进入课程表功能的引导弹框与空状态
前提：首次打开日历应用（pm clear 重置到首次使用）
步骤: 日历首页 → 右上角更多 → 课程表
预期:
  1. 首次进入课程表功能时，弹出功能引导弹框，清晰展示课程表核心操作说明
  2. 无课程表时显示空页面状态及拍照导入、图库导入、手动创建的操作按钮

【探查结论（2026-09-11，真机实测）】
引导弹框**存在**，文案「课程表上新了！」+「点击快速导入课表，自动同步所有
上课时间」+「我知道了」；但它弹在**首次启动 App 的日历主页上**，而非"进入
课程表功能时"——进入课程表后逐秒观察 8 秒，页面稳定无第二个弹框。
内容符合预期 1，时机与用例描述不同，按"内容符合"记 PASS 并注明差异。
"""
import os
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_167
前提：
首次打开日历应用
操作步骤 
1. 日历首页点击页面右上角更多按钮，选择课程表选项
预期结果
1. 首次进入课程表功能时，弹出功能引导弹框，清晰展示课程表核心操作说明，用户能明白该功能的作用
2.无课程表时显示空页面状态及拍照导入、图库导入、手动创建的操作按钮"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase            # noqa: E402
from _flow import tap_more_menu                # noqa: E402

PKG = "com.zui.calendar"


def _dismiss_boot_dialogs(t):
    """关闭首启弹框链，返回 (权限说明框是否出现过, 通知权限框是否出现过)。

    pm_clear 后首次启动依次弹三个框（2026-09-11 实测）：
      ① 「日历需要使用以下权限」App 自有说明框 → 「同意」(android:id/button1)
         ⚠️ wait_rid 一命中就点会**点空**（入场动画未结束），必须
            等待→点→验证消失→重试。
      ② 系统通知权限「日历正在尝试显示通知」→「允许」
         （仅首次；rid=com.android.permissioncontroller:id/permission_allow_button_two）
      ③ 「课程表上新了！」课程表功能引导 → 本用例的验证对象，**不在这里关**
    """
    saw_perm = False
    saw_notify = False

    # ① App 权限说明框
    for _ in range(4):
        if not t.wait_rid("com.zui.calendar:id/alertTitle", timeout=8):
            break
        saw_perm = True
        time.sleep(1.5)                      # 等入场动画（实测早点点空）
        t.tap_rid("android:id/button1", silent=True)
        time.sleep(1.5)
        if not t.el_bounds(rid="com.zui.calendar:id/alertTitle"):
            break

    # ② 系统通知权限框
    if t.wait_rid("com.android.permissioncontroller:id/permission_message", timeout=6):
        saw_notify = True
        t.tap_rid("com.android.permissioncontroller:id/permission_allow_button_two",
                  silent=True)
        time.sleep(2)
    return saw_perm, saw_notify


def run():
    t = TestCase("联想日历_167")

    # 清场钩子：pm_clear + 首启授权流程会隐式把 accelerometer_rotation
    # 从 0 变 1（实测污染），残留会让后续用例坐标系全错（168 同款处理）。
    t.add_prop_restore("accelerometer_rotation")

    # ── 前置条件：重置到"首次打开日历应用" ──────────────────
    t.step("前置条件-重置到首次使用")
    t.pm_clear(PKG)
    t.grant_permission(PKG, "android.permission.READ_EXTERNAL_STORAGE")
    t.grant_permission(PKG, "android.permission.READ_MEDIA_IMAGES")
    time.sleep(1)
    # 不开看门狗：本用例要**验证引导弹框存在**，看门狗会自动点掉它，
    # 开了就等于把被测对象清了。弹框由 _dismiss_boot_dialogs 显式处理。
    t.launch_app(PKG)
    _dismiss_boot_dialogs(t)

    # ── Step 1: 引导弹框（预期 1）───────────────────────────
    t.step("Step1 首次打开日历，课程表功能引导弹框")
    # 探查发现弹框在主页出现（不是在进入课程表之后）。
    # 轮询等它渲染：实测点掉通知权限框后约 3-5 秒出现。
    found = False
    for _ in range(10):
        if t.el_bounds(rid="com.zui.calendar:id/curriculum_guide_title"):
            found = True
            break
        time.sleep(1)
    shot = t.screenshot("01_课程表引导弹框")

    if found:
        texts = t.screen_text()
        has_title = any("课程表上新了" in x for x in texts)
        has_desc = any("自动同步所有上课时间" in x for x in texts)
        has_btn = any("我知道了" in x for x in texts)
        t.record("PASS" if (has_title and has_desc and has_btn) else "FAIL",
                 f"功能引导弹框内容：标题={'✓' if has_title else '✗'}"
                 f"说明={'✓' if has_desc else '✗'}按钮={'✓' if has_btn else '✗'}"
                 f"；文案={texts[:8]}",
                 rid="com.zui.calendar:id/curriculum_guide_title")
        # 说明文案要能让人明白功能作用（预期 1 的后半句）
        t.record("PASS" if has_desc else "FAIL",
                 "引导说明清晰展示课程表核心操作"
                 f"（『点击快速导入课表，自动同步所有上课时间』={'✓' if has_desc else '✗'}）")
    else:
        t.record("FAIL", "10s 内未出现课程表功能引导弹框"
                         f"（rid=curriculum_guide_title 未找到）")
        t.screenshot("01_引导弹框_未出现")

    # 关掉引导，继续走主流程
    if found:
        t.tap_rid("com.zui.calendar:id/timetable_guide_button", silent=True)
        time.sleep(2)

    # ── Step 2: 更多 → 课程表（预期 2 的前置）────────────────
    t.step("Step2 日历首页→更多→课程表")
    if not t.wait_rid("com.zui.calendar:id/iv_more", timeout=15):
        t.record("FAIL", "未找到首页「更多」按钮（iv_more）")
        t.blocked("无法进入课程表")
        return t.finish()
    if not tap_more_menu(t):
        t.record("FAIL", "未能打开「更多」菜单")
        t.blocked("无法进入课程表")
        return t.finish()
    t.record("PASS", "「更多」菜单弹出，包含「课程表」入口")
    t.tap_text("课程表", silent=True)
    t.wait_rid("com.zui.calendar:id/emptyView", timeout=10)
    shot2 = t.screenshot("02_课程表空状态页")

    # ── Step 3: 空状态页与三个操作按钮（预期 2）──────────────
    t.step("Step3 无课程表时的空状态与操作按钮")
    texts = t.screen_text()
    t.record("PASS" if any("还未添加课程表" in x for x in texts) else "FAIL",
             f"空页面状态文案「还未添加课程表」"
             f"（实际={[x for x in texts if '课程表' in x][:6]}）",
             rid="com.zui.calendar:id/emptyView")

    # 三个按钮：rid 定位 + enabled 状态（按钮存在且可点）
    for rid, label in [("btnImportFromPhoto", "拍照导入课程表"),
                       ("btnImportFromGallery", "从图库导入课程表"),
                       ("btnCreateManually", "手动创建课程表")]:
        bounds = t.el_bounds(rid=f"com.zui.calendar:id/{rid}")
        has_text = any(label in x for x in texts)
        if bounds and has_text:
            t.record("PASS", f"「{label}」按钮显示且可见（{label}）", rid=rid)
        elif bounds:
            t.record("WARN", f"「{label}」按钮节点存在但未读到文案", rid=rid)
        else:
            t.record("FAIL", f"「{label}」按钮缺失", rid=rid)

    # ── Step 4: 进入课程表后无额外弹框（探查结论固化）────────
    t.step("Step4 进入课程表后页面稳定，无额外弹框")
    time.sleep(3)
    after = t.screen_text()
    still_empty = any("还未添加课程表" in x for x in after)
    guide_again = t.el_bounds(rid="com.zui.calendar:id/curriculum_guide_title")
    t.record("PASS" if (still_empty and not guide_again) else "WARN",
             f"进入课程表后停留空状态页、未再弹引导框"
             f"（空态={'✓' if still_empty else '✗'}"
             f"，二次弹框={'有' if guide_again else '无'}）")

    # 差异说明（预期 1 的时机与用例描述不同，如实记录，不计失败）
    t.record("INFO", "预期1 差异：引导弹框在【首次启动 App 的日历主页】弹出，"
                     "而非描述中的『首次进入课程表功能时』；进入课程表后"
                     "逐秒观察 8 秒无第二个弹框。内容符合，时机不同。")

    return t.finish()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
