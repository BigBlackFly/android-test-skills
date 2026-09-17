#!/usr/bin/env python3
"""evals/ 静态 lint 与事实守门单测（不需要 Android 设备）。

覆盖：lint_case.py 四条规则、check_facts.py toast 断言词守门。

运行（skill 包根目录）：
    python -m unittest tests.test_evals -v
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "evals"))

import json            # noqa: E402
from unittest import mock  # noqa: E402

import lint_case       # noqa: E402
import check_facts     # noqa: E402
import lint_probes as _lp  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import judge_generation as _jg
except ImportError:      # 缺依赖时跳过（判定器自身用例）
    _jg = None

try:
    import audit_waits as _aw
except ImportError:      # 等待合理性审计（真机验证轮新增）
    _aw = None


# ── lint_case.py：规则覆盖 ──────────────────────────────────────
class TestLintRules(unittest.TestCase):

    def _lint(self, source):
        """将源码写入临时文件再 lint。"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(source)
            path = f.name
        try:
            return lint_case.lint_file(path)
        finally:
            os.unlink(path)

    def test_good_case_no_errors(self):
        """好用例：有 USER_INPUT、无裸坐标、sleep 有注释。"""
        src = '''
USER_INPUT = "测试步骤"
def run():
    t = TestCase("test")
    t.step("s1")
    t.tap_xy(cx, cy)  # 坐标从元素推导
    time.sleep(1)  # 等页面渲染
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertEqual(len(errors), 0)

    def test_missing_user_input(self):
        """规则 1：缺少 USER_INPUT。"""
        src = '''
def run():
    t = TestCase("test")
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("missing_user_input" in e[1] for e in errors))

    def test_bare_tap_xy_constants(self):
        """规则 2：裸坐标 tap_xy(100, 200)。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.tap_xy(100, 200)
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("bare_tap_xy" in e[1] for e in errors))

    def test_tap_xy_with_variables_ok(self):
        """规则 2：坐标含变量 → 合法，不报错。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    cx = 100
    t.tap_xy(cx, 200)
    t.tap_xy(cx + 5, cy * 2)
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_tap_xy" in e[1] for e in errors))

    def test_bare_sleep_no_comment(self):
        """规则 3：无注释裸 sleep（> 3s 才报错，≤ 3s 视为 settle）。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(5)
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("bare_sleep" in e[1] for e in errors))

    def test_short_settle_sleep_exempt(self):
        """规则 3：≤ 3s sleep 视为 settle 等待，豁免。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(1)
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_sleep_with_comment_ok(self):
        """规则 3：有注释的 sleep → 不报错。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(1)  # 等页面渲染
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_sleep_in_documented_function_ok(self):
        """规则 3：有 docstring 的函数内 sleep → 函数级豁免。"""
        src = '''
USER_INPUT = "test"
def _back(t):
    """返回上一页。"""
    t.adb_shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.3)
def run():
    t = TestCase("test")
    _back(t)
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_tap_no_guard_hint(self):
        """规则 4：tap 后无 if 判返回值 → 提示级。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.tap_text("确定")
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("tap_no_guard" in h[1] for h in hints))

    def test_tap_in_if_no_hint(self):
        """规则 4：tap 在 if 体内 → 不提示。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    if not t.tap_text("确定"):
        t.record("FAIL", "not found")
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("tap_no_guard" in h[1] for h in hints))

    def test_tap_assignment_no_hint(self):
        """规则 4：ok = t.tap_... 赋值形态 → 返回值已被捕获，不提示。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    ok = t.tap_text("确定")
    t.record("PASS" if ok else "FAIL", "result")
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("tap_no_guard" in h[1] for h in hints))

    # ── 规则 5/6/7：像素写死（坐标脆弱性的静态门禁）────────────────
    def test_pixel_const_reported_175_form(self):
        """规则 6：`DLG_TOP, DLG_BOTTOM = 1350, 1870` —— 实测 175.py:64 的形态。

        这是 M1 验收项（"lint_case.py 能报出 175.py:64"）的**可重复版本**：
        现场那条已加 `# noqa` 留痕（避免 CI 红），所以验收靠本测试保证，
        而不是靠一个会阻断 CI 的活体违规。
        """
        src = '''
USER_INPUT = "test"
DLG_TOP, DLG_BOTTOM = 1350, 1870
def run():
    t = TestCase("test")
    return t.finish()
'''
        errors, _ = self._lint(src)
        self.assertEqual(len([e for e in errors if e[1] == "pixel_const"]), 2)

    def test_pixel_const_noqa_exempt(self):
        """规则 6 的 noqa 出口：留痕豁免必须生效（否则只能整体关掉规则）。"""
        src = '''
USER_INPUT = "test"
# noqa: pixel_const
DLG_TOP, DLG_BOTTOM = 1350, 1870
def run():
    t = TestCase("test")
    return t.finish()
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    def test_pixel_const_ignores_non_pixel_names(self):
        """规则 6 只认空间语义名：`TIMEOUT = 30` 这类合法常量绝不能误报
        （规则一旦误报就会被整体关掉，比漏报更糟）。"""
        src = '''
