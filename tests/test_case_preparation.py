"""环境准备的真实设备测试；未指定设备或设备不在线时明确跳过。"""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parent.parent


class TestPreparationDevice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serial = os.environ.get("DSH_TEST_DEVICE", "").strip()
        cls.package = os.environ.get("DSH_TEST_PACKAGE", "").strip()
        if not cls.serial or not cls.package:
            raise unittest.SkipTest("真机测试需设置 DSH_TEST_DEVICE 和 DSH_TEST_PACKAGE；不使用模拟设备")
        if not shutil.which("adb"):
            raise unittest.SkipTest("adb 不在 PATH，无法操作真实设备")
        try:
            state = subprocess.run(["adb", "-s", cls.serial, "get-state"],
                                   capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise unittest.SkipTest(f"设备 {cls.serial} 连接超时")
        if state.returncode or state.stdout.strip() != b"device":
            raise unittest.SkipTest(f"指定设备 {cls.serial} 未连接或未授权")

    def test_preparation_on_real_device(self):
        name = datetime.now().strftime("%Y%m%d_%H%M%S") + "_unittest_" + uuid.uuid4().hex[:8]
        output = ROOT / "storage/preparation-device" / name
        # 启动真实验收入口；其内部直接调用 TestCase、ADB 和 uiautomator2。
        result = subprocess.run([
            sys.executable, "-u", str(ROOT / "scripts/check_preparation_device.py"),
            "--device", self.serial, "--package", self.package, "--output", str(output),
        ], cwd=ROOT, timeout=900)
        self.assertEqual(result.returncode, 0, f"真机验收失败，详情见 {output}")
        report = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.assertIn(report["status"], ("PASS", "WARN"))
        self.assertTrue(report["checks"], "验收入口未执行任何检查")
        self.assertTrue(all(check["status"] == "PASS" for check in report["checks"]), report)
        print(f"真机验收结论：{report['status']}；结果：{output / 'result.json'}")
        if report.get("permission_warning"):
            print("设备存在未生效授权，已记 WARN 并验证后续执行；不能视为全部授权成功。")


if __name__ == "__main__":
    unittest.main()
