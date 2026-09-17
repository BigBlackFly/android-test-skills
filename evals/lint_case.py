#!/usr/bin/env python3
"""用例脚本静态 lint：AST 解析，不执行。

检查规则
────────
| # | 规则                        | 级别   | 检查方式                          |
|---|-----------------------------|--------|----------------------------------|
| 1 | 必须含 USER_INPUT 字符串常量 | ERROR  | ast 找 Assign(targets=Name('USER_INPUT')) |
| 2 | 禁止裸坐标 tap_xy            | ERROR  | ast 找 Call(func=Name('tap_xy'))，参数全为常量字面量才违规 |
| 3 | 禁止无注释裸 time.sleep      | ERROR  | ast 找 sleep Call + 源码行注释检测       |
| 4 | tap 后无 if 判返回值          | HINT   | 启发：Expr(value=Call(tap_*)) 不在 If 内（**默认折叠输出**，见下） |
| 5 | OCR/视觉的像素参数写死字面量   | ERROR  | 只看 _PIXEL_PARAMS 声明的参数，非全参数扫描 |
| 6 | 模块级"写死的像素常量"        | ERROR  | 模块级 Assign 且名字带空间语义（TOP/BOTTOM/X/Y/…）+ 值为裸 int |
| 7 | 像素参数里的固定偏移          | HINT   | `bounds[i] ± 字面量` 形态（正解：按元素自身尺寸的**比例**派生） |
| 8 | 手写轮询（`while time.time()` + sleep） | HINT | ast While：test 引用 `time.time()` 且 body 内有 sleep → 改用 `t.wait_*` |

**豁免出口（判据，P0a 必清项③ / 2026-09-16 定义）**：只有规则 5/6/7 可豁免，
写在同行或前一行：
- `# noqa`（无规则名）→ 豁免该行**全部**规则（保留的向后兼容出口）
- `# noqa: <rule>[, <rule>…]` → **只豁免列出的规则**（精确豁免，推荐写法）
- 规则名写错 → 记一条 HINT（防"以为豁免了、实际没生效"的静默失效）
- flake8 风格代码（E402/F401…）不属本工具命名空间，忽略不报
- 理由文本**不强制**（机器判"是否算理由"不可靠）；留痕靠注释本身，review 时可见

存在理由：确有必须写死像素的场景（特定 ROM 的固定弹窗区域）需要一条**留痕**
的例外，而不是把规则整体关掉。规则 2/3 有自己的语义化豁免（见各自注释）。

**默认折叠的 HINT**：规则 4（`tap_no_guard`）实测占 39/41 条提示（cases/ 存量），
逐条打印会把接入 `run_case` 后的输出淹掉 → 默认只报计数，`--all-hints` 才逐条列出。

退出码
------
- 违规（ERROR 级）→ 1（附行号）
- 仅提示（HINT 级）→ 0
- 好用例 → 0

用法
----
    python evals/lint_case.py cases/com.zui.calendar/172.py
    python evals/lint_case.py cases/ --baseline    # 基线模式：只检查指定文件
    python evals/lint_case.py cases/ --all-hints   # 连折叠的噪音型 HINT 也逐条列出
"""
import ast
import os
import re
import sys


# ── 规则 5/6/7 的判据表 ────────────────────────────────────────────
# 像素语义命名：模块级常量名命中**且**值为裸 int → 视为"写死的设备坐标"（规则 6）。
# 刻意收窄（只认明确的空间词）：`TIMEOUT = 30` / `MAX_PICK_ATTEMPTS = 3` 这类
# 合法常量绝不能误报 —— 误报会让人直接关掉规则，比漏报更糟。
_PIXEL_NAME_RE = re.compile(
    r"(^|_)(TOP|BOTTOM|LEFT|RIGHT|X|Y|PX|PIXEL|COORD|OFFSET|MARGIN)($|_)",
    re.IGNORECASE)

# 各 API 的"像素参数"（规则 5/7 **只看这些**，不扫全部参数）。
# 为什么必须逐 API 列表而不能"参数里有 int 就报"：
#   `tap_vision(desc, repeat=1, timeout=30)` 里 1/30 不是像素，
#   全参数扫描会把正常代码全报成违规（= 规则不可用）。
_PIXEL_PARAMS = {
    "ocr": ({0, 1}, {"y_min", "y_max"}),        # 屏幕 y 像素区间
    "tap_vision": (set(), {"bounds"}),          # 视觉裁剪区域（屏幕像素）
}

