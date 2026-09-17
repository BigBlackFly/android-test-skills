#!/usr/bin/env python3
"""framework 纯逻辑单测（不需要 Android 设备）。

覆盖：不改设备的确定性逻辑 —— XML 节点解析、场景卡解析/自动注册、
用例名解析、USER_INPUT 提取、工作区漂移检测、Web UI 路径安全。

运行（skill 包根目录）：
    python -m unittest discover -s tests -v
_parse_nodes 的用例需要 uiautomator2（import test_framework 依赖），
系统 Python 没有时会自动跳过；用工作区 venv 跑可覆盖全部：
    ~/android-test-skills-data/.venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
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

import db          # noqa: E402
import run_case    # noqa: E402
import run_metrics  # noqa: E402
import version_gate  # noqa: E402
import states    # noqa: E402
import vision    # noqa: E402
import webui     # noqa: E402

# scripts/ 目录加入 sys.path 以便导入预算门禁脚本
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import check_context_budget as budget_mod  # noqa: E402
import knowledge_index as ki_mod  # noqa: E402

# framework/smoke.py 加入 sys.path 以便测试 _check_adb
import smoke  # noqa: E402

try:
    import test_framework as tf
except ImportError:  # 系统 Python 无 uiautomator2 时跳过相关用例
    tf = None
_tf = tf             # 别名：P1b 用例沿用 _tf 写法（与 test_run_suite 一致）


# ── states.py：场景卡解析与自动注册 ──────────────────────────────────
class TestParseCmd(unittest.TestCase):
    def test_with_adb_prefix(self):
        self.assertEqual(
            states._parse_cmd("adb shell settings get global zen_mode"),
            ["settings", "get", "global", "zen_mode"])

    def test_without_prefix(self):
        self.assertEqual(states._parse_cmd("settings get global zen_mode"),
                         ["settings", "get", "global", "zen_mode"])

    def test_empty(self):
        self.assertIsNone(states._parse_cmd(""))
        self.assertIsNone(states._parse_cmd(None))
        self.assertIsNone(states._parse_cmd("adb shell"))


class TestParseSimpleCard(unittest.TestCase):
    def test_scalar_and_list(self):
        card = "场景: 勿扰模式\n判定命令: settings get global zen_mode\n触发词:\n- 勿扰\n- dnd\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d["场景"], "勿扰模式")
        self.assertEqual(d["判定命令"], "settings get global zen_mode")
        self.assertEqual(d["触发词"], ["勿扰", "dnd"])

    def test_heading_ends_current_key(self):
        # MD 标题之后的 "- 项" 不应被吸进前面的列表键
        card = "触发词:\n- a\n## 正文\n- 这不是触发词\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d["触发词"], ["a"])

    def test_comment_and_prose_ignored(self):
        card = "<!-- 注释 -->\n场景: x\n随便一行散文\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d, {"场景": "x"})


class TestScenarioRegistration(unittest.TestCase):
    """场景卡写「判定命令」→ 自动注册 is_xxx / raw_xxx。"""

    def test_load_scenarios_registers_methods(self):
        card = ("场景: 单测假场景\n"
                "判定方法: states.is_test_dummy_mode()\n"
                "判定命令: settings get global test_dummy\n"
                "触发词:\n- 单测假场景\n")
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "sys.单测.md"), "w", encoding="utf-8") as f:
                f.write(card)
            old = os.environ.get("DSH_SCENARIOS_DIR")
            os.environ["DSH_SCENARIOS_DIR"] = td
            try:
                states.load_scenarios()
                self.assertTrue(hasattr(states.States, "is_test_dummy_mode"))
                self.assertTrue(hasattr(states.States, "raw_test_dummy_mode"))

                class FakeAdb:
                    def shell(self, *args):
                        return "2"

                s = states.States(adb=FakeAdb())
                self.assertTrue(s.is_test_dummy_mode())      # 非0 → True
                self.assertEqual(s.raw_test_dummy_mode(), "2")
            finally:
                if old is None:
                    os.environ.pop("DSH_SCENARIOS_DIR", None)
                else:
                    os.environ["DSH_SCENARIOS_DIR"] = old
                # 清理注册，避免污染同进程其它测试
                for attr in ("is_test_dummy_mode", "raw_test_dummy_mode"):
                    if attr in states.States.__dict__:
                        delattr(states.States, attr)
                states._REGISTRY[:] = [m for m in states._REGISTRY
                                       if m["name"] != "is_test_dummy_mode"]


class TestTruthy(unittest.TestCase):
    def test_values(self):
        t = states.States._truthy
        for v in ("1", "2", "true", "on", "yes"):
            self.assertTrue(t(v), v)
        for v in ("", "0", "null", "none", "false", "off", None):
            self.assertFalse(t(v), v)


# ── run_case.py：用例解析与 USER_INPUT 提取 ─────────────────────────
class TestResolveCase(unittest.TestCase):
    def setUp(self):
        self._old_dirs = run_case.CASE_DIRS
        self._td = tempfile.TemporaryDirectory()
        root = self._td.name
        os.makedirs(os.path.join(root, "cases", "com.a.x"))
        os.makedirs(os.path.join(root, "cases", "com.b.y"))
        for rel in ("com.a.x/1.py", "com.b.y/1.py", "com.a.x/2.py",
                    "com.a.x/_flow.py"):
            p = os.path.join(root, "cases", *rel.split("/"))
            with open(p, "w", encoding="utf-8") as f:
                f.write("def run():\n    pass\n")
        run_case.CASE_DIRS = [os.path.join(root, "cases")]

    def tearDown(self):
        run_case.CASE_DIRS = self._old_dirs
        self._td.cleanup()

    def test_qualified_path(self):
        hit = run_case.resolve_case("com.a.x/2.py")
        self.assertTrue(hit.endswith(os.path.join("com.a.x", "2.py")))

    def test_bare_unique(self):
        hit = run_case.resolve_case("2.py")
        self.assertIsNotNone(hit)

    def test_bare_ambiguous_returns_none(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(run_case.resolve_case("1.py"))  # 两个包都有

    def test_shared_module_not_matched(self):
        self.assertIsNone(run_case.resolve_case("_flow.py"))

    def test_missing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(run_case.resolve_case("999.py"))


class TestExtractUserInput(unittest.TestCase):
    def test_triple_quoted(self):
        src = 'USER_INPUT = """前提：无\n步骤：点\n预期：成"""\n'
        self.assertEqual(run_case.extract_user_input_from_source(src),
                         "前提：无\n步骤：点\n预期：成")

    def test_content_with_quotes(self):
        # 旧正则在这类内容上会截断/失配；ast 按语法树取常量
        src = "USER_INPUT = '''包含 \"\"\" 三引号 和 \"双引号\" 的内容'''"
        self.assertEqual(run_case.extract_user_input_from_source(src),
                         '包含 """ 三引号 和 "双引号" 的内容')

    def test_single_line(self):
        src = 'USER_INPUT = "单行描述"'
        self.assertEqual(run_case.extract_user_input_from_source(src), "单行描述")

    def test_missing_and_broken(self):
        self.assertIsNone(run_case.extract_user_input_from_source("x = 1"))
        self.assertIsNone(run_case.extract_user_input_from_source("def (:"))
        self.assertIsNone(run_case.extract_user_input_from_source('USER_INPUT = 123'))

    def test_from_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write('USER_INPUT = """文件里的描述"""\n')
            path = f.name
        try:
            self.assertEqual(run_case.extract_user_input(path), "文件里的描述")
        finally:
            os.unlink(path)

    def test_from_file_with_utf8_bom(self):
        """带 UTF-8 BOM 的用例文件必须照样提取出 USER_INPUT。

        回归 2026-09-14 实测的**三重静默**事故：`utf-8` 读带 BOM 文件 → 首字符
        是 U+FEFF → `ast.parse` 抛 SyntaxError → 被 except 吞成 None → ①
        `user_input` 写空；② `_should_record()` 判 False → 用例不入库；③
        `_guard_exec_cache()` 用同一判据 → 执行期缓存守卫静默失效。
        而 `importlib` 加载同一文件是正常的（Python 按 utf-8-sig 处理），
        所以现象是"用例照跑、PASS、退出码 0，但没记录、没守卫、无任何提示"。
        """
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8-sig") as f:
            f.write('USER_INPUT = """带 BOM 的描述"""\n')
            path = f.name
        try:
            with open(path, "rb") as rb:
                # 前置自检：确认文件真的有 BOM（否则本测试会"因错误的原因通过"）
                self.assertEqual(rb.read(3), b"\xef\xbb\xbf")
            self.assertEqual(run_case.extract_user_input(path), "带 BOM 的描述")
        finally:
            os.unlink(path)

    def test_extract_from_source_with_bom_prefix(self):
        """直接传字符串（不经文件读取）时也要能剥掉 BOM。"""
        src = '\ufeffUSER_INPUT = "带 BOM 字符串"'
        self.assertEqual(run_case.extract_user_input_from_source(src),
                         "带 BOM 字符串")


class TestFrameworkDrift(unittest.TestCase):
    def _mk(self, root, files):
        fw = os.path.join(root, "framework")
        os.makedirs(fw)
        for name, content in files.items():
            with open(os.path.join(fw, name), "w", encoding="utf-8") as f:
                f.write(content)
        return fw

    def test_drift_detected_and_clean(self):
        base = {f: "same" for f in run_case._DRIFT_KEY_FILES}
        with tempfile.TemporaryDirectory() as td:
            self._mk(os.path.join(td, "ws"), base)
            self._mk(os.path.join(td, "skill"), base)
            # 三方比对：把运行副本注入成工作区那份（真实 HERE 是开发仓，内容不同）
            run_fw = os.path.join(td, "ws", "framework")
            old = {k: os.environ.get(k)
                   for k in ("DSH_WORKSPACE_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_WORKSPACE_DIR"] = os.path.join(td, "ws")
            os.environ["DSH_SKILL_DIR"] = os.path.join(td, "skill")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run_fw)
                self.assertNotIn("不一致", buf.getvalue())
                # 制造漂移
                with open(os.path.join(td, "ws", "framework",
                                       "test_framework.py"), "w") as f:
                    f.write("changed")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run_fw)
                self.assertIn("不一致", buf.getvalue())
                self.assertIn("test_framework.py", buf.getvalue())
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_three_way_detects_stale_installed_copy(self):
        """运行副本纳入比对：从开发仓直接跑时也能发现安装副本过期（168 实测场景）。"""
        base = {f: "same" for f in run_case._DRIFT_KEY_FILES}
        with tempfile.TemporaryDirectory() as td:
            self._mk(os.path.join(td, "ws"), base)
            self._mk(os.path.join(td, "skill"), base)
            run = self._mk(os.path.join(td, "run"), base)
            for f in run_case._DRIFT_KEY_FILES:
                with open(os.path.join(run, f), "w", encoding="utf-8") as fh:
                    fh.write("newer")          # 运行副本比另外两份新
            old = {k: os.environ.get(k)
                   for k in ("DSH_WORKSPACE_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_WORKSPACE_DIR"] = os.path.join(td, "ws")
            os.environ["DSH_SKILL_DIR"] = os.path.join(td, "skill")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run)
                out = buf.getvalue()
                self.assertIn("正在运行", out)
                self.assertIn("工作区备份", out)
                self.assertIn("skill包", out)
                self.assertIn("test_framework.py", out)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


# ── webui.py：用例路径安全 ──────────────────────────────────────────
class TestSafeCaseName(unittest.TestCase):
    def test_flat_and_subdir(self):
        self.assertEqual(webui._safe_case_name("172.py"), "172.py")
        self.assertEqual(webui._safe_case_name("com.zui.calendar/172.py"),
                         "com.zui.calendar/172.py")
        self.assertEqual(webui._safe_case_name("com.zui.calendar\\172.py"),
                         "com.zui.calendar/172.py")

    def test_rejects_traversal_and_bad_input(self):
        for bad in ("../escape.py", "com.zui.calendar/../../x.py",
                    "a/b/c.py", ".hidden/x.py", "x.txt", "", "/abs/x.py"):
            self.assertIsNone(webui._safe_case_name(bad), bad)


# ── test_framework.py：UI XML 节点解析（需要 uiautomator2 环境）──────
_DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="He said &quot;hi&quot;" resource-id="com.x:id/tv"
        class="android.widget.TextView" content-desc=""
        clickable="false" enabled="true" selected="false" checked="false"
        bounds="[0,100][200,160]" />
  <node text="" resource-id="" class="android.widget.ImageView"
        content-desc="更多" clickable="true" enabled="true"
        bounds="[1600,100][1800,200]" />