USER_INPUT = "test"
TIMEOUT = 30
MAX_PICK_ATTEMPTS = 3
RID_LESSON = "com.x:id/tv_lesson"
def run():
    t = TestCase("test")
    return t.finish()
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    def test_pixel_const_only_module_level(self):
        """规则 6 只看模块级：函数里算出来的像素中间量不算违规。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    Y_TOP = int(h * 0.8)
    return t.finish()
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    def test_pixel_literal_in_ocr(self):
        """规则 5：`t.ocr(1350, 1870)` 的写死像素区间。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.ocr(1350, 1870)
'''
        errors, _ = self._lint(src)
        self.assertTrue(any(e[1] == "pixel_literal" for e in errors))

    def test_pixel_literal_noqa(self):
        """规则 5 的 noqa 出口。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.ocr(1350, 1870)  # noqa
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_literal" for e in errors))

    def test_ocr_with_variables_not_flagged(self):
        """规则 5 只报字面量：`t.ocr(y0, y1)`（变量）由规则 6 管定义处。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    y0, y1 = 1, 2
    t.ocr(y0, y1)
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_literal" for e in errors))

    def test_pixel_literal_ignores_non_pixel_params(self):
        """规则 5 的**关键边界**：`tap_vision(desc, repeat=1, timeout=30)`
        里的 1/30 不是像素，绝不能报 —— 逐 API 列像素参数就是为了避免这种
        误报（全参数扫描会把正常代码全报成违规）。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.tap_vision("保存按钮", repeat=1, timeout=30)
'''
        errors, _ = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_literal" for e in errors))

    def test_pixel_fixed_offset_is_hint_only(self):
        """规则 7：`tap_xy(b[0] + 61, b[1] + 31)` —— 实测 179.py:281 形态。

        **ERROR 级**（2026-09-16 起，§2.3「偏移溯源」）：它看起来比"全写死"温和
        （毕竟用了 bounds），但危害相同 —— 61 只对某一档字号/密度成立，换设备即
        点空；而且**比全写死更难查**（形态上像正确的派生）。

        升级前置条件（必须遵守顺序）：**存量先清零再升 ERROR**。实测全量
        `cases/**/*.py` + `_flow.py` 的命中数为 0（179.py:281 已改为按元素自身
        尺寸 1/8 派生）。顺序反了 CI 立刻红，且红灯会掩盖真问题。
        """
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    b = t.el_bounds(rid="save")
    if b:
        t.tap_xy(b[0] + 61, b[1] + 31, observe=False)