# 规则 7 额外覆盖：这些 API 的像素参数以"bounds 派生 + 固定偏移"出现时提示。
# 为什么规则 5 **不**覆盖它们：`tap_xy` 的"全字面量"形态已由规则 2 报出，
# 再报一次是重复噪音（同一条违规两个 rule 名会让人以为有两处问题）。
# 实测样本：`179.py:281  t.tap_xy(b[0] + 61, b[1] + 31, observe=False)`
#   —— 看着像从 bounds 派生，实际把"热区偏移"按某台设备写死了。
_PIXEL_OFFSET_PARAMS = {
    "tap": ({0, 1}, set()),
    "tap_xy": ({0, 1}, set()),
}


# ── 规则名全集与 `# noqa` 豁免判据（P0a 必清项③）────────────────────
# `# noqa: <rule>` 的合法取值；写错规则名会让豁免**静默失效**（看起来留了痕、
# 规则照报），所以必须能机器识别并提示。
_RULE_NAMES = frozenset({
    "missing_user_input", "bare_tap_xy", "bare_sleep", "tap_no_guard",
    "pixel_literal", "pixel_const", "pixel_offset", "hand_polling",
    "syntax_error", "noqa_unknown_rule",
})

# flake8 风格代码（E402/F401…）来自其它工具，不属本工具命名空间：
# 拿它们当"未知规则名"会误报（cases/ 里有 17 处 `# noqa: E402`）。
_FLAKE8_CODE_RE = re.compile(r"^[A-Z]{1,3}\d{3,4}$")

# `# noqa` 或 `# noqa: pixel_const, pixel_offset`
# 规则名列表刻意只吃"逗号分隔的标识符"：否则 `# noqa: pixel_const because xx`
# 里的英文散文会被一并吃进来，误报成未知规则名。
# ⚠️ 字符类必须**含数字**（`[A-Za-z_][A-Za-z0-9_]*`）：否则 flake8 码
# `E402` 会被截成 `"E"` → 被误判成"未知规则名"（实测踩过）。
_NOQA_RE = re.compile(
    r"#\s*noqa\b(?:\s*:\s*(?P<rules>[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*))?")

# 默认不逐条打印的 HINT 规则（噪音型启发式）：计数照常，`--all-hints` 才逐条列。
# tap_no_guard 不在 §1.1「留 3~4 条最硬」（裸坐标/裸 sleep/手写轮询/偏移溯源）
# 的名单里，P4b 阶段随规则删减一并处理。
_QUIET_HINT_RULES = frozenset({"tap_no_guard"})


# ── 规则分层（§1.1「留 3~4 条最硬的」/ §八 P4b）────────────────────
# P4b 的动作是**删规则**，而删规则 = 假设"现在的模型不再犯此错"。没有量化证据
# 就删，等于把回归保护换成一个信念（§1.3 的原话）。所以这里先把**分层机制**
# 做出来，让删除变成"改一个集合"而不是"翻代码找规则"；规则本身**暂不删**：
# 删除的前置条件是 P3b 生成 eval 在真实模型上跑出"对应陷阱连续通过"。
#
#   core      §1.1 点名的 4 条最硬规则（裸坐标 / 裸 sleep / 手写轮询 / 偏移溯源）
#             —— 保留理由是它们**有明确的对错**（写死就是写死），不依赖模型水平
#   heuristic 维护成本高、模型变强后大概率自动改善的启发式 → P4b 候选删除名单
#   structure 与"生成质量"无关的结构性检查（入库判据 / 语法 / noqa 拼写）
#             —— **不能删**：`missing_user_input` 同时是入库判据与执行期缓存守卫
#             判据（§八 P4b 备注），删了会连带破坏机制
CORE_RULES = frozenset({
    "bare_tap_xy", "bare_sleep", "hand_polling",
    # 像素类合并计为 §1.1 的"裸坐标"这一条
    "pixel_offset", "pixel_literal", "pixel_const",
})
HEURISTIC_RULES = frozenset({"tap_no_guard"})
STRUCTURE_RULES = frozenset({
    "missing_user_input", "syntax_error", "noqa_unknown_rule",
})