</hierarchy>"""


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestParseNodes(unittest.TestCase):
    def test_elementtree_path(self):
        nodes = tf._parse_nodes(_DUMP)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["text"], 'He said "hi"')  # 转义被正确还原
        self.assertEqual(nodes[0]["bounds_xy"], (0, 100, 200, 160))
        self.assertEqual(nodes[0]["clickable"], "false")
        self.assertEqual(nodes[1]["desc"], "更多")
        self.assertEqual(nodes[1]["clickable"], "true")

    def test_regex_fallback_on_broken_xml(self):
        # 属性值含未转义的 & —— ElementTree 判定非法 XML，回退正则提取
        broken = '<hierarchy><node text="a & b" bounds="[1,2][3,4]" clickable="true"/></hierarchy>'
        nodes = tf._parse_nodes(broken)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["text"], "a & b")
        self.assertEqual(nodes[0]["bounds_xy"], (1, 2, 3, 4))


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestWaitPrimitives(unittest.TestCase):
    """条件等待原语：命中即返回、超时有界（替代裸 sleep）。"""
    _XML = ('<hierarchy><node text="目标文字" resource-id="com.x:id/btn" '
            'bounds="[0,0][10,10]" clickable="true"/></hierarchy>')

    def _mk(self):
        t = object.__new__(tf.TestCase)     # 绕过 __init__（不连设备）
        t._wd_enabled = False

        class FakeD:
            def dump_hierarchy(self_):
                return TestWaitPrimitives._XML
        t.d = FakeD()
        return t

    def test_wait_text_hit_and_timeout(self):
        t = self._mk()
        self.assertTrue(t.wait_text("目标文字", timeout=1, interval=0.1))
        t0 = time.time()
        self.assertFalse(t.wait_text("不存在", timeout=0.5, interval=0.2))
        self.assertLess(time.time() - t0, 2)   # 超时确实有界返回

    def test_wait_rid(self):
        t = self._mk()
        self.assertTrue(t.wait_rid("com.x:id/btn", timeout=1, interval=0.1))
        self.assertFalse(t.wait_rid("com.x:id/none", timeout=0.4, interval=0.2))

    def test_wait_activity(self):
        t = self._mk()
        t.current_activity = lambda: "com.x/.ui.MainActivity"  # 实例属性遮蔽方法
        self.assertEqual(t.wait_activity("mainactivity", timeout=0.5),
                         "com.x/.ui.MainActivity")
        self.assertEqual(t.wait_activity("settings", timeout=0.3, interval=0.1), "")


# ── 结果语义：最终状态与退出码 ─────────────────────────────────────
class TestExitCodes(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(run_case.exit_code_for("PASS"), 0)
        self.assertEqual(run_case.exit_code_for("WARN"), 0)
        self.assertEqual(run_case.exit_code_for("FAIL"), 1)
        self.assertEqual(run_case.exit_code_for("BLOCKED"), 2)
        # 未知/缺失一律按 ERROR(3)，绝不默认 0 放行
        self.assertEqual(run_case.exit_code_for("ERROR"), 3)
        self.assertEqual(run_case.exit_code_for(None), 3)
        self.assertEqual(run_case.exit_code_for("随便什么"), 3)


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestFinalStatus(unittest.TestCase):
    """最终结论规则：FAIL > BLOCKED > WARN > PASS，不从摘要文本推断。"""

    def _mk(self, results):
        t = object.__new__(tf.TestCase)
        t.steps = [{"name": "s", "results":
                    [{"result": r, "detail": "x"} for r in results],
                    "evidences": []}]
        # 清场钩子（finish() → _run_cleanups）与漂移检测要求的字段：桩用
        # object.__new__ 绕过 __init__，这些属性不会自动存在
        #（清场钩子上线时漏补，曾导致 9 个测试红）。
        t._cleanups = []
        t._cleanups_ran = False
        t._env_baseline = None
        t._env_ignore = set()
        return t

    def test_priority(self):
        self.assertEqual(self._mk(["PASS", "PASS"])._compute_final_status(), "PASS")
        self.assertEqual(self._mk(["PASS", "WARN"])._compute_final_status(), "WARN")
        self.assertEqual(self._mk(["BLOCKED"])._compute_final_status(), "BLOCKED")
        # 关键回归：只有 BLOCKED 的用例绝不能是 PASS
        self.assertNotEqual(self._mk(["BLOCKED"])._compute_final_status(), "PASS")
        self.assertEqual(self._mk(["BLOCKED", "FAIL"])._compute_final_status(), "FAIL")
        self.assertEqual(self._mk(["WARN", "FAIL"])._compute_final_status(), "FAIL")
        self.assertEqual(self._mk(["INFO"])._compute_final_status(), "PASS")

    def test_fatal_error_forces_error(self):
        """异常路径（N4）：run_case 捕获异常设 _fatal_error 后补调 finish()，
        结论必须压成 ERROR —— 用例没跑完，断言统计再好看也不可信。"""
        t = self._mk(["PASS", "PASS"])
        t.name = "单测_fatal_error"
        t.device_info = "fake-device"
        t.case_dir = tempfile.mkdtemp()      # finish() 报告头引用证据目录
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = RuntimeError("模拟执行中途异常")
        old_last = tf.LAST_CASE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "ERROR")
        finally:
            tf.LAST_CASE = old_last

    def test_finish_idempotent(self):
        """finish() 幂等守卫（P3-1）：用例正常 finish 后收尾代码再抛异常时，
        run_case 的异常兜底会再调一次 finish —— 必须直接返回旧报告，
        不重复备份/写库/重算结论。"""
        t = self._mk(["PASS"])
        t.name = "单测_finish_幂等"
        t.device_info = "fake-device"
        t.case_dir = tempfile.mkdtemp()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        old_last = tf.LAST_CASE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                p1 = t.finish()
                self.assertTrue(t._finished)
                self.assertEqual(t.final_status, "PASS")
                # 模拟"finish 后收尾代码又出事"：改变状态再调 finish，
                # 守卫应直接返回旧路径，final_status 不被重算覆盖
                t._fatal_error = RuntimeError("finish 之后的收尾异常")
                p2 = t.finish()
            self.assertEqual(p1, p2)
            self.assertEqual(t.final_status, "PASS")   # 不变 ERROR
            self.assertEqual(buf.getvalue().count("报告已生成"), 1)
        finally:
            tf.LAST_CASE = old_last


# ── 被测 App 包名推断（finish() 报告头 / DB package 列的数据源）──────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestCasePackageInference(unittest.TestCase):
    """package 取值优先级：脚本目录名（像包名）> 收尾前台包。

    背景：用例常停在 PhotoPicker 等系统页收尾，前台包可能根本不是被测 App
    （168 实测报成 com.android.providers.media.module）。"""

    def _mk(self, script_path):
        t = object.__new__(tf.TestCase)
        t.script_path = script_path
        return t

    def test_infers_from_windows_script_path(self):
        t = self._mk("C:\\Users\\u\\.agents\\skills\\android-test-skills"
                     "\\cases\\com.zui.calendar\\168.py")
        self.assertEqual(t._case_package_from_script(), "com.zui.calendar")

    def test_infers_from_posix_script_path(self):
        t = self._mk("/home/u/skills/android-test-skills/cases/com.a.b/172.py")
        self.assertEqual(t._case_package_from_script(), "com.a.b")

    def test_non_package_dir_returns_none(self):
        t = self._mk("D:\\x\\cases\\联想日历\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_cases_segment_returns_none(self):
        t = self._mk("D:\\somewhere\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_script_path_returns_none(self):
        self.assertIsNone(self._mk(None)._case_package_from_script())


# ── db.py：库文件父目录缺失时自动创建（webui 纯前端场景回归）────────
class TestDbMissingParentDir(unittest.TestCase):
    """storage/ 未创建（纯 Web UI / 首次使用 / HOME 被重定向）时，
    connect 曾直接抛 OperationalError: unable to open database file。"""

    def test_connect_creates_missing_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            deep = os.path.join(td, "ws", "storage", "sub")
            dbp = os.path.join(deep, "test_records.db")
            self.assertFalse(os.path.isdir(deep))
            rdb = db.RecordDB(path=dbp)
            try:
                self.assertEqual(rdb.list_cases(), [])   # 不抛 CANTOPEN
            finally:
                rdb.close()
            self.assertTrue(os.path.isfile(dbp))


# ── vision.py：鉴权头使用真实 Key（mock HTTP，不触网、不泄露）──────
class TestVisionAuth(unittest.TestCase):
    def test_authorization_uses_real_key(self):
        v = vision.Vision(api_key="sk-unit-test-dummy", base_url="http://127.0.0.1")
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "OK"}}]}).encode()

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return FakeResp()

        with mock.patch.object(vision.urllib.request, "urlopen", fake_urlopen):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                out = v.ask("测试", b"\x89PNG\r\n\x1a\n")  # 假 PNG 字节即可
        self.assertEqual(out, "OK")
        self.assertEqual(captured["auth"], "Bearer sk-unit-test-dummy")
        # 日志/输出不得出现明文 Key
        self.assertNotIn("sk-unit-test-dummy", buf.getvalue())


# ── db.py / webui.py：产物目录白名单 ────────────────────────────────
class TestArtifactPath(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("DSH_WORKSPACE_DIR")
        os.environ["DSH_WORKSPACE_DIR"] = self._td.name
        self.shots = os.path.join(self._td.name, "storage", "screenshots")
        os.makedirs(self.shots)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DSH_WORKSPACE_DIR", None)
        else:
            os.environ["DSH_WORKSPACE_DIR"] = self._old
        self._td.cleanup()

    def test_inside_outside(self):
        inside = os.path.join(self.shots, "case_1", "01.png")
        self.assertTrue(db.is_artifact_path(inside))
        self.assertTrue(db.is_artifact_path(
            os.path.join(self._td.name, "storage", "reports", "r.md")))
        # 目录外 / 相对路径 / 穿越伪装一律拒绝
        self.assertFalse(db.is_artifact_path(os.path.join(self._td.name, "x.py")))
        self.assertFalse(db.is_artifact_path("relative.png"))
        evil = os.path.join(self.shots, "..", "..", "db.py")
        self.assertFalse(db.is_artifact_path(evil))
        self.assertFalse(db.is_artifact_path(""))


# ── test_framework.py：多设备 serial 绑定与 require_* 强语义 ────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestResolveSerial(unittest.TestCase):
    def _fake_adb_devices(self, stdout):
        def fake_run(cmd, *a, **k):
            class R:
                pass
            r = R()
            r.stdout = stdout
            return r
        return mock.patch("subprocess.run", fake_run)

    def test_single_device(self):
        out = "List of devices attached\nemulator-5554\tdevice\n\n"
        with self._fake_adb_devices(out):
            self.assertEqual(tf._resolve_serial(), "emulator-5554")

    def test_multi_device_fails_fast(self):
        out = ("List of devices attached\nemulator-5554\tdevice\n"
               "192.168.1.2:5555\tdevice\n\n")
        with self._fake_adb_devices(out):
            with self.assertRaises(RuntimeError):
                tf._resolve_serial()

    def test_no_device(self):
        with self._fake_adb_devices("List of devices attached\n\n"):
            with self.assertRaises(RuntimeError):
                tf._resolve_serial()

    def test_explicit_passthrough(self):
        # 显式指定时不查 adb devices
        self.assertEqual(tf._resolve_serial("device-b"), "device-b")


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRequireTap(unittest.TestCase):
    """require_* 找不到元素：记 FAIL + 抛 CaseAbort（防假通过）。"""

    def _mk(self, xml):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()

        class FakeD:
            def dump_hierarchy(self_):
                return xml
        t.d = FakeD()
        return t

    def test_missing_element_aborts(self):
        t = self._mk('<hierarchy><node text="别的" bounds="[0,0][1,1]"/></hierarchy>')
        buf = io.StringIO()
        with self.assertRaises(tf.CaseAbort):
            with contextlib.redirect_stdout(buf):   # record 会打 ❌ emoji，GBK 控制台会崩
                t.require_tap_text("不存在", wait=0.4)
        results = t._cur_step["results"]
        self.assertTrue(any(r["result"] == "FAIL" for r in results))


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRecordAutoStep(unittest.TestCase):
    """record() 在未开 step 时自动补一个可追溯步骤，而不是崩在 NoneType。

    回归保护：框架 record() 曾直接解引用 _cur_step，调用方忘了 t.step() 就抛
    TypeError: 'NoneType' object is not subscriptable —— 报错完全看不出根因是
    没开 step（175 用例踩到，排查成本很高）。
    """

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._cur_step = None              # 关键前提：从未开过 step
        t.steps = []
        t._db = None
        t._db_step_id = None
        t._db_step_ord = 0
        t._wd_enabled = False
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        return t

    def test_record_without_step_auto_creates(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()):
            t.record("PASS", "无 step 直接记录")      # 不该抛异常
        self.assertEqual(len(t.steps), 1)
        self.assertIn("未显式声明", t.steps[0]["name"])
        self.assertEqual(t.steps[0]["results"][0]["detail"], "无 step 直接记录")

    def test_blocked_without_step_auto_creates(self):
        t = self._mk()                                 # blocked() 走 record，同样受保护
        with contextlib.redirect_stdout(io.StringIO()):
            t.blocked("环境不满足")
        self.assertEqual(len(t.steps), 1)
        self.assertIn("阻塞", t.steps[0]["results"][0]["detail"])

    def test_existing_step_not_overridden(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()):
            t.step("我的步骤")
            t.record("PASS", "正常记录")
        # 已开过 step 时不能另起一个，结果必须落在原步骤里
        self.assertEqual(len(t.steps), 1)
        self.assertEqual(t.steps[0]["name"], "我的步骤")


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestTapUnifiedContract(unittest.TestCase):
    """统一动作基元契约（N1/N2 修复的回归保护）：
    tap_* 轮询定位返回 bool；找不到默认记 WARN；silent=True 时交由调用方
    守卫分支记录（防双重记录）；observe=False 立即返回不延迟。"""

    _XML = ('<hierarchy><node text="确定" resource-id="com.x:id/btn" '
            'bounds="[10,20][110,60]" clickable="true"/></hierarchy>')

    def _mk(self, xml=None):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        t._auto_screenshot = lambda *a, **k: None     # 单测不真截屏
        clicks = []

        class FakeD:
            def dump_hierarchy(self_):
                return xml if xml is not None else TestTapUnifiedContract._XML

            def click(self_, x, y):
                clicks.append((x, y))

            def send_keys(self_, s):
                pass

            def clear_text(self_):
                pass
        t.d = FakeD()
        t._clicks = clicks
        return t

    def test_hit_returns_true_and_clicks_center(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertTrue(t.tap_text("确定", wait=1))
        self.assertEqual(t._clicks, [(60, 40)])   # bounds [10,20][110,60] 中心

    def test_miss_returns_false_and_records_warn(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertFalse(t.tap_text("不存在", wait=0.3))
        self.assertEqual([r["result"] for r in t._cur_step["results"]], ["WARN"])

    def test_miss_silent_records_nothing(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertFalse(t.tap_text("不存在", wait=0.3, silent=True))
        self.assertEqual(t._cur_step["results"], [])   # 守卫语义交还调用方

    def test_observe_false_returns_immediately(self):
        t = self._mk()
        t0 = time.time()
        self.assertTrue(t.tap_rid("com.x:id/btn", observe=False))
        self.assertLess(time.time() - t0, 0.5)          # 不 sleep(ACTION_DELAY)
        self.assertEqual(t._clicks, [(60, 40)])

    def test_tap_xy_returns_true(self):
        t = self._mk()
        self.assertIs(t.tap_xy(5, 5, observe=False), True)  # 不再返回 self 链式

    def test_input_text_hit_and_miss_silent(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertTrue(t.input_text("com.x:id/btn", "你好", wait=1))
            self.assertFalse(t.input_text("com.x:id/none", "x", wait=0.3,
                                          silent=True))
        self.assertEqual(t._clicks, [(60, 40)])
        self.assertEqual(t._cur_step["results"], [])   # silent：无 WARN 兜底


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRequireTapSingleImpl(unittest.TestCase):
    """require_* 收敛为 tap_* 的 silent 模式（N2 竞态消除）：
    命中时不产生任何 WARN/FAIL 记录 —— 旧实现"先 wait 后 tap"两步之间的
    竞态窗口已消除，且不会双重记录。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        t._auto_screenshot = lambda *a, **k: None

        class FakeD:
            def dump_hierarchy(self_):
                return ('<hierarchy><node text="确定" resource-id="com.x:id/btn" '
                        'bounds="[0,0][100,50]" clickable="true"/></hierarchy>')

            def click(self_, x, y):
                pass
        t.d = FakeD()
        return t

    def test_hit_records_nothing(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertIs(t.require_tap_text("确定", wait=1), True)
        self.assertEqual(t._cur_step["results"], [])   # 命中：零记录零噪声


# ── states.py：环境漂移检测 ─────────────────────────────────────
class TestEnvSnapshot(unittest.TestCase):
    """env_snapshot() 取设备环境快照，env_diff() 比对差异。"""

    def _mk_states(self, values):
        """造一个假 States，adb.shell 按命令返回预设值。"""
        s = states.States.__new__(states.States)

        class FakeAdb:
            def shell(self_, *args):
                cmd = " ".join(args)
                for key, val in values.items():
                    if key in cmd:
                        return val
                return ""

        s.adb = FakeAdb()
        return s

    def test_snapshot_returns_all_keys(self):
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        snap = s.env_snapshot()
        self.assertEqual(snap["accelerometer_rotation"], "0")
        self.assertEqual(snap["user_rotation"], "0")
        self.assertEqual(snap["stay_on_while_plugged_in"], "3")
        self.assertEqual(snap["zen_mode"], "0")
        # foreground_package 和 screen_brightness 已移除（误报源）
        self.assertNotIn("foreground_package", snap)
        self.assertNotIn("screen_brightness", snap)

    def test_diff_no_change(self):
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        baseline = s.env_snapshot()
        diff = s.env_diff(baseline)
        self.assertEqual(diff, {})

    def test_diff_detects_change(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        # 用例跑了之后 zen_mode 变成了 1（勿扰）
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "1",
        })
        diff = s.env_diff(before)
        self.assertIn("zen_mode", diff)
        self.assertEqual(diff["zen_mode"], ("0", "1"))
        self.assertNotIn("accelerometer_rotation", diff)

    def test_diff_ignore_keys(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        # 用例改了 user_rotation（合法操作），但豁免
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "1",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        diff = s.env_diff(before, ignore=("user_rotation",))
        self.assertEqual(diff, {})
        # 不豁免则能检测到
        diff2 = s.env_diff(before)
        self.assertIn("user_rotation", diff2)

    def test_diff_multiple_changes(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        s = self._mk_states({
            "accelerometer_rotation": "1",
            "user_rotation": "3",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "2",
        })
        diff = s.env_diff(before)
        self.assertEqual(len(diff), 3)   # 除了 stay_on_while_plugged_in


# ── test_framework.py：finish() 环境漂移检测集成 ────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestEnvDriftInFinish(unittest.TestCase):
    """finish() 前环境漂移检测：有漂移 → PASS 降级为 WARN；env_ignore 豁免。"""

    def _mk(self, env_baseline, env_after, env_ignore=()):
        t = object.__new__(tf.TestCase)
        t.name = "单测_漂移检测"
        t.device_info = "fake"
        t.case_dir = tempfile.mkdtemp()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        t._env_ignore = set(env_ignore)
        t._env_baseline = env_baseline
        t.steps = [{"name": "s", "results":
                    [{"result": "PASS", "detail": "ok"}],
                    "evidences": []}]
        # mock states
        s = states.States.__new__(states.States)

        class FakeAdb:
            def shell(self_, *args):
                cmd = " ".join(args)
                for key, val in env_after.items():
                    if key in cmd:
                        return val
                return ""

        s.adb = FakeAdb()
        t.states = s
        t._cleanups = []
        t._cleanups_ran = False
        # _cur_step 必须指到一个 step：finish() 的漂移检测会 record("WARN", ...)，
        # 缺这个属性会让 record() 抛 AttributeError，被漂移块的 `except: pass`
        # 静默吞掉 → 漂移检测退化成永远不降级（本测试曾因此恒 PASS）。
        t._cur_step = t.steps[0]
        return t

    def test_no_drift_stays_pass(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        t = self._mk(baseline, dict(baseline))
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "PASS")
        finally:
            tf.LAST_CASE = old_last

    def test_drift_downgrades_to_warn(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"   # 用例开了勿扰没关
        t = self._mk(baseline, after)
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "WARN")
            # 报告里应有漂移信息
            warn_records = [r for s in t.steps for r in s["results"]
                           if r["result"] == "WARN"]
            self.assertTrue(any("污染设备环境" in r["detail"]
                               for r in warn_records))
        finally:
            tf.LAST_CASE = old_last

    # ── 方向变化检测（不受 env_ignore 约束）──────────────────────────
    def _mk_rot(self, rot_start, rot_end, env_ignore=()):
        """_mk 的桩 + 可注入的方向读数（环境本身无漂移，隔离被测行为）。"""
        baseline = {"accelerometer_rotation": "1", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        t = self._mk(baseline, dict(baseline), env_ignore=env_ignore)
        t._rotation_baseline = rot_start
        t.device_rotation = lambda: rot_end
        return t

    def _finish(self, t):
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
        finally:
            tf.LAST_CASE = old_last
        return " ".join(r["detail"] for s in t.steps for r in s["results"])

    def test_rotation_change_warns_even_with_env_ignore(self):
        """方向变化**不被 env_ignore 抑制** —— 178 正是这么把它静音掉的。

        env_ignore 的语义是"用例自己合法改了环境"，不能掩盖"环境被外部
        改写"（App 冷启动把 accelerometer_rotation 改回 1）。"""
        t = self._mk_rot(0, 1, env_ignore=("accelerometer_rotation",))
        details = self._finish(t)
        self.assertEqual(t.final_status, "WARN")
        self.assertIn("方向变化", details)

    def test_rotation_restored_stays_pass(self):
        """转屏后**还原**的用例不该报（snapshot/restore 成对）—— 起始==结束。"""
        t = self._mk_rot(1, 1)
        self._finish(t)
        self.assertEqual(t.final_status, "PASS")

    def test_rotation_unknown_does_not_warn(self):
        """探测不到（None）不报 —— 不能把"未知"当"变了"。"""
        t = self._mk_rot(None, 1)
        self._finish(t)
        self.assertEqual(t.final_status, "PASS")

    def test_env_ignore_prevents_drift_warn(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["user_rotation"] = "1"   # 合法操作（横屏用例）
        t = self._mk(baseline, after, env_ignore=("user_rotation",))
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "PASS")
        finally:
            tf.LAST_CASE = old_last

    def test_drift_skipped_on_error(self):
        """ERROR 状态不做漂移检测（用例没跑完，环境不可信）。"""
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"
        t = self._mk(baseline, after)
        t._fatal_error = RuntimeError("模拟异常")
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "ERROR")
            # ERROR 路径不应添加漂移 WARN 记录
            warn_records = [r for s in t.steps for r in s["results"]
                           if r["result"] == "WARN" and "污染" in r["detail"]]
            self.assertEqual(len(warn_records), 0)
        finally:
            tf.LAST_CASE = old_last

    def test_fail_not_downgraded_by_drift(self):
        """FAIL 不被漂移检测覆盖（漂移只影响 PASS 用例）。"""
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"
        t = self._mk(baseline, after)
        # 覆盖步骤为 FAIL
        t.steps = [{"name": "s", "results":
                    [{"result": "FAIL", "detail": "断言失败"}],
                    "evidences": []}]
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "FAIL")
        finally:
            tf.LAST_CASE = old_last


# ── scripts/check_context_budget.py：上下文预算门禁 ────────────
class TestContextBudget(unittest.TestCase):
    """临时目录造超限/达标文件，验证 check() 返回值。"""

    def _setup_root(self, files):
        """files: {rel_path: line_count}"""
        td = tempfile.TemporaryDirectory()
        for rel, count in files.items():
            fp = os.path.join(td.name, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write("\n".join([f"line {i}" for i in range(count)]))
        return td

    def test_over_limit_returns_error(self):
        """超限文件 → errors 非空。"""
        td = self._setup_root({"SKILL.md": 300})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 1)
            self.assertIn("超限", errors[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_within_limit_no_error(self):
        """达标文件 → errors 为空。"""
        td = self._setup_root({"SKILL.md": 50})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 0)
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_warn_at_80_percent(self):
        """达 80% 预算 → warnings 非空但 errors 为空。"""
        td = self._setup_root({"SKILL.md": 85})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 0)
            self.assertEqual(len(warnings), 1)
            self.assertIn("接近", warnings[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_glob_pattern(self):
        """glob 模式匹配多个文件。"""
        td = self._setup_root({
            "knowledge/a.md": 30,
            "knowledge/b.md": 500,
        })
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("knowledge/*.md", 400)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 1)   # b.md 超限
            self.assertIn("b.md", errors[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_missing_file_ignored(self):
        """预算规则中的文件不存在 → 不报错。"""
        td = tempfile.TemporaryDirectory()
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("nonexistent.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(errors, [])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_token_estimate(self):
        """token 估算：中文 + 英文混合。"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as f:
            f.write("这是中文测试 hello world test")
            path = f.name
        try:
            tok = budget_mod._token_estimate(path)
            # 6 中文 + 4 英文词 = 6 + 4*1.3 ≈ 11
            self.assertGreater(tok, 5)
            self.assertLess(tok, 20)
        finally:
            os.unlink(path)


# ── 3.2 VERSION + drift ─────────────────────────────────────────────────
class TestVersionInDrift(unittest.TestCase):
    """VERSION 纳入 _DRIFT_KEY_FILES，版本不一致时触发漂移告警。"""

    def test_version_in_drift_keys(self):
        self.assertIn("VERSION", run_case._DRIFT_KEY_FILES)

    def test_version_file_exists(self):
        """framework/VERSION 文件存在且内容合法（语义化版本 x.y.z）。"""
        ver_path = os.path.join(_ROOT, "framework", "VERSION")
        self.assertTrue(os.path.isfile(ver_path))
        with open(ver_path, encoding="utf-8") as f:
            ver = f.read().strip()
        # 简单语义化版本校验
        self.assertRegex(ver, r"^\d+\.\d+\.\d+")


# ── 3.4 _is_loopback ──────────────────────────────────────────────────
class TestIsLoopback(unittest.TestCase):
    def test_loopback_addresses(self):
        for addr in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(webui._is_loopback(addr), addr)

    def test_non_loopback_addresses(self):
        for addr in ("0.0.0.0", "192.168.1.1", "10.0.0.5", ""):
            self.assertFalse(webui._is_loopback(addr), addr)


# ── 3.3 smoke._check_adb ────────────────────────────────────────────────
class TestSmokeCheckAdb(unittest.TestCase):
    """smoke._check_adb() 的 mock 测试（不依赖真实设备）。"""

    def test_adb_not_found(self):
        """adb 不在 PATH → 返回 False + 引导文案。"""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            ok, msg = smoke._check_adb()
        self.assertFalse(ok)
        self.assertIn("adb", msg)

    def test_no_device(self):
        """adb 在但无设备 → 返回 False。"""
        mock_result = mock.Mock()
        mock_result.stdout = "List of devices attached\n\n"
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, msg = smoke._check_adb()
        self.assertFalse(ok)
        self.assertIn("设备", msg)

    def test_device_found(self):
        """有授权设备 → 返回 True。"""
        mock_result = mock.Mock()
        mock_result.stdout = "List of devices attached\nABCDEF123\tdevice\n"
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, msg = smoke._check_adb()
        self.assertTrue(ok)
        self.assertIn("ABCDEF123", msg)


# ── 阶段四：record() RESULT_TYPES 枚举 ──────────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRecordResultTypes(unittest.TestCase):
    """record() 非标准结果类型抛 ValueError（拼写错误在开发期即暴露）。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._cur_step = None
        t.steps = []
        t._db = None
        t._db_step_id = None
        t._db_step_ord = 0
        t._wd_enabled = False
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        return t

    def test_valid_types_no_error(self):
        """标准结果类型不抛异常。"""
        for rt in ("PASS", "FAIL", "WARN", "INFO", "BLOCKED"):
            t = self._mk()
            with contextlib.redirect_stdout(io.StringIO()):
                t.record(rt, f"test {rt}")   # 不应抛

    def test_invalid_type_raises(self):
        """拼写错误 → ValueError。"""
        t = self._mk()
        with self.assertRaises(ValueError) as cm:
            t.record("PASSS", "typo")
        self.assertIn("PASSS", str(cm.exception))

    def test_lowercase_raises(self):
        """小写也不行。"""
        t = self._mk()
        with self.assertRaises(ValueError):
            t.record("pass", "lowercase")


# ── 阶段四：finish() 报告文件名清洗 ──────────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestFinishSafeName(unittest.TestCase):
    """用例名含文件系统非法字符时，finish() 报告文件名用 re.sub 清洗。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t.steps = [{"name": "s1", "results": [{"result": "PASS", "detail": "ok",
                     "state": None, "evidence": None}], "evidences": [], "actions": []}]
        t._cur_step = t.steps[0]
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        t._env_baseline = None
        t._env_ignore = ()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._finished = False
        t._report_path = None
        t.device_info = "fake"
        t.case_dir = tempfile.mkdtemp()
        t.script_path = ""
        t.states = None
        t._cleanups = []
        t._cleanups_ran = False
        return t

    def test_colon_in_name(self):
        """用例名含 : → 替换为 _，报告正常生成。"""
        t = self._mk()
        t.name = "用例:带冒号"
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()) as rd:
                path = t.finish()
            self.assertNotIn(":", os.path.basename(path))
            self.assertTrue(os.path.isfile(path))
        finally:
            tf.LAST_CASE = old_last

    def test_slash_in_name(self):
        """用例名含 / → 替换为 _。"""
        t = self._mk()
        t.name = "用例/带斜杠"
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                path = t.finish()
            self.assertNotIn("/", os.path.basename(path).replace("_报告.md", ""))
            self.assertTrue(os.path.isfile(path))
        finally:
            tf.LAST_CASE = old_last


# ── 阶段四：already_finished 分支（run_case.py L288-301）─────────
class TestAlreadyFinishedBranch(unittest.TestCase):
    """用例正常 finish 后收尾代码再抛异常时，退出码沿用 final_status 映射而非 3。"""

    def test_exit_code_uses_final_status(self):
        """already_finished=True 时 exit_code_for(final_status) 而非 3。"""
        # PASS 用例的 already_finished 分支应返回 0
        self.assertEqual(run_case.exit_code_for("PASS"), 0)
        self.assertEqual(run_case.exit_code_for("WARN"), 0)
        # FAIL 用例应返回 1
        self.assertEqual(run_case.exit_code_for("FAIL"), 1)

    def test_already_finished_logic_in_run_case(self):
        """模拟 run_case.py 的 already_finished 判定逻辑。"""
        # 模拟：tc._finished = True, final_status = "PASS"
        class FakeTC:
            _finished = True
            final_status = "PASS"
        tc = FakeTC()
        already_finished = bool(tc is not None and getattr(tc, "_finished", False))
        self.assertTrue(already_finished)
        code = run_case.exit_code_for(getattr(tc, "final_status", None))
        self.assertEqual(code, 0)   # PASS → 0，不是 3


# ── 阶段四：VISION_CONF_FILE 惰性求值 ──────────────────────────
class TestVisionConfLazyEval(unittest.TestCase):
    """import vision 后改环境变量，_vision_conf_path() 应反映新路径。"""

    def test_lazy_eval_after_env_change(self):
        old = os.environ.get("DSH_WORKSPACE_DIR")
        try:
            os.environ["DSH_WORKSPACE_DIR"] = "/tmp/test_ws_1"
            p1 = vision._vision_conf_path()
            self.assertIn("test_ws_1", p1)

            os.environ["DSH_WORKSPACE_DIR"] = "/tmp/test_ws_2"
            p2 = vision._vision_conf_path()
            self.assertIn("test_ws_2", p2)
            self.assertNotEqual(p1, p2)
        finally:
            if old is None:
                os.environ.pop("DSH_WORKSPACE_DIR", None)
            else:
                os.environ["DSH_WORKSPACE_DIR"] = old


# ── 阶段四：学习词表 (mtime, words) 缓存 ──────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestDialogWordsCache(unittest.TestCase):
    """\u005f_load_learned_words 带 (mtime, words) 缓存，文件未变时不重读。"""

    def test_cache_hit_same_mtime(self):
        """同一文件连续两次调用，只读一次磁盘。"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "dialog_words.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"guide": ["ok1"], "allow": [], "deny": []}, f)
            # 指到临时文件
            old_file = tf.LEARNED_WORDS_FILE
            old_cache = tf._dialog_words_cache.copy()
            tf.LEARNED_WORDS_FILE = fp
            tf._dialog_words_cache = {"mtime": None, "words": None}
            try:
                t = object.__new__(tf.TestCase)
                w1 = t._load_learned_words()
                self.assertEqual(w1["guide"], ["ok1"])
                # 缓存已填充
                self.assertIsNotNone(tf._dialog_words_cache["mtime"])
                # 第二次调用应从缓存返回
                w2 = t._load_learned_words()
                self.assertEqual(w2["guide"], ["ok1"])
            finally:
                tf.LEARNED_WORDS_FILE = old_file
                tf._dialog_words_cache = old_cache

    def test_cache_invalidate_on_file_change(self):
        """文件 mtime 变化后缓存失效，重新读取。"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "dialog_words.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"guide": ["old"], "allow": [], "deny": []}, f)
            old_file = tf.LEARNED_WORDS_FILE
            old_cache = tf._dialog_words_cache.copy()
            tf.LEARNED_WORDS_FILE = fp
            tf._dialog_words_cache = {"mtime": None, "words": None}
            try:
                t = object.__new__(tf.TestCase)
                w1 = t._load_learned_words()
                self.assertEqual(w1["guide"], ["old"])
                # 改文件
                time.sleep(0.05)  # 确保 mtime 变化
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump({"guide": ["new"], "allow": [], "deny": []}, f)
                w2 = t._load_learned_words()
                self.assertEqual(w2["guide"], ["new"])
            finally:
                tf.LEARNED_WORDS_FILE = old_file
                tf._dialog_words_cache = old_cache


# ── 阶段四：结构化日志 _setup_logging ─────────────────────────
class TestSetupLogging(unittest.TestCase):
    """run_case._setup_logging() 创建日志文件并写入日志。"""

    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as td:
            ws = os.path.join(td, "ws")
            os.makedirs(os.path.join(ws, "storage"))
            old = os.environ.get("DSH_WORKSPACE_DIR")
            os.environ["DSH_WORKSPACE_DIR"] = ws
            try:
                log_path = run_case._setup_logging()
                self.assertTrue(os.path.isfile(log_path))
                self.assertIn("run_", os.path.basename(log_path))
                # 日志文件含至少一行日志
                with open(log_path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("日志文件", content)
            finally:
                if old is None:
                    os.environ.pop("DSH_WORKSPACE_DIR", None)
                else:
                    os.environ["DSH_WORKSPACE_DIR"] = old
                # 清理 logging handler 避免污染其它测试
                import logging
                for h in logging.getLogger().handlers[:]:
                    if isinstance(h, logging.FileHandler):
                        h.close()
                        logging.getLogger().removeHandler(h)


# ── db.py: list_cases 基本查询 ────────────────────────────
class TestListCasesBasic(unittest.TestCase):
    """list_cases 基本查询（迭代清理由 start_case mtime 检测处理）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._td.name, "test.db")
        self.rec = db.RecordDB(self.db_path)

    def tearDown(self):
        self.rec.close()
        self._td.cleanup()

    def _insert(self, name, sp, status="PASS"):
        cid = self.rec.start_case(name, device="test", script_path=sp)
        self.rec.finish_case(cid, f"/tmp/{name}_报告.md", f"1 通过 / 0 失败",
                             final_status=status)
        return cid

    def test_returns_all_records(self):
        self._insert("app_1", "/cases/com.a/1.py", "FAIL")
        self._insert("app_1", "/cases/com.a/1.py", "PASS")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2)

    def test_no_script_path_kept(self):
        self._insert("manual_1", None, "PASS")
        self._insert("manual_2", None, "FAIL")
        recs = self.rec.list_cases()
        self.assertEqual(len(recs), 2)


# ── db.py: 迭代清理（mtime 信号）──────────────────────────────
class TestIterativeCleanup(unittest.TestCase):
    """cleanup_iterated_cases() 基于脚本 mtime 清理迭代旧记录（判定规则）。

    ⚠️ 调用时机（2026-09-15 改）：由 `finish_case` **之后**调用，不再在 `start_case`
    里 —— 在 start_case 里清 = 用例一开始就删掉上次记录 + 报告 + 截图，本次被杀就
    两边都不剩。本类只覆盖判定规则；"必须在 finish 之后"由
    TestRecordCleanupTiming 用行为 + 源码级守卫兜住。
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._td.name, "test.db")
        self.rec = db.RecordDB(self.db_path)
        # 创建临时脚本文件（mtime 可控）
        self.script = os.path.join(self._td.name, "test_case.py")
        with open(self.script, "w") as f:
            f.write("# v1\n")

    def tearDown(self):
        self.rec.close()
        self._td.cleanup()

    def _run(self, status="PASS", device="dev1", suite_id=None, script=None):
        """跑一次完整生命周期：start → finish → 迭代清理（模拟 finish() 的顺序）。"""
        sp = script or self.script
        cid = self.rec.start_case("test", device=device, script_path=sp,
                                  suite_id=suite_id)
        self.rec.finish_case(cid, f"/tmp/test_{cid}_报告.md",
                             "1 通过 / 0 失败", final_status=status)
        self.rec.cleanup_iterated_cases(sp, device, cid)
        return cid

    def test_iterate_deletes_old(self):
        """脚本被改过 → 旧记录是迭代 → 清掉（但必须发生在本轮跑完之后）。"""
        cid1 = self._run("FAIL")   # 第 1 次
        # 模拟 Agent 改脚本
        time.sleep(1.1)  # 确保 mtime 差异（精度 1s）
        with open(self.script, "w") as f:
            f.write("# v2\n")
        # 第 2 次"建档"时不许清：此刻被打断也要保住 cid1
        cid2 = self.rec.start_case("test", device="dev1", script_path=self.script)
        self.assertIn(cid1, [r["id"] for r in self.rec.list_cases()],
                      "start_case 不许清旧记录（时机已挪到 finish 之后）")
        self.rec.finish_case(cid2, f"/tmp/test_{cid2}_报告.md",
                             "1 通过 / 0 失败", final_status="PASS")
        self.rec.cleanup_iterated_cases(self.script, "dev1", cid2)
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 1, "跑完后应只留 1 条")
        self.assertEqual(all_recs[0]["status"], "PASS")

    def test_no_change_keeps_old(self):
        """脚本没改 → 有意复跑 → 保留。"""
        self._run("PASS")          # 第 1 次
        time.sleep(0.5)
        self._run("PASS")          # 第 2 次，脚本没动
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "脚本没改应保留全部")

    def test_different_device_keeps(self):
        """换设备 → 保留。"""
        self._run("PASS", device="devA")
        time.sleep(1.1)
        with open(self.script, "w") as f:
            f.write("# v2\n")
        self._run("PASS", device="devB")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "换设备应保留")

    def test_suite_id_keeps(self):
        """套件记录 → 保留（suite_id 不为 NULL 不清理）。"""
        cid1 = self.rec.start_case("test", device="dev1", script_path=self.script,
                                   suite_id=42)
        self.rec.finish_case(cid1, "/tmp/r.md", "1/0", final_status="FAIL")
        time.sleep(1.1)
        with open(self.script, "w") as f:
            f.write("# v2\n")
        cid2 = self.rec.start_case("test", device="dev1", script_path=self.script,
                                   suite_id=43)
        self.rec.finish_case(cid2, "/tmp/r.md", "1/0", final_status="PASS")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "套件记录应保留")

    def test_script_missing_no_cleanup(self):
        """脚本不存在 → 不清理（安全降级）。"""
        fake = os.path.join(self._td.name, "nonexistent.py")
        self._run("FAIL", script=fake)
        self._run("PASS", script=fake)
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "脚本不存在时不应清理")

    def test_multiple_iterations_keep_only_latest(self):
        """连续 5 次迭代，始终只留最新一条。"""
        for i in range(5):
            time.sleep(1.1)
            with open(self.script, "w") as f:
                f.write(f"# v{i+1}\n")
            status = "FAIL" if i < 4 else "PASS"
            self._run(status)
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 1)
        self.assertEqual(all_recs[0]["status"], "PASS")


# ── P1-9：执行期缓存守卫（cached_* 在正式用例中必须 record FAIL + raise）──
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestExecTimeCacheGuard(unittest.TestCase):
    """执行期误用探查缓存 = 拿过期数据当结论 = 假 PASS，必须 raise 阻断。

    回归保护：cached_ocr / cached_dump 曾完全无守卫。当前 cases/ 里调用数为 0，
    但"框架没有这条边界"本身就是缺口（plan/gaps-and-roadmap.md 缺口 3）——
    防的是下一个把 cached_* 写进正式用例的人。
    """

    def _mk(self, *, user_input="用这个skills执行测试用例 联想日历_178",
            name="联想日历_178", in_probe=False):
        t = object.__new__(tf.TestCase)
        t.name = name
        t.user_input = user_input
        t._in_probe_page = in_probe
        t.record = mock.Mock()
        return t

    def test_error_is_case_abort(self):
        """必须继承 CaseAbort：走 FAIL(1) 路径，而不是 RuntimeError → ERROR(3)。"""
        self.assertTrue(issubclass(tf.ExecutionTimeCacheError, tf.CaseAbort))

    def test_cached_ocr_in_formal_case_raises(self):
        t = self._mk()
        with self.assertRaises(tf.ExecutionTimeCacheError):
            t._guard_exec_cache("cached_ocr")
        # 必须**先 record("FAIL") 再 raise**：只 raise 不 record 的话 finish() 会按
        # "目前所有断言都 PASS" 算出 PASS，而退出码是 1 —— 报告与退出码打架。
        t.record.assert_called_once()
        self.assertEqual(t.record.call_args[0][0], "FAIL")
        self.assertIn("cached_ocr", t.record.call_args[0][1])

    def test_cached_dump_in_formal_case_raises(self):
        t = self._mk()
        with self.assertRaises(tf.ExecutionTimeCacheError):
            t._guard_exec_cache("cached_dump")
        self.assertEqual(t.record.call_args[0][0], "FAIL")

    def test_probe_page_exempt(self):
        """probe_page 免检：它是"给正式用例里临时探一下的兜底"，本就允许执行期调用。"""
        t = self._mk(in_probe=True)
        self.assertIsNone(t._guard_exec_cache("cached_dump"))
        t.record.assert_not_called()

    def test_aux_script_not_guarded(self):
        """辅助脚本（无 USER_INPUT）不拦 —— 探查/补采/备数据全靠 cached_*。"""
        t = self._mk(user_input=None)
        self.assertIsNone(t._guard_exec_cache("cached_ocr"))
        t.record.assert_not_called()

    def test_blank_user_input_not_guarded(self):
        t = self._mk(user_input="   ")
        self.assertIsNone(t._guard_exec_cache("cached_ocr"))
        t.record.assert_not_called()

    def test_cached_dump_delegates_to_guard(self):
        """cached_dump 进入即被拦（不必真走到读缓存文件那一步）。"""
        t = self._mk()
        with self.assertRaises(tf.ExecutionTimeCacheError):
            t.cached_dump("某页面")

    def test_cached_ocr_delegates_to_guard(self):
        t = self._mk()
        with self.assertRaises(tf.ExecutionTimeCacheError):
            t.cached_ocr("某页面")


# ── P1-5：ocr() 指定区域读空 → WARN（把「静默降级」变可见）────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestOcrEmptyRegionWarns(unittest.TestCase):
    """设备相关硬编码最典型的后果：OCR 在写死的区域里读空 → 旧实现静默返回 []。

    活样本 175.py:92 的 t.ocr(DLG_TOP, DLG_BOTTOM)：其 docstring 自述"只作 INFO
    参考"，于是换设备后读空也无人可见。本测试锁住"读空必须可见"这条契约。
    """

    def _mk(self, texts=()):
        from PIL import Image
        t = object.__new__(tf.TestCase)
        # 1600×1600 → ocr() 内部缩放系数 s = 1600/max(w,h) = 1.0，
        # 假 OCR 返回的坐标即最终坐标，测试里可直接按像素写。
        if texts:
            box = [[0, 150], [10, 150], [10, 160], [0, 160]]   # 中心 y≈155
            t._ocr = lambda arr: ([(box, texts[0], 0.99)], None)
        else:
            t._ocr = lambda arr: ([], None)
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1600), "white").save(buf, format="PNG")
        t._png = buf.getvalue()
        t._screencap_bytes = lambda: t._png
        t.record = mock.Mock()
        return t

    def test_full_screen_no_warn(self):
        """t.ocr() 全屏读空 → 不告警（只有"写死范围"才值得告警）。"""
        t = self._mk()
        self.assertEqual(t.ocr(), [])
        t.record.assert_not_called()

    def test_empty_custom_region_warns(self):
        t = self._mk()
        self.assertEqual(t.ocr(100, 200), [])
        t.record.assert_called_once()
        self.assertEqual(t.record.call_args[0][0], "WARN")

    def test_frozen_frame_not_warned(self):
        """image_bytes 非空 = capture_toast 在读已定格的帧，读空是合法结果，不告警。"""
        t = self._mk()
        self.assertEqual(t.ocr(100, 200, image_bytes=t._png), [])
        t.record.assert_not_called()

    def test_nonempty_custom_region_no_warn(self):
        t = self._mk(texts=("50",))
        self.assertEqual(len(t.ocr(100, 200)), 1)
        t.record.assert_not_called()


# ── 记录清理的调用时机：必须在 finish()，不能在建档路径上 ──────────────
class TestRecordCleanupTiming(unittest.TestCase):
    """旧记录（含报告/截图）只能在**本次记录完整落库之后**才删。

    两处清理同一原则（2026-09-15 改）：
      ① `drop_previous_cases`    —— 原在 `TestCase.__init__`
      ② `cleanup_iterated_cases` —— 原在 `RecordDB.start_case`
    在"开始"处删 = 用例一开跑就销毁上次的好记录 + 报告 + 截图；本次若被杀
    （套件超时 / Ctrl-C / 断连 / 用例崩）→ 旧记录与新记录**两边都不剩**。
    实测代价：一次被中断的套件把 168-177 共 8 个用例清成空壳；178 的 09-11 基线
    也这样丢过一次（备份早于实战，救不回来）。
    """

    # 调用点守卫用的字面量：一律带 `self.` 调用前缀，注释里只写方法名不会命中
    CALL_DROP = "self._db.drop_previous_cases("
    CALL_ITER = "self._db.cleanup_iterated_cases("   # test_framework.finish() 里
    CALL_ITER_DB = "self.cleanup_iterated_cases("    # RecordDB 内部若调用即此形式

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rec = db.RecordDB(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        import shutil
        self.rec.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _done_record(self, name="联想日历_178", sp="/w/cases/178.py"):
        """造一条"跑完了"的记录。"""
        cid = self.rec.start_case(name, "dev-1", user_input="用例", script_path=sp)
        self.rec.finish_case(cid, "/w/reports/178_报告.md", "ok",
                             final_status="PASS", package="com.zui.calendar")
        return cid

    def _ids(self):
        return [r[0] for r in self.rec._connect().execute(
            "SELECT id FROM cases ORDER BY id")]

    def test_start_case_keeps_previous(self):
        """建档**不**清旧记录 —— 中断时旧记录必须还在（本次回归的核心）。"""
        old = self._done_record()
        new = self.rec.start_case("联想日历_178", "dev-1", user_input="用例",
                                  script_path="/w/cases/178.py")
        self.assertEqual(self._ids(), [old, new])
        self.assertEqual(self.rec.get_case(old)["final_status"], "PASS",
                         "旧记录仍是完整态，没被本次的 start_case 动过")

    def test_drop_after_finish_keeps_newest_only(self):
        """跑完再删：原目标不变 —— 只剩最新一条。"""
        old = self._done_record()
        new = self._done_record()
        self.rec.drop_previous_cases("联想日历_178", "/w/cases/178.py", keep_id=new)
        self.assertEqual(self._ids(), [new])
        self.assertNotIn(old, self._ids())

    def test_iterated_cleanup_moved_to_finish(self):
        """脚本被改过 → 旧记录是探索噪音；但必须"跑完才清"（A 方案）。

        改前它在 `start_case` 里清：改完脚本单跑一次（最常见的开发节奏），
        一开始就把旧记录 + 报告 + 截图删掉，本次中断 → 两边都不剩。
        """
        sp = os.path.join(self.tmp, "178.py")
        with open(sp, "w", encoding="utf-8") as f:
            f.write("# v1\n")
        old = self._done_record(sp=sp)
        time.sleep(1.1)                 # 比较是秒级：确保改脚本后 mtime 前进
        with open(sp, "w", encoding="utf-8") as f:
            f.write("# v2\n")           # 脚本被改过 → 旧记录成为探索噪音

        new = self.rec.start_case("联想日历_178", "dev-1", user_input="用例",
                                  script_path=sp)
        self.assertIn(old, self._ids(), "建档不许清：中断时旧记录必须留得住")

        self.rec.finish_case(new, "/w/reports/178_报告.md", "ok",
                             final_status="PASS", package="com.zui.calendar")
        self.assertEqual(self.rec.cleanup_iterated_cases(sp, "dev-1", new), 1)
        self.assertEqual(self._ids(), [new])

    def test_iterated_cleanup_keeps_suite_records(self):
        """套件记录与"本次是套件跑"两种情况都不动探索记录。"""
        sp = os.path.join(self.tmp, "178.py")
        with open(sp, "w", encoding="utf-8") as f:
            f.write("# v1\n")
        suite = self.rec.start_case("套件_178", "dev-1", user_input="用例",
                                    script_path=sp, suite_id=7)
        self.rec.finish_case(suite, "/w/s.md", "ok", final_status="PASS")
        time.sleep(1.1)
        with open(sp, "w", encoding="utf-8") as f:
            f.write("# v2\n")
        new = self.rec.start_case("联想日历_178", "dev-1", user_input="用例",
                                  script_path=sp)
        self.rec.finish_case(new, "/w/r.md", "ok", final_status="PASS")
        # 本次（exclude_id）本身是套件记录 → 完全不清
        self.assertEqual(self.rec.cleanup_iterated_cases(sp, "dev-1", suite), 0)
        # 本次非套件 → 可清，但套件记录（suite_id 非空）不在清理范围内
        self.assertEqual(self.rec.cleanup_iterated_cases(sp, "dev-1", new), 0)
        self.assertIn(suite, self._ids())

    def test_cleanup_keeps_current_report(self):
        """旧记录的"同前缀报告备份"清理不许删掉**本次刚写的**报告。

        报告名 `<name>_报告.md` 是**共享**的（finish() 的既定语义：重跑覆盖），
        而该清理按名字前缀扫目录 —— 一旦挪到 finish() 之后，旧记录的产物清理
        就会把本次报告一起删掉。2026-09-15 实测：183 记录在、报告消失（连
        时间戳备份一起，0 个残留）。
        """
        rdir = os.path.join(db.default_test_dir(), "storage", "reports")
        os.makedirs(rdir, exist_ok=True)
        name = "_unittest_清理守卫"
        rp = os.path.join(rdir, f"{name}_报告.md")
        bak = os.path.join(rdir, f"{name}_20260101_000000_报告.md")
        try:
            old = self.rec.start_case(name, "dev-1", script_path="/w/x.py")
            self.rec.finish_case(old, rp, "old", final_status="FAIL")
            with open(bak, "w", encoding="utf-8") as f:
                f.write("old backup\n")          # 旧记录留下的时间戳备份

            new = self.rec.start_case(name, "dev-1", script_path="/w/x.py")
            self.rec.finish_case(new, rp, "new", final_status="PASS")
            with open(rp, "w", encoding="utf-8") as f:
                f.write("current report\n")      # 本次刚写的报告

            self.rec.drop_previous_cases(name, "/w/x.py", keep_id=new)
            self.assertTrue(os.path.exists(rp),
                            "本次刚写的报告不许被旧记录的产物清理删掉")
            self.assertFalse(os.path.exists(bak),
                             "旧记录自己的时间戳备份仍应清掉")
        finally:
            for p in (rp, bak):
                if os.path.exists(p):
                    os.remove(p)

    def test_call_site_is_finish_not_init(self):
        """调用点守卫：两处清理都必须在 `finish()` 之后，不许留在建档路径上。

        时机没有对外钩子，只能做**源码级**断言 —— 把"时机回归"从一次数据事故
        降级成一条单测红。
        """
        with open(os.path.join(_ROOT, "framework", "test_framework.py"),
                  encoding="utf-8-sig") as f:
            src = f.read()
        head, sep, tail = src.partition("def finish(")
        self.assertTrue(sep, "在 test_framework.py 里找不到 def finish(")
        for call in (self.CALL_DROP, self.CALL_ITER):
            self.assertNotIn(
                call, head,
                f"{call} 不许在 __init__ 侧调用：用例一开始就销毁上次记录 + 报告 + 截图")
            self.assertIn(
                call, tail,
                f"{call} 必须在 finish() 里调用：跑完才用新记录替换旧记录")

        # db.py 侧：迭代清理不许退回 start_case（那是"开始即销毁"的老位置）
        with open(os.path.join(_ROOT, "framework", "db.py"),
                  encoding="utf-8-sig") as f:
            db_src = f.read()
        body = db_src.split("def start_case(", 1)[1].split("\n    def ", 1)[0]
        self.assertNotIn(self.CALL_ITER_DB, body,
                         "cleanup_iterated_cases 不许在 start_case 里调用")


# ── P1-6：case_metrics —— duration_sec 是唯一性能口径 ──────────────
class TestCaseMetrics(unittest.TestCase):
    """度量落 append-only 表：cases 被 drop_previous_cases 删旧行 → 没趋势。

    这正是本表存在的理由：178 的 09-11 记录已被下一次运行连同报告一起删掉，
    "改前 vs 改后"只能靠这里累积。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rec = db.RecordDB(os.path.join(self.tmp, "m.db"))

    def tearDown(self):
        import shutil
        self.rec.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        h = self.rec.health_check()
        self.assertEqual(h["samples"], 0)
        self.assertEqual(h["duration_p90"], 0)

    def test_roundtrip_and_percentiles(self):
        for d in (10.0, 20.0, 30.0, 40.0):
            self.rec.record_metrics("/w/178.py", "dev-1", d,
                                    ocr=0, derived=58, dump=116)
        h = self.rec.health_check()
        self.assertEqual(h["samples"], 4)
        self.assertEqual(h["duration_p50"], 30.0)
        self.assertEqual(h["duration_p90"], 40.0)
        self.assertEqual(h["dump_p90"], 116)

    def test_none_script_path_skipped(self):
        """直跑临时脚本无 script_path → 跳过（列是 NOT NULL）。"""
        self.assertIsNone(self.rec.record_metrics(None, "dev", 1.0))
        self.assertEqual(self.rec.health_check()["samples"], 0)

    def test_accumulates_same_case(self):
        """核心价值：同一用例重复跑，度量累积而非被覆盖。"""
        self.rec.record_metrics("/w/178.py", "dev", 311.5)
        self.rec.record_metrics("/w/178.py", "dev", 205.0)
        self.assertEqual(self.rec.health_check()["samples"], 2)

    def test_rotation_columns_roundtrip(self):
        """方向列可写可读：起止不同 → rotation_changed 计数 +1。"""
        self.rec.record_metrics("/w/178.py", "dev", 311.5, rot_start=0, rot_end=1)
        h = self.rec.health_check()
        self.assertEqual(h["rotation_changed"], 1)
        row = self.rec._connect().execute(
            "SELECT rotation_start, rotation_end FROM case_metrics").fetchone()
        self.assertEqual(tuple(row), (0, 1))

    def test_rotation_same_not_counted(self):
        self.rec.record_metrics("/w/178.py", "dev", 1.0, rot_start=1, rot_end=1)
        self.assertEqual(self.rec.health_check()["rotation_changed"], 0)

    def test_rotation_none_not_counted(self):
        """None ≠ 0：探测失败 / 老库补列不能算成"方向变过"。"""
        self.rec.record_metrics("/w/a.py", "dev", 1.0, rot_start=None, rot_end=1)
        self.rec.record_metrics("/w/b.py", "dev", 1.0, rot_start=0, rot_end=None)
        self.rec.record_metrics("/w/c.py", "dev", 1.0)
        self.assertEqual(self.rec.health_check()["rotation_changed"], 0)

    def test_rotation_changed_counted_among_others(self):
        self.rec.record_metrics("/w/a.py", "dev", 1.0, rot_start=0, rot_end=1)
        self.rec.record_metrics("/w/b.py", "dev", 1.0, rot_start=1, rot_end=1)
        self.rec.record_metrics("/w/c.py", "dev", 1.0, rot_start=1, rot_end=0)
        h = self.rec.health_check()
        self.assertEqual(h["samples"], 3)
        self.assertEqual(h["rotation_changed"], 2)

    def test_started_at_is_iso_string(self):
        """started_at 必须是 ISO 字符串：float 会让 ORDER BY started_at 退化。"""
        self.rec.record_metrics("/w/178.py", "dev", 1.0)
        at = self.rec._connect().execute(
            "SELECT started_at FROM case_metrics").fetchone()[0]
        self.assertIsInstance(at, str)
        self.assertIn("-", at)


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestDeviceRotation(unittest.TestCase):
    """真实旋转探测：解析 `dumpsys window displays` + 语义映射。

    为什么必须有这条：方向一直是"看不见的变量"——178 实测锁了竖屏、冷启动把
    accelerometer_rotation 改回 1、全用例横屏跑完仍 PASS（坐标全部现场派生），
    而**没有任何地方记录过方向变过**。这个类把"看得见"补上。
    """

    # 真实输出片段（2026-09-14 实测，TB323FU / Android 17）
    SAMPLE = (
        "Display: mDisplayId=0 (organized)\n"
        "  init=1904x3040 440dpi mMinSizeOfResizeableTaskDp=220 "
        "cur=3040x1904 app=3040x1904 rng=1904x1904-3040x3040\n"
        "  overrideConfig={1.0 ?mcc0mnc [zh_CN_#Hans] winConfig={ "
        "mBounds=Rect(0, 0 - 3040, 1904) "
        "mDisplayRotation=ROTATION_90 mRotation=ROTATION_90}}\n"
        "  mRotation=1 mDeferredRotationPauseCount=0\n"
    )

    def test_parses_rotation_from_real_dump(self):
        m = tf.TestCase._ROT_RE.search(self.SAMPLE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "1")

    def test_does_not_match_word_form(self):
        """`mRotation=ROTATION_90`（winConfig 里的写法）不能被当成数字。"""
        self.assertIsNone(tf.TestCase._ROT_RE.search(
            "  overrideConfig={ mRotation=ROTATION_90}}"))

    def test_fallback_matches_standalone_line(self):
        out = "  mRotation=0\n"
        self.assertIsNone(tf.TestCase._ROT_RE.search(out))
        m = tf.TestCase._ROT_RE_FALLBACK.search(out)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "0")

    def test_rot_name_semantics(self):
        """0/2 竖、1/3 横 —— 与设备实测一致（accel=0/user=0 → mRotation=0 竖屏）。"""
        self.assertEqual(tf._rot_name(0), "竖屏")
        self.assertEqual(tf._rot_name(2), "竖屏(反向)")
        self.assertEqual(tf._rot_name(1), "横屏")
        self.assertEqual(tf._rot_name(3), "横屏(反向)")
        self.assertIn("9", tf._rot_name(9))

    def test_returns_none_when_adb_raises(self):
        """探测失败必须返回 None 而不是抛：收尾阶段绝不能被它中断。"""
        t = object.__new__(tf.TestCase)

        def _boom(*a, **k):
            raise RuntimeError("adb 挂了")

        t.adb_shell = _boom
        self.assertIsNone(t.device_rotation())

    def test_returns_none_on_empty_or_garbage(self):
        t = object.__new__(tf.TestCase)
        t.adb_shell = lambda *a, **k: ""
        self.assertIsNone(t.device_rotation())
        t.adb_shell = lambda *a, **k: "no rotation info here"
        self.assertIsNone(t.device_rotation())