'''
        errors, _hints = self._lint(src)
        self.assertTrue(any(e[1] == "pixel_offset" for e in errors))

    def test_pixel_offset_can_be_exempted_with_noqa(self):
        """留痕出口仍在：确有必要时 `# noqa: pixel_offset` 可豁免（§3.4 第 2 步）。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    b = t.el_bounds(rid="save")
    if b:
        t.tap_xy(b[0] + 61, b[1] + 31, observe=False)  # noqa: pixel_offset
'''
        errors, _hints = self._lint(src)
        self.assertFalse(any(e[1] == "pixel_offset" for e in errors))

    def test_ratio_expression_not_offset(self):
        """规则 7 不看比例式：`w // 2` / `int(h * 0.8)` 是动态推导，不提示。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    w, h = 100, 200
    t.tap_xy(w // 2, int(h * 0.8))
'''
        errors, hints = self._lint(src)
        self.assertFalse(any(h_[1] == "pixel_offset" for h_ in hints))

    def test_syntax_error(self):
        """语法错误的文件 → syntax_error 报错。"""
        src = 'USER_INPUT = "test"\ndef (: pass\n'
        errors, hints = self._lint(src)
        self.assertTrue(any("syntax_error" in e[1] for e in errors))


# ── P0a：`# noqa` 判据 / 手写轮询规则 / 噪音型 HINT 折叠 ──────────────
class TestLintP0a(unittest.TestCase):
    """lint_case 在 P0a 的三项改动（plan/mechanism-over-prose-plan.md §八 P0a）。

    ① 必清项③：`# noqa` 的可执行判据——精确到规则名，写错规则名必须能被发现
       （旧实现按子串 "noqa" 匹配 → 写了别的规则名也会误豁免、拼错则静默失效）；
    ② 必清项②：噪音型 HINT（tap_no_guard）默认折叠成计数，--all-hints 才逐条；
    ③ 规则 8：手写轮询（while time.time() + sleep）→ 提示改用 t.wait_*。
    """

    # 触发规则 6（模块级像素常量）的样板；`{suffix}` 放 noqa 注释做对照
    _PIXEL_CONST = '''
USER_INPUT = "test"
DLG_TOP = 1350  {suffix}
def run():
    pass
'''

    def _lint(self, source):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(source)
            path = f.name
        try:
            return lint_case.lint_file(path)
        finally:
            os.unlink(path)

    # ① `# noqa` 判据 ─────────────────────────────────────────────
    def test_noqa_bare_exempts_all(self):
        """裸 `# noqa`（无规则名）→ 仍豁免（向后兼容出口，未收紧）。"""
        errors, _ = self._lint(self._PIXEL_CONST.format(suffix="# noqa"))
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    def test_noqa_rule_scoped_exempts_listed_rule(self):
        """`# noqa: pixel_const` → 豁免该规则（175.py:67 的现有写法）。"""
        errors, _ = self._lint(self._PIXEL_CONST.format(suffix="# noqa: pixel_const"))
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    def test_noqa_rule_scoped_does_not_exempt_other_rule(self):
        """精确豁免：写的是别的规则名 → 本规则照样报。"""
        errors, _ = self._lint(self._PIXEL_CONST.format(suffix="# noqa: bare_sleep"))
        self.assertTrue(any(e[1] == "pixel_const" for e in errors))

    def test_noqa_unknown_rule_name_is_flagged(self):
        """规则名写错（pixel_constt）→ 记 HINT，且该行仍然报违规。"""
        errors, hints = self._lint(
            self._PIXEL_CONST.format(suffix="# noqa: pixel_constt"))
        self.assertTrue(any(h[1] == "noqa_unknown_rule" for h in hints))
        self.assertTrue(any(e[1] == "pixel_const" for e in errors))

    def test_noqa_flake8_code_not_reported_as_unknown(self):
        """`# noqa: E402` 属别的工具命名空间 → 不报未知规则名（也不豁免本工具规则）。

        做成"触发规则的行 + flake8 码"才有意义：否则 `_noqa` 根本不会被调用。
        """
        errors, hints = self._lint(self._PIXEL_CONST.format(suffix="# noqa: E402"))
        self.assertFalse(any(h[1] == "noqa_unknown_rule" for h in hints))
        self.assertTrue(any(e[1] == "pixel_const" for e in errors))

    def test_noqa_english_prose_after_rule_not_misparsed(self):
        """`# noqa: pixel_const because hot area` —— 只吃规则名，英文散文不误报。"""
        errors, hints = self._lint(self._PIXEL_CONST.format(
            suffix="# noqa: pixel_const because hot area"))
        self.assertFalse(any(h[1] == "noqa_unknown_rule" for h in hints))
        self.assertFalse(any(e[1] == "pixel_const" for e in errors))

    # ③ 规则 8：手写轮询 ───────────────────────────────────────────
    def test_hand_polling_detected(self):
        """`while time.time() < deadline` + sleep → HINT 提示改用 wait_*。"""
        src = '''
USER_INPUT = "test"
import time
def run():
    t = TestCase("test")
    deadline = time.time() + 5
    while time.time() < deadline:
        if t.el_bounds(rid="x"):
            break
        time.sleep(0.5)
'''
        _, hints = self._lint(src)
        self.assertTrue(any(h[1] == "hand_polling" for h in hints))

    def test_hand_polling_not_flagged_without_sleep(self):
        """纯 while 循环（无 sleep）不是"等界面"，不提示。"""
        src = '''
USER_INPUT = "test"
import time
def run():
    t = TestCase("test")
    deadline = time.time() + 5
    while time.time() < deadline:
        do_something()
'''
        _, hints = self._lint(src)
        self.assertFalse(any(h[1] == "hand_polling" for h in hints))

    def test_wait_primitive_not_flagged(self):
        """正解写法（t.wait_rid + 短 settle sleep）不提示。"""
        src = '''
USER_INPUT = "test"
import time
def run():
    t = TestCase("test")
    t.wait_rid("x", timeout=5)
    time.sleep(0.5)  # settle
'''
        _, hints = self._lint(src)
        self.assertFalse(any(h[1] == "hand_polling" for h in hints))

    # ② 噪音型 HINT 折叠 ─────────────────────────────────────────
    def test_quiet_hint_rules_include_tap_no_guard(self):
        """折叠集合存在且含 tap_no_guard（实测 39/41 条提示的来源）。"""
        self.assertIn("tap_no_guard", lint_case._QUIET_HINT_RULES)

    def test_rule_names_covers_all_emitted_rules(self):
        """_RULE_NAMES 必须覆盖本工具实际产出的规则名。

        否则 `# noqa: <某个真规则>` 会被误判成"未知规则名"，把可用的豁免出口堵死。
        """
        emitted = {
            "missing_user_input", "bare_tap_xy", "bare_sleep", "tap_no_guard",
            "pixel_literal", "pixel_const", "pixel_offset", "hand_polling",
            "syntax_error", "noqa_unknown_rule",
        }
        self.assertTrue(emitted <= lint_case._RULE_NAMES)

    # ④ 辅助模块（`_` 前缀）：豁免规则 1、其余照查 ───────────────────
    def _write_named(self, fname, body):
        td = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, td, True)
        p = os.path.join(td, fname)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_aux_module_exempt_from_rule1_but_others_apply(self):
        """`_flow.py` 豁免规则 1（缺 USER_INPUT），但规则 2（裸坐标）照报。

        为什么必须这样：否则把 sleep/裸坐标从用例挪进 `_flow.py` 就能让指标变绿、
        而实际行为一点没改（§7.2 M2 备注明确警告过这个漏洞）。
        """
        p = self._write_named("_flow.py",
                              "def run():\n    t.tap_xy(100, 200)\n")
        errors, _ = lint_case.lint_file(p)
        self.assertFalse(any(e[1] == "missing_user_input" for e in errors))
        self.assertTrue(any(e[1] == "bare_tap_xy" for e in errors))

    def test_formal_case_still_requires_user_input(self):
        """正式用例（非 `_` 前缀）规则 1 不变 —— 豁免只针对辅助模块。"""
        p = self._write_named("172.py", "def run():\n    pass\n")
        errors, _ = lint_case.lint_file(p)
        self.assertTrue(any(e[1] == "missing_user_input" for e in errors))

    def test_is_aux_module(self):
        self.assertTrue(lint_case.is_aux_module("/x/_flow.py"))
        self.assertTrue(lint_case.is_aux_module("/x/_lib/inventory.py"))
        self.assertFalse(lint_case.is_aux_module("/x/172.py"))
        self.assertFalse(lint_case.is_aux_module(""))


# ── check_facts.py：toast 断言词守门 ─────────────────────────────
class TestCheckFacts(unittest.TestCase):

    def _lint_facts(self, source, storage_content=None):
        """将源码写入临时文件，语料写入临时目录，执行 check_facts。"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(source)
            path = f.name
        td = tempfile.mkdtemp()
        if storage_content:
            with open(os.path.join(td, "probe.jsonl"), "w", encoding="utf-8") as pf:
                pf.write(storage_content)
        try:
            return check_facts.check_facts_file(path, [td])
        finally:
            os.unlink(path)
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_no_capture_toast_no_suspects(self):
        """没有 capture_toast 调用 → 零检查零嫌疑。"""
        src = '''
def run():
    t = TestCase("test")
    t.record("PASS", "ok")
'''
        suspects, checked = self._lint_facts(src)
        self.assertEqual(suspects, [])
        self.assertEqual(checked, 0)

    def test_assertion_word_found_in_storage(self):
        """断言词在语料中找到 → 不报嫌疑。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("课程时间有冲突" in s for s in texts)
'''
        suspects, checked = self._lint_facts(
            src, storage_content='{"text": "课程时间有冲突，无法设置"}')
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 1)

    def test_assertion_word_not_found(self):
        """断言词在语料中找不到 → 报疑似编造。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("课程时间有冲突" in s for s in texts)
