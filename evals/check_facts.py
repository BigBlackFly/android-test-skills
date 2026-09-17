#!/usr/bin/env python3
"""动态事实守门：检查 capture_toast 下游断言词在语料中是否有出处。

设计目的
────────
toast 文案是真正的动态事实（屏幕上一闪而过的文字），全量 record detail 守门
误报率太高。本脚本只检查 capture_toast 结果下游的断言匹配词。

检查范围
────────
1. AST 找 `capture_toast` 调用，提取其赋值的变量名（如 `texts, _shot = ...`）
2. 在该变量下游找 `any("..." in s for s in texts)` 或 `[s for s in texts if "..."]` 形态的字符串字面量
3. 字符串 < 4 字符（英文）或 < 2 个汉字 → 跳过（短串 grep 噪声太大）
4. 前缀白名单自动豁免：「步骤/完成/成功/已/阻塞」开头的是过程描述，不是事实引用

语料来源
--------
grep storage/probes/ + storage/traces/ 中是否包含该断言词。
找不到 → 标记「疑似编造文案」→ 回探索补采。

用法
----
    python evals/check_facts.py cases/com.zui.calendar/179.py
    python evals/check_facts.py cases/ --storage storage/
"""
import ast
import glob
import os
import re
import sys


# 前缀白名单：这些开头的字符串是过程描述，不是事实引用
_PREFIX_WHITELIST = ("步骤", "完成", "成功", "已", "阻塞", "失败", "未")


class _ToastVisitor(ast.NodeVisitor):
    """提取 capture_toast 下游的断言匹配词。"""

    def __init__(self):
        self.toast_vars = set()       # capture_toast 赋值给的变量名
        self.assertion_words = []     # (line, word) 需要守门的断言词

    def visit_Assign(self, node):
        # 找 texts, _shot = t.capture_toast(...) 形态
        if isinstance(node.value, ast.Call):
            fn = self._call_name(node.value)
            if fn == "capture_toast":
                for t in node.targets:
                    if isinstance(t, ast.Tuple):
                        for elt in t.elts:
                            if isinstance(elt, ast.Name):
                                self.toast_vars.add(elt.id)
                    elif isinstance(t, ast.Name):
                        self.toast_vars.add(t.id)
        self.generic_visit(node)

    def visit_Call(self, node):
        # 找 any("..." in s for s in texts) 形态
        fn = self._call_name(node)
        if fn == "any" and node.args:
            gen = node.args[0]
            if isinstance(gen, (ast.GeneratorExp, ast.ListComp)):
                # 提取 in 比较中的字符串常量
                for comp in gen.generators:
                    if comp.target and hasattr(comp, 'iter'):
                        iter_name = self._name_of(comp.iter)
                        if iter_name in self.toast_vars:
                            # 提取 elt 中的 "..." in s 比较
                            self._extract_string_comparisons(gen.elt)
        self.generic_visit(node)

    def _extract_string_comparisons(self, node):
        """从比较表达式中提取字符串字面量（'...' in s 形态）。"""
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.In):
                    # 左操作数是字符串常量
                    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                        word = node.left.value
                        if self._is_long_enough(word) and not word.startswith(_PREFIX_WHITELIST):
                            self.assertion_words.append((node.lineno, word))
                    # 右操作数是字符串常量（s in "..." 形态，少见但可能）
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                        word = comp.value
                        if self._is_long_enough(word) and not word.startswith(_PREFIX_WHITELIST):
                            self.assertion_words.append((node.lineno, word))
        # 递归检查 BoolOp（and/or 连接的比较）
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self._extract_string_comparisons(v)

    @staticmethod
    def _is_long_enough(word):
        """CJK ≥ 2 汉字或英文 ≥ 4 字符。"""
        cjk_count = sum(1 for ch in word if '\u4e00' <= ch <= '\u9fff')
        if cjk_count >= 2:
            return True
        return len(word) >= 4

    @staticmethod
    def _call_name(node):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    @staticmethod
    def _name_of(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Subscript):
            return _ToastVisitor._name_of(node.value)
        return None


