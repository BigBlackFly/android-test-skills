#!/usr/bin/env python3
"""RunMetrics 采集器：把"这次跑得怎么样"变成可查的数字（plan §7.1）。

职责边界（刻意轻量）：
- 只做**静态可算**的部分（不碰设备、不碰 TestCase 实例）：用例脚本 hash、
  `time.sleep` 字面量静态求和、rid 集合 hash、守门计数
- 运行期计数（dump / 截图 / 等待）由 framework 各计数器提供，本模块只把它们
  规整成一行记录所需的结构
- 落库走 `db.record_metrics()`（append-only 的 `case_metrics` 表）

**为什么脚本 hash 必须进 RunMetrics**：它是变更归因的第一排除项 —— "用例行为
变了"多半是自己改脚本改出来的（SKILL.md 原话："失败历史多为'改脚本改出来的
中间态'"）。没有这个 hash，任何差异都只能怪到 App/配置头上。

**为什么基线存这里**：`case_metrics` 是 append-only 且**刻意不被
`drop_previous_cases` 清理**（见 db.py 建表注释）→ "上次运行"的数据不会因为
"同用例只留最新一条"策略而消失。`cases` 子表与 `storage/probes/`（30 分钟即清）
都不能当基线存放处。
"""
import ast
import hashlib
import json
import os

# 静态指标经环境变量从 run_case 传给 TestCase（两者不同实例/不同进程阶段，
# 与 DSH_CASE_SCRIPT_PATH / DSH_CASE_USER_INPUT 同一套传递方式）。
ENV_STATIC = "DSH_RUN_STATIC_METRICS"

# 允许写入 case_metrics 的增量列（键名 = 列名）。白名单而非 **kwargs 直通：
# 拼错的键写进 SQL 会变成"no such column"整条记录丢失，白名单下静默忽略。
EXTRA_COLS = (
    "package", "script_hash", "app_version_name", "app_version_code",
    "sleep_static_sec", "sleep_static_sec_with_flow", "wait_calls", "wait_sec",
    "screenshots", "screenshot_bytes", "rid_set", "rid_set_hash",
    "gate_lint_errors", "gate_check_facts_suspects", "gate_checked_words",
    # P5：自愈/降级命中次数（§7.1「质量」维度的"自愈事件数"）
    "healing_hits",
)


def _md5_12(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def src_hash(path):
    """用例脚本内容 hash（md5 前 12 位）。读不到 → None（不假装知道）。"""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except OSError:
        return None


def sleep_static_seconds(path):
    """`time.sleep(<数字字面量>)` 参数求和（静态口径，见 §7.2 M2）。

    刻意只算**字面量**：`time.sleep(wait)` 这种动态值静态拿不到，所以这是
    "下界"而不是"实际等待"（M2 分母的已知口径，见 §7.2 M2 备注）。
    解析失败/读不到 → None（**不返回 0** —— 0 会被当成"真的一点没等"）。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None
    total = 0.0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name != "sleep" or not node.args:
            continue
        arg = node.args[0]
        # bool 是 int 的子类 → 显式排除，防 `sleep(True)` 被算成 1 秒
        if (isinstance(arg, ast.Constant)
                and isinstance(arg.value, (int, float))
                and not isinstance(arg.value, bool)):
            total += float(arg.value)
    return round(total, 2)


def sibling_aux_sources(path):
    """用例关联的**共享**辅助模块：`_flow.py` + `_lib/*.py`（两个位置都查）。

    为什么 M2（sleep 合计）必须连它们一起算：否则把 sleep 从用例挪进
    `_flow.py`（或 `cases/_lib/`）就能让指标变绿、而墙钟一动不动
    （§7.2 M2 明确警告过这个漏洞）。

    **两处都要查**（实测布局）：`_flow.py` 与包目录同级
    （`cases/<pkg>/_flow.py`），而共享库在 **cases 根**（`cases/_lib/`）——
    只查"用例同目录"会漏掉后者，指标又可以被绕过。
    """
    case_dir = os.path.dirname(os.path.abspath(path or ""))
    out = []
    flow = os.path.join(case_dir, "_flow.py")
    if os.path.isfile(flow):
        out.append(flow)
    seen = set()
    for lib in (os.path.join(case_dir, "_lib"),
                os.path.join(os.path.dirname(case_dir), "_lib")):
        if not os.path.isdir(lib) or lib in seen:
            continue
        seen.add(lib)
        for name in sorted(os.listdir(lib)):
            if name.endswith(".py") and not name.startswith("__"):
                out.append(os.path.join(lib, name))
    return out


def sleep_seconds_with_flow(path):
    """用例 + 同目录共享辅助模块的 sleep 静态和（M2 的防作弊口径）。

    任一侧解析失败 → None（**不假装 0**：宁可这一列空着，也不能给出"没在等"
    的假结论）。
    """
    total = sleep_static_seconds(path)
    if total is None:
        return None
    for p in sibling_aux_sources(path):
        v = sleep_static_seconds(p)
        if v is None:
            return None
        total += v
    return round(total, 2)


def rid_set_hash(rids):
    """rid 集合的稳定 hash（排序后 join 再取 md5 前 12 位）。

    **必须先排序**：集合无序，直接 hash 集合的迭代顺序会导致同一页面每次
    都算出不同值 → 版本门禁信号 2 天天报"界面变了"（假变化）。
    空集合 → None（"没采到"与"确实没有元素"不是一回事，不能混同）。
    """
    if rids is None:
        return None
    uniq = sorted({r for r in rids if r})
    if not uniq:
        return None
    return _md5_12("\n".join(uniq))


def rid_diff(old_rids, new_rids):
    """新增 / 消失的 rid（报告「本次与历史的差异」节用，见 §10.2）。

    返回 (disappeared, appeared)。缺失任一侧（无基线）→ (None, None)，
    由调用方标"跳过对比"而不是标"未归因"（§10.2 决策表首行）。
    """
    if old_rids is None or new_rids is None:
        return None, None
    old, new = set(old_rids), set(new_rids)
    return sorted(old - new), sorted(new - old)


def collect_static(path, gates=None):
    """静态指标（不依赖设备与 TestCase 实例）。

    gates: 守门计数 {lint_errors, check_facts_suspects, checked_words}
           （run_gates 的返回值，见 run_case.run_gates）
    """
    g = gates or {}
    return {
        "script_hash": src_hash(path),
        "sleep_static_sec": sleep_static_seconds(path),
        "sleep_static_sec_with_flow": sleep_seconds_with_flow(path),
        "gate_lint_errors": g.get("lint_errors"),
        "gate_check_facts_suspects": g.get("check_facts_suspects"),
        "gate_checked_words": g.get("checked_words"),
    }


def dump_static(metrics):
    """把静态指标写进环境变量，供 TestCase.finish() 取用。失败不阻断。"""
    try:
        os.environ[ENV_STATIC] = json.dumps(metrics or {}, ensure_ascii=False)
    except Exception:
        pass


def load_static():
    """读回静态指标；没有/损坏 → {}（收尾阶段绝不能因此崩）。"""
    try:
        raw = os.environ.get(ENV_STATIC)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def sanitize_extra(d):
    """只保留白名单列（拼错的键静默丢弃，不写进 SQL）。"""
    d = d or {}
    return {k: v for k, v in d.items() if k in EXTRA_COLS}