def rule_tier(name):
    """规则分层（见上方注释）。未知规则名归 `structure`（保守：不误删）。"""
    if name in CORE_RULES:
        return "core"
    if name in HEURISTIC_RULES:
        return "heuristic"
    return "structure"


def rule_inventory():
    """规则清单 + 分层（P4b 的决策依据）。返回 [(tier, rule), ...]。"""
    return sorted(((rule_tier(r), r) for r in sorted(_RULE_NAMES)),
                  key=lambda x: ({"core": 0, "structure": 1,
                                  "heuristic": 2}[x[0]], x[1]))


# ── 规则实现 ──────────────────────────────────────────────────────

class _LintVisitor(ast.NodeVisitor):
    """遍历 AST，收集违规与提示。"""

    def __init__(self, source_lines):
        self.source_lines = source_lines
        self.errors = []     # (line, rule, msg)
        self.hints = []      # (line, rule, msg)
        self._has_user_input = False
        self._in_if_body = False  # 是否在 if 语句体内
        self._in_tap_assign = False  # 是否在 tap_* 赋值语句中（ok = t.tap_...）
        self._func_has_docstring = False  # 当前函数是否有 docstring
        self._func_depth = 0     # 函数嵌套深度（0 = 模块级；规则 6 只看模块级）
        self._noqa_warned = set()  # (lineno, name)：未知规则名只提示一次，不刷屏

    def visit_FunctionDef(self, node):
        """记录当前函数是否有 docstring，然后遍历子节点。"""
        old_doc = self._func_has_docstring
        has_doc = (node.body and isinstance(node.body[0], ast.Expr)
                   and isinstance(node.body[0].value, ast.Constant)
                   and isinstance(node.body[0].value.value, str))
        self._func_has_docstring = has_doc
        self._func_depth += 1
        for child in node.body:
            self.visit(child)
        self._func_depth -= 1
        self._func_has_docstring = old_doc

    def visit_Assign(self, node):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "USER_INPUT":
                # 必须是字符串常量
                if isinstance(node.value, (ast.Constant,)) and isinstance(node.value.value, str):
                    self._has_user_input = True
            # ── 规则 6：模块级"写死的像素常量"（**只认模块级**）────────
            # 依据：坐标脆弱性的实际形态 —— 实测样本
            #   `175.py:64  DLG_TOP, DLG_BOTTOM = 1350, 1870`
            # 这种常量一旦被 `t.ocr(DLG_TOP, DLG_BOTTOM)` 消费就与设备/方向
            # 绑定。函数内部算出来的中间量（如 `y = int(h * 0.8)`）不算违规。
            if self._func_depth == 0:
                self._check_pixel_const(t, node.value, node.lineno)
        # tap_* 赋值：ok = t.tap_text(...) 形态，返回值已被捕获
        if isinstance(node.value, ast.Call):
            fn = self._call_name(node.value)
            if fn and fn.startswith("tap_"):
                old = self._in_tap_assign
                self._in_tap_assign = True
                self.generic_visit(node)
                self._in_tap_assign = old
                return
        self.generic_visit(node)

    def visit_Call(self, node):
        # ── 规则 2：裸坐标 tap_xy ──────────────────────────────────
        fn = self._call_name(node)
        if fn == "tap_xy":
            if self._all_args_literal(node):
                self.errors.append(
                    (node.lineno, "bare_tap_xy",
                     "禁止裸坐标 tap_xy（坐标应从元素 bounds 推导）"))

        # ── 规则 5：OCR / 视觉的像素参数写死字面量 ─────────────────
        bad_px = self._pixel_args_literal(node)
        if bad_px and not self._noqa(node.lineno, "pixel_literal"):
            shown = ", ".join(f"{n}={v}" for n, v in bad_px)
            self.errors.append(
                (node.lineno, "pixel_literal",
                 f"{fn}() 传了写死的像素值（{shown}）—— 坐标应从元素 bounds / "
                 f"当前窗口尺寸推导；确有必要写死则加 `# noqa`"))

        # ── 规则 7：像素参数里的固定偏移（**ERROR 级**，§2.3 偏移溯源）──
        # 2026-09-16 从 HINT 升为 ERROR，前置条件是**存量清零**：实测全量
        # `cases/**/*.py` + `_flow.py` 的 pixel_offset 命中数为 **0**（179.py:281
        # 的两处 `b[0]+61` 已改为按元素自身尺寸 1/8 派生）。
        # 顺序不能反：先清零再升级；否则 CI 立刻红，且红灯掩盖真问题。
        for off in self._pixel_fixed_offset(node):
            if not self._noqa(node.lineno, "pixel_offset"):
                self.errors.append(
                    (node.lineno, "pixel_offset",
                     f"{fn}() 的参数里带固定偏移 ±{off}（看着像派生，实际把间距"
                     f"按某台设备写死了 —— 换设备/字号即失效）。"
                     f"改为按元素自身尺寸派生，确有必要则加 `# noqa: pixel_offset`"))

        # ── 规则 3：无注释裸 time.sleep ────────────────────────────
        # 豁免条件（任一成立即放行）：
        #   a) 同行或前一行有注释
        #   b) 所在函数有 docstring（函数级说明）
        #   c) sleep ≤ 3s（settle 型等待：操作后等动画/渲染稳定，
        #      与 case-writing.md「settle 注释」约定一致）
        if fn == "sleep" and not self._func_has_docstring:
            if not self._is_short_settle(node) and not self._line_has_comment(node.lineno):
                self.errors.append(
                    (node.lineno, "bare_sleep",
                     "time.sleep 必须有注释说明等待原因（或 ≤ 3s 的 settle 等待）"))

        # ── 规则 4：tap 后无 if 判返回值（提示级）────────────────
        if fn and fn.startswith("tap_") and fn != "tap_xy" and not self._in_if_body and not self._in_tap_assign:
            self.hints.append(
                (node.lineno, "tap_no_guard",
                 f"{fn}() 返回值未判断（建议 if not {fn}(...): 或 require_*）"))

        self.generic_visit(node)

    def visit_If(self, node):
        old = self._in_if_body
        self._in_if_body = True
        # 遍历 If 条件本身（检查 if t.tap_xy(...): 形态）
        self.visit(node.test)
        for child in node.body:
            self.visit(child)
        self._in_if_body = old
        for child in node.orelse:
            self.visit(child)

    def visit_Expr(self, node):
        # Expr 语句中的 Call 直接调用 generic_visit（不在 if 体内时已处理）
        self.generic_visit(node)

    def visit_While(self, node):
        # ── 规则 8：手写轮询（提示级）──────────────────────────────
        # 形态：`while time.time() < deadline:` 的循环体里 `time.sleep(...)`。
        # 这是"等到就停"的手搓版：超时是固定的（慢设备不够 / 快设备白等），
        # 且每次轮询的等待时长与状态无关。框架已有 wait_rid / wait_text /
        # wait_activity（命中即停、超时有界），直接用即可（见 §2.2 / §2.4）。
        if self._is_time_poll_test(node.test) and self._body_has_sleep(node.body):
            self.hints.append(
                (node.lineno, "hand_polling",
                 "手写轮询（while + time.time() + sleep）——改用 t.wait_rid / "
                 "wait_text / wait_activity：命中即停、超时有界，不必手搓 deadline"))
        self.generic_visit(node)

    @staticmethod
    def _is_time_poll_test(test):
        """test 里是否出现 `time.time()`（判定"手搓 deadline"）。"""
        for n in ast.walk(test):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "time"):
                return True
        return False

    @staticmethod
    def _body_has_sleep(body):
        """循环体内是否有 sleep 调用（含嵌套）。"""
        for stmt in body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Call):
                    fn = n.func
                    name = fn.id if isinstance(fn, ast.Name) else (
                        fn.attr if isinstance(fn, ast.Attribute) else None)
                    if name == "sleep":
                        return True
        return False

    # ── 辅助方法 ──────────────────────────────────────────────────
    @staticmethod
    def _call_name(node):
        """取 Call 节点的最简函数名（支持 t.tap_xy / time.sleep / sleep）。"""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    @staticmethod
    def _all_args_literal(node):
        """位置参数与关键字参数全部为常量字面量 → True（裸坐标）。
        含变量/表达式/Attribute/BinOp → False（从元素推导，合法）。"""
        for arg in node.args:
            if not isinstance(arg, ast.Constant):
                return False
        for kw in node.keywords:
            if not isinstance(kw.value, ast.Constant):
                return False
        return True

    @staticmethod
    def _is_short_settle(node):
        """sleep ≤ 3s → settle 型等待，豁免。"""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                return arg.value <= 3
        return False

    @staticmethod
    def _is_int_literal(v):
        """是否为裸 int 字面量（**bool 不算** —— True 也是 int 的子类）。"""
        return (isinstance(v, ast.Constant) and isinstance(v.value, int)
                and not isinstance(v.value, bool))

    def _noqa(self, lineno, rule):
        """该行（或前一行）是否豁免 `rule`（规则 5/6/7 的唯一出口）。

        判据（2026-09-16 定义，P0a 必清项③）：
        - `# noqa`（无规则名）→ 豁免该行**全部**规则（向后兼容，保留该出口）
        - `# noqa: <rule>[, <rule>…]` → **只豁免列出的规则**（精确豁免）
        - 规则名写错 → 记 HINT（防"以为豁免了、实际没生效"的静默失效）
        - flake8 代码（E402/F401…）不属本工具命名空间，忽略不报
        出口存在的理由：确有必须写死像素的场景（特定 ROM 的固定弹窗区域），
        需要一条**留痕**的例外，而不是把规则整体关掉；痕迹留在代码里，review 可见。
        """
        for delta in (0, -1):
            comment = self._comment_of(lineno + delta)
            if not comment:
                continue
            m = _NOQA_RE.search(comment)
            if not m:
                continue
            names = [n.strip() for n in (m.group("rules") or "").split(",")
                     if n.strip()]
            if not names:
                return True                     # 裸 `# noqa`：豁免全部
            for n in names:
                if n in _RULE_NAMES:
                    if n == rule:
                        return True
                elif not _FLAKE8_CODE_RE.match(n):
                    key = (lineno, n)
                    if key not in self._noqa_warned:
                        self._noqa_warned.add(key)
                        self.hints.append(
                            (lineno, "noqa_unknown_rule",
                             f"`# noqa: {n}` 不是已知规则名（豁免会**静默失效**）："
                             f"合法值 {sorted(_RULE_NAMES)}"))
        return False

    def _comment_of(self, lineno):
        """该行的注释文本（`#` 到行尾）；字符串内的 `#` 不算注释。

        与 `_line_has_comment` 同一套引号扫描，避免 `"a#b"` 被误当注释。
        """
        idx = lineno - 1
        if not (0 <= idx < len(self.source_lines)):
            return ""
        line = self.source_lines[idx]
        in_str = None
        for i, ch in enumerate(line):
            if ch in ('"', "'") and (i == 0 or line[i - 1] != "\\"):
                if in_str is None:
                    in_str = ch
                elif ch == in_str:
                    in_str = None
            elif ch == "#" and in_str is None:
                return line[i:]
        return ""

    def _check_pixel_const(self, target, value, lineno):
        """规则 6：模块级 `NAME = <int>`，名字带空间语义 → 写死的设备坐标。

        支持元组解包（`DLG_TOP, DLG_BOTTOM = 1350, 1870`）。
        """
        if isinstance(target, ast.Name):
            if self._is_int_literal(value) and _PIXEL_NAME_RE.search(target.id):
                self._flag_pixel_const(target.id, value.value, lineno)
        elif isinstance(target, (ast.Tuple, ast.List)):
            elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else []
            for i, sub in enumerate(target.elts):
                self._check_pixel_const(sub, elts[i] if i < len(elts) else None,
                                        lineno)

    def _flag_pixel_const(self, name, val, lineno):
        if self._noqa(lineno, "pixel_const"):
            return
        self.errors.append(
            (lineno, "pixel_const",
             f"{name} = {val} 是按设备写死的像素值（坐标应从元素 bounds / "
             f"当前窗口尺寸推导；确有必要写死则加 `# noqa`）"))

    def _pixel_args_literal(self, node):
        """规则 5：本 Call 的**像素参数**里是否出现裸 int 字面量。

        返回 [(参数名, 值)]。只认 `_PIXEL_PARAMS` 声明的像素参数，**不扫全部
        参数** —— 否则 `tap_vision(desc, repeat=1, timeout=30)` 的 1/30 会被
        误报（规则一旦误报就会被整体关掉）。
        """
        spec = _PIXEL_PARAMS.get(self._call_name(node) or "")
        if not spec:
            return []
        pos_idx, kw_names = spec
        bad = []
        for i, a in enumerate(node.args):
            if i in pos_idx and self._is_int_literal(a):
                bad.append((f"#{i}", a.value))
        for kw in node.keywords:
            if kw.arg in kw_names and self._is_int_literal(kw.value):
                bad.append((kw.arg, kw.value.value))
        return bad

    def _pixel_fixed_offset(self, node):
        """规则 7：像素参数里 `bounds[i] ± 字面量` 形态的固定偏移。

        实测样本：`179.py:281 b[0] + 61`。这类表达式"看着像从 bounds 派生"，
        实际把某台设备的间距写死了 —— 换设备/换字号即失效。

        级别：**ERROR**（2026-09-16 起，§2.3「偏移溯源」）。它看起来比规则 5/6
        温和（毕竟用了 bounds），但危害相同：`b[0] + 61` 的 61 只对某一档字号/
        密度成立，换设备即点空 —— 而且**比全写死更难查**（形态上像正确的派生）。
        """
        fn = self._call_name(node) or ""
        spec = _PIXEL_PARAMS.get(fn) or _PIXEL_OFFSET_PARAMS.get(fn)
        if not spec:
            return []
        pos_idx, kw_names = spec
        cands = [a for i, a in enumerate(node.args) if i in pos_idx]
        cands += [kw.value for kw in node.keywords
                  if kw.arg in kw_names or kw.arg == "bounds"]
        out = []
        for c in cands:
            if isinstance(c, ast.BinOp) and isinstance(c.op, (ast.Add, ast.Sub)):
                # 任意一侧是裸 int 字面量即为固定偏移（`b[0]+61` / `cx+5` /
                #  `61+x` 都算）。乘除不算（`h * 0.8` / `w // 2` 是比例）。
                for side in (c.left, c.right):
                    if self._is_int_literal(side):
                        out.append(side.value)
        return out

    def _line_has_comment(self, lineno):
        """检查同行或前一行是否有注释（排除字符串内的 #）。"""
        for delta in (0, -1):
            idx = lineno - 1 + delta
            if 0 <= idx < len(self.source_lines):
                line = self.source_lines[idx]
                # 整行注释
                if line.lstrip().startswith("#"):
                    return True
                # 行尾注释：找 # 且其前无引号包裹
                in_str = None
                for i, ch in enumerate(line):
                    if ch in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                        if in_str is None:
                            in_str = ch
                        elif ch == in_str:
                            in_str = None
                    elif ch == '#' and in_str is None:
                        return True
        return False


