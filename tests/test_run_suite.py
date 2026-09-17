#!/usr/bin/env python3
"""run_suite + run_case._parse_args 纯逻辑单测（不需要 Android 设备）。

覆盖：用例收集与包名过滤、退出码计算、聚合报告生成、设备断连熔断、
run_case --device 参数解析（L231 name 覆盖回归）。

运行（skill 包根目录）：
    python -m unittest tests.test_run_suite -v
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "framework"))

# webui 模块级会解析工作区路径；指到临时目录避免读写真实工作区
os.environ.setdefault("DSH_WORKSPACE_DIR",
                      os.path.join(tempfile.gettempdir(), "dsh-unittest-ws"))

import db         # noqa: E402
import run_case    # noqa: E402
import run_suite   # noqa: E402

try:
    import test_framework as _tf
except ImportError:      # 系统 Python 无 uiautomator2 时跳过相关用例
    _tf = None


# ── run_case._parse_args 回归测试（L231 name 覆盖 bug）──────────────
class TestParseArgs(unittest.TestCase):
    """_parse_args 必须正确解析 --device 参数，不被 sys.argv 覆盖。

    P1a 起返回三元组 (name, device, stop_after)：`--stop-after N` 是 edit-run
    调试循环的入口（只跑前 N 步，退出码 0、不入库）。
    """

    def test_bare_name(self):
        name, device, stop_after = run_case._parse_args(["prog", "172.py"])
        self.assertEqual(name, "172.py")
        self.assertIsNone(device)
        self.assertIsNone(stop_after)

    def test_device_before_name(self):
        name, device, _ = run_case._parse_args(
            ["prog", "--device", "ABC123", "com.zui.calendar/172.py"])
        self.assertEqual(name, "com.zui.calendar/172.py")
        self.assertEqual(device, "ABC123")

    def test_device_after_name(self):
        name, device, _ = run_case._parse_args(
            ["prog", "172.py", "--device", "SERIAL_X"])
        self.assertEqual(name, "172.py")
        self.assertEqual(device, "SERIAL_X")

    def test_no_args_exits(self):
        with self.assertRaises(SystemExit):
            run_case._parse_args(["prog"])

    def test_stop_after_space_form(self):
        name, _, stop_after = run_case._parse_args(
            ["prog", "--stop-after", "5", "172.py"])
        self.assertEqual(name, "172.py")
        self.assertEqual(stop_after, 5)

    def test_stop_after_equals_form(self):
        """`--stop-after=7` 与 `--stop-after 7` 两种写法都接受。"""
        _, _, stop_after = run_case._parse_args(["prog", "172.py",
                                                 "--stop-after=7"])
        self.assertEqual(stop_after, 7)

    def test_stop_after_with_device(self):
        name, device, stop_after = run_case._parse_args(
            ["prog", "--device", "S1", "--stop-after", "3", "172.py"])
        self.assertEqual((name, device, stop_after), ("172.py", "S1", 3))

    def test_stop_after_rejects_non_positive(self):
        """0/负数/非数字 → 报错退出（**不静默忽略**：静默忽略会让人以为
        只跑了前几步、实际跑完整个用例，比报错难查得多）。"""
        for bad in ("0", "-1", "abc", ""):
            with self.assertRaises(SystemExit):
                run_case._parse_args(["prog", "--stop-after", bad, "172.py"])

    def test_stop_after_validation_helper(self):
        self.assertEqual(run_case._parse_stop_after(" 12 "), 12)


# ── P1a：--stop-after 的步数上限行为 ────────────────────────────────
@unittest.skipIf(_tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestStopAfter(unittest.TestCase):
    """到达 N 步 → 不开新步骤、记 INFO 说明原因、抛 PartialRun。

    PartialRun 与 CaseAbort 的后果完全不同（退出码 0 vs 1、不入库 vs 入库），
    所以这里锁两件事：① 步数上限真的拦住了；② 拦下时留下了可追溯的记录。
    """

    def _mk(self, stop_after):
        t = object.__new__(_tf.TestCase)
        t.steps = [{"name": "s1", "results": [], "evidences": []}]
        t._cur_step = t.steps[0]
        t.steps[0]["results"] = []
        t._stop_after = stop_after
        t._db = None
        t._db_case_id = None
        t._finished = False
        return t

    def test_limit_reached_raises_partial_run(self):
        t = self._mk(1)
        with self.assertRaises(_tf.PartialRun):
            t.step("s2")
        self.assertEqual(len(t.steps), 1)          # 没有开新步骤

    def test_limit_records_reason_in_last_step(self):
        t = self._mk(1)
        with self.assertRaises(_tf.PartialRun):
            t.step("s2")
        self.assertTrue(
            any("stop-after" in r["detail"] for r in t.steps[0]["results"]),
            "提前收尾必须留下可追溯的记录（否则报告里看不出为什么只跑了一步）")

    def test_no_limit_does_not_raise(self):
        """未设 --stop-after → 正常开新步骤（不能把正常路径也拦下）。"""
        t = self._mk(None)
        t.step("s2")     # 设备相关动作在 step 内部已被 try/except 兜住
        self.assertEqual(len(t.steps), 2)


# ── _collect_cases：用例收集与包名过滤 ──────────────────────────────
class TestCollectCases(unittest.TestCase):
    def setUp(self):
        self._old_dirs_rc = run_case.CASE_DIRS
        self._old_dirs_rs = run_suite.CASE_DIRS
        self._td = tempfile.TemporaryDirectory()
        root = self._td.name
        # 建两个包的用例目录
        for pkg in ("com.a.x", "com.b.y"):
            os.makedirs(os.path.join(root, "cases", pkg))
        for rel in ("com.a.x/1.py", "com.a.x/2.py",
                    "com.b.y/3.py", "com.a.x/_flow.py"):
            p = os.path.join(root, "cases", *rel.split("/"))
            with open(p, "w", encoding="utf-8") as f:
                f.write("def run():\n    pass\n")
        new_dirs = [os.path.join(root, "cases")]
        run_case.CASE_DIRS = new_dirs
        run_suite.CASE_DIRS = new_dirs   # from-import 创建了独立引用

    def tearDown(self):
        run_case.CASE_DIRS = self._old_dirs_rc
        run_suite.CASE_DIRS = self._old_dirs_rs
        self._td.cleanup()

    def test_all_cases(self):
        cases = run_suite._collect_cases(package=None)
        names = [rel.replace("\\", "/") for _, rel in cases]
        # _flow.py 被排除（_ 开头），应只有 3 个用例
        self.assertEqual(len(cases), 3)
        self.assertIn("com.a.x/1.py", names)
        self.assertIn("com.b.y/3.py", names)

    def test_package_filter(self):
        cases = run_suite._collect_cases(package="com.a.x")
        self.assertEqual(len(cases), 2)
        for _, rel in cases:
            self.assertTrue(rel.replace("\\", "/").startswith("com.a.x/"))

    def test_package_filter_no_match(self):
        cases = run_suite._collect_cases(package="com.nonexistent")
        self.assertEqual(len(cases), 0)


# ── _suite_exit_code：退出码计算 ─────────────────────────────────────
class TestSuiteExitCode(unittest.TestCase):
    def test_all_pass(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 5, "warn": 0, "fail": 0, "blocked": 0, "error": 0}), 0)

    def test_any_fail(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 3, "warn": 0, "fail": 1, "blocked": 0, "error": 0}), 1)

    def test_no_fail_with_error(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 3, "warn": 0, "fail": 0, "blocked": 0, "error": 2}), 3)

    def test_only_blocked(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 0, "warn": 0, "fail": 0, "blocked": 3, "error": 0}), 2)

    def test_fail_takes_priority_over_error(self):
        """FAIL 存在时退出码 = 1，即使也有 ERROR。"""
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 0, "warn": 0, "fail": 1, "blocked": 0, "error": 1}), 1)


# ── _generate_report：聚合报告生成 ──────────────────────────────────
class TestGenerateReport(unittest.TestCase):
    def test_report_content(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.time() - 60
            results = [
                ("com.a.x/1.py", "PASS", 12.3, 0),
                ("com.a.x/2.py", "FAIL", 8.1, 1),
                ("com.b.y/3.py", "ERROR", 3.0, 3),
            ]
            counts = {"pass": 1, "warn": 0, "fail": 1, "blocked": 0,
                      "error": 1, "skipped": 0}
            path = run_suite._generate_report(
                results, counts, started, "package=com.a.x", td)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # 关键内容断言
            self.assertIn("套件报告", content)
            self.assertIn("com.a.x/1.py", content)
            self.assertIn("FAIL", content)
            self.assertIn("ERROR", content)
            self.assertIn("package=com.a.x", content)

    def test_report_with_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            results = [("com.a.x/1.py", "ERROR", 5.0, 3)]
            counts = {"pass": 0, "warn": 0, "fail": 0, "blocked": 0,
                      "error": 1, "skipped": 4}
            path = run_suite._generate_report(
                results, counts, time.time(), "全量", td)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("未执行", content)
            self.assertIn("4", content)


# ── _device_online：设备在线检测（mock adb）──────────────────────────
class TestDeviceOnline(unittest.TestCase):
    def test_serial_found(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nABC123\tdevice\nDEF456\toffline\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertTrue(run_suite._device_online("ABC123"))

    def test_serial_not_found(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nDEF456\tdevice\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online("ABC123"))

    def test_serial_offline(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nABC123\toffline\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online("ABC123"))

    def test_no_serial_any_device(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nXYZ\tdevice\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertTrue(run_suite._device_online(None))

    def test_no_serial_no_device(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online(None))

    def test_adb_failure(self):
        with mock.patch("subprocess.run", side_effect=OSError("adb not found")):
            self.assertFalse(run_suite._device_online())


# ── P3a：--repeat / 多设备 / 一致率 ─────────────────────────────────
class TestRepeatAndDevices(unittest.TestCase):
    """M11（同设备 5 次一致率）与 M12（双设备一致率）的数据源逻辑。

    这两项验收的共性：都是"把稳定变成数字"，所以口径必须**可复现且可判**——
    一致率的分母是"首轮之外的轮次"，与首轮相同即算一致。
    """

    def test_resolve_devices_none_keeps_legacy(self):
        """不传 --device → [None]，交给 TestCase 自己选唯一设备（保持旧行为）。"""
        self.assertEqual(run_suite._resolve_devices(None), [None])

    def test_resolve_devices_explicit_multi(self):
        self.assertEqual(run_suite._resolve_devices("S1,S2"), ["S1", "S2"])
        self.assertEqual(run_suite._resolve_devices("S1"), ["S1"])

    def test_resolve_devices_all_without_device_exits(self):
        """`--device all` 但零台在线 → 报错退出，而不是静默按"自动选择"跑。"""
        with mock.patch.object(run_suite, "list_devices", return_value=[]):
            with self.assertRaises(SystemExit):
                run_suite._resolve_devices("all")

    def test_resolve_devices_all(self):
        with mock.patch.object(run_suite, "list_devices",
                               return_value=["A", "B"]):
            self.assertEqual(run_suite._resolve_devices("all"), ["A", "B"])

    def test_consistency_rate(self):
        """一致率 = 与首轮相同的轮次 / 总轮次（首轮是基线，不入分母）。"""
        recs = {"a.py": ["PASS", "PASS", "PASS", "FAIL", "PASS"],
                "b.py": ["FAIL", "FAIL", "FAIL"]}
        out = dict((r[0], r) for r in run_suite._consistency(recs))
        self.assertAlmostEqual(out["a.py"][3], 0.75)     # 4 轮里 3 轮同首轮
        self.assertAlmostEqual(out["b.py"][3], 1.0)

    def test_consistency_needs_at_least_two_runs(self):
        self.assertEqual(run_suite._consistency({"a.py": ["PASS"]}), [])

    def test_consistency_sorted_worst_first(self):
        """最不稳定的排前面 —— 报告要让人先看到问题。"""
        recs = {"good.py": ["PASS", "PASS"], "bad.py": ["PASS", "FAIL"]}
        out = run_suite._consistency(recs)
        self.assertEqual(out[0][0], "bad.py")


class TestFlakyRetry(unittest.TestCase):
    """P5：FAIL 复跑 → 确定性 FAIL vs 疑似抖动（FLAKY）。

    ⚠️ 这条与 SKILL.md 原则 8 的边界：它是**机制自动复跑**（Agent 无感知），
    不是"Agent 另起一轮独立复核"。测试锁住"复跑通过 ⇒ 结论被摘出阻断统计"。
    """

    def test_flaky_still_blocks(self):
        """**FLAKY 计入阻断**（2026-09-16 人确认："优先保证不能假 PASS"）。

        首跑失败就是失败。复跑成功的价值是**归因**（确定性 vs 环境抖动），
        不是豁免 —— 把 flaky 当通过正是假 PASS 最典型的形态：报告全绿、缺陷
        留在原地，还比普通 FAIL 更难察觉（看起来像"已处理过"）。
        """
        c = {"pass": 5, "warn": 0, "fail": 0, "flaky": 3, "blocked": 0, "error": 0}
        self.assertEqual(run_suite._suite_exit_code(c), 1)

    def test_deterministic_fail_still_blocks(self):
        c = {"pass": 5, "warn": 0, "fail": 1, "flaky": 3, "blocked": 0, "error": 0}
        self.assertEqual(run_suite._suite_exit_code(c), 1)

    def test_fail_family_beats_error(self):
        """FAIL/FLAKY 优先于 ERROR（与单用例"任一 FAIL→1"口径同构）。"""
        c = {"pass": 0, "warn": 0, "fail": 0, "flaky": 1, "blocked": 0, "error": 1}
        self.assertEqual(run_suite._suite_exit_code(c), 1)
        c2 = {"pass": 0, "warn": 0, "fail": 0, "flaky": 0, "blocked": 0, "error": 1}
        self.assertEqual(run_suite._suite_exit_code(c2), 3)


class TestFailReason(unittest.TestCase):
    """失败原因提取：报告**必须给原因**，不能只说"失败了"。

    这条在 FLAKY 场景下尤其关键：首跑失败、复跑通过时，首跑原因必须留在报告里，
    否则"疑似抖动"就退化成一个无法复查的标签。
    """

    STDOUT = """