# ── P0b-①：探查缓存清理阈值可配（DSH_PROBES_MAXIDLE_MIN，0=关闭）──────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestCleanupProbesTtl(unittest.TestCase):
    """cleanup_probes 的阈值来源与 0 语义。

    背景（plan/mechanism-over-prose-plan.md §八 P0b-①）：编辑用例时常要复用同一批
    probes 做离线预检（inventory.py verify），而 30 分钟会在写作途中把缓存清掉，
    逼着回真机重探 —— 这是"改一点要整跑"的直接成因之一。阈值改为可由
    DSH_PROBES_MAXIDLE_MIN 覆盖，与 traces 侧 DSH_TRACE_MAXIDLE_MIN 同一模式
    （trace_recorder.py:57-59）。

    本类最重要的一条是 test_zero_disables_and_keeps_files：旧实现把 0 直接当阈值
    （cutoff = now → **删光所有缓存**），与 traces 侧"设 0 可关闭"语义相反。
    锁住正确语义，防脚枪复发。
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dsh-probes-")
        self.addCleanup(tmp.cleanup)
        self._dir = tmp.name
        self._old_probe_dir = tf.PROBE_DIR
        tf.PROBE_DIR = self._dir
        self.addCleanup(self._restore)
        os.environ.pop("DSH_PROBES_MAXIDLE_MIN", None)

    def _restore(self):
        tf.PROBE_DIR = self._old_probe_dir
        os.environ.pop("DSH_PROBES_MAXIDLE_MIN", None)

    def _mk_probe(self, label, idle_min):
        """造一份 idle_min 分钟前访问过的缓存；返回其目录。"""
        d = os.path.join(self._dir, "com.example.app", label)
        os.makedirs(d, exist_ok=True)
        stamp = time.time() - idle_min * 60
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"label": label, "package": "com.example.app",
                       "accessed": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                 time.localtime(stamp))}, f)
        return d

    def test_zero_disables_and_keeps_files(self):
        """0 = 关闭清理（不是"删光"）——旧实现反语义脚枪的回归保护。"""
        d = self._mk_probe("超时很久的页", 999)
        self.assertEqual(tf.TestCase.cleanup_probes(max_idle_min=0,
                                                   verbose=False), (0, 0))
        self.assertTrue(os.path.isdir(d))

    def test_env_zero_disables(self):
        """环境变量 0 同样 = 关闭（与 traces 侧同语义）。"""
        d = self._mk_probe("超时很久的页", 999)
        os.environ["DSH_PROBES_MAXIDLE_MIN"] = "0"
        self.assertEqual(tf.TestCase.cleanup_probes(verbose=False), (0, 0))
        self.assertTrue(os.path.isdir(d))

    def test_env_is_read(self):
        """设了环境变量就按它清理（1 分钟 → 超时的被删）。"""
        d = self._mk_probe("超时很久的页", 999)
        os.environ["DSH_PROBES_MAXIDLE_MIN"] = "1"
        removed, kept = tf.TestCase.cleanup_probes(verbose=False)
        self.assertEqual((removed, kept), (1, 0))
        self.assertFalse(os.path.isdir(d))

    def test_explicit_arg_beats_env(self):
        """显式参数优先于环境变量（env 关闭、显式 60 → 仍清理）。"""
        d = self._mk_probe("超时很久的页", 999)
        os.environ["DSH_PROBES_MAXIDLE_MIN"] = "0"
        removed, _ = tf.TestCase.cleanup_probes(max_idle_min=60, verbose=False)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.isdir(d))

    def test_default_is_still_30(self):
        """不设环境变量 → 默认仍是 30 分钟，行为与历史一致。"""
        stale = self._mk_probe("30 分钟外的页", 999)
        fresh = self._mk_probe("1 分钟前的页", 1)
        removed, kept = tf.TestCase.cleanup_probes(verbose=False)
        self.assertEqual((removed, kept), (1, 1))
        self.assertFalse(os.path.isdir(stale))
        self.assertTrue(os.path.isdir(fresh))

    def test_invalid_env_falls_back_to_30(self):
        """环境变量非法值 → 回落 30（不静默跳过清理，也不抛）。"""
        d = self._mk_probe("超时很久的页", 999)
        os.environ["DSH_PROBES_MAXIDLE_MIN"] = "三十分钟"
        removed, _ = tf.TestCase.cleanup_probes(verbose=False)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.isdir(d))

    def test_missing_probe_dir_is_noop(self):
        tf.PROBE_DIR = os.path.join(self._dir, "不存在的目录")
        self.assertEqual(tf.TestCase.cleanup_probes(verbose=False), (0, 0))


# ── P0a：run_case 执行前静态守门（lint 拒跑 / check_facts 仅告警）────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRunGates(unittest.TestCase):
    """守门级别定义：lint ERROR → 拒跑；check_facts → 只告警；辅助脚本跳过。

    背景（plan/mechanism-over-prose-plan.md §八 P0a）：run_case 此前**零处**
    调用 lint / check_facts（静态守门形同虚设，只在 CI 里跑）。接入后必须保证：
    ① 正式用例的 lint ERROR 拦得住；
    ② 辅助脚本（`_` 前缀）不被规则 1（缺 USER_INPUT）误拦——它本就无该常量；
    ③ check_facts 不因"无语料"把干净用例判违规（probes 30 分钟即清是常态）。
    """

    _CLEAN = 'USER_INPUT = "跑一下"\n\n\ndef run():\n    pass\n'
    _BAD = ('USER_INPUT = "跑一下"\n\n\ndef run():\n    t = TestCase("x")\n'
            '    t.tap_xy(100, 200)\n')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dsh-gates-")
        self.addCleanup(tmp.cleanup)
        self._tmp = tmp.name
        self._old_skip = os.environ.pop("DSH_SKIP_GATES", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.pop("DSH_SKIP_GATES", None)
        if self._old_skip is not None:
            os.environ["DSH_SKIP_GATES"] = self._old_skip

    def _mk_case(self, name, body):
        p = os.path.join(self._tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def _storage(self):
        return os.path.join(self._tmp, "storage")

    def test_lint_error_blocks(self):
        """lint ERROR（裸坐标）→ 拒跑。"""
        p = self._mk_case("171.py", self._BAD)
        self.assertFalse(run_case.run_gates(p))

    def test_clean_case_passes(self):
        p = self._mk_case("172.py", self._CLEAN)
        self.assertTrue(run_case.run_gates(p))

    def test_aux_script_skipped(self):
        """辅助脚本（`_` 前缀）整段跳过：它本就无 USER_INPUT，规则 1 不适用。"""
        p = self._mk_case("_explore_172.py", "def run():\n    pass\n")
        self.assertTrue(run_case.run_gates(p))

    def test_skip_gates_env_escapes(self):
        """DSH_SKIP_GATES=1 → 即使有 ERROR 也放行（调试逃生口）。"""
        os.environ["DSH_SKIP_GATES"] = "1"
        p = self._mk_case("173.py", self._BAD)
        self.assertTrue(run_case.run_gates(p))

    def test_pkg_from_case_path(self):
        self.assertEqual(
            run_case._pkg_from_case_path("/x/cases/com.a.b/172.py"), "com.a.b")
        # 目录名不像包名 → None（守门据此不去猜语料位置）
        self.assertIsNone(run_case._pkg_from_case_path("/x/cases/随便/172.py"))

    def test_missing_corpus_is_not_a_violation(self):
        """无语料 → 不判违规（放行）—— check_facts 的正确语义。"""
        p = self._mk_case("174.py", self._CLEAN)
        with mock.patch.object(run_case, "_storage_dir",
                               return_value=self._storage()):
            self.assertFalse(run_case._case_has_corpus("com.a.b", "174"))
            self.assertTrue(run_case.run_gates(p, "com.a.b"))

    def test_empty_corpus_dir_is_not_corpus(self):
        """**per-case 判据**：目录存在但为空 ≠ 有语料。"""
        os.makedirs(os.path.join(self._storage(), "probes", "com.a.b"))
        with mock.patch.object(run_case, "_storage_dir",
                               return_value=self._storage()):
            self.assertFalse(run_case._case_has_corpus("com.a.b", "176"))

    def test_corpus_present_goes_through_check_facts(self):
        """有当次语料 → 走 check_facts 分支（空语料无害，仍放行）。"""
        d = os.path.join(self._storage(), "probes", "com.a.b", "首页")
        os.makedirs(d)
        with open(os.path.join(d, "dump.xml"), "w", encoding="utf-8") as f:
            f.write("<hierarchy/>")
        p = self._mk_case("175.py", self._CLEAN)
        with mock.patch.object(run_case, "_storage_dir",
                               return_value=self._storage()):
            self.assertTrue(run_case._case_has_corpus("com.a.b", "175"))
            self.assertTrue(run_case.run_gates(p, "com.a.b"))


# ── P0a：RunMetrics 采集器与落库（§7.1）─────────────────────────────
class TestRunMetrics(unittest.TestCase):
    """脚本 hash / sleep 静态求和 / rid 集合 hash 与差集 / 增量列落库。

    这是"变更归因"的地基（§10.2 决策表第一行就是脚本 hash）：没有它，
    "用例行为变了"只能怪到 App/配置头上，而实际多半是自己改脚本改出来的。
    """

    _CASE = '''
USER_INPUT = "x"
import time


def run():
    t = TestCase("x")
    time.sleep(1.5)
    time.sleep(2)
    w = 3
    time.sleep(w)          # 动态值：静态口径不计入（所以是"下界"）
    time.sleep(True)       # bool 不算数值（bool 是 int 子类）
'''

    def _mk(self, body):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(body)
        self.addCleanup(os.unlink, f.name)
        return f.name

    # ── sleep 静态求和（§7.2 M2 的分母口径）─────────────────────────
    def test_sleep_static_only_literals(self):
        """只累计数字字面量：1.5 + 2 = 3.5。"""
        self.assertEqual(
            run_metrics.sleep_static_seconds(self._mk(self._CASE)), 3.5)

    def test_sleep_static_no_sleep_is_zero(self):
        self.assertEqual(
            run_metrics.sleep_static_seconds(self._mk("x = 1\n")), 0.0)

    def test_sleep_static_unparsable_is_none(self):
        """解析失败 → None（**不返回 0**：0 会被当成"真的一点没等"）。"""
        self.assertIsNone(
            run_metrics.sleep_static_seconds(self._mk("def (: pass\n")))

    # ── 脚本 hash（变更归因第一排除项）──────────────────────────────
    def test_src_hash_changes_with_content(self):
        a = run_metrics.src_hash(self._mk("x = 1\n"))
        b = run_metrics.src_hash(self._mk("x = 2\n"))
        self.assertIsNotNone(a)
        self.assertNotEqual(a, b)

    def test_src_hash_missing_file_is_none(self):
        self.assertIsNone(run_metrics.src_hash("/不存在/172.py"))

    # ── rid 集合 hash / 差集 ────────────────────────────────────────
    def test_rid_set_hash_is_order_independent(self):
        """集合无序 → 必须先排序，否则同一页面每次 hash 都不同（假变化）。"""
        self.assertEqual(run_metrics.rid_set_hash(["b", "a", "c"]),
                         run_metrics.rid_set_hash(["c", "a", "b"]))

    def test_rid_set_hash_empty_is_none(self):
        self.assertIsNone(run_metrics.rid_set_hash([]))
        self.assertIsNone(run_metrics.rid_set_hash(None))

    def test_rid_diff(self):
        gone, new = run_metrics.rid_diff(["a", "b"], ["b", "c"])
        self.assertEqual(gone, ["a"])
        self.assertEqual(new, ["c"])

    def test_rid_diff_without_baseline_is_none(self):
        """无基线 → (None, None)，由调用方标"跳过对比"而非"未归因"（§10.2）。"""
        self.assertEqual(run_metrics.rid_diff(None, ["a"]), (None, None))
        self.assertEqual(run_metrics.rid_diff(["a"], None), (None, None))

    # ── 静态指标打包 / 环境变量传递 / 白名单 ────────────────────────
    def test_collect_static_passes_gate_counts(self):
        p = self._mk(self._CASE)
        m = run_metrics.collect_static(
            p, {"lint_errors": 2, "check_facts_suspects": 1, "checked_words": 5})
        self.assertEqual(m["sleep_static_sec"], 3.5)
        self.assertEqual(m["gate_lint_errors"], 2)
        self.assertEqual(m["gate_check_facts_suspects"], 1)
        self.assertEqual(m["gate_checked_words"], 5)
        self.assertIsNotNone(m["script_hash"])

    def test_static_env_roundtrip(self):
        old = os.environ.get(run_metrics.ENV_STATIC)
        self.addCleanup(lambda: os.environ.pop(run_metrics.ENV_STATIC, None)
                        if old is None else
                        os.environ.__setitem__(run_metrics.ENV_STATIC, old))
        run_metrics.dump_static({"script_hash": "h"})
        self.assertEqual(run_metrics.load_static()["script_hash"], "h")
        os.environ[run_metrics.ENV_STATIC] = "{坏 json"
        self.assertEqual(run_metrics.load_static(), {})   # 坏数据不炸收尾

    def test_sanitize_extra_drops_unknown_keys(self):
        """未知键必须丢掉：拼错的列名进 SQL 会 no such column → 整条记录丢失。"""
        out = run_metrics.sanitize_extra({"script_hash": "h", "bogus": 1})
        self.assertEqual(out, {"script_hash": "h"})

    # ── 落库 + 取基线（§7.1「基线必须不受保留策略影响」）────────────
    def test_metrics_extra_roundtrip_and_baseline(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        rdb = db.RecordDB(os.path.join(tmp, "t.db"))
        rdb.record_metrics("/x/172.py", "dev-1", 12.5, dump=3,
                           script_hash="abc", rid_set_hash="h1",
                           rid_set='["a","b"]', screenshots=7, wait_calls=2,
                           bogus_col="应该被忽略")
        m = rdb.latest_metrics("/x/172.py")
        self.assertEqual(m["script_hash"], "abc")
        self.assertEqual(m["rid_set_hash"], "h1")
        self.assertEqual(m["rid_set"], '["a","b"]')
        self.assertEqual(m["duration_sec"], 12.5)

    def test_latest_metrics_empty_when_no_history(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        rdb = db.RecordDB(os.path.join(tmp, "t.db"))
        self.assertEqual(rdb.latest_metrics("/x/never.py"), {})

    # ── M2 防作弊口径：sleep 挪进 _flow.py 不算数 ───────────────────
    def _pkg_dir(self, case_body, flow_body=None, lib=None):
        td = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, td, True)
        case = os.path.join(td, "178.py")
        with open(case, "w", encoding="utf-8") as f:
            f.write(case_body)
        if flow_body is not None:
            with open(os.path.join(td, "_flow.py"), "w", encoding="utf-8") as f:
                f.write(flow_body)
        if lib:
            os.makedirs(os.path.join(td, "_lib"))
            for name, body in lib.items():
                with open(os.path.join(td, "_lib", name), "w",
                          encoding="utf-8") as f:
                    f.write(body)
        return case

    def test_sleep_with_flow_counts_sibling_flow(self):
        """把 sleep 从用例挪进 `_flow.py` → case-only 变小、with_flow 不变。"""
        case = self._pkg_dir("import time\n\ndef run():\n    time.sleep(2)\n",
                             "import time\n\ndef goto(t):\n    time.sleep(3)\n")
        self.assertEqual(run_metrics.sleep_static_seconds(case), 2.0)
        self.assertEqual(run_metrics.sleep_seconds_with_flow(case), 5.0)

    def test_sleep_with_flow_counts_lib(self):
        case = self._pkg_dir("x = 1\n", lib={
            "helper.py": "import time\n\ndef h():\n    time.sleep(1.5)\n",
            "__init__.py": "",
        })
        names = [os.path.basename(p)
                 for p in run_metrics.sibling_aux_sources(case)]
        self.assertIn("helper.py", names)
        self.assertNotIn("__init__.py", names)   # dunder 不算源文件
        self.assertEqual(run_metrics.sleep_seconds_with_flow(case), 1.5)

    def test_sleep_with_flow_without_aux_is_case_only(self):
        case = self._pkg_dir("import time\n\ndef run():\n    time.sleep(4)\n")
        self.assertEqual(run_metrics.sleep_seconds_with_flow(case), 4.0)

    def test_sleep_with_flow_counts_cases_root_lib(self):
        """**实测布局**：共享库在 cases 根（`cases/_lib/`），不在包目录下。

        只查"用例同目录"会漏掉它 —— 那样把 sleep 挪进 `cases/_lib` 就能让
        M2 变绿。这条锁住两个位置都查。
        """
        td = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, td, True)
        pkg = os.path.join(td, "com.pkg")
        os.makedirs(pkg)
        case = os.path.join(pkg, "178.py")
        with open(case, "w", encoding="utf-8") as f:
            f.write("import time\n\ndef run():\n    time.sleep(1)\n")
        with open(os.path.join(pkg, "_flow.py"), "w", encoding="utf-8") as f:
            f.write("import time\n\ndef g(t):\n    time.sleep(2)\n")
        lib = os.path.join(td, "_lib")          # cases 根下的共享库
        os.makedirs(lib)
        with open(os.path.join(lib, "inventory.py"), "w",
                  encoding="utf-8") as f:
            f.write("import time\n\ndef i():\n    time.sleep(4)\n")
        names = [os.path.basename(p)
                 for p in run_metrics.sibling_aux_sources(case)]
        self.assertEqual(names, ["_flow.py", "inventory.py"])
        self.assertEqual(run_metrics.sleep_seconds_with_flow(case), 7.0)


# ── P0a：版本门禁 + 双信号源归因（§4.1 / §10.2）─────────────────────
class TestVersionGate(unittest.TestCase):
    """dumpsys 解析 / 知识卡验证版本 / 六类归因码 / WARN 判定。

    门禁的定位是"挂的时候消灭歧义"：只打 WARN、不废缓存。因此**误报比漏报更
    有害**（假信号会稀释真信号），本类重点锁"不该报的不报"：
    首次运行不报、改脚本不报、换设备不报（更不许当成配置变更报）。
    """

    _DUMPSYS = """Packages:
  Package [com.zui.calendar] (8f2a):
    versionCode=83 minSdk=28 targetSdk=33
    versionName=9.0.0.83