def is_aux_module(path):
    """`_` 开头的文件是**辅助模块**（`_flow.py` / `cases/_lib/*` / 一次性探针）。

    这类模块本就不该有 `USER_INPUT`（规则 1 是"正式用例"的判据），故规则 1 对
    它们不适用；但**其余规则（定位 / 时序 / 像素类）同样适用** —— 否则把 sleep
    或裸坐标从用例挪进 `_flow.py` 就能让 M2 等指标变绿、而实际行为一点没改
    （§7.2 M2 的备注明确警告过这个漏洞）。

    注：目录扫描仍跳过 `_` 文件（一次性探针脚本故意写死坐标做标定，全量纳入
    会把真相淹掉）；显式传路径（如 `lint_case.py .../_flow.py`）即可 lint 它。
    """
    p = os.path.abspath(path or "")
    if os.path.basename(p).startswith("_"):
        return True
    # 只看**直接父目录**（如 cases/<pkg>/_lib/inventory.py 是共享库、不是用例）：
    # 不逐级向上找，避免把用户主目录里偶然带 `_` 的一段路径误判成辅助模块。
    return os.path.basename(os.path.dirname(p)).startswith("_")


def lint_file(path):
    """lint 单个用例文件。返回 (errors, hints)。

    ⚠️ `utf-8-sig` 而非 `utf-8`：用例 .py 可能带 BOM，用 utf-8 读会解出首字符
    U+FEFF，`ast.parse()` 抛 SyntaxError → 这里会报成「语法错误」（**误导性
    错误信息**：真因是多了 3 个字节，不是语法问题）。用 utf-8-sig 后同文件
    按真实内容正常 lint。对无 BOM 文件行为完全一致。
    """
    with open(path, encoding="utf-8-sig") as f:
        source = f.read()
    source_lines = source.splitlines()

    errors = []
    hints = []

    # 规则 1：USER_INPUT
    visitor = _LintVisitor(source_lines)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        errors.append((e.lineno or 0, "syntax_error", f"语法错误: {e.msg}"))
        return errors, hints

    visitor.visit(tree)

    # 规则 1 只对**正式用例**成立：辅助模块（_ 前缀）本就没有该常量
    if not visitor._has_user_input and not is_aux_module(path):
        errors.append((1, "missing_user_input",
                       "缺少 USER_INPUT 字符串常量（用例原文）"))

    errors.extend(visitor.errors)
    hints.extend(visitor.hints)

    return errors, hints