▶ [保存]
   ✅ 已进入编辑页
   ❌ 断言失败：课程仍在列表中
   ⛔ 前置条件不满足: 需先有一条课程表
📄 报告已生成: /tmp/x_报告.md
"""

    def test_extracts_fail_and_blocked_lines(self):
        r = run_suite._fail_reason(self.STDOUT)
        self.assertIn("断言失败：课程仍在列表中", r)
        self.assertIn("前置条件不满足", r)

    def test_ignores_pass_and_report_lines(self):
        r = run_suite._fail_reason(self.STDOUT)
        self.assertNotIn("已进入编辑页", r)
        self.assertNotIn("报告已生成", r)

    def test_empty_stdout(self):
        self.assertEqual(run_suite._fail_reason(""), "")
        self.assertEqual(run_suite._fail_reason(None), "")

    def test_limit_keeps_last_n(self):
        out = "   ❌ 一\n   ❌ 二\n   ❌ 三\n"
        self.assertEqual(run_suite._fail_reason(out, limit=1), "三")


class TestRunOne(unittest.TestCase):
    """_run_one：子进程退出码 → 状态映射 + 报告路径提取 + 超时/异常兜底。"""

    def _with_proc(self, code, stdout="", stderr=""):
        return mock.patch.object(
            run_suite.subprocess, "run",
            return_value=mock.Mock(returncode=code, stdout=stdout, stderr=stderr))

    def test_status_mapping(self):
        for code, want in ((0, "PASS"), (1, "FAIL"), (2, "BLOCKED"), (3, "ERROR")):
            with self._with_proc(code):
                self.assertEqual(
                    run_suite._run_one("x.py", None, 10, {})["status"], want)

    def test_report_path_extracted(self):
        with self._with_proc(0, stdout="📄 报告已生成: /tmp/a/b_报告.md\n"):
            r = run_suite._run_one("x.py", None, 10, {})
        self.assertTrue(r["report"].endswith("_报告.md"))

    def test_timeout_maps_to_error(self):
        with mock.patch.object(run_suite.subprocess, "run",
                               side_effect=run_suite.subprocess.TimeoutExpired(
                                   "cmd", 1)):
            r = run_suite._run_one("x.py", None, 10, {})
        self.assertEqual(r["status"], "ERROR")
        self.assertTrue(r["timeout"])

    def test_subprocess_exception_maps_to_error(self):
        with mock.patch.object(run_suite.subprocess, "run",
                               side_effect=OSError("boom")):
            r = run_suite._run_one("x.py", None, 10, {})
        self.assertEqual(r["status"], "ERROR")
        self.assertIn("boom", r["stderr"])

    def test_device_passed_to_child(self):
        with self._with_proc(0) as m:
            run_suite._run_one("com.x/1.py", "SERIAL9", 10, {})
        cmd = m.call_args[0][0]
        self.assertIn("--device", cmd)
        self.assertEqual(cmd[cmd.index("--device") + 1], "SERIAL9")


class TestSuiteReport(unittest.TestCase):
    """聚合报告：FLAKY 与确定性 FAIL 必须分开呈现（不能只说"失败了"）。"""

    def _report(self, results, counts, consistency=None, repeat=1):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return open(run_suite._generate_report(
            results, counts, time.time(), "test", d, consistency,
            ["dev-a"], repeat), encoding="utf-8").read()

    def test_flaky_and_fail_are_differentiated(self):
        counts = {"pass": 0, "warn": 0, "fail": 1, "flaky": 1,
                  "blocked": 0, "error": 0, "skipped": 0}
        results = [("a.py", "FAIL", 1.0, 1, "", "dev-a", "FAIL"),
                   ("b.py", "FLAKY", 1.0, 1, "", "dev-a", "PASS")]
        txt = self._report(results, counts)
        self.assertIn("确定性", txt)
        self.assertIn("疑似抖动", txt)
        self.assertIn("🎲", txt)

    def test_consistency_section_included(self):
        counts = {"pass": 2, "warn": 0, "fail": 0, "flaky": 0,
                  "blocked": 0, "error": 0, "skipped": 0}
        results = [("a.py", "PASS", 1.0, 0, "", "dev-a", "")]
        # 3 轮：首轮是基线不入分母 → 2 轮里 1 轮与首轮同 → 50%
        cons = run_suite._consistency({"a.py": ["PASS", "PASS", "FAIL"]})
        txt = self._report(results, counts, cons, repeat=3)
        self.assertIn("结论一致率", txt)
        self.assertIn("50%", txt)

    def test_no_consistency_section_when_single_run(self):
        counts = {"pass": 1, "warn": 0, "fail": 0, "flaky": 0,
                  "blocked": 0, "error": 0, "skipped": 0}
        txt = self._report([("a.py", "PASS", 1.0, 0, "", "dev-a", "")], counts)
        self.assertNotIn("结论一致率", txt)


# ── 真机实测回归：用例枚举去重（2026-09-16 事故）────────────────────
class TestCollectCasesDedupe(unittest.TestCase):
    """同一用例在两个 cases 目录下**只能出现一次**。

    回归 2026-09-16 真机实测：`CASE_DIRS` 同时含「工作区 cases」与「skill 包
    cases」，同一用例两处各一份 —— 绝对路径不同、**相对路径相同**。旧实现按
    绝对路径去重 ⇒ 枚举两次；而子进程拿到的是相对路径、`run_case.py` 又按
    **工作区优先**解析 ⇒ **同一份文件真的跑了两遍**（实测套件报 32 个用例，
    实际只有 16 个；重复结果还会污染 M11 一致率）。
    """

    @staticmethod
    def _mk(root, rel):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("# stub\n")
        return p

    def _patch(self, first_dir, second_dir, rel):
        p1 = self._mk(first_dir, rel)
        p2 = self._mk(second_dir, rel)
        return mock.patch.object(run_suite, "CASE_DIRS",
                                 [first_dir, second_dir]), \
            mock.patch.object(run_suite, "_iter_case_files",
                              return_value=[p1, p2]), p1, p2

    def test_duplicate_rel_path_kept_once(self):
        d1 = tempfile.mkdtemp()
        d2 = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d1, True)
        self.addCleanup(__import__("shutil").rmtree, d2, True)
        rel = os.path.join("com.demo", "1.py")
        p_c, p_i, p1, _p2 = self._patch(d1, d2, rel)
        with p_c, p_i:
            cases = run_suite._collect_cases("com.demo")
        self.assertEqual(len(cases), 1, "同一用例被枚举了两次")
        # 保留先枚举到的那份（CASE_DIRS 顺序 = 工作区优先，= 实际会被执行的那份）
        self.assertEqual(cases[0][0], p1)
        self.assertEqual(cases[0][1], rel)

    def test_different_rel_paths_both_kept(self):
        """不同用例当然都要保留（别把去重做成"只留一个"）。"""
        d1 = tempfile.mkdtemp()
        d2 = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d1, True)
        self.addCleanup(__import__("shutil").rmtree, d2, True)
        a = self._mk(d1, os.path.join("com.demo", "1.py"))
        b = self._mk(d1, os.path.join("com.demo", "2.py"))
        self._mk(d2, os.path.join("com.demo", "1.py"))
        with mock.patch.object(run_suite, "CASE_DIRS", [d1, d2]), \
                mock.patch.object(run_suite, "_iter_case_files",
                                  return_value=[a, b]):
            cases = run_suite._collect_cases("com.demo")
        self.assertEqual(sorted(c[1] for c in cases),
                         sorted([os.path.join("com.demo", "1.py"),
                                 os.path.join("com.demo", "2.py")]))

    def test_package_filter_still_works(self):
        d1 = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d1, True)
        a = self._mk(d1, os.path.join("com.demo", "1.py"))
        b = self._mk(d1, os.path.join("com.other", "9.py"))
        with mock.patch.object(run_suite, "CASE_DIRS", [d1]), \
                mock.patch.object(run_suite, "_iter_case_files",
                                  return_value=[a, b]):
            cases = run_suite._collect_cases("com.demo")
        self.assertEqual(len(cases), 1)
        self.assertIn("com.demo", cases[0][1])


# ── 真机实测回归：设备查询与版本采集（2026-09-16 事故）──────────────
class TestDeviceVersionCollection(unittest.TestCase):
    """版本门禁的设备查询依赖必须**真实可用**。

    回归 2026-09-16 真机实测事故：`run_case.py` **没有 import subprocess**，而
    `_single_device_serial()` 的 `except Exception: return None` 把 `NameError`
    静默吞成"没有唯一设备" → 版本门禁**一次都没采集过**版本号（DB 里
    `app_version_*` 恒 NULL），全程零报错。

    这类"缺导入 + 宽 except"是静默失效的典型形态：功能看起来接好了、单测也过，
    实际一次都没生效。这里的 patch 对象是 `run_case.subprocess` 本身 ——
    若哪天 import 又丢了，patch 自身就会 AttributeError 失败，比断言更早拦住。
    """

    def test_run_case_imports_subprocess(self):
        self.assertTrue(hasattr(run_case, "subprocess"),
                        "run_case 必须能解析 subprocess（采集设备版本要用）")

    def _patch_run(self, stdout="", returncode=0, side_effect=None):
        proc = mock.Mock(returncode=returncode, stdout=stdout, stderr="")
        return mock.patch.object(run_case.subprocess, "run",
                                 return_value=proc, side_effect=side_effect)

    def test_single_device_serial_parses_adb_output(self):
        out = "List of devices attached\n51e8bf1c\tdevice\n\n"
        with self._patch_run(out):
            self.assertEqual(run_case._single_device_serial(), "51e8bf1c")

    def test_single_device_serial_none_for_multiple(self):
        """多台设备不猜 —— 猜一台会让版本门禁报出另一台的版本，比不报更糟。"""
        out = "List of devices attached\nA\tdevice\nB\tdevice\n"
        with self._patch_run(out):
            self.assertIsNone(run_case._single_device_serial())

    def test_single_device_serial_none_for_zero(self):
        with self._patch_run("List of devices attached\n\n"):
            self.assertIsNone(run_case._single_device_serial())

    def test_unauthorized_device_not_counted(self):
        """未授权（unauthorized）不算在线设备 —— 否则会拿它去查版本。"""
        out = "List of devices attached\nX\tunauthorized\n"
        with self._patch_run(out):
            self.assertIsNone(run_case._single_device_serial())

    def test_collect_app_version_parses_dumpsys(self):
        out = ("  Package [com.demo]:\n    versionCode=83 minSdk=28\n"
               "    versionName=9.0.0.83\n")
        with self._patch_run(out) as m:
            self.assertEqual(run_case.collect_app_version("S1", "com.demo"),
                             ("9.0.0.83", "83"))
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[:5], ["adb", "-s", "S1", "shell", "dumpsys"])
        self.assertIn("com.demo", cmd)

    def test_collect_app_version_no_serial_short_circuits(self):
        with mock.patch.object(run_case.subprocess, "run") as m:
            self.assertEqual(run_case.collect_app_version(None, "com.demo"),
                             (None, None))
            m.assert_not_called()

    def test_collect_app_version_failure_is_visible(self):
        """失败必须**可见**（不许静默 None）—— 这正是本次事故的核心。"""
        with mock.patch.object(run_case.subprocess, "run",
                               side_effect=OSError("adb 挂了")):
            with mock.patch("builtins.print") as mp:
                self.assertEqual(run_case.collect_app_version("S1", "d"),
                                 (None, None))
        printed = " ".join(str(c.args[0]) for c in mp.call_args_list if c.args)
        self.assertIn("版本采集失败", printed)


if __name__ == "__main__":
    unittest.main()


# ── db.case_history + db.flaky_stats：flakiness 查询 ──────────────────
class TestFlakiness(unittest.TestCase):
    """构造临时 SQLite 内存库，测试 case_history / flaky_stats 查询逻辑。"""

    def _mk_db(self):
        """建一个独立的 RecordDB 实例（内存库），不影响全局单例。"""
        d = db.RecordDB(":memory:")
        d._connect()  # 触发 schema 创建 + migration
        return d

    def _insert(self, d, script_path, status, started, finished, package=None):
        """插入一条用例记录（绕过 start_case/finish_case 简化构造）。"""
        with d._lock:
            conn = d._connect()
            conn.execute(
                "INSERT INTO cases (name, device, started_at, finished_at,"
                " script_path, final_status, package)"
                " VALUES (?,?,?,?,?,?,?)",
                ("test", "dev", started, finished, script_path, status, package))
            conn.commit()

    def test_case_history_order_and_limit(self):
        """同一脚本的 10 条记录，新→旧排序，limit=5 只返回最近 5 条。"""
        d = self._mk_db()
        sp = "/cases/com.x/1.py"
        for i in range(10):
            self._insert(d, sp, "PASS" if i % 2 == 0 else "FAIL",
                         f"2026-09-0{i%9+1}T10:00:00",
                         f"2026-09-0{i%9+1}T10:01:00")
        hist = d.case_history(sp, limit=5)
        self.assertEqual(len(hist), 5)
        # 最新的是 id=10，应该在第一个
        self.assertEqual(hist[0]["id"], 10)
        self.assertGreater(hist[0]["id"], hist[1]["id"])

    def test_case_history_excludes_empty_status(self):
        """final_status 为空或 NULL 的记录不出现在历史中。"""
        d = self._mk_db()
        sp = "/cases/com.x/2.py"
        self._insert(d, sp, "PASS", "2026-09-01T10:00:00", "2026-09-01T10:01:00")
        # 插入一条 final_status 为空的（模拟未完成的记录）
        with d._lock:
            d._connect().execute(
                "INSERT INTO cases (name, started_at, finished_at, script_path)"
                " VALUES (?,?,?,?)",
                ("test", "2026-09-02T10:00:00", "2026-09-02T10:01:00", sp))
            d._local.conn.commit()
        hist = d.case_history(sp)
        self.assertEqual(len(hist), 1)  # 只有 PASS 那条

    def test_flaky_stats_pass_rate(self):
        """6 PASS + 4 FAIL = 60% 通过率，flaky=True（5<=runs 且 0.2<0.6<0.8）。"""
        d = self._mk_db()
        sp = "/cases/com.x/3.py"
        for i in range(10):
            status = "PASS" if i < 6 else "FAIL"
            self._insert(d, sp, status,
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30", "com.x")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["runs"], 10)
        self.assertAlmostEqual(s["pass_rate"], 0.6, places=2)
        self.assertTrue(s["flaky"])

    def test_flaky_stats_warn_counts_as_pass(self):
        """WARN 算通过：4 PASS + 2 WARN + 4 FAIL = 60%。"""
        d = self._mk_db()
        sp = "/cases/com.x/4.py"
        statuses = ["PASS"]*4 + ["WARN"]*2 + ["FAIL"]*4
        for i, st in enumerate(statuses):
            self._insert(d, sp, st,
                         f"2026-09-01T10:{i:02d}:00",
                         f"2026-09-01T10:{i:02d}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertAlmostEqual(stats[0]["pass_rate"], 0.6, places=2)

    def test_flaky_stats_below_min_runs_not_flaky(self):
        """4 条记录（< min_runs=5）→ flaky=False（样本不足不误报）。"""
        d = self._mk_db()
        sp = "/cases/com.x/5.py"
        for i in range(4):
            status = "PASS" if i < 2 else "FAIL"
            self._insert(d, sp, status,
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertFalse(stats[0]["flaky"])

    def test_flaky_stats_all_pass_not_flaky(self):
        """100% 通过率 → flaky=False。"""
        d = self._mk_db()
        sp = "/cases/com.x/6.py"
        for i in range(6):
            self._insert(d, sp, "PASS",
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertAlmostEqual(stats[0]["pass_rate"], 1.0)
        self.assertFalse(stats[0]["flaky"])


# ── db.card_freshness：知识卡新鲜度查询 ────────────────────────────────
class TestCardFreshness(unittest.TestCase):
    """测试 card_freshness() 的入库与查询逻辑。"""

    def _mk_db(self):
        d = db.RecordDB(":memory:")
        d._connect()
        return d

    def _insert(self, d, script_path, status, started, finished, package=None):
        with d._lock:
            conn = d._connect()
            conn.execute(
                "INSERT INTO cases (name, device, started_at, finished_at,"
                " script_path, final_status, package)"
                " VALUES (?,?,?,?,?,?,?)",
                ("test", "dev", started, finished, script_path, status, package))
            conn.commit()

    def test_basic_pass_record(self):
        """有 PASS 记录的包 → last_pass_at 非空。"""
        d = self._mk_db()
        self._insert(d, "/c/com.x/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.x")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["package"], "com.x")
        self.assertIsNotNone(r["last_pass_at"])
        self.assertIn("2026-09-01", r["last_pass_at"])
        self.assertEqual(r["runs"], 1)

    def test_warn_not_counted_as_pass(self):
        """WARN 不算验证通过 → last_pass_at 为 None（从未 PASS）。"""
        d = self._mk_db()
        self._insert(d, "/c/com.y/1.py", "WARN",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.y")
        self._insert(d, "/c/com.y/2.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.y")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertIsNone(r["last_pass_at"])
        self.assertEqual(r["runs"], 2)  # 两条都算执行次数

    def test_never_passed_package(self):
        """只有 FAIL 的包 → last_pass_at=None，runs 正确。"""
        d = self._mk_db()
        for i in range(3):
            self._insert(d, f"/c/com.z/{i}.py", "FAIL",
                         f"2026-09-0{i+1}T10:00:00",
                         f"2026-09-0{i+1}T10:01:00", "com.z")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["last_pass_at"])
        self.assertEqual(rows[0]["runs"], 3)

    def test_multiple_packages(self):
        """多个包各自独立统计。"""
        d = self._mk_db()
        self._insert(d, "/c/com.a/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.a")
        self._insert(d, "/c/com.b/1.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.b")
        rows = d.card_freshness()
        pkgs = {r["package"]: r for r in rows}
        self.assertIn("com.a", pkgs)
        self.assertIn("com.b", pkgs)
        self.assertIsNotNone(pkgs["com.a"]["last_pass_at"])
        self.assertIsNone(pkgs["com.b"]["last_pass_at"])

    def test_last_pass_id_matches_latest(self):
        """last_pass_id 指向最近的 PASS 记录（而非最早的）。"""
        d = self._mk_db()
        self._insert(d, "/c/com.p/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.p")
        self._insert(d, "/c/com.p/1.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.p")
        self._insert(d, "/c/com.p/1.py", "PASS",
                     "2026-09-03T10:00:00", "2026-09-03T10:01:00", "com.p")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # 最近 PASS 是第 3 条（id=3）
        self.assertEqual(r["last_pass_id"], 3)
        self.assertIn("2026-09-03", r["last_pass_at"])
        self.assertEqual(r["runs"], 3)