"""

    def _card_dir(self, pkg, body):
        td = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, td, True)
        with open(os.path.join(td, f"{pkg}.md"), "w", encoding="utf-8") as f:
            f.write(body)
        return td

    # ── dumpsys 解析 ───────────────────────────────────────────────
    def test_parse_version(self):
        self.assertEqual(version_gate.parse_version(self._DUMPSYS),
                         ("9.0.0.83", "83"))

    def test_parse_version_garbage_is_none(self):
        """解析不到就回 None —— 门禁宁可不判，也不猜一个版本号出来。"""
        self.assertEqual(version_gate.parse_version(""), (None, None))
        self.assertEqual(version_gate.parse_version("no version here"),
                         (None, None))

    # ── 知识卡「验证版本」 ─────────────────────────────────────────
    def test_read_card_version(self):
        d = self._card_dir("com.x", "# X\n\n- **app**: `com.x`｜"
                                    "**验证版本**: 1.2.3｜**最近验证**: 2026-09-14\n")
        self.assertEqual(version_gate.read_card_version([d], "com.x"), "1.2.3")

    def test_read_card_version_missing_field_is_none(self):
        """卡里没该字段 → None。**缺失 ≠ 失配**（5 张卡里只有 1 张有这个字段，
        把缺失当"版本变了"会造成大面积误报）。"""
        d = self._card_dir("com.y", "# Y\n\n没有版本字段的卡\n")
        self.assertIsNone(version_gate.read_card_version([d], "com.y"))
        self.assertIsNone(version_gate.read_card_version([d], "com.nothere"))
        self.assertIsNone(version_gate.read_card_version([], "com.y"))

    # ── 归因码（§10.2 决策表）──────────────────────────────────────
    def _prev(self, **kw):
        base = {"script_hash": "h1", "app_version_name": "9.0.0.83",
                "app_version_code": "83", "rid_set_hash": "r1", "device": "dev-a"}
        base.update(kw)
        return base

    def test_no_baseline(self):
        self.assertEqual(version_gate.classify({}, self._prev())[0], "no_baseline")

    def test_script_hash_wins(self):
        """脚本自己改过 → 归因 script，且**优先于**版本/指纹（最常见先排除）。"""
        code, _ = version_gate.classify(
            self._prev(), self._prev(script_hash="h2", app_version_name="10.0",
                                     rid_set_hash="r2"))
        self.assertEqual(code, "script")

    def test_apk_on_version_change(self):
        code, detail = version_gate.classify(
            self._prev(), self._prev(app_version_name="10.0.0.1",
                                     app_version_code="90"))
        self.assertEqual(code, "apk")
        self.assertIn("10.0.0.1", detail)

    def test_layout_beats_config(self):
        """换设备 → layout（**不许**报成 config）：换机时 rid 集合天然不同，
        若让 config 先命中，每次换机都会误报"服务端配置变更"。"""
        code, _ = version_gate.classify(
            self._prev(), self._prev(device="dev-b", rid_set_hash="r2"))
        self.assertEqual(code, "layout")

    def test_config_on_fingerprint_change_only(self):
        """版本与脚本都没变、界面变了 → config（服务端下发 / A-B）。"""
        code, detail = version_gate.classify(
            self._prev(), self._prev(rid_set_hash="r2"))
        self.assertEqual(code, "config")
        self.assertIn("版本未变但界面已变", detail)

    def test_unchanged(self):
        self.assertEqual(version_gate.classify(self._prev(), self._prev())[0],
                         "unchanged")

    # ── WARN 判定 ──────────────────────────────────────────────────
    def test_config_change_warns(self):
        lines, code = version_gate.gate_warn_lines(
            self._prev(), self._prev(rid_set_hash="r2"))
        self.assertEqual(code, "config")
        self.assertEqual(len(lines), 1)

    def test_script_change_does_not_warn(self):
        """改脚本不告警：那不是产品变更（SKILL.md：失败多为改脚本的中间态），
        给它 WARN 只会稀释真信号。"""
        lines, code = version_gate.gate_warn_lines(
            self._prev(), self._prev(script_hash="h2"))
        self.assertEqual(code, "script")
        self.assertEqual(lines, [])

    def test_unchanged_does_not_warn(self):
        lines, _ = version_gate.gate_warn_lines(self._prev(), self._prev())
        self.assertEqual(lines, [])

    def test_no_baseline_does_not_warn(self):
        lines, code = version_gate.gate_warn_lines({}, self._prev())
        self.assertEqual(code, "no_baseline")
        self.assertEqual(lines, [])

    def test_card_version_mismatch_warns_even_without_other_change(self):
        """与知识卡「验证版本」失配 → 单独告警（缓存是旧版本时期的）。"""
        lines, code = version_gate.gate_warn_lines(
            self._prev(), self._prev(), card_version="8.0.0.1")
        self.assertEqual(code, "unchanged")
        self.assertEqual(len(lines), 1)
        self.assertIn("8.0.0.1", lines[0])

    # ── 2026-09-16 真机实测补充的两条 ─────────────────────────────
    def test_norm_version_drops_build_suffix(self):
        """真机实测：卡里 `9.0.0.83`，真机 `9.0.0.83-2026.07.22-release`。

        逐字符比会在**每次运行**都报失配 —— 而假 WARN 比漏报更糟：它会训练人
        忽略这条告警，等真失配时也就没人看了。
        """
        self.assertEqual(
            version_gate.norm_version("9.0.0.83-2026.07.22-release"), "9.0.0.83")
        self.assertEqual(version_gate.norm_version(" 1.2.3 "), "1.2.3")
        self.assertEqual(version_gate.norm_version("1.2.3+build9"), "1.2.3")
        self.assertEqual(version_gate.norm_version(None), "")

    def test_card_version_abbreviation_no_false_warn(self):
        """卡里写简写 + 真机带构建后缀 → **不告警**（归一化后相同）。

        prev/cur 用**同一个真机值**：真实流程里两者都来自设备采集，这里要隔离的是
        "卡 vs 真机"这一层比对，不是"上次 vs 这次"（后者见下面两条）。
        """
        real = "9.0.0.83-2026.07.22-release"
        lines, code = version_gate.gate_warn_lines(
            self._prev(app_version_name=real), self._prev(app_version_name=real),
            card_version="9.0.0.83")
        self.assertEqual(code, "unchanged")
        self.assertEqual(lines, [])

    def test_card_mismatch_line_is_single_source(self):
        """卡失配判断只有**一份**实现：run_case 启动打印与 finish 告警必须同结论。

        回归 2026-09-16 真机实测：两处各写一份精确比较 → 只给 finish 那条加了
        归一化 → 启动时照样报假 WARN（看上去像"改了没生效"，实为另一份副本）。
        """
        real = "9.0.0.83-2026.07.22-release"
        # 一致（卡里是简写）→ 两处都不报
        self.assertIsNone(version_gate.card_mismatch_line("9.0.0.83", real))
        lines, _ = version_gate.gate_warn_lines(
            self._prev(app_version_name=real), self._prev(app_version_name=real),
            card_version="9.0.0.83")
        self.assertEqual(lines, [])
        # 真失配 → 两处都报，且**文案完全相同**（同源）
        self.assertIsNotNone(version_gate.card_mismatch_line("8.0.0.1", real))
        lines2, _ = version_gate.gate_warn_lines(
            self._prev(app_version_name=real), self._prev(app_version_name=real),
            card_version="8.0.0.1")
        self.assertEqual(lines2,
                         [version_gate.card_mismatch_line("8.0.0.1", real)])

    def test_card_mismatch_line_needs_both_sides(self):
        """单侧缺失（没卡 / 没采到版本）→ None，**不判失配**（缺失 ≠ 变化）。"""
        self.assertIsNone(version_gate.card_mismatch_line("", "1.0"))
        self.assertIsNone(version_gate.card_mismatch_line("1.0", None))
        self.assertIsNone(version_gate.card_mismatch_line(None, None))

    def test_build_suffix_change_counts_as_apk(self):
        """只有构建后缀变（同版本号重新打包）也算 apk —— 那是新 APK 的信号。

        与上一条的区别：卡比对**归一化**（容忍人手写简写），而"上次 vs 这次"
        采集值**精确比**（重新打包确实换了包）。两处口径不同是刻意的。
        """
        code, _ = version_gate.classify(
            self._prev(app_version_name="9.0.0.83-2026.07.22-release"),
            self._prev(app_version_name="9.0.0.83-2026.08.01-release"))
        self.assertEqual(code, "apk")

    def test_version_code_change_wins_over_same_name(self):
        """versionName 没变但 versionCode 变了 → 仍是 apk（P2 后主判据）。"""
        code, _ = version_gate.classify(
            self._prev(app_version_code="90083"),
            self._prev(app_version_code="90084"))
        self.assertEqual(code, "apk")

    def test_old_record_without_comparable_fields_is_no_baseline(self):
        """有历史记录但字段全 NULL（RunMetrics 上线前的旧记录）→ `no_baseline`。

        不这么判会落进 `unchanged`，而 `unchanged` 是"**比过了**、没变"的结论 ——
        实测 2026-09-16：168 首跑就报 `unchanged`（当时库里只有旧记录）。
        """
        old = {"id": 37, "started_at": "2026-09-14T10:00:00"}
        code, detail = version_gate.classify(old, self._prev())
        self.assertEqual(code, "no_baseline")
        self.assertIn("缺少可比字段", detail)


# ── P1b：同 dump 优先级定位（§4.2）────────────────────────────────
@unittest.skipIf(_tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestPriorityMatch(unittest.TestCase):
    """rid > desc > text 的**优先级**语义 + 同 dump 内降级。

    回归点：旧实现是"同一循环里 OR"，按**树序**返回任一属性命中的第一个节点
    —— 于是 tap_el(rid=R, text=T) 可能点到树上更靠前、text==T 的无关节点，
    SKILL.md 约定的优先级形同虚设（实为潜在误点，比找不到更危险）。
    """

    @staticmethod
    def _n(rid=None, text=None, desc=None, bounds=(0, 0, 10, 10)):
        return {"rid": rid or "", "text": text or "", "desc": desc or "",
                "bounds_xy": bounds}

    def test_rid_wins_over_text_even_if_text_node_is_earlier(self):
        nodes = [self._n(text="保存"), self._n(rid="id_save")]
        n, layer, status = tf._priority_match(nodes, rid="id_save", text="保存")
        self.assertEqual((layer, status), ("rid", "hit"))
        self.assertEqual(n["rid"], "id_save")

    def test_desc_wins_over_text(self):
        nodes = [self._n(text="保存"), self._n(desc="保存")]
        n, layer, _ = tf._priority_match(nodes, desc="保存", text="保存")
        self.assertEqual(layer, "desc")

    def test_falls_back_to_text_when_rid_absent(self):
        """rid 完全不在树上 → 允许降级到 text，但**必须标 fallback**（留痕用）。"""
        nodes = [self._n(text="保存")]
        n, layer, status = tf._priority_match(nodes, rid="id_save",
                                             desc="保存btn", text="保存")
        self.assertEqual((layer, status), ("text", "fallback"))
        self.assertEqual(n["text"], "保存")

    def test_only_text_given_is_not_a_fallback(self):
        """没传 rid 时 text 命中就是 hit —— 不许把正常用法记成"降级"。"""
        n, layer, status = tf._priority_match([self._n(text="保存")],
                                             text="保存")
        self.assertEqual((layer, status), ("text", "hit"))
        self.assertIsNotNone(n)

    def test_no_match_returns_miss(self):
        n, layer, status = tf._priority_match([self._n(rid="other")],
                                             rid="id_save")
        self.assertIsNone(n)
        self.assertIsNone(layer)
        self.assertEqual(status, "miss")

    # ── 正确性保证（2026-09-16 人确认：rid 优先，且要保证取到的是对的节点）──
    def test_rid_present_without_bounds_does_not_fall_back(self):
        """**最重要的一条**：rid 在树上但无 bounds → 绝不降级去匹配 text。

        这是"该滚的时候去猜 text"的防线：出屏/折叠的元素，正确答案是滚动或
        报找不到；退到 text 会命中一个同名乱入的无关节点并"成功"点到错的东西
        —— 比找不到更危险（报告还是绿的）。
        """
        nodes = [self._n(rid="id_save", bounds=None),
                 self._n(text="保存")]          # 树上另有一个同名 text 节点
        n, layer, status = tf._priority_match(nodes, rid="id_save", text="保存")
        self.assertIsNone(n)
        self.assertEqual(layer, "rid")
        self.assertEqual(status, "present_no_bounds")

    def test_multiple_rid_prefers_clickable(self):
        """同一 rid 挂在容器与子控件上时，优先取 clickable 的那个。"""
        nodes = [self._n(rid="id_item", bounds=(0, 0, 900, 300)),      # 容器
                 self._n(rid="id_item", bounds=(10, 10, 200, 60))]     # 子控件
        nodes[1]["clickable"] = "true"
        n, _layer, status = tf._priority_match(nodes, rid="id_item")
        self.assertEqual(n["bounds_xy"], (10, 10, 200, 60))
        self.assertEqual(status, "hit")

    def test_multiple_rid_prefers_smallest_area_when_none_clickable(self):
        """都不可点击时取**面积最小**的（容器通常更大、更"外"）。"""
        nodes = [self._n(rid="id_item", bounds=(0, 0, 900, 300)),
                 self._n(rid="id_item", bounds=(10, 10, 200, 60))]
        n, _layer, _status = tf._priority_match(nodes, rid="id_item")
        self.assertEqual(n["bounds_xy"], (10, 10, 200, 60))

    def test_ambiguous_flagged_when_indistinguishable(self):
        """同面积并列 → 取一个但标 ambiguous，**不静默选第一个**。"""
        nodes = [self._n(rid="id_item", bounds=(0, 0, 100, 100)),
                 self._n(rid="id_item", bounds=(0, 200, 100, 300))]
        n, layer, status = tf._priority_match(nodes, rid="id_item")
        self.assertIsNotNone(n)
        self.assertEqual((layer, status), ("rid", "ambiguous"))

    def test_local_name_compat(self):
        """兼容某些 dump 只回本地名（无包名前缀）的形态。"""
        nodes = [self._n(rid="id_save")]
        n, layer, status = tf._priority_match(nodes, rid="com.demo:id/id_save")
        self.assertIsNotNone(n)
        self.assertEqual((layer, status), ("rid", "hit"))

    def test_exact_match_wins_over_local_compat(self):
        """精确匹配优先：本地名兼容是第二级，不能让跨包同名节点抢先命中。"""
        nodes = [self._n(rid="otherpkg:id/id_save"), self._n(rid="com.demo:id/id_save")]
        n, _l, _s = tf._priority_match(nodes, rid="com.demo:id/id_save")
        self.assertEqual(n["rid"], "com.demo:id/id_save")

    def test_same_dump_only_one_source(self):
        """三级降级共用**同一份** nodes（§4.2「只允许同一 dump 内降级」）。

        本函数不接 dump 回调、只吃 nodes 列表 —— 结构上不可能跨 dump 重找，
        这条锁的是"不引入第二次采集"的设计约束。
        """
        nodes = [self._n(desc="保存")]
        n1, l1, _ = tf._priority_match(nodes, rid="x", desc="保存")
        n2, l2, _ = tf._priority_match(nodes, rid="x", desc="保存")
        self.assertIs(n1, n2)          # 同一份列表 → 同一结果，无隐藏重查
        self.assertEqual((l1, l2), ("desc", "desc"))


# ── P1b：wait_gone / scroll_to_rid / region_of / 快照复用 ───────────
@unittest.skipIf(_tf is None, "需要 uiautomator2")
class TestP1bApis(unittest.TestCase):
    def _bare(self):
        t = object.__new__(_tf.TestCase)
        t.steps = []
        t._cur_step = None
        t._db = None
        t._db_case_id = None
        t.trace = None
        return t

    # wait_gone -----------------------------------------------------
    def test_wait_gone_requires_a_criterion(self):
        """无判据 → 抛 ValueError，**不许**静默返回 True（那是假 PASS）。"""
        with self.assertRaises(ValueError):
            self._bare().wait_gone()

    def test_wait_gone_true_when_absent(self):
        t = self._bare()
        t.el_bounds = lambda **kw: None
        self.assertTrue(t.wait_gone(rid="gone_rid", timeout=0.1))

    def test_wait_gone_false_when_still_present(self):
        t = self._bare()
        t.el_bounds = lambda **kw: (0, 0, 10, 10)
        self.assertFalse(t.wait_gone(rid="still_here", timeout=0.1,
                                     interval=0.05))

    # wait_text_contains -------------------------------------------
    def test_wait_text_contains_hits_substring(self):
        """子串等待：`wait_text` 是精确匹配，表达不了"某句话里含某个词"。

        真机场景（178）：询问框文案是整句「是否根据课程时长和休息时长**自动调整**
        其他课程」，等它只能按子串 —— 旧写法是 sleep(2.5) 后一次性读屏。
        """
        t = self._bare()
        t.screen_text = lambda: ["是否根据课程时长和休息时长自动调整其他课程"]
        self.assertTrue(t.wait_text_contains("自动调整", timeout=0.2))

    def test_wait_text_contains_timeout(self):
        t = self._bare()
        t.screen_text = lambda: ["别的文案"]
        self.assertFalse(t.wait_text_contains("自动调整", timeout=0.1,
                                             interval=0.05))

    def test_wait_text_contains_does_not_fake_exact_match(self):
        """它**不是** `wait_text` 的替代品：精确匹配仍走 `wait_text`。"""
        t = self._bare()
        t.screen_text = lambda: ["取消", "确定"]
        self.assertTrue(t.wait_text_contains("消", timeout=0.2))   # 子串命中
        self.assertTrue(t.wait_text_contains("取消", timeout=0.2))  # 精确也是子串

    # wait_text_any_contains ---------------------------------------
    def test_wait_any_contains_returns_matched_word(self):
        """多候选共享一个预算，并返回**命中的那个**（同时回答"等到了"+"是哪种"）。"""
        t = self._bare()
        t.screen_text = lambda: ["应用权限", "前往设置"]
        self.assertEqual(t.wait_text_any_contains(("相机权限", "前往设置"),
                                                  timeout=0.2), "前往设置")

    def test_wait_any_contains_does_not_double_budget(self):
        """关键是**共享预算**：顺序等两次会把时间翻倍（3+3=6s）。"""
        t = self._bare()
        calls = []

        def _st():
            calls.append(1)
            return ["无关文案"]
        t.screen_text = _st
        import time as _t
        t0 = _t.time()
        self.assertEqual(t.wait_text_any_contains(("a", "b", "c"), timeout=0.3,
                                                  interval=0.05), "")
        self.assertLess(_t.time() - t0, 1.0)      # 不是 0.3×3
        self.assertGreaterEqual(len(calls), 2)

    def test_wait_any_contains_requires_candidates(self):
        """空候选 → 抛错（不许静默超时：那会变成"等了个寂寞"）。"""
        with self.assertRaises(ValueError):
            self._bare().wait_text_any_contains([], timeout=0.1)

    # scroll_to_rid -------------------------------------------------
    def test_scroll_returns_immediately_when_visible(self):
        """已在树上 → 不滑动（省一次 swipe，也避免把页面滚走）。"""
        t = self._bare()
        t._screen_size = lambda: (1080, 2400)
        swiped = []
        t.swipe = lambda *a, **k: swiped.append(a) or True
        t.el_bounds = lambda **kw: (0, 100, 10, 200)
        self.assertEqual(t.scroll_to_rid("rid_x"), (0, 100, 10, 200))
        self.assertEqual(swiped, [])

    def test_scroll_swipes_until_found(self):
        t = self._bare()
        t._screen_size = lambda: (1080, 2400)
        state = {"n": 0}
        t.swipe = lambda *a, **k: state.__setitem__("n", state["n"] + 1) or True

        def _eb(**kw):
            return (0, 5, 10, 15) if state["n"] >= 2 else None
        t.el_bounds = _eb
        self.assertEqual(t.scroll_to_rid("rid_x", settle=0), (0, 5, 10, 15))
        self.assertEqual(state["n"], 2)

    def test_scroll_gives_up_and_returns_none(self):
        t = self._bare()
        t._screen_size = lambda: (1080, 2400)
        t.swipe = lambda *a, **k: True
        t.el_bounds = lambda **kw: None
        self.assertIsNone(t.scroll_to_rid("never", max_swipes=2, settle=0))

    def test_scroll_swipe_failure_breaks_loop(self):
        """滑动本身失败（设备断连）→ 立刻退出，不做无意义的重试。"""
        t = self._bare()
        t._screen_size = lambda: (1080, 2400)
        t.swipe = lambda *a, **k: False
        t.el_bounds = lambda **kw: None
        self.assertIsNone(t.scroll_to_rid("never", settle=0))

    # region_of -----------------------------------------------------
    def test_region_of_from_bounds(self):
        t = self._bare()
        t.el_bounds = lambda **kw: (100, 200, 300, 400)
        self.assertEqual(t.region_of(rid="r", pad=10), (190, 410, 90, 310))

    def test_region_of_none_when_element_absent(self):
        """元素不存在 → None（调用方据此退化全屏，而不是拿到一个垃圾区间）。"""
        t = self._bare()
        t.el_bounds = lambda **kw: None
        self.assertIsNone(t.region_of(rid="r"))

    # dump 快照复用（M4）-------------------------------------------
    def test_snapshot_reuses_within_ttl(self):
        t = self._bare()
        calls = []
        t._dump = lambda: calls.append(1) or "<x/>"
        t.dump_snapshot()
        t.dump_snapshot()
        self.assertEqual(len(calls), 1)      # 第二次走缓存，没再 dump

    def test_snapshot_refresh_forces_new_dump(self):
        t = self._bare()
        calls = []
        t._dump = lambda: calls.append(1) or "<x/>"
        t.dump_snapshot()
        t.dump_snapshot(refresh=True)
        self.assertEqual(len(calls), 2)

    def test_snapshot_expires_after_ttl(self):
        t = self._bare()
        calls = []
        t._dump = lambda: calls.append(1) or "<x/>"
        t.dump_snapshot(ttl=0)
        t.dump_snapshot(ttl=0)
        self.assertEqual(len(calls), 2)

    # 截图降载（M3）------------------------------------------------
    def _shot_case(self, **attrs):
        t = self._bare()
        t.case_dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t.case_dir, True)
        t._shot_idx = 0
        t._db_step_id = None
        t.SHOT_MAX_SIDE = 0        # 跳过压缩（本组测配额，不测编码）
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._screencap_bytes = lambda: b"PNGDATA"
        for k, v in attrs.items():
            setattr(t, k, v)
        return t

    def test_shot_every_action_by_default(self):
        """**默认不限张数**（2026-09-16 人确认：每步还是要留图）。

        证据链优先于指标：M3 的 ≤6MB 靠**压缩**达成，不靠少截图。用"少留证"
        换指标等于拿掉出错时唯一的排查依据。
        """
        t = self._shot_case()
        self.assertEqual(t.SHOT_PER_STEP, 0)
        p1 = t._auto_screenshot("a")
        p2 = t._auto_screenshot("b")
        self.assertTrue(p1 and os.path.isfile(p1))
        self.assertTrue(p2 and os.path.isfile(p2))
        self.assertEqual(len(t._cur_step["evidences"]), 2)

    def test_shot_quota_switch_still_works(self):
        """配额开关保留（压测/排查时临时收窄），显式设 1 时才生效。"""
        t = self._shot_case()
        t.SHOT_PER_STEP = 1
        p1 = t._auto_screenshot("a")
        p2 = t._auto_screenshot("b")
        self.assertTrue(p1 and os.path.isfile(p1))
        self.assertIsNone(p2)                       # 被配额挡掉
        self.assertEqual(t._shot_skipped, 1)        # 但可统计，不是静默

    def test_shot_force_bypasses_quota(self):
        """失败证据（force=True）不受配额限制 —— 失败现场永远要留。"""
        t = self._shot_case()
        t.SHOT_PER_STEP = 1
        t._auto_screenshot("a")
        p = t._auto_screenshot("失败", force=True)
        self.assertTrue(p and os.path.isfile(p))

    def test_encode_shot_webp(self):
        """M3 达标靠 WebP（实测 PNG 14.2MB / JPEG 11.6MB / WebP 3.7MB）。

        这条锁"默认编码真的是 WebP 且真的变小了" —— 编码器静默退回 PNG 的话，
        M3 会悄悄回到超标，而报告上看不出任何异常。
        """
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (1904, 3040), (240, 240, 240)).save(buf, format="PNG")
        raw = buf.getvalue()
        t = self._shot_case()
        t.SHOT_MAX_SIDE, t.SHOT_FORMAT = 400, "webp"
        out, ext = t._encode_shot(raw)
        self.assertEqual(ext, "webp")
        self.assertLess(len(out), len(raw))
        # 约束的是**长边**（SHOT_MAX_SIDE 的语义），不是宽
        self.assertEqual(max(Image.open(_io.BytesIO(out)).size), 400)

    def test_encode_shot_png_option_and_fallback(self):
        """`SHOT_FORMAT="png"` 走无损；输入不是图片时回退原图（不许丢证据）。"""
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (800, 600), (10, 10, 10)).save(buf, format="PNG")
        raw = buf.getvalue()
        t = self._shot_case()
        t.SHOT_MAX_SIDE, t.SHOT_FORMAT = 0, "png"
        out, ext = t._encode_shot(raw)
        self.assertEqual(ext, "png")
        # 非图片字节 → 回退原图 PNG（不抛异常、不返回空）
        out2, ext2 = t._encode_shot(b"NOT-AN-IMAGE")
        self.assertEqual((out2, ext2), (b"NOT-AN-IMAGE", "png"))

    def test_shot_quota_resets_each_step(self):
        t = self._shot_case()
        t.SHOT_PER_STEP = 1
        t._auto_screenshot("a")
        t._shot_in_step = 0          # 相当于 step() 重置
        self.assertTrue(t._auto_screenshot("b"))

    def test_dump_updates_snapshot(self):
        """`_dump()` 必须更新 `_snap` —— 它是失败工件包 dump.xml 的**唯一**来源。

        回归 2026-09-16 真机实测：原来只有 `dump_snapshot()` 写 `_snap`，而实际
        采集路径（`el_bounds` / `wait_*` / `screen_text`）都直接调 `_dump()`
        → `_snap` 恒为 None → **每次失败工件包都没有 dump.xml**（只剩
        state.json + 截图）。而"失败那一刻的 UI 树"正是工件包最有价值的产出。
        """
        t = self._bare()
        t._dump_count = 0
        t.trace = mock.Mock()
        t.ensure_awake = lambda *a, **k: None
        t.d = mock.Mock()
        t.d.dump_hierarchy.return_value = "<hierarchy/>"
        self.assertIsNone(getattr(t, "_snap", None))
        t._dump()
        self.assertIsNotNone(getattr(t, "_snap", None))
        self.assertEqual(t._snap[1], "<hierarchy/>")

    # 失败工件包（§五）----------------------------------------------
    def test_failure_bundle_written(self):
        t = self._shot_case()
        t.SHOT_PER_STEP = 0
        t._snap = (time.time(), "<hierarchy>现场</hierarchy>")
        t.current_package = lambda: "com.demo"
        t.current_activity = lambda: "DemoActivity"
        t.adb_shell = lambda *a: "0"
        d = t._write_failure_bundle("断言失败：课程仍在")
        self.assertTrue(os.path.isdir(d))
        self.assertIn("dump.xml", os.listdir(d))
        self.assertIn("state.json", os.listdir(d))
        with open(os.path.join(d, "dump.xml"), encoding="utf-8") as f:
            self.assertIn("现场", f.read())
        with open(os.path.join(d, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["activity"], "DemoActivity")
        self.assertTrue(st["has_dump"])

    def test_failure_bundle_without_snapshot_says_so(self):
        """没有 dump 快照时不假装有：has_dump=False（而不是写个空 XML）。"""
        t = self._shot_case()
        t.SHOT_PER_STEP = 0
        t.current_package = lambda: "com.demo"
        t.current_activity = lambda: ""
        t.adb_shell = lambda *a: "0"
        d = t._write_failure_bundle("x")
        self.assertFalse(os.path.exists(os.path.join(d, "dump.xml")))
        with open(os.path.join(d, "state.json"), encoding="utf-8") as f:
            self.assertFalse(json.load(f)["has_dump"])


# ── P1b：BLOCKED 变体 + 退出码（§2.2 / §10.3）──────────────────────
@unittest.skipIf(_tf is None, "需要 uiautomator2")
class TestBlockedSemantics(unittest.TestCase):
    def _bare(self):
        t = object.__new__(_tf.TestCase)
        t.steps = [{"name": "s", "results": [], "evidences": []}]
        t._cur_step = t.steps[0]
        t._db = None
        t._db_case_id = None
        t.trace = None
        t._stop_after = None
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t.case_dir, True)
        # 失败工件包会写盘；本组只关心语义，直接短路它
        t._write_failure_bundle = lambda detail: None
        return t

    def test_case_blocked_is_case_abort(self):
        """继承 CaseAbort → 复用"中止 + 照常出报告"的路径。"""
        self.assertTrue(issubclass(_tf.CaseBlocked, _tf.CaseAbort))

    def test_block_unless_passes_when_true(self):
        t = self._bare()
        self.assertTrue(t.block_unless(True, "不该触发"))
        self.assertEqual(t.steps[0]["results"], [])

    def test_block_unless_raises_and_records_blocked(self):
        t = self._bare()
        with self.assertRaises(_tf.CaseBlocked):
            t.block_unless(False, "需要先有一条课程表", probe="emptyView 在")
        r = t.steps[0]["results"][0]
        self.assertEqual(r["result"], "BLOCKED")
        self.assertIn("需要先有一条课程表", r["detail"])
        self.assertIn("emptyView 在", r["detail"])

    def test_block_unless_accepts_callable(self):
        """惰性求值：cond 为 callable 时才在调用点求值（可含 dump 查询）。"""
        t = self._bare()
        seen = []
        t.block_unless(lambda: seen.append(1) or True, "x")
        self.assertEqual(seen, [1])

    def test_block_unless_callable_exception_becomes_blocked(self):
        """判定本身抛异常（设备断了）→ 记 BLOCKED，不是崩溃。"""
        t = self._bare()
        with self.assertRaises(_tf.CaseBlocked):
            t.block_unless(lambda: 1 / 0, "前置")
        self.assertIn("判定本身抛异常", t.steps[0]["results"][0]["detail"])

    def test_require_on_absent_blocked_records_blocked(self):
        t = self._bare()
        t.tap_rid = lambda *a, **k: False
        with self.assertRaises(_tf.CaseBlocked):
            t.require_tap_rid("id_x", on_absent="BLOCKED")
        self.assertEqual(t.steps[0]["results"][0]["result"], "BLOCKED")

    def test_require_default_still_fail(self):
        """默认仍是 FAIL —— BLOCKED 是显式选择，不能悄悄改变既有语义。"""
        t = self._bare()
        t.tap_rid = lambda *a, **k: False
        with self.assertRaises(_tf.CaseAbort) as cm:
            t.require_tap_rid("id_x")
        self.assertNotIsInstance(cm.exception, _tf.CaseBlocked)
        self.assertEqual(t.steps[0]["results"][0]["result"], "FAIL")

    def test_exit_code_blocked_is_2(self):
        """退出码陷阱（§2.2）：BLOCKED 必须是 2，不能被写成 FAIL 的 1。"""
        import run_case
        self.assertEqual(run_case.exit_code_for("BLOCKED"), 2)
        self.assertEqual(run_case.exit_code_for("FAIL"), 1)


# ── P2：知识索引生成器（§6.1 / §6.2 / §3.4）───────────────────────
class TestKnowledgeIndex(unittest.TestCase):
    """卡头解析必须吃掉**三种现状格式**，否则"生成式索引"第一次跑就失效。

    这组用例的价值不在"脚本能跑"，而在**锁定格式容错**：现状卡并不统一
    （表格 / 平铺词列表 / `「词」→「节」`引用块）。只支持一种，另一批卡的
    触发词列就会变空 —— 而**空列看起来像"没写"，不像"解析失败"**（静默降级）。
    """

    def _card(self, body, name="com.test.app.md"):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return d, p

    def test_parse_list_header(self):
        _, p = self._card("# T\n\n- **app**: `com.a`\n- **name**: A\n"
                          "- **验证版本**: 1.2.3\n- **versionCode**: 12\n"
                          "- **最近验证**: 2026-01-01\n")
        c = ki_mod.parse_card(p)
        self.assertEqual((c["app"], c["version_code"], c["version_name"]),
                         ("com.a", "12", "1.2.3"))

    def test_parse_merged_header(self):
        """单行合并式（`**k**: v｜**k2**: v2`）是旧格式，不能因为"不整齐"就解析不到。"""
        _, p = self._card("# T\n\n- **app**: `com.a`｜**验证版本**: 9.0.0.83｜"
                          "**最近验证**: 2026-09-14（真机 31/31）\n")
        c = ki_mod.parse_card(p)
        self.assertEqual(c["version_name"], "9.0.0.83")
        self.assertIn("31/31", c["last_verified"])

    def test_explanatory_paren_dropped(self):
        """字段值后的解释性括号必须丢掉：否则 `待采集（…）` 判据失效 → 告警静默消失。"""
        _, p = self._card("# T\n\n- **app**: `com.a`\n"
                          "- **versionCode**: 待采集（首次真机运行由 §4.1 落盘）\n")
        self.assertEqual(ki_mod.parse_card(p)["version_code"], ki_mod.PENDING)

    def test_non_app_card_skipped(self):
        _, p = self._card("# T\n\n- **app**: `_system`\n", name="_system.md")
        self.assertIsNone(ki_mod.parse_card(p))

    def test_missing_app_field_returns_none(self):
        _, p = self._card("# 没有 app 字段的 md\n\n随便写点\n")
        self.assertIsNone(ki_mod.parse_card(p))

    def test_triggers_from_quoted_map(self):
        """引用块散文式（`com.zui.calendar.md` 形态）：只取「→」左边的词。"""
        _, p = self._card("# T\n\n- **app**: `com.a`\n\n"
                          "> **检索索引**：先用触发词定位。\n"
                          "> 「导入 / 图库 / 拍照」→「标准链路」；\n"
                          "> 「权限 / 相机」→「权限弹窗」。\n")
        c = ki_mod.parse_card(p)
        self.assertEqual(c["triggers"][:3], ["导入", "图库", "拍照"])
        self.assertNotIn("标准链路", c["triggers"])    # 右侧是小节名，不是触发词

    def test_triggers_from_plain_list(self):
        """平铺词列表式（settings / launcher 形态）。"""
        _, p = self._card("# T\n\n- **app**: `com.a`\n\n## 检索索引\n\n"
                          "桌面、launcher、主屏幕、dock、长按、\n"
                          "卸载、应用信息\n")
        c = ki_mod.parse_card(p)
        self.assertIn("桌面", c["triggers"])
        self.assertIn("应用信息", c["triggers"])

    def test_triggers_from_table(self):
        _, p = self._card("# T\n\n- **app**: `com.a`\n\n## 检索索引\n\n"
                          "| 触发词 | 小节 |\n|---|---|\n"
                          "| 滚轮、时间选择器 | 「时间选择器」 |\n")
        self.assertIn("滚轮", ki_mod.parse_card(p)["triggers"])

    def test_triggers_not_grabbed_from_prose(self):
        """解释性长句不能被当成触发词（否则索引里会出现整句中文）。"""
        _, p = self._card("# T\n\n- **app**: `com.a`\n\n## 检索索引\n\n"
                          "**这张表是按需读卡的入口**：Agent 先用关键词检索。\n"
                          "不整卡进上下文（卡 400 行 ≈ 6.5k tokens）。\n"
                          "桌面、dock\n")
        self.assertEqual(ki_mod.parse_card(p)["triggers"], ["桌面", "dock"])

    def test_index_heading_mention_in_body_is_not_heading(self):
        """正文里提到「检索索引」不算节标题 —— 否则会从别处乱抽词。"""
        self.assertFalse(ki_mod._is_index_heading("命中词补进「检索索引」"))
        self.assertTrue(ki_mod._is_index_heading("## 检索索引"))
        self.assertTrue(ki_mod._is_index_heading("> **检索索引**：..."))

    def test_check_detects_drift(self):
        """卡头改了但没刷新索引 → 必须能检出（这是"索引腐烂"的唯一防线）。"""
        d, p = self._card("# T\n\n- **app**: `com.a`\n- **最近验证**: x\n")
        ki_mod.write_index(kdir=d)
        self.assertTrue(ki_mod.check_index(kdir=d)[0])
        with open(p, "a", encoding="utf-8") as f:
            f.write("- **versionCode**: 99\n")
        ok, detail = ki_mod.check_index(kdir=d)
        self.assertFalse(ok)
        self.assertIn("不一致", detail)

    def test_check_ignores_timestamp(self):
        """生成时间戳每次都变，不能拿它当漂移（否则校验永远报红 = 噪音）。"""
        d, _ = self._card("# T\n\n- **app**: `com.a`\n")
        path = ki_mod.write_index(kdir=d)[0]
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt.replace("于 2026", "于 2099"))
        self.assertTrue(ki_mod.check_index(kdir=d)[0])

    def test_check_reports_missing_index(self):
        d, _ = self._card("# T\n\n- **app**: `com.a`\n")
        ok, detail = ki_mod.check_index(kdir=d)
        self.assertFalse(ok)
        self.assertIn("不存在", detail)

    def test_audit_finds_device_values(self):
        """§3.4 缓存回流审计：bound/机型戳/屏幕尺寸要能被找出来（是清单不是门禁）。"""
        d, _ = self._card("# T\n\n- **app**: `com.a`\n\n"
                          "> bounds ≈ (503,2706)-(682,2889) [TB323FU]\n")
        hits = ki_mod.audit(kdir=d)
        self.assertTrue(hits)
        self.assertTrue(any("机型戳" in h[2] or "bounds" in h[2] for h in hits))

    def test_audit_clean_card_reports_nothing(self):
        d, _ = self._card("# T\n\n- **app**: `com.a`\n\n"
                          "> 坐标全部从活体 bounds 派生\n")
        self.assertEqual(ki_mod.audit(kdir=d), [])


if __name__ == "__main__":
    unittest.main()