# ── 入口 ──────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法: python evals/lint_case.py <文件或目录> [--baseline] [--all-hints]")
        sys.exit(2)

    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]
    baseline_mode = "--baseline" in flags
    show_all_hints = "--all-hints" in flags
    if "--rules" in flags:
        # P4b 的决策依据：看清哪些规则属哪一层，再决定删谁（§1.3）
        print("规则→分层（core 保留；heuristic = P4b 候选删除；"
              "structure 不能删）\n")
        for tier, rule in rule_inventory():
            print(f"  [{tier:9}] {rule}")
        print("\n⚠️ heuristic 不是「现在就可以删」：删除前置条件是 P3b 生成 eval "
              "在真实模型上跑出该陷阱连续通过（§1.3）。")
        sys.exit(0)
    if not targets:
        print("用法: python evals/lint_case.py <文件或目录> [--baseline] "
              "[--all-hints] [--rules]")
        sys.exit(2)

    files = []
    for t in targets:
        if os.path.isfile(t):
            files.append(t)
        elif os.path.isdir(t):
            for root, dirs, fnames in os.walk(t):
                dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith("_")]
                for fn in fnames:
                    if fn.endswith(".py") and not fn.startswith("_"):
                        files.append(os.path.join(root, fn))

    total_errors = 0
    total_hints = 0
    quiet_hints = 0
    for f in sorted(files):
        errors, hints = lint_file(f)
        try:
            rel = os.path.relpath(f)
        except ValueError:
            # 跨盘符（Windows：脚本在 D:\，用例在工作区 C:\）→ relpath 抛
            # ValueError("path is on mount 'C:', start on mount 'D:'")，
            # 会让整个 lint 只输出一行异常就退出。改用绝对路径展示。
            rel = os.path.abspath(f)
        if errors:
            for line, rule, msg in errors:
                print(f"  ❌ {rel}:{line} [{rule}] {msg}")
            total_errors += len(errors)
        # 噪音型启发式默认只计数（见 _QUIET_HINT_RULES 说明），--all-hints 才逐条
        shown = hints if show_all_hints else [
            h for h in hints if h[1] not in _QUIET_HINT_RULES]
        quiet_hints += len(hints) - len(shown)
        for line, rule, msg in shown:
            print(f"  💡 {rel}:{line} [{rule}] {msg}")
        total_hints += len(shown)
        if not errors and not hints:
            if baseline_mode:
                print(f"  ✅ {rel}")

    if quiet_hints:
        print(f"  （另有 {quiet_hints} 条 "
              f"[{', '.join(sorted(_QUIET_HINT_RULES))}] 提示已折叠，"
              f"逐条查看加 --all-hints）")
    print(f"\n{len(files)} 个文件: {total_errors} 违规, "
          f"{total_hints + quiet_hints} 提示")
    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
