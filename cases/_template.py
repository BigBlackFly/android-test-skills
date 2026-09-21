#!/usr/bin/env python3
"""用例脚本模板：复制本文件为 cases/<包名>/<用例编号>.py，按下面结构填写。
用例按被测 App 包名分目录（如 cases/com.zui.calendar/175.py），
与该 App 的知识卡 knowledge/<包名>.md、探查缓存 storage/probes/<包名>/ 同键。
不会写？直接把用例发给我（AI），我按这个模板帮你转成脚本。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)  # 同目录 _flow.py：本 App 的可复用流程（≥2 用例共享才提取进去）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))
from test_framework import TestCase

PKG = None  # Agent 根据用例查阅 app_packages.py 后填写包名；无法确定时保留 None

# 用户原始输入（口述用例全文）。run_case.py 启动时提取入库，
# 没有 USER_INPUT 时追溯链断一环（只知道跑了什么脚本，不知道用户要什么）。
USER_INPUT = """（把用户口述的用例原文完整粘在这里：前提 / 操作步骤 / 预期结果）"""


def run():
    t = TestCase("用例名称", target_package=PKG)  # 初始化依次重置文件、接收包名、清数据、授权
    # t.clear_sdcard_files()  # 仅在用例明确要求测试开始前设备中不能有任何图片或视频文件时调用。

    # ── 前置条件（可选）────────────────────────────────────────────
    # 环境判断，不满足直接 BLOCKED
    # if not t.has_network():
    #     t.blocked("环境原因：设备无网络")
    #     return t.finish()
    # 冷启动 App（禁止裸拼 adb monkey —— 多设备会串台，一律用 t.launch_app）
    # t.launch_app(PKG)

    # ── 步骤 1 ─────────────────────────────────────────────────────
    t.step("步骤1: 打开XX页面")
    # 元素操作（选一种）。定位优先级：resource-id > content-desc > text > 坐标。
    # App 自有控件有 rid 时【禁止】用 text 定位（文案会随版本/多语言变）；
    # text 只用于系统弹窗（无 rid）或文案本身即被测对象的场景。
    # 所有 tap_*/input_* 返回 bool：True=已执行，False=超时未找到（不抛异常）。
    # t.tap_rid("com.example:id/btn")              # 按 resource-id 点（首选）
    # t.tap_desc("返回")                            # 按 content-desc 点（图标按钮）
    # t.tap_text("确定")                            # 按文字点（仅系统弹窗/文案被测）
    # t.tap_xy(100, 200)                            # 按坐标点（最后手段）
    # t.input_text("com.example:id/edit", "中文输入")
    # 可选步骤点不到时默认记 WARN；有守卫分支（if not ...）时加 silent=True 防双重记录：
    # if not t.tap_rid("com.example:id/btn", silent=True):
    #     t.record("FAIL", "未找到 XX 按钮")
    # 链路关键步骤（点不到必须中止）用 require_*（找不到=FAIL+中止，无竞态）：
    # t.require_tap_rid("com.example:id/btn", wait=8, msg="未找到 XX 按钮")
    # 等界面用条件等待，禁止裸 sleep：
    # t.wait_text("目标文字", timeout=10) / t.wait_rid("com.example:id/x") / t.wait_activity("main")
    # 断言
    # t.assert_text("com.example:id/result", "期望值", "说明")
    # t.assert_switch("com.example:id/switch", "true", "开关打开")
    t.screenshot("01_步骤1证据")           # 截图留证

    # ── 步骤 2 ─────────────────────────────────────────────────────
    t.step("步骤2: ...")
    # t.record("PASS", "说明")   /   t.record("FAIL", "说明")
    # t.blocked("无法执行原因")

    # ── 生成报告 ───────────────────────────────────────────────────
    return t.finish()


if __name__ == "__main__":
    run()