'''
        suspects, checked = self._lint_facts(
            src, storage_content='{"text": "其他内容"}')
        self.assertEqual(len(suspects), 1)
        self.assertEqual(suspects[0][1], "课程时间有冲突")

    def test_short_string_skipped(self):
        """< 4 字的断言词 → 跳过（噪声太大）。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("成功" in s for s in texts)
'''
        suspects, checked = self._lint_facts(src, storage_content="")
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 0)  # 短串不计入检查

    def test_prefix_whitelist_exempt(self):
        """前缀白名单豁免：「完成/成功/已/阻塞」开头。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("完成操作" in s for s in texts)
'''
        suspects, checked = self._lint_facts(src, storage_content="")
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 0)  # 白名单不计入检查


# ── P2：交叉比对 lint（§3.3，本地探索期工具）──────────────────────
@unittest.skipIf(_lp is None, "需要 evals/lint_probes.py")
class TestLintProbes(unittest.TestCase):
    """缓存值回流检测：case 字面量 × probes 落盘数值。

    这组用例锁两件最容易做错的事：
    ① **无语料时必须说"未校验"**，不能判成"通过"（静默降级）也不能判成违规；
    ② **只比像坐标的值**，否则 `timeout=60` 撞上某个 OCR conf 会让误报率爆表。
    """

    def _probes(self, files):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        pkg = os.path.join(d, "com.demo")
        os.makedirs(pkg)
        for name, body in files.items():
            with open(os.path.join(pkg, name), "w", encoding="utf-8") as f:
                f.write(body)
        return d

    def _case(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "178.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_collect_from_dump_bounds(self):
        d = self._probes({"dump.xml":
                          '<node bounds="[100,200][300,400]" text="x"/>'})
        nums = _lp.collect_probe_numbers(d, "com.demo")
        self.assertEqual(nums, {100, 200, 300, 400})

    def test_collect_from_ocr_json_geometry_only(self):
        """只取几何/置信度字段 —— 版本号、计数、时间戳不能进来。"""
        d = self._probes({"ocr.json": json.dumps({
            "version": 20260916, "count": 7777,
            "items": [{"x": 120, "y": 340, "conf": 0.98, "text": "课程表"}]})})
        nums = _lp.collect_probe_numbers(d, "com.demo")
        self.assertIn(120, nums)
        self.assertIn(340, nums)
        self.assertNotIn(7777, nums)      # count 不在白名单
        self.assertNotIn(20260916, nums)  # version 不在白名单

    def test_cross_check_finds_module_constant(self):
        p = self._case("import time\n\nROW_Y = 2540\n\ndef run():\n    pass\n")
        hits, checked = _lp.cross_check(p, {2540, 910}, tol=10)
        self.assertEqual(checked, 1)
        self.assertEqual(hits[0][1], 2540)
        self.assertIn("模块级常量", hits[0][2])

    def test_cross_check_tolerance(self):
        p = self._case("ROW_Y = 2548\n")
        self.assertEqual(_lp.cross_check(p, {2540}, tol=10)[0][0][1], 2548)
        self.assertEqual(_lp.cross_check(p, {2540}, tol=2)[0], [])

    def test_cross_check_ignores_small_values(self):
        """<10 的字面量不参与：0/1/2/3 到处都有，比了全是噪音。"""
        p = self._case("FLAG = 1\n")
        self.assertEqual(_lp.cross_check(p, {1, 2, 3})[1], 0)

    def test_cross_check_pixel_param_position(self):
        p = self._case("def run(t):\n    t.tap_xy(1520, 940)\n")
        hits, checked = _lp.cross_check(p, {1524}, tol=10)
        self.assertTrue(checked >= 2)      # x 与 y 都是候选
        self.assertTrue(any("tap_xy" in h[2] for h in hits))

    def test_no_corpus_means_not_checked(self):
        """无语料 → 集合为空 → 调用方据此打印"未校验"（不是通过）。"""
        self.assertEqual(_lp.collect_probe_numbers("/nonexistent/dir", "com.x"),
                         set())

    def test_default_probes_dir_follows_workspace(self):
        with mock.patch.dict(os.environ,
                             {"DSH_WORKSPACE_DIR": os.path.join("X", "ws")}):
            self.assertTrue(_lp.default_probes_dir()
                            .replace("\\", "/").startswith("X/ws"))


# ── P3b：生成 eval 判定器的回归网 ─────────────────────────────────
@unittest.skipIf(_jg is None, "需要 evals/judge_generation.py")
class TestGenerationJudge(unittest.TestCase):
    """判定器**自己**也要有回归网。

    为什么：它也是代码，也会静默失效 —— 判据写错 → 永远 PASS → 整个生成 eval
    看起来"全绿"却什么都没测（这正是本 plan 反复出现的那类失效）。两个固定样本
    （`fixtures/generation_bad.py` / `generation_good.py`）就是它的网。
    """

    @staticmethod
    def _fixture(name):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "evals", "fixtures", name)

    def test_bad_fixture_fails_every_trap(self):
        """反面样本必须把**每一个**诱导陷阱都判出来（漏一个 = 判据失效）。"""
        r = _jg.judge(self._fixture("generation_bad.py"))
        self.assertEqual(r["verdict"], "FAIL")
        for c in ("has_user_input", "no_hardcoded_coords", "waits_not_sleeps",
                  "no_blind_first_asset", "records_every_step"):
            self.assertIn(c, r["failed"],
                          f"判据 {c} 没抓到反面样本（判据可能已失效）")

    def test_good_fixture_passes(self):
        """正面样本必须全项通过 —— 否则判据过严，会把人教成"绕判据写代码"。"""
        r = _jg.judge(self._fixture("generation_good.py"))
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["verdict"], "PASS")

    def test_na_is_not_counted_in_denominator(self):
        """无语料 → no_fabricated_text 记 N/A 且**不进分母**。

        否则"没语料"会被算成失败，M7 通过率就变成"跑没跑过语料"的函数
        （与 §3.3 / check_facts 降级是同一条原则）。
        """
        r = _jg.judge(self._fixture("generation_good.py"))
        crit = {c["criterion"]: c["verdict"] for c in r["criteria"]}
        self.assertEqual(crit["no_fabricated_text"], _jg.NA)
        self.assertNotIn("no_fabricated_text", r["failed"])

    def test_score_excludes_na(self):
        r = _jg.judge(self._fixture("generation_good.py"))
        got, total = (int(x) for x in r["score"].split("/"))
        self.assertLessEqual(got, total)
        self.assertEqual(total, len(r["criteria"]) - 1)   # 减掉那条 N/A

    def test_result_is_json_serializable(self):
        """CI / 记分卡要机器消费 → 结果必须可直接 JSON 化。"""
        json.dumps(_jg.judge(self._fixture("generation_good.py")))

    def test_syntax_error_is_reported_not_crashed(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "broken.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write("def run(:\n")
        r = _jg.judge(p)
        self.assertEqual(r["verdict"], "FAIL")
        self.assertIn("syntax", r["failed"])

    def test_nonexistent_probes_means_unvalidated(self):
        """指定一个不存在的语料目录 → 走"未校验"，不是"通过"也不是"违规"。"""
        r = _jg.judge(self._fixture("generation_good.py"),
                      probes_dir="/nonexistent/probes")
        crit = {c["criterion"]: c["verdict"] for c in r["criteria"]}
        self.assertEqual(crit["no_fabricated_text"], _jg.NA)


# ── 等待合理性审计（M1/M2 的正确目标：抓"不合理"，不追总时长）──────
@unittest.skipIf(_aw is None, "需要 evals/audit_waits.py")
class TestAuditWaits(unittest.TestCase):
    """审计工具自身的回归网：判据不能把"有道理的等待"打成问题。

    重要性：这个工具的输出会被人拿去**删代码**。误报（把必要的 settle 判成多余）
    的代价是"改坏一个本来能过的用例"，比漏报严重得多。所以三条正向判据都要锁。
    """

    def _case(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "t.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_sleep_then_wait_is_redundant(self):
        """`sleep(3)` 后紧跟 `wait_rid(..., timeout=10)` = 同一件事做两遍。"""
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    time.sleep(3)\n"
            "    t.wait_rid('x', timeout=10)\n"))
        self.assertEqual(rows[0]["kind"], "多余")
        self.assertIn("可直接删", rows[0]["why"])

    def test_sleep_then_tap_warns_about_animation(self):
        """sleep 后紧跟 tap_* → 判多余，但提示**不要只删**（动画中点空）。"""
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    time.sleep(1.5)\n"
            "    t.tap_rid('x')\n"))
        self.assertEqual(rows[0]["kind"], "多余")
        self.assertIn("点会落空", rows[0]["why"])

    def test_sleep_then_single_shot_check_is_suspicious(self):
        """睡一觉再看一眼（`el_bounds` 单次判真假）→ 可疑，界面慢即假 FAIL。"""
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    time.sleep(2)\n"
            "    if t.el_bounds(rid='x'):\n        t.record('PASS', 'ok')\n"))
        self.assertEqual(rows[0]["kind"], "可疑")

    def test_sleep_before_read_is_fine(self):
        """拨档后读值类 settle 是**合理**的（值变化 wait_* 表达不了）。"""
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    time.sleep(1)\n"
            "    t.record('PASS', t.read_rid('x')['text'])\n"))
        self.assertEqual(rows[0]["kind"], "合理")

    def test_sleep_in_loop_is_fine(self):
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    for _ in range(3):\n"
            "        t.tap_rid('plus')\n        time.sleep(1.0)\n"))
        self.assertEqual(rows[0]["kind"], "合理")

    def test_self_waiting_but_other_purpose_is_human_judgement(self):
        """`force_stop → sleep(1) → launch_app`：sleep 等的是**进程退出**，
        而 launch_app 等的是"应用起来" —— 两者无关，不能判多余。"""
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    t.force_stop('com.x')\n"
            "    time.sleep(1)\n    t.launch_app('com.x')\n"))
        self.assertEqual(rows[0]["kind"], "需人判")

    def test_else_branch_is_scanned(self):
        """**`else:` 分支必须被扫到**（回归 2026-09-16 实测盲区）。

        最初只走 `node.body`，`orelse` 完全没扫 → `_flow.py` 的权限被拒分支
        （`time.sleep(3)` + 单次 screen_text）被漏掉。一个"只扫一半代码"的审计
        工具比没有更糟：它会给出"已检查、没问题"的结论。
        """
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t, x):\n    if x:\n        t.record('PASS', 'y')\n"
            "    else:\n        time.sleep(3)\n        t.record('PASS', t.read_rid('a'))\n"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sec"], 3)

    def test_try_finally_and_handler_scanned(self):
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    try:\n        t.tap_rid('a')\n"
            "    except Exception:\n        time.sleep(1)\n        t.back()\n"
            "    finally:\n        time.sleep(2)\n        t.tap_rid('b')\n"))
        self.assertEqual(sorted(r["sec"] for r in rows), [1, 2])

    def test_audit_returns_seconds_and_line(self):
        rows = _aw.audit_file(self._case(
            "import time\n\ndef run(t):\n    time.sleep(2.5)\n    t.back()\n"))
        self.assertEqual(rows[0]["sec"], 2.5)
        self.assertEqual(rows[0]["line"], 4)

    def test_no_sleep_no_rows(self):
        self.assertEqual(_aw.audit_file(self._case("def run(t):\n    pass\n")), [])


# ── 事实核查：截图扩展名（截图默认已切 WebP）──────────────────────
class TestCheckFactsImageExt(unittest.TestCase):
    """`_grep_storage` 必须认 WebP / JPEG 文件名。

    截图默认已切 WebP（M3 达标靠它，实测 14.2MB→3.7MB）。若事实核查仍只认
    `.png`，后果不是"漏一条"而是**把有真机证据的断言判成"编造"** —— 假阳性
    会让守门误杀正确用例，且随着时间推移越用越糟（所有新截图都不被认）。
    """

    def _storage_with(self, fname):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        with open(os.path.join(d, fname), "wb") as f:
            f.write(b"\x00")
        return d

    def test_webp_filename_is_evidence(self):
        self.assertTrue(check_facts._grep_storage(
            "保存成功", [self._storage_with("toast_保存成功.webp")]))

    def test_jpg_filename_is_evidence(self):
        self.assertTrue(check_facts._grep_storage(
            "保存成功", [self._storage_with("toast_保存成功.jpg")]))

    def test_png_still_works(self):
        self.assertTrue(check_facts._grep_storage(
            "保存成功", [self._storage_with("toast_保存成功.png")]))

    def test_missing_word_is_not_evidence(self):
        self.assertFalse(check_facts._grep_storage(
            "保存成功", [self._storage_with("toast_别的文案.webp")]))

    def test_text_corpus_still_works(self):
        """文本语料（json/xml/txt）路径不受影响。"""
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        with open(os.path.join(d, "ocr.json"), "w", encoding="utf-8") as f:
            f.write('{"text": "保存成功"}')
        self.assertTrue(check_facts._grep_storage("保存成功", [d]))


# ── P4b：规则分层（删规则必须先有证据）────────────────────────────
class TestRuleTiers(unittest.TestCase):
    """P4b 的动作是"删规则"，而删规则 = 假设"模型不再犯此错"。

    没有量化证据就删，等于把回归保护换成一个信念。这组用例锁两件事：
    ① §1.1 点名的 4 条最硬规则**不许**被误分层；② 结构性判据**不许**被删。
    """

    def test_core_rules_are_the_four_hard_ones(self):
        """§1.1：裸坐标 / 裸 sleep / 手写轮询 / 偏移溯源 —— 保留理由是"有明确对错"。"""
        for r in ("bare_tap_xy", "bare_sleep", "hand_polling", "pixel_offset"):
            self.assertEqual(lint_case.rule_tier(r), "core", r)
        # 像素类合并计为"裸坐标"这一条
        for r in ("pixel_literal", "pixel_const"):
            self.assertEqual(lint_case.rule_tier(r), "core", r)

    def test_structure_rules_never_deletable(self):
        """`missing_user_input` 同时是入库判据与执行期缓存守卫判据（§八 P4b 备注）。

        删了它 → 用例不入库 + 守卫静默失效 → 三重静默回归。所以它属 structure。
        """
        for r in ("missing_user_input", "syntax_error", "noqa_unknown_rule"):
            self.assertEqual(lint_case.rule_tier(r), "structure", r)

    def test_unknown_rule_defaults_to_structure(self):
        """未知规则名归 structure：**宁可留着也不误删**。"""
        self.assertEqual(lint_case.rule_tier("some_future_rule"), "structure")

    def test_inventory_covers_every_rule_name(self):
        """清单必须覆盖 `_RULE_NAMES` 全集。

        否则新加的规则会"不在任何一层" → 分层决策时被漏审（静默失效）。
        """
        self.assertEqual({r for _t, r in lint_case.rule_inventory()},
                         set(lint_case._RULE_NAMES))

    def test_tap_no_guard_is_the_deletion_candidate(self):
        self.assertEqual(lint_case.rule_tier("tap_no_guard"), "heuristic")


if __name__ == "__main__":
    unittest.main()