def _grep_storage(word, storage_dirs):
    """在语料目录中 grep 断言词，返回是否找到。
    支持文本文件（json/jsonl/txt/xml）和 PNG 文件名（toast 截图证据）。
    """
    for d in storage_dirs:
        if not os.path.isdir(d):
            continue
        # 文本语料
        for pattern in ("**/*.jsonl", "**/*.json", "**/*.txt", "**/*.xml"):
            for fp in glob.glob(os.path.join(d, pattern), recursive=True):
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as f:
                        if word in f.read():
                            return True
                except (OSError, IOError):
                    continue
        # PNG 文件名语料（toast 截图证据文件名常含文案）
        # ⚠️ 必须同时认 PNG / JPEG / WebP：截图默认已切 WebP（见
        # test_framework.SHOT_FORMAT 的实测对比）。只认 .png 的后果不是"漏一条"，
        # 而是**把有真机证据的断言判成"编造"**（假阳性 → 守门误杀）。
        for _ext in ("png", "jpg", "jpeg", "webp"):
            for fp in glob.glob(os.path.join(d, f"**/*.{_ext}"), recursive=True):
                if word in os.path.basename(fp):
                    return True
    return False


def check_facts_file(path, storage_dirs):
    """检查单个用例文件的 toast 断言词是否有出处。
    返回 (suspects, checked)  —— suspects: [(line, word)], checked: int。

    ⚠️ `utf-8-sig` 而非 `utf-8`：带 BOM 的用例 .py 用 utf-8 读会解出首字符
    U+FEFF → `ast.parse` 抛 SyntaxError → 被下面的 except 吞成 `([], 0)`，
    **静默跳过该文件的事实检查**（这是四处 BOM 敏感读取里最隐蔽的一处）。
    """
    with open(path, encoding="utf-8-sig") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return [], 0

    visitor = _ToastVisitor()
    visitor.visit(tree)

    suspects = []
    for lineno, word in visitor.assertion_words:
        if not _grep_storage(word, storage_dirs):
            suspects.append((lineno, word))

    return suspects, len(visitor.assertion_words)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法: python evals/check_facts.py <文件或目录> [--storage storage/]")
        sys.exit(2)

    storage_arg = None
    args = [a for a in sys.argv[1:] if a != "--storage"]
    if "--storage" in sys.argv:
        idx = sys.argv.index("--storage")
        if idx + 1 < len(sys.argv):
            storage_arg = sys.argv[idx + 1]
            args = [a for a in args if a != storage_arg]

    # 确定语料目录
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if storage_arg:
        storage_dirs = [storage_arg]
    else:
        ws = os.environ.get("DSH_WORKSPACE_DIR", root)
        storage_dirs = [
            os.path.join(ws, "storage", "probes"),
            os.path.join(ws, "storage", "traces"),
        ]

    # 收集文件
    files = []
    for t in args:
        if os.path.isfile(t):
            files.append(t)
        elif os.path.isdir(t):
            for r, ds, fs in os.walk(t):
                ds[:] = [d for d in ds if d != "__pycache__"]
                for fn in fs:
                    if fn.endswith(".py") and not fn.startswith("_"):
                        files.append(os.path.join(r, fn))

    total_suspects = 0
    total_checked = 0
    for f in sorted(files):
        suspects, checked = check_facts_file(f, storage_dirs)
        total_checked += checked
        rel = os.path.relpath(f)
        if suspects:
            for line, word in suspects:
                print(f"  🔍 {rel}:{line} 疑似编造文案: {word!r}")
            total_suspects += len(suspects)

    print(f"\n{len(files)} 个文件: {total_checked} 个断言词, "
          f"{total_suspects} 个疑似编造")
    sys.exit(1 if total_suspects > 0 else 0)


if __name__ == "__main__":
    main()
