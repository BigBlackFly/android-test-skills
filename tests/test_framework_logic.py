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

import db        # noqa: E402
import run_case  # noqa: E402
import states    # noqa: E402
import vision    # noqa: E402
import webui     # noqa: E402

# scripts/ 目录加入 sys.path 以便导入预算门禁脚本
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import check_context_budget as budget_mod  # noqa: E402

# framework/smoke.py 加入 sys.path 以便测试 _check_adb
import smoke  # noqa: E402

try:
    import test_framework as tf
except ImportError:  # 系统 Python 无 uiautomator2 时跳过相关用例
    tf = None


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


if __name__ == "__main__":
    unittest.main()
