#!/usr/bin/env python3
"""
Android GUI 测试框架：元素操作、断言、截图、Toast 捕捉、置灰判断、报告生成
依赖: Python 3.10+（str | None 语法）+ uiautomator2 + rapidocr_onnxruntime
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

import uiautomator2 as u2

# ── 存储分工 ──────────────────────────────────────────────────────
# 运行产物（机器私有，不同步）→ <工作区>/storage
#   storage/screenshots  截图证据（每次执行一个 case_<时间戳> 子目录）
#   storage/reports      Markdown 测试报告
# 用例与知识卡（单一数据源，随版本同步）→ <skill包>/cases、<skill包>/knowledge
# 工作区根目录与 SQLite（db.default_test_dir）同源：环境变量 DSH_WORKSPACE_DIR
# > 默认 ~/android-test-skills-data。不随 framework 副本位置漂移——从 skill 包副本直接
# 运行时，截图/报告/探查缓存仍落同一工作区，与 test_records.db 保持一致。
try:
    from db import default_test_dir
    _WORKSPACE = default_test_dir()
except Exception:                      # db 不可用时按副本位置兜底
    _WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(_WORKSPACE, "storage")
SCREENSHOT_DIR = os.path.join(STORAGE_DIR, "screenshots")
REPORT_DIR = os.path.join(STORAGE_DIR, "reports")
# 探查缓存（生成用例阶段复用，避免重复 dump/OCR）
#   storage/probes/<包名>/<label>/dump.xml  UI 树
#                            /ocr.json      OCR 结果
#                            /meta.json     元信息（时间/包名/前台Activity）
# 纯文本存储，agent 可直接 grep / re 检索，不必连设备
PROBE_DIR = os.path.join(STORAGE_DIR, "probes")
# 采集会话档案（探查/采集模式开启，正常回归不开）：
#   storage/traces/<用例名>/<会话时间戳>/  一次采集会话一个目录
#     00001.xml … 000NN.xml   每次 _dump() 的 UI 树快照（原始档案）
#     events.jsonl            统一事件日志（动作/等待/看门狗/异常，逐行 JSON）
#     index.json              dump 序号 ↔ 时间/触发点 的对应关系
# 用途：probes 语义缓存缺料或排查异常时，从档案"重新找"当时那份 dump，
#       不必重跑真机。events 定位"卡在哪个动作"，dump 快照看"当时页面状态"。
TRACE_DIR = os.path.join(STORAGE_DIR, "traces")
ACTION_DELAY = 1.0   # 每次操作后的统一延时（防动画/时序竞态）

# 断言结果类型枚举：record() 只接受这些值，拼写错误在开发期即抛错
# （比 ❓ 兗底更早暴露；已审计存量 207 处调用全部为标准值，无兼容风险）
RESULT_TYPES = ("PASS", "FAIL", "WARN", "INFO", "BLOCKED")

# 学习词表缓存：(mtime, words_dict)，文件 mtime 未变时不重读
_dialog_words_cache = {"mtime": None, "words": None}

# 弹窗自动点击词表（u2 原生 watcher 注册用）
DIALOG_GUIDE_WORDS = ("我知道了", "知道了", "立即开始", "开始使用")
DIALOG_ALLOW_WORDS = ("允许", "同意", "始终允许", "仅在使用中允许",
                      "仅在使用时允许", "仅本次使用时允许", "全部允许", "选择照片")
DIALOG_DENY_WORDS = ("拒绝并不再询问", "拒绝", "不允许", "禁止")

# AI 学习词表持久化文件：AI 处理过的未知弹窗按钮自动并入，下次走快路径。
# 放工作区 storage/（运行产物）而非 framework/：framework/ 受变更管控，
# AI 学词会静默改写它，且多副本各自漂移（sync 互相覆盖）。
LEARNED_WORDS_FILE = os.path.join(STORAGE_DIR, "dialog_words.json")


def _migrate_learned_words():
    """旧版本把学习词表放在 framework/（skill 包）内，现迁到工作区 storage/。
    新文件尚不存在且有旧文件时，把旧词表搬过去（幂等，失败静默）。"""
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dialog_words.json")
    if os.path.isfile(LEARNED_WORDS_FILE) or not os.path.isfile(legacy):
        return
    try:
        import shutil
        os.makedirs(os.path.dirname(LEARNED_WORDS_FILE), exist_ok=True)
        shutil.copyfile(legacy, LEARNED_WORDS_FILE)
    except OSError:
        pass


def _dump_call_src():
    """定位 _dump() 的业务调用点（供 TraceRecorder.snapshot 登记 src）。

    回溯调用栈：0=_dump_call_src 1=_dump 2=直接调用者（如 el_bounds），
    再向上取业务层（如 tap_text 的源码行）作参考链。语义上下文（probe
    label）存在 recorder.ctx 时优先于本标签。定位失败退回 "dump"。
    """
    try:
        fr = sys._getframe(2)
        fr1 = fr
        try:
            fr1 = sys._getframe(4)
        except ValueError:
            pass
        chain = f"{fr.f_code.co_name}:{fr.f_lineno}"
        if fr1 is not fr and fr1.f_code.co_name != fr.f_code.co_name:
            chain = f"{fr1.f_code.co_name}:{fr1.f_lineno} -> {chain}"
        return chain
    except Exception:
        return "dump"


def _parse_nodes(xml):
    """把 dump_hierarchy 的 XML 解析成节点字典列表。

    统一入口：ElementTree 解析（属性顺序无关、正确处理 &quot; 等转义）。
    dump 偶发含非法字符导致 XML 不合法时，回退到旧的逐属性正则提取。

    每个节点: {rid, text, desc, cls, bounds(原始字符串),
               bounds_xy((x1,y1,x2,y2) | None),
               clickable/enabled/selected/checked(原始 "true"/"false" 字符串，缺失为 "")}
    """
    def _bounds_xy(raw):
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
        return tuple(map(int, m.groups())) if m else None

    try:
        import xml.etree.ElementTree as ET
        return [{
            "rid": el.get("resource-id") or "",
            "text": el.get("text") or "",
            "desc": el.get("content-desc") or "",
            "cls": el.get("class") or "",
            "bounds": el.get("bounds") or "",
            "bounds_xy": _bounds_xy(el.get("bounds")),
            "clickable": el.get("clickable") or "",
            "enabled": el.get("enabled") or "",
            "selected": el.get("selected") or "",
            "checked": el.get("checked") or "",
        } for el in ET.fromstring(xml).iter("node")]
    except Exception:
        pass
    # 回退：正则提取（旧行为；对非法 XML 尽力而为）
    out = []
    for n in re.findall(r"<node[^>]*>", xml):
        def _g(k, _n=n):
            m = re.search(r'%s="([^"]*)"' % k, _n)
            return m.group(1) if m else ""
        out.append({
            "rid": _g("resource-id"), "text": _g("text"),
            "desc": _g("content-desc"), "cls": _g("class"),
            "bounds": _g("bounds"), "bounds_xy": _bounds_xy(_g("bounds")),
            "clickable": _g("clickable"), "enabled": _g("enabled"),
            "selected": _g("selected"), "checked": _g("checked"),
        })
    return out


def _match_node(nodes, spec):
    """按 locate() 组合属性匹配节点（全部条件 AND，任一不满足即跳过）。

    组合定位的稳定性来源：单个属性（如 desc="日历"）可能命中图标/widget/菜单
    等多处，叠加 cls / clickable 等语义属性后收缩到唯一目标。只允许语义属性
    组合（rid/desc/text/cls/clickable/contains），禁止 bounds/位置索引——
    布局一改就全崩，且属性变化不携带任何业务含义（见 docs/case-writing.md「定位规范与旋屏约定」节）。
    """
    for n in nodes:
        if spec.get("rid") and n["rid"] != spec["rid"]:
            continue
        if spec.get("desc") and n["desc"] != spec["desc"]:
            continue
        if spec.get("text") and n["text"] != spec["text"]:
            continue
        if spec.get("cls") and n["cls"] != spec["cls"]:
            continue
        if spec.get("clickable") is not None \
                and (n["clickable"] == "true") != bool(spec["clickable"]):
            continue
        if spec.get("contains") \
                and spec["contains"] not in (n["text"] + n["desc"]):
            continue
        if not n["bounds_xy"]:          # 无坐标的节点（不可见/离屏）不可操作
            continue
        return n
    return None


class CaseAbort(Exception):
    """必需操作失败（require_* 系列），用例应立即中止。
    run_case.py 捕获后仍会生成报告，退出码按 FAIL（1）处理。"""


class PartialRun(Exception):
    """`--stop-after N`：到达步数上限，**主动**提前收尾（不是失败）。

    与 CaseAbort 的区别在**语义与后果**（两者都会被 run_case 捕获）：

    | | 含义 | 结论 | 退出码 | 入库 |
    |---|---|---|---|---|
    | `CaseAbort` | 必需操作失败 | FAIL | 1 | 是 |
    | `PartialRun` | 本次只跑前 N 步 | 按已跑部分正常算 | **0** | **否** |

    用途是 edit-run 调试循环（改一点、验一点）。局部执行**不入库**：只跑了前
    N 步的 "PASS" 会误导记录库，也会污染 flaky 统计（§八 P1a 明确"不计入
    flaky"）。退出码 0 是为了能在 shell 里用 `&&` 串起来。
    """


class CaseBlocked(CaseAbort):
    """前置条件不满足 → 结论 **BLOCKED**、退出码 **2**（不是 FAIL）。

    为什么需要独立的异常类型：`require_*` 找不到元素有两种完全不同的成因 ——
    ① App 真的坏了（缺陷）；② 前置条件缺失（配置未命中 / 数据没准备 / 换设备后
    布局不同）。两者记成同一个 FAIL，缺陷库就被"环境噪音"污染，且验收 12 的
    "配置差异除外"永远无法机械判定（§2.2 / §10.3）。

    继承 CaseAbort 是为了复用"中止用例 + 照常出报告"的既有路径；退出码不再由
    run_case 硬编码，而是用它记录的结果（BLOCKED）映射为 2。
    """


class ExecutionTimeCacheError(CaseAbort):
    """执行期误用缓存 API —— 必须 raise 阻断，否则拿过期数据当结论 = 假 PASS。

    **必须继承 CaseAbort（不是 RuntimeError）**：CaseAbort 走 FAIL(1) 路径、
    报告正常出；RuntimeError 会被 run_case 当 `_fatal_error` → 结论压成 ERROR、
    退出码 3，报告还会写"执行异常终止，结论不可信"。
    """


def _rid_candidates(nodes, rid):
    """收集与 rid 匹配的候选节点。返回 (候选列表, 形态)。

    形态：`exact` = rid 完全相等；`local` = 只匹配 `:id/` 后的本地名（兼容
    某些 dump 不回包名前缀的机型）；`miss` = 都不匹配。
    **先精确再兼容**：本地名匹配放在第二级，避免跨包同名节点抢先命中。
    """
    exact = [n for n in nodes if n.get("rid") == rid]
    if exact:
        return exact, "exact"
    if ":" in (rid or ""):
        local = rid.split("/")[-1]
        approx = [n for n in nodes if n.get("rid") == local]
        if approx:
            return approx, "local"
    return [], "miss"


def _pick_rid_node(vis):
    """从**多个同 rid 且有 bounds** 的节点里选最可能是目标的那个。

    返回 (node, ambiguous)。为什么需要它：Android 里同一 rid 常同时挂在
    **容器与子控件**上（`id/item` 出现在列表项根和它的文本上），此时"取树序
    第一个"很可能点到容器（点容器可能被拦截或点错位置）。

    判据顺序（对 §4.2 的落实）：
      ① 只有一个 → 是它
      ② 优先 `clickable=true`（rid 定位的目的绝大多数是点击/取值）
      ③ 仍多个 → 取**面积最小**的（容器通常更大、更"外"）
      ④ 仍并列 → 取树序第一个，但标 **ambiguous**（暴露歧义，绝不静默）
    """
    if not vis:
        return None, False
    if len(vis) == 1:
        return vis[0], False
    clickable = [n for n in vis if n.get("clickable") == "true"]
    pool = clickable or vis
    if len(pool) == 1:
        return pool[0], False

    def _area(n):
        b = n["bounds_xy"]
        return abs(b[2] - b[0]) * abs(b[3] - b[1])

    pool = sorted(pool, key=_area)
    amb = len(pool) > 1 and _area(pool[0]) == _area(pool[1])
    return pool[0], amb


def _priority_match(nodes, rid=None, desc=None, text=None):
    """按 **rid → desc → text** 优先级在同一份节点列表里取命中节点。

    与旧实现（同一循环里 OR、按树序返回）的差别是**语义**：OR 写法下
    `tap_el(rid=R, text=T)` 可能点到树上更靠前、`text==T` 的**无关节点**
    —— SKILL.md 约定的 rid > desc > text 优先级形同虚设（实为潜在误点）。

    §4.2 硬约束「只允许在同一个 dump 内降级」由本函数天然满足：nodes 来自
    **一次** dump，三级降级在同一份树上完成，不存在"跨 dump 重找 → 元素身份
    保证断裂"。

    返回 `(node, layer, status)`：

    | status | 含义 | 调用方该做什么 |
    |---|---|---|
    | `hit` | rid 精确命中且节点可用 | 直接用 |
    | `ambiguous` | rid 命中多个且无法区分（同面积） | 已按 ②③ 取最具体那个，**留痕** |
    | `present_no_bounds` | **rid 在树上存在但没有 bounds** | **绝不降级**，见下 |
    | `fallback` | rid 未命中，靠 desc/text 命中 | 留痕（OTA 漂移信号） |
    | `miss` | 都没命中 | 按"找不到"处理 |

    **`present_no_bounds` 是本函数最重要的一条设计**：rid 存在但 bounds 为空
    意味着元素被折叠/出屏/未布局（§2.4 归因②「没滚到」），此时正确答案是
    **滚动或报找不到**，而**不是**退到 text 去匹配 —— 那会命中一个同名乱入的
    无关节点，进而"成功"点到错的东西（**比找不到更危险**：报告还是绿的）。
    """
    if rid:
        cands, _form = _rid_candidates(nodes, rid)
        vis = [n for n in cands if n.get("bounds_xy")]
        if vis:
            node, amb = _pick_rid_node(vis)
            return node, "rid", ("ambiguous" if amb else "hit")
        if cands:
            # rid 找到了、但全都没有 bounds → 存在但不可用，禁止降级
            return None, "rid", "present_no_bounds"

    if desc:
        for n in nodes:
            if n.get("desc") == desc and n.get("bounds_xy"):
                return n, "desc", ("fallback" if rid else "hit")
    if text:
        for n in nodes:
            if n.get("text") == text and n.get("bounds_xy"):
                return n, "text", ("fallback" if rid else "hit")
    return None, None, "miss"


class _Located:
    """locate() 的返回值：组合属性定位结果的轻量包装。

    与 tap_* 同一契约：click/long_click 轮询定位（wait 秒内）→ 操作 →
    observe 检查链；找不到不抛异常，默认记 WARN（silent=True 跳过）。
    观察性读取用 exists / bounds / center / node 属性（exists 每次读
    都会重新 dump，是实时视图不是缓存快照）。
    """

    def __init__(self, tc, spec):
        self._tc = tc
        self._spec = spec
        self._desc = " AND ".join(f"{k}={v!r}" for k, v in spec.items())
        self._node = None

    def _find(self, timeout=0.0):
        """轮询查找目标节点；命中返回节点 dict，超时返回 None。
        每次轮询的 dump 都顺带驱动弹窗看门狗（与 wait_* 行为一致）。"""
        deadline = time.time() + timeout
        while True:
            xml = self._tc._dump()
            self._tc._run_dialog_watchers(xml)
            n = _match_node(_parse_nodes(xml), self._spec)
            if n:
                self._node = n
                return n
            if time.time() >= deadline:
                self._node = None
                return None
            time.sleep(0.5)

    def __repr__(self):
        return f"<_Located {self._desc} hit={self._node is not None}>"

    @property
    def exists(self):
        """目标当前是否可见可操作（每次访问都重新 dump，实时判定）。"""
        return self._find(0.0) is not None

    @property
    def bounds(self):
        """命中的 bounds (x1,y1,x2,y2)；未命中 None。"""
        return self._node["bounds_xy"] if self._node else None

    @property
    def center(self):
        """命中元素中心 (x, y)；未命中 None。"""
        b = self.bounds
        return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) if b else None

    @property
    def node(self):
        """命中的完整属性 dict（text/desc/rid/cls/clickable/bounds_xy...）。"""
        return dict(self._node) if self._node else None

    @property
    def text(self):
        """命中的 text（通常与 spec 的 text 相同，主要为 contains 定位服务）。"""
        return self._node["text"] if self._node else None

    def wait(self, timeout=8.0):
        """轮询等待目标出现。出现返回 True。"""
        return self._find(timeout) is not None

    def click(self, wait=5.0, observe=True, silent=False):
        """点击目标。返回 bool（与 tap_* 统一契约）。"""
        n = self._find(wait)
        if not n:
            if not silent:
                self._tc.record("WARN", f"locate 点击未找到: {self._desc}")
            return False
        b = n["bounds_xy"]
        t0 = time.time()
        self._tc.d.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
        if not observe:
            self._tc._log_action("tap", f"locate[{self._desc}] observe=False", t0)
            return True
        time.sleep(ACTION_DELAY)
        self._tc._check_dialogs_after_action()
        self._tc._log_action("tap", f"locate[{self._desc}]", t0)
        self._tc._auto_screenshot(f"点击_locate")
        return True

    def long_click(self, wait=5.0, duration=1.0, observe=True, silent=False):
        """长按目标（launcher 长按菜单等）。返回 bool。
        ⚠️ 用 ATX 坐标长按（long_press_xy），input swipe 模拟长按在
        launcher 上经常弹不出菜单（实测，见 knowledge/_system.md）。"""
        n = self._find(wait)
        if not n:
            if not silent:
                self._tc.record("WARN", f"locate 长按未找到: {self._desc}")
            return False
        b = n["bounds_xy"]
        t0 = time.time()
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        ok = self._tc.long_press_xy(cx, cy, duration=duration)
        if not ok:
            if not silent:
                self._tc.record("WARN", f"locate 长按手势失败: {self._desc}")
            return False
        if not observe:
            self._tc._log_action(
                "long_click", f"locate[{self._desc}] observe=False", t0)
            return True
        time.sleep(ACTION_DELAY)
        self._tc._log_action("long_click", f"locate[{self._desc}]", t0)
        self._tc._auto_screenshot("长按_locate")
        return True


# 最近一次完成的 TestCase 实例（finish 时登记）——run_case.py 据此取最终结论定退出码
LAST_CASE = None


def _resolve_serial(device_id=None):
    """确定本用例操作的唯一设备 serial。

    显式传入 device_id 则直接使用；否则要求恰好一台已授权设备：
    零台 → 抛错（BLOCKED 语义的前置）；多台 → 抛错要求显式指定。
    宁可在启动时崩，也不让 u2 和裸 adb 各连一台设备（操作与证据分家）。
    """
    if device_id:
        return device_id
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                         timeout=10).stdout
    devs = [l.split()[0] for l in out.splitlines()[1:]
            if len(l.split()) >= 2 and l.split()[1] == "device"]
    if not devs:
        raise RuntimeError("adb 无已授权设备（adb devices 无 device 状态的行）")
    if len(devs) > 1:
        raise RuntimeError(
            f"检测到多台设备 {devs}，请 TestCase(device_id=...) 显式指定一台")
    return devs[0]


# 探针/探查类用例（名称以 PROBE_ / RECON_ 等前缀开头）不入库：
# 它们是执行过程的中间调试数据，不是正式测试结果（用户确认的规则）。
_PROBE_NAME_PREFIXES = ("PROBE_", "RECON_")

def _is_probe_case(name):
    """判断用例名是否为探针/探查类（不入库）。大小写不敏感。"""
    return bool(name) and name.upper().startswith(_PROBE_NAME_PREFIXES)


def _should_record(user_input, name):
    """是否把本次执行写入 SQLite 记录库（用户 2026-09-11 定的规则）。

    **主判据 = 有 USER_INPUT**：只有"用户口述的正式用例"才带 USER_INPUT
    （run_case.py 的 extract_user_input 从脚本源码里提取），探查/补采/
    备数据/补验/框架自测这类辅助脚本天然没有。

    为什么不用命名前缀判定（旧规则只有 _PROBE_NAME_PREFIXES）：
    命名是**约定**、可能被违反——实测 AI 起的临时脚本名是「探查_186_设为当前
    与清空」「备数据_186c」「补采D_185_置灰逻辑」，一个都不匹配 PROBE_/RECON_，
    于是全部混入记录库（13 条脏记录）。USER_INPUT 是**事实**，不可能被起名绕过。

    `_is_probe_case` 保留作补充：smoke.py 的 PROBE_smoke 无 USER_INPUT，
    两种判据都会挡住；显式前缀意图更明确，留着无害。
    """
    if user_input is not None and str(user_input).strip():
        return True
    return False


def _rot_name(rot):
    """mRotation 的官方语义 → 中文（0/2 = 竖，1/3 = 横）。"""
    return {0: "竖屏", 2: "竖屏(反向)", 1: "横屏", 3: "横屏(反向)"}.get(
        rot, f"未知({rot})")


class TestCase:
    def __init__(self, name, device_id=None, case_dir=None, user_input=None, script_path=None,
                 vision=None, env_ignore=()):
        self.name = name
        # 未显式传入时，从环境变量取（run_case.py 注入：用户原始输入 + 脚本路径）
        self.user_input = user_input if user_input is not None \
            else os.environ.get("DSH_CASE_USER_INPUT")
        self.script_path = script_path if script_path is not None \
            else os.environ.get("DSH_CASE_SCRIPT_PATH")
        # 设备绑定：整个用例生命周期内所有 adb/u2 操作锁定同一 serial
        # 未显式传入时，从环境变量取（run_case.py --device 注入 / run_suite.py 透传）
        if device_id is None:
            device_id = os.environ.get("DSH_DEVICE_ID")
        self.serial = _resolve_serial(device_id)
        # 唤醒屏幕并解锁
        self._adb_run("shell", "input", "keyevent", "KEYCODE_WAKEUP", timeout=10)
        self._adb_run("shell", "wm", "dismiss-keyguard", timeout=10)
        # 防锁屏保活（根因防护）：USB 供电期间保持屏幕常亮，
        # 避免长用例执行中设备因休眠超时被锁屏，导致后续 adb/u2 交互打到
        # keyguard、dump 读不到 App 节点、元素定位失败 → 用例莫名 FAIL/BLOCKED。
        try:
            subprocess.run(self._adb("shell", "svc", "power", "stayon", "true"),
                           capture_output=True, timeout=10)
        except Exception:
            pass
        self._awake_last = 0.0       # ensure_awake 节流基准
        time.sleep(0.5)
        self.d = u2.connect(self.serial)
        # 设备信息（报告与数据库留痕：操作/断言/证据属于哪台机器）
        self.device_info = self._probe_device_info()
        # 状态检测（framework/states.py）：可执行的确定性判断，不占 AI 上下文
        # 用法: t.states.is_xxx()（场景卡 knowledge/scenarios/*.md 写「判定命令」即自动注册）
        try:
            # 直接跑用例时 framework/ 未必在 sys.path（run_case.py 会加，
            # 但 `import test_framework` 的其它入口不一定），这里兜一下。
            import os as _os, sys as _sys
            _fw = _os.path.dirname(_os.path.abspath(__file__))
            if _fw not in _sys.path:
                _sys.path.insert(0, _fw)
            from states import States
            self.states = States(serial=self.serial)
        except Exception as e:
            print(f"[states] 状态检测初始化失败（不影响主流程）: {e}")
            self.states = None      # states.py 缺失时不影响主流程
        self.steps = []
        self._cur_step = None
        self._step_start_time = None
        self._case_start_time = time.time()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.case_dir = case_dir or os.path.join(SCREENSHOT_DIR, f"case_{ts}")
        os.makedirs(self.case_dir, exist_ok=True)
        self._shot_idx = 0
        self._ocr = None
        self._dump_count = 0
        # ── RunMetrics 运行期计数（P0a，plan §7.1）────────────────────────
        # 全部用 getattr 惰性补齐：单测用 object.__new__ 绕过 __init__。
        self._wait_calls = 0      # wait_* 调用次数
        self._wait_sec = 0.0      # wait_* 累计等待（"等到就停"的实测值）
        self._rid_seen = set()    # 本轮见过的 rid 集合（指纹基线，§4.1 信号 2）
        # --stop-after N：只跑前 N 步（edit-run 调试循环）。None = 不限制。
        # 值非法按"不限制"处理：调试开关不该因一个笔误把用例彻底卡死。
        # （命令行侧的取值校验在 run_case._parse_stop_after，非法值在那里就退出了）
        try:
            _sa = os.environ.get("DSH_STOP_AFTER")
            self._stop_after = int(_sa) if _sa else None
        except ValueError:
            self._stop_after = None
        # 采集会话档案：TraceRecorder 独立模块承担落盘，TestCase 只做委托
        # （set_trace 开启后 dump 快照全落盘 + events.jsonl + index.json）
        # ⚠️ 必须在 _maybe_auto_trace() **之前**创建：那个方法会调用
        #    set_trace() 开启会话，若此处再 new 一个就把它覆盖掉
        #    （2026-09-11 踩过：enabled 被重置为 False）。
        from trace_recorder import TraceRecorder
        self.trace = TraceRecorder(STORAGE_DIR)
        self._maybe_auto_trace()
        # 视觉模型路由（VisionProvider）：用户配置（vision.json）> Agent 注入。
        # vision= 参数只在生成期/调试期由 Agent 注入（建议实现 ask/ask_json，
        # 契约见 vision_provider.py 模块头）；run_case 独立执行时无 Agent 在环，
        # 只能依赖用户配置——缺失时视觉链路降级 WARN，不升级为 ERROR。
        self._agent_vision = vision
        self._vision = None
        # 弹窗 watcher 状态：单连接 + 主流程驱动，无独立线程、无并发 dump
        self._wd_enabled = False
        self._wd_policy = "allow"
        # AI 未知弹窗处理：节流（同一次动作窗口内最多 N 次 AI 调用）
        self._ai_dialog_calls = 0
        self._ai_dialog_max = 3        # 每次动作窗口最多 AI 处理几次（防失控连环点）
        self._ai_dialog_last = 0.0     # 上次 AI 调用时间戳
        self._ai_dialog_interval = 8.0 # 两次 AI 调用最小间隔（秒）
        # SQLite 记录：用例/步骤/结果入库
        self._db = None
        self._db_case_id = None
        self._db_step_id = None
        self._db_step_ord = 0
        # SQLite 记录：**只记带 USER_INPUT 的正式用例**（用户 2026-09-11 定）。
        #   - 正式用例：源码含 USER_INPUT（run_case.py 提取 → DSH_CASE_USER_INPUT）→ 入库
        #   - 探查/补采/备数据/补验/框架自测等辅助脚本 → **完全不入库**（B1）
        #     报告/截图/trace 照常生成，只是不写记录库——记录库是给人看的验收
        #     结果，不是调试日志；探查数据已由 traces/ + probes/ 持久化。
        #   - 无 script_path（直接跑临时脚本）→ 不入库
        # 旧规则用名称前缀（PROBE_/RECON_）判定，被中文临时脚本名绕过 →
        # 实测混入 13 条脏记录。详见 _should_record 的说明。
        # --stop-after 局部执行**不入库**：只跑了前 N 步的 "PASS" 会误导记录库，
        # 也会污染 flaky 统计（§八 P1a 明确"不计入 flaky"）。
        if (self.script_path and _should_record(self.user_input, self.name)
                and self._stop_after is None):
            try:
                from db import get_db
                self._db = get_db()
                # 套件 runner 透传 suite_id（环境变量，无则单跑）
                _suite_id = os.environ.get("DSH_SUITE_ID")
                self._db_case_id = self._db.start_case(
                    self.name, self.device_info,
                    user_input=self.user_input, script_path=self.script_path,
                    suite_id=int(_suite_id) if _suite_id else None)
                # ⚠️ 「只留最新」的 drop_previous_cases **刻意不在这里**（2026-09-15 改）：
                #    在此处删 = 用例一"开始"就把上次的好记录**连同报告与截图**销毁，
                #    本次若被杀（套件超时 / Ctrl-C / 断连 / 用例崩）→ 两边都不剩。
                #    已挪到 finish() 的 finish_case 成功之后，见那里的说明。
            except Exception as e:
                # 入库失败不再静默：记录丢失意味着 Web UI/追溯链断裂
                print(f"⚠️ [db] 用例入库失败（测试继续，但本次执行无记录）: {e}")
                self._db = None
        # 中途异常也能出报告：实例一创建就登记（run_case.py 异常兜底取它补
        # finish()；否则"跑一半崩了" = 无报告 + DB 里永远没 finished_at 的悬挂记录）。
        # finish() 时会再次登记（幂等，以完成者为准）。
        global LAST_CASE
        LAST_CASE = self
        # 执行异常标记（run_case.py 捕获非 CaseAbort 异常时设置）：
        # 存在时最终结论按 ERROR（用例没跑完，结论不可信，需人工介入）
        self._fatal_error = None
        # finish() 幂等守卫：正常收尾后置位；run_case.py 异常兜底再调
        # finish() 时直接返回旧报告，不重复"备份旧报告+二次写库"
        self._finished = False
        self._report_path = None
        # 环境漂移检测基线：__init__ 取初始快照，lock_portrait() 后刷新
        # （lock_portrait 会改 accelerometer_rotation/user_rotation，
        #  不刷新的话这两个键每次都报漂移，毫无意义）。
        # finish() 前再取一次，与基线比对 → 非空记 WARN。
        self._env_ignore = set(env_ignore) if env_ignore else set()
        self._env_baseline = None
        try:
            if self.states:
                self._env_baseline = self.states.env_snapshot()
        except Exception:
            pass   # 取基线失败不阻断用例
        # 方向基线（**真实旋转**，不是 settings 值）：__init__ 取一次，
        # lock_portrait() 后刷新（否则锁屏动作本身会被判成"方向变了"），
        # finish() 再取一次 → 两者不同 = 用例中途坐标系换过 → 记 WARN。
        # 与 _env_baseline 的关键差别：该检测**不受 env_ignore 约束**
        # （env_ignore 的语义是"用例自己合法改环境"，不能掩盖外部改写）。
        self._rotation_baseline = self.device_rotation()
        # ── 清场钩子（cleanups）──────────────────────────────────────
        # 用例用 add_cleanup() 登记"必须还原"的动作，finish() 时逆序执行。
        # 存在的理由：用例中途 return t.finish() 或抛异常时，写在函数末尾的
        # 还原代码会被跳过 → 设备状态残留污染下一个用例（168 实测把
        # accelerometer_rotation 留在 1，后续用例坐标系全错）。
        # 与 try/finally 的区别：一次登记、任何退出路径都执行（含异常兜底）。
        self._cleanups = []
        self._cleanups_ran = False
        # 探针用例：进程退出兜底清理（覆盖未正常调用 finish 的悬挂场景）
        if _is_probe_case(self.name):
            import atexit
            atexit.register(self._cleanup_probe_artifacts)

    # ── 设备命令（统一带 serial，多设备时不会操作错机器）─────────────
    def _adb(self, *args):
        """构造绑定本用例 serial 的 adb 命令列表。"""
        return ["adb", "-s", self.serial, *args]

    def _adb_run(self, *args, timeout=30.0, text=True):
        """带超时的 adb 执行原语（绑定本用例 serial）。
        设备掉线/adb server 卡死时在 timeout 秒后抛 subprocess.TimeoutExpired，
        由上层异常兜底产出 ERROR 报告，而不是无限挂起。
        text=False 用于需要原始字节的场景（screencap）。返回 CompletedProcess。"""
        return subprocess.run(self._adb(*args), capture_output=True, text=text,
                              timeout=timeout)

    # ── 防锁屏保活（跑用例期间屏幕必须保持点亮）──────────────────────
    # 失败根因：长用例执行过程中设备因休眠超时被锁屏，后续 adb/u2 交互打到
    # keyguard，dump 读不到 App 节点 → 元素定位失败 → 用例莫名其妙 FAIL/BLOCKED。
    # 两层防护：
    #   1) __init__ 里 svc power stayon true：USB 供电期间屏幕常亮，从根上不锁屏；
    #   2) ensure_awake()：每次读屏/截屏前兜底唤醒 + 解 keyguard，节流下发。
    # 纯通用机制（与具体 App/用例无关），不抛异常（保活失败不应中断用例）。
    def ensure_awake(self, throttle=3.0):
        """保活：唤醒屏幕并解除 keyguard，防锁屏导致交互失败。
        throttle：内部节流，N 秒内不重复下发命令，可安全高频调用。"""
        now = time.time()
        last = getattr(self, "_awake_last", 0.0)
        if (now - last) < throttle:
            return
        self._awake_last = now
        try:
            subprocess.run(self._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP"),
                           capture_output=True, timeout=5)
            subprocess.run(self._adb("shell", "wm", "dismiss-keyguard"),
                           capture_output=True, timeout=5)
        except Exception as e:
            print(f"[ensure_awake] 保活命令失败（不影响主流程）: {e}")

    def _dump(self):
        """UI 树采集统一入口：计数 + 单点 dump。

        所有 dump_hierarchy 调用必须走这里，便于量化每用例的 UI 采集成本
        （finish() 会打印总次数）。弹窗看门狗由各调用方拿到 xml 后喂
        _run_dialog_watchers，不在这里做——保持"采集"与"检查"解耦。
        trace 模式开启时（set_trace），每次 dump 快照落盘到采集会话档案，
        供事后排查 / 补料（见 _trace_snapshot）。
        """
        # 读屏前保活：确保屏幕未锁，否则拿到的会是 keyguard 节点而非 App 界面
        self.ensure_awake()
        # 单测用 object.__new__(TestCase) 绕过 __init__，此计数属性可能缺失；
        # 惰性补齐，避免纯逻辑单测因未初始化而报错。
        if not hasattr(self, "_dump_count"):
            self._dump_count = 0
        if not hasattr(self, "trace"):
            from trace_recorder import TraceRecorder
            self.trace = TraceRecorder(STORAGE_DIR)
        self._dump_count += 1
        xml = self.d.dump_hierarchy()
        # 每次都更新"最近一份 dump"快照：`dump_snapshot()` 与失败工件包（§5）
        # 都靠它。**不在这里更新的话工件包里永远没有 dump.xml** —— 而"失败
        # 那一刻的 UI 树"正是工件包最有价值的产出（2026-09-16 真机实测：
        # BLOCKED 现场只有 state.json + 截图，dump 缺失）。
        # 只保留一份：XML 可能 ~1MB，留着旧的没有价值（要的就是"当时"）。
        self._snap = (time.time(), xml)
        self.trace.snapshot(xml, src=_dump_call_src())
        return xml

    # ── 采集会话档案：薄委托 TraceRecorder（trace_recorder.py）──────
    # 采集脚本文件名模式：这类脚本是"探路用的一次性脚本"，跑完即删，
    # 其全部价值就在**过程中留下的 dump**，故自动开 trace。
    COLLECT_SCRIPT_RE = re.compile(r"^(_collect|_probe|_explore)_.*\.py$")

    def _maybe_auto_trace(self):
        """采集脚本（_collect_*.py / _probe_*.py / _explore_*.py）自动开 trace。

        识别依据是**脚本文件名**，不靠调用方记得调 set_trace()——"机制管记性"。
        正式用例文件名（如 167.py）不匹配 → 不开 → 零额外 IO，行为不变。
        可用环境变量强制开/关：DSH_TRACE=1 / DSH_TRACE=0。
        """
        force = os.environ.get("DSH_TRACE")
        if force == "0":
            return False
        if force != "1":
            sp = self.script_path or ""
            if not self.COLLECT_SCRIPT_RE.match(os.path.basename(sp)):
                return False
        # trace 在 _dump() 里是惰性创建的，此处需先补齐（__init__ 早于首次 dump）
        if getattr(self, "trace", None) is None:
            from trace_recorder import TraceRecorder
            self.trace = TraceRecorder(STORAGE_DIR)
        self.set_trace()
        print(f"   📼 采集脚本自动开启采集会话（{os.path.basename(self.script_path or '?')}）")
        return True

    def set_trace(self, on=True):
        """开启采集会话档案：之后每次 _dump() 的 UI 树快照与关键事件全部落盘
        storage/traces/<用例名>/<会话时间戳>/。

        事后排查：events.jsonl 定位"卡在哪个动作"（超时/错误），dump 快照看
        "当时页面什么状态"；probes 缺料也能从原始快照重新解析。
        仅探查/采集脚本显式调用；正式回归不开 → 零额外 IO，行为不变。
        """
        if on:
            self.trace.start(self.name)
            print(f"   📼 采集会话: {self.trace.session_dir}")
        else:
            self.trace.stop()

    def _event(self, etype, detail, result=None, start=None):
        """统一事件日志（委托 TraceRecorder，trace 关闭时为空操作）。
        事件流与 dump 快照共享会话时间线：排查先看事件定位问题动作。"""
        rec = getattr(self, "trace", None)
        if rec is None:
            return None
        return rec.event(etype, detail, result=result, start=start)

    def _probe_device_info(self):
        """采集设备身份信息：serial + 型号 + Android 版本 + 屏幕尺寸。"""
        def _gp(k):
            r = self._adb_run("shell", "getprop", k, timeout=15)
            return r.stdout.strip()
        try:
            model = _gp("ro.product.model") or "未知型号"
            ver = _gp("ro.build.version.release") or "?"
            size = ""
            r = self._adb_run("shell", "wm", "size", timeout=15)
            m = re.search(r"(\d+x\d+)", r.stdout)
            if m:
                size = f"，{m.group(1)}"
            return f"{self.serial}（{model}，Android {ver}{size}）"
        except Exception:
            return self.serial

    # ── 视觉模型通道（颜色/布局/OCR 盲区检查 + 视觉定位）──────────────
    def _get_vision(self):
        """懒加载视觉调用统一入口（VisionProvider）：用户配置 > Agent 注入。
        视觉模型不可用时 ask/ask_json 抛 RuntimeError，由各调用方 catch
        降级 WARN——视觉链路是增强通道，缺配置不该把用例打成 ERROR。"""
        if self._vision is None:
            from vision_provider import VisionProvider
            self._vision = VisionProvider(
                agent_vision=getattr(self, "_agent_vision", None))
        return self._vision

    def vision_ask(self, prompt, rid=None, bounds=None):
        """通用视觉问答：截图（可裁剪到元素）→ 文本结论。
        走公共截图管线（screenshot.py）；裁剪保留几何信息（offset/scale），
        问答场景无需坐标换算。"""
        from screenshot import capture, crop_bounds, encode_base64
        screen = capture(self)
        b = bounds
        if b is None and rid:
            v = self.read_rid(rid)
            b = v["bounds"] if v else None
        if b:
            screen = crop_bounds(screen, b)
        return self._get_vision().ask(prompt, encode_base64(screen))

    def assert_visual(self, prompt, expect, msg="视觉断言", rid=None, bounds=None):
        """视觉断言：让视觉模型判断截图状态，期望命中关键词（expect 可含多个任一词）。
        用于颜色/布局/样式等 UI 树读不到、像素断言又不可靠的场景。"""
        try:
            answer = self.vision_ask(prompt, rid=rid, bounds=bounds)
        except Exception as e:
            return self.record("WARN", f"{msg}: 视觉调用失败 {e}")
        expects = [expect] if isinstance(expect, str) else list(expect)
        ok = any(e in answer for e in expects)
        return self.record("PASS" if ok else "FAIL",
                           f"{msg}: 视觉模型回答={answer!r}", rid=rid)

    def assert_button_state_visual(self, rid, expected, msg="视觉按钮状态断言"):
        """视觉按钮状态断言：直接让视觉模型判断按钮置灰/可点击。
        expected: 'grayed'=断言置灰 | 'clickable'=断言可点击。
        替代 _region_contrast 像素法（后者只能测亮度差，对样式变化不可靠）。"""
        state = "grayed" if expected == "grayed" else "clickable"
        prompt = ("这个 Android 界面元素处于什么状态？请判断它是否被置灰（disabled/不可点击）。"
                  "只回答：置灰 或 可点击。")
        expect = ("置灰", "灰", "不可点击", "禁用") if state == "grayed" else ("可点击", "可用")
        return self.assert_visual(prompt, expect, msg=msg, rid=rid)

    def assert_grayed_visual(self, rid, msg="视觉置灰断言"):
        """视觉置灰断言（等价 assert_button_state_visual(rid, 'grayed')）"""
        return self.assert_button_state_visual(rid, "grayed", msg=msg)

    # ── 弹窗自动点击（u2 原生 watcher，主流程驱动，零额外 dump）──────
    def _load_learned_words(self):
        """读取 AI 学习词表（AI 处理过的未知弹窗按钮），返回 {category: [words]}。
        带 (mtime, words) 缓存：文件未变时不重读磁盘（弹窗密集期每动作都调用，
        减少 I/O 开销）。"""
        global _dialog_words_cache
        _migrate_learned_words()  # 旧 framework/ 位置迁到工作区（幂等）
        try:
            mt = os.path.getmtime(LEARNED_WORDS_FILE)
        except OSError:
            return {"guide": [], "allow": [], "deny": []}
        if _dialog_words_cache["mtime"] == mt and _dialog_words_cache["words"] is not None:
            return {k: list(v) for k, v in _dialog_words_cache["words"].items()}
        try:
            import json
            with open(LEARNED_WORDS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _dialog_words_cache["mtime"] = mt
            _dialog_words_cache["words"] = data
            return {k: list(v) for k, v in data.items()}
        except (OSError, ValueError):
            return {"guide": [], "allow": [], "deny": []}

    def _learn_word(self, category, word):
        """AI 命中后学习按钮文字：持久化并入词表，下次同款弹窗走快路径"""
        if not word or len(word) > 30:
            return
        learned = self._load_learned_words()
        if word in learned.get(category, []):
            return
        learned.setdefault(category, []).append(word)
        try:
            import json
            os.makedirs(os.path.dirname(LEARNED_WORDS_FILE), exist_ok=True)
            with open(LEARNED_WORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(learned, f, ensure_ascii=False, indent=2)
            print(f"🧠 [AI弹窗] 已学习按钮 {word!r} → {category} 词表")
        except OSError as e:
            print(f"[AI弹窗] 学习词表写入失败: {e}")

    def _dialog_words(self, policy):
        """当前策略下的弹窗词：内置词表 + AI 学习词表"""
        learned = self._load_learned_words()
        if policy == "deny":
            return DIALOG_GUIDE_WORDS + tuple(learned.get("guide", [])) \
                + DIALOG_DENY_WORDS + tuple(learned.get("deny", []))
        return DIALOG_GUIDE_WORDS + tuple(learned.get("guide", [])) \
            + DIALOG_ALLOW_WORDS + tuple(learned.get("allow", []))

    def _register_dialog_watchers(self, policy=None):
        """注册 u2 原生 watcher：命中词即点击。
        匹配与点击都复用已 dump 的 source（PageSource），不产生新 dump。
        检查由主流程每次 dump 后调用 _run_dialog_watchers 触发。"""
        policy = policy or self._wd_policy
        self._wd_policy = policy
        try:
            self.d.watcher.reset()
            for w in self._dialog_words(policy):
                self.d.watcher.when(w).click()
        except Exception as e:
            print(f"[watcher] 注册失败: {e}")

    def _run_dialog_watchers(self, xml):
        """在已 dump 的 XML 上运行弹窗 watcher（不重新 dump）。
        两层：① 词表命中 → u2 watcher 点击（毫秒级）；② 词表未命中但疑似弹窗
        → AI 视觉识别（节流 + 置信度门槛 + 次数上限），覆盖未知弹窗。"""
        if not self._wd_enabled or not xml:
            return
        words = self._dialog_words(self._wd_policy)
        hit = next((w for w in words if f'text="{w}"' in xml), None)
        if hit:
            self._event("watchdog", f"词表命中 {hit!r}", result="click")
            try:
                from uiautomator2.xpath import PageSource
                self.d.watcher.run(PageSource.parse(xml))
            except Exception:
                pass
            return
        # 词表未命中：疑似未知弹窗 → AI 兜底
        self._handle_unknown_dialog(xml)

    def _handle_unknown_dialog(self, xml):
        """AI 处理未知弹窗：词表未命中时，用视觉模型识别弹窗并决策。
        触发条件：UI 树存在可点击文本节点（说明有交互浮层/对话框）。
        保护：节流（min 间隔）+ 次数上限（防失控连环点）+ 置信度门槛。"""
        # 无任何可点击文本 → 不是可交互弹窗，不触发 AI
        if not re.search(r'clickable="true"[^>]*text="[^"]+"', xml) \
           and not re.search(r'text="[^"]+"[^>]*clickable="true"', xml):
            return
        # 页面内常见按钮词（非弹窗）→ 跳过，避免把 App 普通页面误判成弹窗
        page_words = ("完成", "取消", "确定", "左转", "右转", "上一步", "下一步",
                      "保存", "删除", "添加", "更多", "设置", "返回")
        for w in page_words:
            if f'text="{w}"' in xml:
                return
        # 节流：距上次 AI 调用不足间隔 → 跳过
        now = time.time()
        if now - self._ai_dialog_last < self._ai_dialog_interval:
            return
        # 次数上限
        if self._ai_dialog_calls >= self._ai_dialog_max:
            return
        self._ai_dialog_calls += 1
        self._ai_dialog_last = now
        self._event("watchdog", "词表未命中疑似弹窗 → AI 视觉识别", result="ai_call")
        try:
            # 收口到统一截屏入口：继承 ensure_awake 锁屏防护与 serial 绑定
            # （旧实现裸拼 adb exec-out screencap 是旁路漏网）。
            raw = self._screencap_bytes()
            res = self._get_vision().ask_json(
                "这是 Android 设备截图。仅当屏幕上出现【模态弹窗/对话框】（居中浮层，"
                "背景变暗被遮罩，通常带标题和确定/取消按钮）时 is_dialog 才为 true。"
                "普通页面上的工具栏按钮、编辑表单、列表项【不算弹窗】。"
                "如果确认为弹窗，识别它并给出处理建议。只输出 JSON："
                "{\"is_dialog\": true/false, \"title\": \"弹窗标题\", "
                "\"buttons\": [\"按钮文字列表\"], "
                "\"action\": \"close|allow|deny|skip\", "
                "\"button_to_click\": \"建议点击的按钮完整文字\", "
                "\"confidence\": 0到1的置信度}。"
                "action 含义: close=点关闭/取消/知道了类按钮 dismiss 掉它; "
                "allow=点允许/同意/确定类按钮; deny=点拒绝类按钮; "
                "skip=不应自动点击（如需要用户选择/输入）。"
                "没有弹窗时 is_dialog=false, action=skip。",
                raw,
                fields=["is_dialog", "title", "buttons", "action",
                        "button_to_click", "confidence"],
                timeout=25)   # 弹窗决策短等待：网络抖动不拖 90s，宁错过下轮再查
        except Exception as e:
            print(f"🤖 [AI弹窗] 识别失败: {e}")
            return
        # 模型输出不可信时（非 dict / 置信度非数值）一律跳过，不抛异常——
        # AI 兜底是增强通道，一次异常输出不能让用例崩成 ERROR。
        if not isinstance(res, dict) or not res.get("is_dialog"):
            return
        try:
            conf = float(res.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        action = str(res.get("action") or "skip")
        btn = str(res.get("button_to_click") or "").strip()
        title = str(res.get("title") or "?")
        # 决策：按当前策略 + AI 建议 + 置信度门槛
        if conf < 0.7 or not btn:
            print(f"🤖 [AI弹窗] 置信度不足({conf:.2f})或未给出按钮，跳过: {title}")
            return
        if action == "skip":
            print(f"🤖 [AI弹窗] AI 建议不自动点击（{title}），记录后跳过")
            self.record("WARN", f"未知弹窗需人工确认: {title} (AI 建议不自动点)")
            return
        if action == "deny" and self._wd_policy != "deny":
            print(f"🤖 [AI弹窗] AI 建议拒绝但策略是 {self._wd_policy}，跳过: {title}")
            return
        if action == "allow" and self._wd_policy != "allow":
            print(f"🤖 [AI弹窗] AI 建议允许但策略是 {self._wd_policy}，跳过: {title}")
            return
        # 执行点击：优先按按钮文字点，失败则记录
        try:
            # disabled 按钮点击无效，跳过并提示（如分享选择器里未选目标时的"仅此一次"）
            xml_now = self._dump()
            disabled = any(n["text"] == btn and n["enabled"] == "false"
                           for n in _parse_nodes(xml_now))
            if disabled:
                print(f"🤖 [AI弹窗] 按钮 {btn!r} 当前 disabled，跳过（{title}）")
                return
            if self.d(text=btn).click_exists(timeout=0.6):
                print(f"🤖 [AI弹窗] 已按 AI 建议点击 {btn!r}（{title}）")
                self._event("watchdog", f"AI 点击 {btn!r}（{title}）", result="ai_click")
                # 学习：按钮文字并入对应词表，下次同款弹窗走快路径
                cat = {"close": "guide", "allow": "allow", "deny": "deny"}.get(action)
                if cat:
                    self._learn_word(cat, btn)
            else:
                self.record("WARN", f"AI 建议点 {btn!r} 但未找到按钮（{title}）")
        except Exception:
            pass

    def _check_dialogs_after_action(self, rounds=3, interval=0.5):
        """点击/输入等普通动作后的弹窗检查窗口（默认 3 轮 ≈1.5s）。

        窗口缩短后漏掉的弹窗不会丢：后续 wait_rid/wait_text/el_bounds 等
        轮询的每次 dump 仍会喂 _run_dialog_watchers，持续兜底。
        首启/授权/安装等高风险动作（弹窗可能在数秒后才冒出）用
        observe_dialogs(rounds=10) 显式开长窗口。
        """
        return self._observe_dialogs_window(rounds, interval)

    def observe_dialogs(self, rounds=10, interval=0.5):
        """高风险动作（首启/授权/安装/弹窗级联）后的长观察窗口。
        用例与 _flow.py 在这类动作后显式调用：t.observe_dialogs()。"""
        return self._observe_dialogs_window(rounds, interval)

    def _observe_dialogs_window(self, rounds, interval):
        """在窗口期内持续 dump 喂 watcher，命中即点击，不提前退出
        （权限弹窗 6-8s 自动消失，必须检测即点）。"""
        if not self._wd_enabled:
            return False
        try:
            from uiautomator2.xpath import PageSource
        except Exception:
            return False
        handled = False
        for _ in range(rounds):
            try:
                if not self._wd_enabled:
                    break
                xml = self._dump()
                words = self._dialog_words(self._wd_policy)
                if any(f'text="{w}"' in xml for w in words):
                    if self.d.watcher.run(PageSource.parse(xml)):
                        handled = True
                else:
                    # 词表未命中：疑似未知弹窗 → AI 兜底
                    self._handle_unknown_dialog(xml)
            except Exception:
                pass
            time.sleep(interval)
        return handled

    # ── 步骤管理 ────────────────────────────────────────────────────
    def step(self, name):
        """开启一个步骤，返回 self（支持 with 或直接调用）"""
        stop_after = getattr(self, "_stop_after", None)
        if stop_after is not None and len(self.steps) >= stop_after:
            # 已跑满 N 步 → 不开新步骤，主动收尾（语义见 PartialRun）。
            # record 落在第 N 步里：报告里能看见"为什么提前结束"。
            self.record("INFO", f"--stop-after {stop_after}：已执行前 {stop_after} 步"
                                "，提前收尾（局部执行，退出码 0、不入库）")
            raise PartialRun(f"--stop-after {stop_after}：只执行前 {stop_after} 步")
        self._cur_step = {"name": name, "results": [], "evidences": []}
        self._shot_in_step = 0     # M3：每步截图配额从这里重新计
        self.steps.append(self._cur_step)
        if self._db is not None and self._db_case_id is not None:
            try:
                self._db_step_ord += 1
                self._db_step_id = self._db.add_step(
                    self._db_case_id, name, self._db_step_ord)
            except Exception:
                self._db_step_id = None
        print(f"\n▶ [{name}]")
        # 每步开始时自动截图留证（步骤级证据）
        try:
            self._auto_screenshot("步骤开始")
        except Exception:
            pass
        return self

    def record(self, result, detail, rid=None, evidence=True):
        """记录一条断言结果: result ∈ {PASS, FAIL, WARN, INFO, BLOCKED}
        - rid: 关联元素 resource-id，自动附 read_rid 状态（enabled/selected/checked/clickable）
        - evidence: 所有结果默认自动截图留证（验证点截图）
        """
        if result not in RESULT_TYPES:
            raise ValueError(
                f"record() result 必须是 {RESULT_TYPES} 之一，收到 {result!r}"
                "——拼写错误必须在此暴露，不能流进报告")
        # 兜底：调用方忘了开 step 时自动补一个可追溯的步骤，而不是崩在
        # "TypeError: 'NoneType' object is not subscriptable" —— 那个报错完全
        # 看不出根因是没调 t.step()，排查成本极高（175 用例踩过）。
        # 步骤名带标注，报告里一眼能看出哪个用例漏写了 step，方便回头补规范。
        # 必须在构造 entry 之前补：_log_action 与证据登记都判 _cur_step 是否为 None，
        # 晚一步补就会静默丢掉操作日志和截图证据。
        if self._cur_step is None:
            self.step("（未显式声明 step）")
        entry = {"result": result, "detail": detail}
        # 状态快照：状态类断言必须记录实际状态值（以 case 为准原则）
        if rid:
            v = self.read_rid(rid)
            if v:
                states = {k: v[k] for k in ("enabled", "selected", "checked", "clickable")
                          if k in v}
                entry["state"] = states        # FAIL/WARN/BLOCKED 自动截屏留证（不打断正常流程）
        # 每步结果都自动截图留证：验证点截图 + FAIL/WARN/BLOCKED 必截图
        # add_to_step=False：验证点截图由 result.evidence 单独展示，不混入步骤级列表
        if evidence:
            try:
                entry["evidence"] = self._auto_screenshot(f"结果_{result}", add_to_step=False)
            except Exception:
                pass
        self._cur_step["results"].append(entry)
        # 入库（失败不阻塞测试，但不再静默——记录丢失会破坏追溯链）
        if self._db is not None and self._db_step_id is not None:
            try:
                self._db.add_result(self._db_step_id, result, detail,
                                    state=entry.get("state"),
                                    evidence=entry.get("evidence"))
            except Exception as e:
                print(f"⚠️ [db] 断言结果入库失败: {e}")
        mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                "BLOCKED": "⛔"}.get(result, "❓")
        print(f"   {mark} {entry['detail']}")
        if entry.get("state"):
            print(f"   📊 状态 {entry['state']}")
        # 证据路径已由 _auto_screenshot 内部打印，避免重复输出
        # ── 失败工件包（§五）：在 **record(FAIL/BLOCKED) 这一刻**抓现场 ──
        # 不放到 finish()：那时页面可能已经变了（甚至已回桌面），dump 说明不了
        # 失败现场。取样源是 _snap（最近一份 dump 快照）= "当时那份 XML"。
        if result in ("FAIL", "BLOCKED") and not getattr(self, "_in_probe_page", False):
            self._write_failure_bundle(detail)
        return result == "PASS"

    def _write_failure_bundle(self, detail):
        """失败工件包（§五，对标 Playwright on-fail 三件套）。

        三件：① 当时那份 dump XML；② 失败截图（force，不受 M3 每步配额限制）；
        ③ state JSON（前台包 / Activity / rotation / 降级事件）。

        **数据敏感性**：dump XML 含界面文本（日程内容、姓名等），仅供**本地排查**、
        不外发报告；需要外发时须先脱敏（§五）。
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            idx = getattr(self, "_fail_idx", 0) + 1
            self._fail_idx = idx
            d = os.path.join(self.case_dir, f"失败现场_{idx:02d}_{ts}")
            os.makedirs(d, exist_ok=True)

            # ① dump XML（当时那份快照；没有快照则明说，不假装有）
            snap = getattr(self, "_snap", None)
            xml = snap[1] if snap else None
            if xml:
                with open(os.path.join(d, "dump.xml"), "w",
                          encoding="utf-8") as f:
                    f.write(xml)

            # ② 截图（force=True：失败现场永远要留）
            # 截图先落在 case_dir（与报告的证据列表一致），**再复制一份进包内**：
            # 工件包要能独立拷走给人看，只给个相对文件名等于还得回头找。
            shot = None
            try:
                shot = self._auto_screenshot(f"失败_{idx:02d}", force=True)
                if shot and os.path.isfile(shot):
                    import shutil as _sh
                    _sh.copy2(shot, os.path.join(d, os.path.basename(shot)))
            except Exception:
                pass

            # ③ state JSON
            def _safe(fn, default=None):
                try:
                    return fn()
                except Exception:
                    return default

            state = {
                "time": ts,
                "detail": detail,
                "step": (self._cur_step or {}).get("name"),
                "package": _safe(self.current_package),
                "activity": _safe(self.current_activity),
                "rotation": _safe(lambda: self.adb_shell(
                    "settings", "get", "system", "user_rotation")),
                "has_dump": bool(xml),
                "screenshot": os.path.basename(shot) if shot else None,
                "healing": [v for v in (getattr(self, "_healing", None) or {}).values()],
            }
            with open(os.path.join(d, "state.json"), "w",
                      encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            print(f"   📦 失败现场已打包: {os.path.basename(d)}/")
            return d
        except Exception as e:
            print(f"⚠️ [失败工件包] 落盘失败（不影响结论）: {e}")
            return None

    # ── 自愈留痕 / Locator Memory（P5 §4.2）─────────────────────────
    def _write_healing_log(self):
        """把本轮的降级记录并入 `healing_log.json`（per App）。

        · 键为 **(activity, rid)**：同 App 多页面常有同名 rid（`btn_ok` /
          `id_save`），仅用 rid 会串页。
        · **APK 真的变了（归因码 apk）→ 清空该 App 全部记录**：rid 可能真变了，
          旧 fallback 不再可信（§4.2「版本门禁触发 WARN 时清除 healing 记录」）。
        · 累计 count ≥3 → 生成知识卡更新提案：这比版本号比对**更精确**的漂移信号
          （版本号可能没动，但定位已连续失效）。
        """
        h = getattr(self, "_healing", None)
        if not h:
            return None
        try:
            pkg = self._case_package_from_script() or "unknown"
            path = os.path.join(STORAGE_DIR, "healing", f"{pkg}.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {}
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f) or {}
                except Exception:
                    data = {}
            if getattr(self, "_attribution_code", None) == "apk":
                # 版本真的换了：保留计数没意义，fallback 的"身份保证"已断
                n = len(data)
                data = {}
                print(f"   🧹 APK 已变化 → 清除 {pkg} 的 {n} 条降级记录"
                      "（rid 可能真变了，旧 fallback 不可信）")
            for (act, rid), rec in h.items():
                key = f"{act}|{rid}"
                old = data.get(key) or {}
                data[key] = {
                    "activity": act, "rid": rid,
                    "layer": rec["layer"], "fallback": rec["fallback"],
                    "count": int(old.get("count", 0)) + rec["count"],
                    "last_seen": datetime.now().strftime("%Y-%m-%d"),
                }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            hits = sum(v["count"] for v in h.values())
            print(f"   🩹 本次定位降级 {hits} 次（{len(h)} 个目标）→ "
                  f"{os.path.basename(path)}")
            props = [v for v in data.values() if v["count"] >= 3]
            if props:
                self._write_card_proposals(pkg, props)
            return path
        except Exception as e:
            print(f"⚠️ [healing] 落盘失败: {e}")
            return None

    def _write_card_proposals(self, pkg, props):
        """count ≥3 的降级目标 → 知识卡更新提案（§4.2）。"""
        p = os.path.join(STORAGE_DIR, "healing", f"{pkg}_知识卡更新提案.md")
        lines = [
            f"# {pkg} 知识卡更新提案（自动生成）", "",
            f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
            "> 判据：同一 (Activity, rid) 降级命中累计 **≥3 次**。",
            "> 这是比版本号比对**更精确**的 OTA 漂移信号 —— 版本号可能没变，",
            "> 但 rid 定位已连续失效（§4.2）。", "",
        ]
        for r in sorted(props, key=lambda x: -x["count"]):
            lines.append(
                f"- `{r['rid']}`（{r['activity'] or '未知页面'}）：rid 连续找不到，"
                f"实际靠 `{r['fallback']}` 命中 **{r['count']} 次**"
                f"（层级 `{r['layer']}`，最近 {r['last_seen']}）")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"   📝 知识卡更新提案已生成: {os.path.basename(p)}"
              f"（{len(props)} 条 rid 连续降级）")

    def blocked(self, reason):
        """环境/前置不满足，无法执行"""
        return self.record("BLOCKED", f"阻塞: {reason}")

    # ── 元素操作 ────────────────────────────────────────────────────
    # 旧 _el() 已删：xpath 选择器通道已废弃，定位统一走 el_bounds/_parse_nodes

    # ── 统一动作基元 ──────────────────────────────────────────────
    # 所有 tap_* 收敛到同一条"轮询定位 → 点击 → 确认"通道，共享同一份契约：
    #   * 返回 bool：True=已点到，False=超时未找到（不再返回 self，链式调用已废弃）
    #   * 找不到元素不抛异常（旧 tap_rid 直接崩、退出码 3 的行为已移除），
    #     默认记一条 WARN；silent=True 时不记（调用方有自己的 FAIL/BLOCKED 分支，
    #     避免"守卫触发 + WARN 兜底"双重记录）
    #   * observe=False：跳过弹窗检查窗口/截图/延迟，用于"触发后立即抓 toast"
    #     的动作（默认链会占满 toast 的 ~2s 显示窗口）
    #   * 轮询期间每次 dump 都顺带驱动弹窗看门狗（与 wait_* 行为一致）
    def _tap_unified(self, rid=None, text=None, desc=None, wait=5.0,
                     observe=True, silent=False):
        """统一点击通道：轮询定位 → 点击 → 确认。返回 bool。"""
        kind = "rid" if rid else ("text" if text else "desc")
        target = rid or text or desc
        deadline = time.time() + wait
        while True:
            b = self.el_bounds(rid=rid, text=text, desc=desc)
            if b:
                t0 = time.time()
                x1, y1, x2, y2 = b
                self.d.click((x1 + x2) // 2, (y1 + y2) // 2)
                if not observe:       # 要立即抓 toast/浮层：跳过检查窗口，不延迟不截图
                    self._log_action("tap", f"{kind}={target} observe=False", t0)
                    return True
                time.sleep(ACTION_DELAY)
                self._check_dialogs_after_action()
                self._log_action("tap", f"{kind}={target}", t0)
                self._auto_screenshot(f"点击_{target}")
                return True
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if not silent:
            self.record("WARN", f"tap 未找到元素: {kind}={target!r}")
        return False

    def tap_rid(self, rid, wait=5.0, observe=True, silent=False):
        """点 resource-id 元素（推荐定位方式：rid 稳定、不受文案/多语言影响）。
        元素未出现时轮询等待（与 tap_text 同一通道、同一份失败语义）。"""
        return self._tap_unified(rid=rid, wait=wait, observe=observe, silent=silent)

    def tap_text(self, text, wait=5.0, observe=True, silent=False):
        """点文字按钮；元素未出现时轮询等待（防导航/时序抖动）。
        text 定位仅用于系统弹窗（无 rid）或文案本身即被测对象的场景。"""
        return self._tap_unified(text=text, wait=wait, observe=observe, silent=silent)

    def tap_text_re(self, pattern, timeout=8.0, clickable=None, observe=True):
        """按正则点击文字按钮（App 无关；跨 App 通用能力）。

        为什么需要它：系统权限弹窗的按钮文案会随状态变化，精确匹配必然失配。
        典型是「拒绝」——用户拒绝过一次后，系统再次弹窗时按钮变成
        「拒绝并不再询问」（见 knowledge/_system.md）。此时 tap_text("拒绝") 永远
        匹配不上，用例表现为时通时不通。用正则一次覆盖两种形态：

            t.tap_text_re(r"^拒绝(并不再询问)?$")
            t.tap_text_re(r"^(仅在使用时允许|仅本次使用时允许|全部允许|选择照片|允许)$")

        pattern   : 正则（re.search）
        timeout   : 轮询等待上限（秒）
        clickable : True 只点可点击节点；None 不限
        observe   : False 时跳过点击后的弹窗检查/截图/延迟（抓 toast 场景）
        返回命中的文案；未命中返回 ""（不记 WARN，交由调用方判断）
        """
        deadline = time.time() + timeout
        rx = re.compile(pattern)
        while time.time() < deadline:
            xml = self._dump()
            for n in _parse_nodes(xml):
                if not n["text"] or not rx.search(n["text"]):
                    continue
                if clickable is True and n["clickable"] != "true":
                    continue
                b = n.get("bounds_xy")
                if not b:
                    continue
                t0 = time.time()
                self.d.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                if observe:
                    # 不走 tap_xy：那会重复记录操作日志+截图（双重留证）。
                    # 这里的观察链与 _tap_unified 保持同一形状。
                    time.sleep(ACTION_DELAY)
                    self._check_dialogs_after_action()
                    self._log_action("tap", f"text_re={pattern} -> {n['text']!r}", t0)
                    self._auto_screenshot(f"点击_{n['text']}")
                else:
                    self._log_action("tap", f"text_re={pattern} -> {n['text']!r} observe=False", t0)
                return n["text"]
            time.sleep(0.4)
        return ""

    def el_bounds(self, rid=None, text=None, desc=None, xpath=None, xml=None):
        """按 资源id / 内容描述 / 文字 定位元素，返回 bounds (x1,y1,x2,y2) 或 None。

        **优先级 rid > desc > text**（SKILL.md 约定），三级在**同一份 dump** 内
        完成（§4.2 硬约束，见 _priority_match）。

        xml：复用一份快照查多个目标时传入（传了就不再 dump，§7.2 M4）。
        降级命中（给了 rid 却靠 desc/text 命中）会留痕 —— 那是 OTA 漂移的精确
        信号（§4.2 / P5）。
        """
        if xml is None:
            xml = self._dump()
            self._run_dialog_watchers(xml)
        nodes = _parse_nodes(xml)
        self._note_rids(nodes)          # 指纹基线（§4.1 信号 2 / §7.1）
        n, layer, status = _priority_match(nodes, rid=rid, desc=desc, text=text)
        if status == "present_no_bounds":
            # rid 在树上但不可交互（出屏/折叠/未布局）→ 这是「没滚到」，不是
            # 「改名了」。**绝不降级**去匹配 desc/text（详见 _priority_match）。
            self._note_no_bounds(rid)
            return None
        if n is not None and status in ("fallback", "ambiguous"):
            self._note_healing(rid or layer, layer, desc=desc, text=text,
                               kind=status)
        return n["bounds_xy"] if n else None

    def _note_no_bounds(self, rid):
        """记一次「rid 存在但无 bounds」（出屏 / 折叠 / 未布局）。

        与"降级"是**两件事**，必须分开：
          · 降级 = rid 没了（改名/重构）→ 归因 OTA 漂移，落 healing_log
          · 无 bounds = rid 还在但不可见 → 多半要**滚动**（`scroll_to_rid`）

        混在一起会诱导"该滚的时候去猜 text"，而那会点到无关节点。同一 rid
        反复出现即提示"这条链路该改用 scroll_to_rid"。
        """
        memo = getattr(self, "_nobounds", None)
        if memo is None:
            memo = {}
            self._nobounds = memo
        memo[rid] = memo.get(rid, 0) + 1
        if memo[rid] <= 2:
            # 只前两次提示：轮询里每次都走到这，刷屏会掩盖真信号
            print(f"   ↕️ {rid}: 在 UI 树上但无可交互 bounds（出屏/折叠）"
                  " → 需要滚动；不降级匹配 text")

    def _note_healing(self, rid, layer, desc=None, text=None, kind="fallback"):
        """记一次定位降级（P5 §4.2）。

        按 **(activity, rid)** 聚合计数 —— 不用仅 rid：同 App 多页面常有同名
        rid（`btn_ok` / `id_save`），仅用 rid 会串页。

        ⚠️ 性能：`current_activity()` 是一次 dumpsys，不能进轮询热路径。故对
        (rid, layer) 只取**一次** activity 并记忆，之后仅计数（降级发生在这里
        说明 rid 已失效，一次 dumpsys 的代价可接受）。
        """
        memo = getattr(self, "_healing_act", None)
        if memo is None:
            memo = {}
            self._healing_act = memo
        sk = (rid, layer)
        act = memo.get(sk)
        if act is None:
            try:
                act = self.current_activity() or ""
            except Exception:
                act = ""
            memo[sk] = act
        h = getattr(self, "_healing", None)
        if h is None:
            h = {}
            self._healing = h
        rec = h.get((act, rid))
        if rec is None:
            rec = {"activity": act, "rid": rid, "layer": layer,
                   "kind": kind,          # fallback（rid 没了）/ ambiguous（多个）
                   "fallback": (f"desc={desc}" if layer == "desc"
                                else f"text={text}"),
                   "count": 0}
            h[(act, rid)] = rec
        rec["count"] += 1
        return rec

    # ── RunMetrics 辅助（P0a）──────────────────────────────────────────
    def _note_rids(self, nodes):
        """累计本轮见过的 rid（元素集合指纹的原始数据，§4.1 信号 2 / §7.1）。

        惰性补齐属性：单测用 object.__new__ 绕过 __init__ 时属性可能不存在。
        取的是**本轮并集**而非"某一次 settled dump"——按 Activity 分组的精确口径
        待 settled 检测立项后启用（见 §4.1 信号 2 的"计算口径"）。
        """
        try:
            seen = getattr(self, "_rid_seen", None)
            if seen is None:
                seen = set()
                self._rid_seen = seen
            for n in nodes:
                rid = n.get("rid")
                if rid:
                    seen.add(rid)
        except Exception:
            pass

    def _note_wait(self, elapsed):
        """累计 wait_* 次数与时长（§7.1「等待」维度）。

        与 lint 静态求和（sleep_static_sec）互补、不可互替：那个只算 case 源码里
        的**固定** `time.sleep(字面量)`（下界）；这里是 wait_* 的"命中即停"实测值。
        """
        try:
            self._wait_calls = getattr(self, "_wait_calls", 0) + 1
            self._wait_sec = round(
                getattr(self, "_wait_sec", 0.0) + max(0.0, elapsed), 3)
        except Exception:
            pass

    def _count_screenshots(self):
        """本轮截图张数 / 总字节（M3 口径）。

        直接量产物目录而不是埋点计数：截图有 4 条写入路径（step/动作/验证点/
        capture_toast），逐个埋点容易漏一个；目录是唯一的真实结果。
        """
        n = total = 0
        try:
            case_dir = getattr(self, "case_dir", None)
            if not case_dir or not os.path.isdir(case_dir):
                return 0, 0
            for name in os.listdir(case_dir):
                # 同时计 PNG / JPEG / WebP（编码格式见 SHOT_FORMAT；只认 .png 会
                # 让"换了编码"之后 M3 统计直接归零 —— 指标静默失效）
                if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                p = os.path.join(case_dir, name)
                if os.path.isfile(p):
                    n += 1
                    total += os.path.getsize(p)
        except Exception:
            pass
        return n, total

    def _run_metrics_extra(self):
        """RunMetrics 增量列（P0a，plan §7.1）：静态指标 + 运行期计数。

        静态部分（脚本 hash / sleep 静态和 / 守门计数）由 run_case 经环境变量
        传入——run_case 与 TestCase 处在不同阶段，实例上拿不到；运行期部分
        （等待 / 截图 / rid 集合）在这里现算。任何一环失败都退化成"少记几列"，
        绝不影响基础记录（耗时/结果）。
        """
        try:
            import json
            import run_metrics as _rm
        except Exception:
            return {}
        try:
            extra = dict(_rm.load_static() or {})
            shots, shot_bytes = self._count_screenshots()
            rids = sorted(getattr(self, "_rid_seen", None) or ())
            extra.update({
                "package": self._case_package_from_script(),
                "wait_calls": getattr(self, "_wait_calls", None),
                "wait_sec": getattr(self, "_wait_sec", None),
                "screenshots": shots,
                "screenshot_bytes": shot_bytes,
                # 完整 rid 集合而非只存 hash：报告要输出"新增/消失的 rid"明细
                # （§10.2），只有 hash 判得出"变了没变"、判不出"哪些变了"。
                "rid_set": json.dumps(rids, ensure_ascii=False) if rids else None,
                "rid_set_hash": _rm.rid_set_hash(rids),
                # P5：自愈/降级命中次数（§7.1「质量」维度）
                "healing_hits": (sum(v["count"] for v in
                                     (getattr(self, "_healing", None) or {}).values())
                                 or None),
            })
            return _rm.sanitize_extra(extra)
        except Exception:
            return {}

    def tap_el(self, rid=None, text=None, desc=None, xpath=None, wait=5.0,
               observe=True, silent=False):
        """按 资源id/文字/内容描述/xpath 点击（元素定位优先，坐标兜底）。
        xpath 参数当前未实现（el_bounds 不支持 xpath），传了也按 rid/text/desc 走。"""
        return self._tap_unified(rid=rid, text=text, desc=desc, wait=wait,
                                 observe=observe, silent=silent)
    
    def tap_desc(self, desc, wait=5.0, observe=True, silent=False):
        """按 content-desc 点击（图标按钮常用）。"""
        return self._tap_unified(desc=desc, wait=wait, observe=observe, silent=silent)

    # ── 组合属性定位（新用例推荐入口）──────────────────────────────
    # 背景：单属性定位（tap_text / tap_desc / el_bounds）语义太弱——
    # desc="日历" 可同时命中桌面图标、widget、长按菜单；text="日历" 会
    # 命中列表项容器+名称标签+其他页面同名节点。组合语义属性（desc+cls、
    # text+clickable、rid+contains...）是 XPath 多条件与的等价实现，
    # 走 _parse_nodes 同一解析通道，行为与 el_bounds/tap_* 完全同构。
    def locate(self, rid=None, desc=None, text=None, cls=None,
               clickable=None, contains=None):
        """组合属性定位，返回 _Located（exists/click/long_click/bounds/center/node）。

        定位优先级约定（SKILL.md）：resource-id > content-desc > text；
        每个传入属性都必须能回答"为什么它必须成立"，答不上来的不加
        （过度约束 = 系统改版即失效）。禁止位置索引/bounds 约束。

        示例:
            t.locate(desc="卸载", cls="android.widget.ImageView").click()
            t.locate(desc="日历", clickable=True).long_click()   # 列表项容器
            it = t.locate(text="恢复", contains="日历")
            if it.wait(5): it.click()
        """
        spec = {k: v for k, v in dict(rid=rid, desc=desc, text=text, cls=cls,
                                      clickable=clickable, contains=contains).items()
                if v is not None}
        if not spec:
            raise ValueError("locate() 至少需要一个定位属性")
        return _Located(self, spec)

    def long_press_xy(self, x, y, duration=1.0):
        """坐标长按（ATX 手势）。返回 bool。
        为什么不用 input swipe 同坐标模拟：launcher 上 swipe 长按经常
        弹不出菜单、UI 无任何变化（实测多次复现，见 knowledge/_system.md）。
        优先 d.long_click(x, y)（u2 设备手势）；不可用时回退 touch API。"""
        try:
            try:
                self.d.long_click(x, y, duration)
            except TypeError:
                self.d.long_click(x, y)
            return True
        except Exception:
            try:
                with self.d.touch.down(x, y):
                    time.sleep(duration)
                return True
            except Exception as e:
                print(f"[long_press_xy] 坐标长按失败 ({x},{y}): {e}")
                return False

    # ── 必需操作（强语义）：找不到元素 = FAIL 并中止用例 ─────────────
    # tap_* 系列失败只记 WARN（可选步骤用）；链路关键步骤用 require_*，
    # 防止"元素没找到但后面忘了断言"导致的假通过。
    # 实现统一为"轮询内直接点击"（tap_* 的 silent 模式）：旧实现"先 wait 后 tap"
    # 两步之间存在竞态窗口（wait 命中后元素消失 → tap 降级 WARN 或裸崩），
    # 与"必需操作失败 = FAIL 中止"的契约冲突。
    def _require_absent(self, detail, on_absent, exc_msg):
        """require_* 失败时的统一收口：记 FAIL 或 BLOCKED，再抛对应异常。

        `on_absent="BLOCKED"` 用于**前置条件类**的必需元素（配置未命中 / 数据未
        准备 / 换设备布局不同）：记 BLOCKED → 退出码 2，不污染缺陷库（§10.3）。
        """
        if str(on_absent).upper() == "BLOCKED":
            self.record("BLOCKED", detail)
            raise CaseBlocked(exc_msg)
        self.record("FAIL", detail)
        raise CaseAbort(exc_msg)

    def require_tap_text(self, text, wait=8.0, msg=None, on_absent="FAIL"):
        """必须点到指定文字的元素；等不到记 FAIL 并抛 CaseAbort 中止用例。
        on_absent="BLOCKED" → 记为阻塞（退出码 2），用于前置条件类元素。"""
        if not self.tap_text(text, wait=wait, silent=True):
            self._require_absent(msg or f"必需元素未出现: text={text!r}，用例中止",
                                 on_absent, f"require_tap_text({text!r}) 超时")
        return True

    def require_tap_rid(self, rid, wait=8.0, msg=None, on_absent="FAIL"):
        """必须点到指定 resource-id 的元素；等不到记 FAIL 并抛 CaseAbort。
        on_absent="BLOCKED" → 记为阻塞（退出码 2）。"""
        if not self.tap_rid(rid, wait=wait, silent=True):
            self._require_absent(msg or f"必需元素未出现: rid={rid!r}，用例中止",
                                 on_absent, f"require_tap_rid({rid!r}) 超时")
        return True

    def require_tap_el(self, rid=None, text=None, desc=None, wait=8.0, msg=None,
                       on_absent="FAIL"):
        """必须点到元素（rid/text/desc 任一）；等不到记 FAIL 并抛 CaseAbort。
        on_absent="BLOCKED" → 记为阻塞（退出码 2）。"""
        if not self.tap_el(rid=rid, text=text, desc=desc, wait=wait, silent=True):
            self._require_absent(
                msg or f"必需元素未出现: rid={rid} text={text} desc={desc}，用例中止",
                on_absent, f"require_tap_el({rid or text or desc!r}) 超时")
        return True

    def block_unless(self, cond, reason, probe=None):
        """前置条件不满足 → 记 BLOCKED 并抛 CaseBlocked（中止用例）。

        cond   : 布尔，或返回布尔的 callable（惰性求值：只在需要时才 dump/查询）
        reason : 给人看的前置条件说明（进报告，也进 §10.1 的 Ask 通道）
        probe  : 可选现场信息（如关键 rid 是否存在），便于人判断该补什么

        用法：
            t.block_unless(lambda: t.el_bounds(rid=RID_LIST), "需先有一条课程表")
        """
        try:
            ok = cond() if callable(cond) else bool(cond)
        except Exception as e:
            ok = False
            reason = f"{reason}（判定本身抛异常: {e}）"
        if ok:
            return True
        msg = f"前置条件不满足: {reason}"
        if probe is not None:
            msg += f"｜现场: {probe}"
        self.record("BLOCKED", msg)
        raise CaseBlocked(msg)

    def tap_xy(self, x, y, observe=True):
        """坐标点击（最后手段；优先用 tap_el/tap_text/tap_rid）。返回 True
        （坐标点击不存在"找不到元素"，与 tap_* 统一 bool 契约，不再返回 self 链式）。
        observe=False：跳过弹窗检查窗口/截图/延迟 —— 用于"触发后要立即抓 toast"
        的动作（tap 默认链的弹窗检查会占满 toast 的 ~2s 显示窗口，导致抓空）。"""
        t0 = time.time()
        self.d.click(x, y)
        if not observe:
            self._log_action("tap", f"x={x}, y={y} observe=False", t0)
            return True
        time.sleep(ACTION_DELAY)
        self._check_dialogs_after_action()
        self._log_action("tap", f"x={x}, y={y}", t0)
        self._auto_screenshot(f"点击坐标_{x}_{y}")
        return True

    def tap_vision(self, description, repeat=1, repeat_interval=0.15, verify="",
                   bounds=None, crop_dialog=True, prefer_ocr=False,
                   observe=True, silent=False, timeout=30.0):
        """视觉定位点击（最后手段；优先 tap_el/tap_text/tap_rid）。

        适用：view tree 与 OCR 均无法定位的元素（Canvas/色盘/无文字图标/
        WebView 私有控件）。返回 bool：成功 True；失败（模型不可用/解析
        失败/verify 未通过）时 silent=False 记 WARN 并返回 False，不抛
        ERROR——视觉链路是增强通道，缺失时用例应继续走其他断言。
        observe 与 silent 正交，语义与 _tap_unified 一致。

        description     : 自然语言描述目标（如 "紫色色块"）
        repeat          : 同坐标连点次数（每次独立走 observe 复核链）
        repeat_interval : 连点间隔秒（防系统合并连续 tap 事件）
        verify          : 点击后让视觉模型判断的陈述句（非空时点后再截图问证）
        bounds          : 限定搜索区域 (x1,y1,x2,y2)（设备坐标）
        crop_dialog     : 无显式 bounds 时自动裁剪到弹窗区（UI 树识别
                          Panel 类容器；识别失败回退全屏，不阻塞）
        prefer_ocr      : 预留 P2 的 OCR 快速通道（当前未生效，勿依赖）
        observe         : 点击后弹窗检查/截图/延迟（与 _tap_unified 同义）
        silent          : 失败不记 WARN（调用方有自己的 FAIL 分支时用）
        timeout         : 视觉调用超时秒（传导至 VisionProvider → Vision）

        示例:
            if not self.tap_vision("紫色色块"):
                return self.record("FAIL", "视觉点击未命中目标")
        """
        t0 = time.time()
        if prefer_ocr:
            print("[tap_vision] prefer_ocr 快速通道为 P2 能力，当前未启用，走视觉模型定位")
        try:
            from screenshot import capture, crop_bounds, resize_for_vision
            from vision_tap import (_coordinate_tap, _som_tap,
                                    find_dialog_bounds, resolve_strategy)
        except ImportError as e:
            if not silent:
                self.record("WARN", f"tap_vision 依赖缺失: {e}")
            return False
        vp = self._get_vision()
        if not vp.available():
            if not silent:
                self.record("WARN", f"视觉模型不可用（未配置且未注入 Agent vision）: "
                                    f"{description!r}")
            return False
        try:
            # 1. 截图（复用 _screencap_bytes，继承锁屏防护与 serial 绑定）
            screen = capture(self)
            # 2. 裁剪：显式 bounds > 弹窗自动识别 > 全屏
            if bounds:
                screen = crop_bounds(screen, bounds)
            elif crop_dialog:
                db = find_dialog_bounds(self._dump(), screen.original_size)
                if db:
                    screen = crop_bounds(screen, db)
            # 3. 策略与定位（tap_strategy 显式配置优先，auto 按模型名启发）
            strategy = resolve_strategy(vp.model_name(), explicit=vp.tap_strategy())
            if strategy == "coordinate":
                screen = resize_for_vision(screen)   # 坐标策略输出比例，允许压缩
                x, y, reason = _coordinate_tap(vp, screen, description, timeout=timeout)
            else:                                     # som（默认）：不压缩（scale=1.0）
                x, y, reason = _som_tap(vp, screen, description, timeout=timeout)
            # 4. 决策留痕（坐标/策略/模型/reason 进时间轴与 DB，事后可复盘）
            self._log_action(
                "tap_vision",
                f"desc={description!r} strategy={strategy} -> ({x},{y}) "
                f"model={vp.model_name()!r} reason={reason}",
                t0)
            # 5. 执行点击（tap_xy 内部自带 observe 链与点击后截图）
            ok = True
            for i in range(max(1, int(repeat))):
                if i:
                    time.sleep(repeat_interval)
                ok = self.tap_xy(x, y, observe=observe) and ok
            # 6. verify：点后再截图问视觉模型，作为断言证据
            if verify and ok:
                ok = self._tap_vision_verify(verify, bounds=bounds, timeout=timeout)
            return ok
        except Exception as e:
            if not silent:
                self.record("WARN", f"tap_vision 失败: {description!r} ({e})")
            return False

    def _tap_vision_verify(self, verify, bounds=None, timeout=None):
        """tap_vision 的点击后验证：截新图让视觉模型判断陈述真假。

        通过记 INFO（不占断言计数，verify 证据是辅助性判断）；未通过记
        WARN 并使 tap_vision 返回 False；验证链路本身异常同样返回 False
        （显式要求的验证没完成，结果不可信）。"""
        try:
            from screenshot import capture, crop_bounds, encode_base64
            screen = capture(self)
            if bounds:
                screen = crop_bounds(screen, bounds)
            data = self._get_vision().ask_json(
                f"点击操作后的 Android 截图。请判断以下陈述是否为真：\n{verify}\n"
                f"只输出 JSON: {{\"answer\": true 或 false, \"reason\": \"简短依据\"}}。",
                encode_base64(screen),
                fields=["answer", "reason"],
                timeout=timeout,
            )
            ans = data.get("answer") if isinstance(data, dict) else None
            ok = (ans is True) or (str(ans).strip().lower() in ("true", "yes", "1"))
            reason = str(data.get("reason") or "") if isinstance(data, dict) else ""
            if ok:
                self.record("INFO", f"tap_vision 验证通过: {verify} ({reason})")
            else:
                self.record("WARN", f"tap_vision 验证未通过: {verify} ({reason})")
            return ok
        except Exception as e:
            self.record("WARN", f"tap_vision 验证调用失败: {e}")
            return False

    def input_text(self, rid, text, wait=5.0, silent=False):
        """点输入框并输入文本（与 tap_* 同一契约：轮询定位，返回 bool）。
        找不到输入框时默认记 WARN；silent=True 时不记（调用方有自己的
        FAIL/BLOCKED 守卫分支时用，避免双重记录）。"""
        deadline = time.time() + wait
        while True:
            b = self.el_bounds(rid=rid)
            if b:
                t0 = time.time()
                self.d.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                time.sleep(ACTION_DELAY)
                self.d.send_keys(text)
                time.sleep(ACTION_DELAY)
                self._check_dialogs_after_action()
                self._log_action("input", f"rid={rid}, text={text}", t0)
                self._auto_screenshot(f"输入_{rid}_{text[:10]}")
                return True
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if not silent:
            self.record("WARN", f"input_text 未找到输入框: rid={rid!r}")
        return False

    def clear_text(self, rid, wait=5.0, silent=False):
        """点输入框并清空内容（与 tap_* 同一契约：轮询定位，返回 bool；
        silent=True 时找不到不记 WARN）。"""
        deadline = time.time() + wait
        while True:
            b = self.el_bounds(rid=rid)
            if b:
                t0 = time.time()
                self.d.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                time.sleep(ACTION_DELAY)
                self.d.clear_text()
                time.sleep(ACTION_DELAY)
                self._log_action("clear", f"rid={rid}", t0)
                self._auto_screenshot(f"清空_{rid}")
                return True
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if not silent:
            self.record("WARN", f"clear_text 未找到输入框: rid={rid!r}")
        return False

    def read_rid(self, rid):
        """读取元素属性字典: text/checked/enabled/selected/clickable/bounds
        （状态值为原始 "true"/"false" 字符串，属性缺失时为 ""）"""
        xml = self._dump()
        self._run_dialog_watchers(xml)
        for n in _parse_nodes(xml):
            if n["rid"] == rid:
                return {"text": n["text"], "checked": n["checked"],
                        "enabled": n["enabled"], "selected": n["selected"],
                        "clickable": n["clickable"], "bounds": n["bounds_xy"]}
        return None

    def first_clickable(self, y_min, y_max):
        """在指定 y 区间找第一个可点击元素中心（按 y 从小到大）"""
        xml = self._dump()
        self._run_dialog_watchers(xml)
        cands = []
        for n in _parse_nodes(xml):
            if n["clickable"] != "true" or not n["bounds_xy"]:
                continue
            x1, y1, x2, y2 = n["bounds_xy"]
            if y_min <= y1 <= y_max:
                cands.append(((x1 + x2) // 2, (y1 + y2) // 2, y1))
        cands.sort(key=lambda c: c[2])
        return (cands[0][0], cands[0][1]) if cands else None

    # ── 条件等待（等界面一律用这些，禁止裸 sleep 碰运气）─────────────
    def wait_rid(self, rid, timeout=10.0, interval=0.5):
        """轮询等待元素（resource-id）出现。出现返回 True，超时 False。
        每次轮询都会 dump UI 树并顺带驱动弹窗看门狗。"""
        deadline = time.time() + timeout
        t0 = time.time()
        hit = False
        while True:
            if self.el_bounds(rid=rid):
                hit = True
                break
            if time.time() >= deadline:
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)          # RunMetrics：等待次数/时长
        self._event("wait", f"rid={rid}", result="hit" if hit else "timeout",
                    start=t0)
        return hit

    def wait_text(self, text, timeout=10.0, interval=0.5):
        """轮询等待指定文字出现。出现返回 True，超时 False。"""
        deadline = time.time() + timeout
        t0 = time.time()
        hit = False
        while True:
            if self.el_bounds(text=text):
                hit = True
                break
            if time.time() >= deadline:
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)          # RunMetrics：等待次数/时长
        self._event("wait", f"text={text}", result="hit" if hit else "timeout",
                    start=t0)
        return hit

    def wait_activity(self, substr, timeout=10.0, interval=0.5):
        """轮询等待前台 Activity 包含 substr（大小写不敏感）。
        命中返回完整 Activity 名，超时返回 ""（falsy，可直接当 bool 用）。"""
        deadline = time.time() + timeout
        t0 = time.time()
        act = ""
        while True:
            try:
                act = self.current_activity()
            except Exception:
                act = ""
            if substr.lower() in act.lower():
                break
            if time.time() >= deadline:
                act = ""                           # 超时统一返回 ""（falsy 契约）
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)          # RunMetrics：等待次数/时长
        self._event("wait", f"activity={substr}",
                    result="hit" if act else "timeout", start=t0)
        return act

    def wait_gone(self, rid=None, text=None, desc=None, timeout=10.0,
                  interval=0.5):
        """轮询等待元素**消失**（删除类断言的另一半）。消失 True，超时 False。

        为什么需要它：现有 wait_* 全是"等出现"，于是"课程已消失"这类断言只能写成
        `assert not t.el_bounds(...)`（删除动画还没播完就判 → **假 FAIL**）或
        `time.sleep(2)` 再判（正是 M2 要消灭的裸 sleep）。

        无判据时**抛错**而不是返回 True —— 后者会让"忘了传参数"变成一条静默的
        假 PASS（§〇：断言类不许静默降级）。
        """
        if not (rid or text or desc):
            raise ValueError("wait_gone 需要 rid / text / desc 至少一个判据")
        deadline = time.time() + timeout
        t0 = time.time()
        gone = False
        while True:
            if not self.el_bounds(rid=rid, text=text, desc=desc):
                gone = True
                break
            if time.time() >= deadline:
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)          # RunMetrics：等待次数/时长
        self._event("wait", f"gone rid={rid} text={text} desc={desc}",
                    result="hit" if gone else "timeout", start=t0)
        return gone

    def wait_text_contains(self, sub, timeout=10.0, interval=0.5):
        """轮询等待**子串**出现（`wait_text` 是精确匹配，这里是 contains）。

        为什么需要它：`wait_text` 走 `el_bounds(text=...)` = **精确相等**，而现实里
        要等的东西常是"某句话里含某个词" —— 典型是询问框文案
        「是否根据课程时长和休息时长自动调整其他课程」，等它只能靠子串。
        旧写法只能 `time.sleep(2.5)` + 一次性 `screen_text()` 判，那正是碰运气
        （界面慢一点就假 FAIL，快一点就白等）。

        ⚠️ 不要拿它等"弹框是否还开着"（子串无法表达"消失"）：那种场景用
        `wait_gone(text=<按钮文案>)`。
        """
        deadline = time.time() + timeout
        t0 = time.time()
        hit = False
        while True:
            if any(sub in x for x in self.screen_text()):
                hit = True
                break
            if time.time() >= deadline:
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)
        self._event("wait", f"text~={sub}", result="hit" if hit else "timeout",
                    start=t0)
        return hit

    def wait_text_any_contains(self, subs, timeout=10.0, interval=0.5):
        """等**任意一个**子串出现（共享一个时间预算）。返回命中的那个，超时返回 ""。

        为什么单列一个 API：现实里"等结果"常常是**多候选**——权限被拒后的提示可能是
        「相机权限」也可能是「前往设置」（同一语义两种文案，见
        `knowledge/_system.md`）。用单个 `wait_text_contains` 会漏一种；**顺序等两次**
        会把时间预算翻倍（3+3=6s）。这里共用一个 deadline，命中即返回命中的那个
        （所以它同时承担"等到了"与"等到的是哪种"两个信息）。

        返回空串是 falsy 契约，与 `wait_activity` 一致，可直接当 bool 用。
        """
        subs = [s for s in (subs or ()) if s]
        if not subs:
            raise ValueError("wait_text_any_contains 需要至少一个候选子串")
        deadline = time.time() + timeout
        t0 = time.time()
        hit = ""
        while True:
            txt = " ".join(self.screen_text())
            for s in subs:
                if s in txt:
                    hit = s
                    break
            if hit or time.time() >= deadline:
                break
            time.sleep(interval)
        self._note_wait(time.time() - t0)
        self._event("wait", f"text~any={subs}", result=hit or "timeout",
                    start=t0)
        return hit

    # ── dump 快照复用（§7.2 M4）─────────────────────────────────────
    # 目标：dump 次数 ≤2×步数（基线 116 ≈ 3.7×步数）。
    # 两个消费方都要求"一次 dump 供多处使用"：
    #   · 组合定位的优先级链（§4.2）——三级降级必须在同一份树上完成
    #   · 失败工件包取样（§五）——FAIL 时抓"当时那份"XML
    # ⚠️ TTL 必须**短**：缓存越久，"看到旧界面"的风险越大，而 UI 当前状态
    # 正是断言的前提。轮询里一律 refresh=True（每次都要新鲜）。
    SNAPSHOT_TTL = 1.5

    def dump_snapshot(self, ttl=None, refresh=False):
        """取一份 dump（短 TTL 内复用）。返回 XML 字符串。

        用例里出现"同一时刻要查多个目标"时用它，避免 N 次 dump：
            xml = t.dump_snapshot()
            if t.el_bounds(rid=A, xml=xml) or t.el_bounds(text=B, xml=xml): ...
        """
        ttl = self.SNAPSHOT_TTL if ttl is None else ttl
        now = time.time()
        snap = getattr(self, "_snap", None)
        if not refresh and snap and (now - snap[0]) <= ttl:
            self._snap_hits = getattr(self, "_snap_hits", 0) + 1
            return snap[1]
        xml = self._dump()
        self._snap = (now, xml)
        self._snap_miss = getattr(self, "_snap_miss", 0) + 1
        return xml

    def region_of(self, rid=None, text=None, desc=None, pad=20):
        """元素 bounds → OCR 区间 `(y_min, y_max, x_min, x_max)`；取不到 None。

        §3.1「数值类缓存禁止进 case」的运行时替代：卡片/用例里不再写死
        y_min/y_max，改为从 anchor 元素现场派生（换设备/换方向自动成立）。
        """
        b = self.el_bounds(rid=rid, text=text, desc=desc)
        if not b:
            return None
        x1, y1, x2, y2 = b
        return (max(0, y1 - pad), y2 + pad, max(0, x1 - pad), x2 + pad)

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        """通用滑动（坐标由调用方按**当次屏幕尺寸**派生，禁止写死像素）。

        各用例此前手写 `t.d.swipe` / `adb shell input swipe`（21 文件里散落多处），
        统一入口后动作计数与失败语义一致（§2.2）。
        """
        t0 = time.time()
        try:
            self.d.swipe(x1, y1, x2, y2, duration)
        except Exception as e:
            try:
                self.adb_shell("input", "swipe", str(int(x1)), str(int(y1)),
                               str(int(x2)), str(int(y2)),
                               str(int(duration * 1000)))
            except Exception as e2:
                print(f"[swipe] 失败 ({x1},{y1})→({x2},{y2}): {e} / {e2}")
                return False
        self._log_action("swipe", f"({x1},{y1})→({x2},{y2}) d={duration}", t0)
        return True

    def scroll_to_rid(self, rid, timeout=12.0, max_swipes=6, direction="up",
                      ratio=0.5, x_ratio=0.5, settle=0.4):
        """循环"滑动 → 查树"直到 rid 出现且有 bounds。返回 bounds 或 None。

        解决 §2.4 归因②「没滚到」：小屏/换形态后控件进 overflow、长列表懒加载
        （178 的"晚上课程"就是首屏不渲染）。此时 `wait_rid` **永远等不到** ——
        rid 根本不在树上，不是渲染慢，等多久都没用。

        direction="up"：内容向上滚（手指上滑 → 看到下面的内容）；"down" 反之。
        位置全部按屏幕比例派生，不写死像素。
        """
        b = self.el_bounds(rid=rid)
        if b:
            return b
        w, h = self._screen_size()
        x = int(w * x_ratio)
        step = int(h * ratio)
        if direction == "up":
            y_from, y_to = int(h * 0.75), int(h * 0.75) - step
        else:
            y_from, y_to = int(h * 0.25), int(h * 0.25) + step
        deadline = time.time() + timeout
        for _ in range(max_swipes):
            if time.time() >= deadline:
                break
            if not self.swipe(x, y_from, x, y_to, 0.3):
                break
            time.sleep(settle)      # settle：等惯性滚动停下（无 UI 信号可等）
            b = self.el_bounds(rid=rid)
            if b:
                return b
        return None

    # ── 系统级操作（通用前置条件）────────────────────────────────
    def adb_shell(self, *args):
        """执行 adb shell 命令（已绑定本用例 serial），返回 stdout"""
        r = self._adb_run("shell", *args, timeout=30)
        return r.stdout.strip()

    def pm_clear(self, package, confirm=False):
        """清空 App 数据，重置到首次使用状态（pm clear）

        前置条件（环境准备）默认不产生断言，避免污染「共 N 条断言」统计：
        - 成功：仅记录操作到时间轴，不计为断言
        - 失败：环境准备未完成，必须暴露为 FAIL（否则测试结果不可信）
        - confirm=True：恢复旧行为，成功也记一条 PASS 断言
        """
        t0 = time.time()
        out = self.adb_shell("pm", "clear", package)
        ok = "Success" in out
        self._log_action("pm_clear", f"package={package}, ok={ok}", t0)
        if not ok:
            self.record("FAIL", f"pm clear {package} 失败（环境准备未完成）: {out}")
        elif confirm:
            self.record("PASS", f"pm clear {package}: 成功")
        return ok

    def force_stop(self, package):
        """强制停止 App"""
        return self.adb_shell("am", "force-stop", package)

    def launch_app(self, package):
        """冷启动 App（monkey LAUNCHER 入口，已绑定本用例 serial）。
        用例里禁止裸拼 `adb shell monkey ...`：多设备时会打到 adb 默认选中
        的那台，与框架"操作、断言、证据锁定同一 serial"的承诺矛盾。"""
        return self.adb_shell("monkey", "-p", package,
                              "-c", "android.intent.category.LAUNCHER", "1")

    def getprop(self, name):
        """读系统属性，如: getprop('ro.build.type') / getprop('persist.sys.xxx')"""
        return self.adb_shell("getprop", name)

    def settings_get(self, scope, key):
        """读系统设置: scope ∈ global|secure|system"""
        return self.adb_shell("settings", "get", scope, key)

    def settings_put(self, scope, key, value):
        """写系统设置"""
        return self.adb_shell("settings", "put", scope, key, value)

    # ── 方向探测（读**真实旋转**，不是 settings 值）────────────────────
    # 为什么必须读 mRotation：
    #   `user_rotation` 只在 `accelerometer_rotation=0` 时被系统采纳；自动旋转
    #   开着时它是**被忽略的残值**——这正是"实测 user_rotation=0 却是横屏"
    #   那次误判的来源（当时 accel=1，测到的是设备物理姿态，不是设置值映射）。
    #   而用例关心的永远是"屏幕现在实际是竖是横"（决定坐标系），故读实际值。
    # 为什么不用 u2 的 d.info：实测 Android 17 上它直接抛 RPCUnknownError
    #   （ApplicationSharedMemory not initialized @ UiDevice.getDisplaySizeDp），
    #   与 smoke.py:64 的注释一致 —— 不能依赖。
    # 成本：`dumpsys window displays` 实测约 0.21s（单次）。
    _ROT_RE = re.compile(r"mRotation=(\d+)\s+mDeferredRotationPauseCount")
    _ROT_RE_FALLBACK = re.compile(r"^\s+mRotation=(\d+)\s*$", re.M)

    def device_rotation(self):
        """当前真实屏幕旋转（0/1/2/3）；任何失败返回 None（收尾绝不能因此中断）。

        0/2 = 竖，1/3 = 横。读不到（adb 异常 / 未连接 / 单测桩对象）→ None，
        调用方按"未知"处理（不比较、不误报）。
        """
        try:
            out = self.adb_shell("dumpsys", "window", "displays")
        except Exception:
            return None
        if not out:
            return None
        m = self._ROT_RE.search(out) or self._ROT_RE_FALLBACK.search(out)
        try:
            return int(m.group(1)) if m else None
        except (AttributeError, ValueError):
            return None

    # ── 旋屏约定（套件基线 = 竖屏锁定，见 docs/case-writing.md）─────────────
    # 血泪教训：119 曾在 finally 写死 accelerometer_rotation=1"还原现场"，
    # 结果设备立马转成横屏，下一个脚本坐标系全错、长按点到状态栏拉下
    # 通知面板。本套件所有用例都在竖屏下执行——普通用例开头 lock_portrait
    # 结尾不恢复（基线即竖屏锁定，"不动"就是正确的现场）；只有真正中途
    # 转屏的用例才用 snapshot_rotation/restore_rotation 成对出现。
    def lock_portrait(self):
        """锁定竖屏（套件基线）。用例开头调用；结尾无需恢复。

        ⚠️ **本方法只保证"调用后立刻是竖屏"，不保证"用例全程是竖屏"**：
        实测 App 冷启动（`pm_clear` + `launch_app`）会把
        `accelerometer_rotation` 改回 1（0→1），锁定态被解开，设备随后可能
        随物理姿态翻回横屏。178 实测正是如此：`lock_portrait()` 后
        cur=1904x3040 / mRotation=0（竖屏 ✅），前置冷启动后 accel 变回 1，
        全用例实际以横屏跑完并 PASS —— 因为它的坐标**全部现场派生**。

        所以：本方法不是"方向保证"，只是"把起始姿态摆正"。
        **全程方向保证在 finish() 的方向变化检测**（见 `_rotation_baseline`），
        它不受 env_ignore 约束。
        """
        self.adb_shell("settings", "put", "system", "accelerometer_rotation", "0")
        self.adb_shell("settings", "put", "system", "user_rotation", "0")
        time.sleep(0.5)
        self._rotation_baseline = self.device_rotation()   # 刷新方向基线（见 __init__）
        # 刷新漂移基线：lock_portrait 会改旋转设置，不刷新则这两个键
        # 每次都报漂移。刷新后 finish() 只检测用例主体逻辑是否污染了环境。
        try:
            if self.states:
                self._env_baseline = self.states.env_snapshot()
        except Exception:
            pass

    def snapshot_rotation(self):
        """记录当前旋转状态。需要中途转屏的用例：转屏前快照，finally 恢复。"""
        return {"accel": self.settings_get("system", "accelerometer_rotation"),
                "user": self.settings_get("system", "user_rotation")}

    # ── 清场钩子（cleanups）────────────────────────────────────────
    # 设计要点（改动前请读完）：
    #  * 逆序执行（LIFO）：与 try/finally 的栈式展开一致，成对操作能正确嵌套还原。
    #  * 单个钩子失败不影响其它：清场是"尽力而为"，不能因为某步失败就丢掉
    #    后续还原，更不能把用例结论改写成 ERROR（清场失败 ≠ 测试失败）。
    #  * 失败记 WARN 并留痕：清场没做干净是**环境问题**，必须让人看见，
    #    否则下一个用例的莫名 FAIL 无从追溯（这是本机制要根治的症状）。
    #  * 必须跑在漂移检测之前：否则清场动作自己会被 env_diff 判成污染。
    def add_cleanup(self, fn, *args, **kwargs):
        """登记一个清场动作，finish() 时逆序执行。

        fn 可以是可调用对象，或形如 (callable, args, kwargs) 的元组。
        典型用法：t.add_cleanup(t.restore_rotation, snap)
        返回 None（登记即可，无需关心返回值）。
        """
        self._cleanups.append((fn, args, kwargs))
        return None

    def _run_cleanups(self):
        """逆序执行所有已登记清场动作。幂等：重复调用只跑一次。

        在 finish() 中、环境漂移检测之前调用，保证清场后的状态才是比对基准。
        """
        if self._cleanups_ran:
            return
        self._cleanups_ran = True
        while self._cleanups:
            fn, args, kwargs = self._cleanups.pop()
            label = getattr(fn, "__name__", repr(fn))
            try:
                fn(*args, **kwargs)
            except Exception as e:
                # 清场失败不改写结论（不是测试失败），但必须留痕：
                # 环境没还原干净会导致下一个用例莫名失败，得能追到源头。
                try:
                    self.record("WARN", f"清场失败（设备状态可能残留）: "
                                        f"{label} → {type(e).__name__}: {e}")
                except Exception:
                    print(f"⚠️ 清场失败（且记录失败）: {label} → {e}")

    def add_prop_restore(self, key, value=None):
        """登记一条系统属性的还原：用例改了什么，这里照原样还回去。

        value=None 时先读取当前值作为"原值"，再登记还原动作——即
        「改之前调用，退出时自动还原」。用法：
            t.add_prop_restore("accelerometer_rotation")   # 先读原值
            t.adb_shell("settings", "put", "system", "accelerometer_rotation", "1")
        """
        if value is None:
            try:
                value = self.settings_get("system", key)
            except Exception as e:
                self.record("WARN", f"读取属性原值失败，跳过还原: {key} → {e}")
                return None
        self.add_cleanup(self.adb_shell, "settings", "put", "system", key, value)
        return value

    def restore_rotation(self, snap):
        """恢复 snapshot_rotation() 记录的状态（恢复"进用例时的状态"，
        不是盲目开自动旋转）。snap 为空时为空操作。"""
        if not snap:
            return
        self.adb_shell("settings", "put", "system",
                       "accelerometer_rotation", snap.get("accel") or "0")
        self.adb_shell("settings", "put", "system",
                       "user_rotation", snap.get("user") or "0")
        time.sleep(0.5)

    def has_network(self):
        """设备是否有活动网络（dumpsys connectivity）"""
        out = self.adb_shell("dumpsys", "connectivity")
        return "Active default network: none" not in out

    def grant_permission(self, package, permission):
        """授予运行时权限"""
        t0 = time.time()
        out = self.adb_shell("pm", "grant", package, permission)
        ok = "Success" in out or " granted" in out
        self._log_action("grant_permission", f"package={package}, permission={permission}, ok={ok}", t0)
        return out

    def current_activity(self):
        """当前前台完整 Activity（如 com.example.app/.ui.MainActivity）"""
        out = self._adb_run("shell", "dumpsys", "activity", "activities",
                            timeout=15).stdout
        m = re.search(r"topResumedActivity=ActivityRecord\{\S* u0 ([\w./]+) ", out)
        if not m:
            m = re.search(r"ResumedActivity: ActivityRecord\{\S* u0 ([\w./]+) ", out)
        return m.group(1) if m else "unknown"

    # ── 通用返回键（IME 感知）────────────────────────────────────────
    # 通用规律（2026-09-10 实测确认）：输入法弹起时，第一次 BACK 只收起
    # 输入法，**不触发页面返回**；IME 未弹起时按一次即可，多按会退过头。
    # 所以"一律按两次"是错的——必须先判断 IME 在不在。
    # 这是跨 App 的 Android 平台行为，与具体 App 无关，故放框架层。
    # 背景：183 连续 3 次 BLOCKED「失败弹窗处理后未回到照片网格」，
    #       根因之一就是裸按一次 BACK 被 IME 吞掉。

    def ime_shown(self):
        """输入法是否弹起（决定 BACK 会不会被吞）。"""
        try:
            out = self.adb_shell("dumpsys", "input_method")
            return "mInputShown=true" in out
        except Exception:
            return False

    def back(self, expect=None, timeout=8.0, silent=False):
        """通用返回键：IME 感知，避免"第一次 BACK 被输入法吃掉"。

        - IME 弹起 → 先按一次收输入法，再按一次真正返回
        - IME 未弹起 → 只按一次（多按会退过头）

        expect: 期望到达的 Activity 子串。给了就等到位；没到位再补按一次
                （IME 判据偶尔失灵时靠目标页自愈）。不传则只发返回键。
        返回: expect 为 None → True（已发键）；否则是否到达 expect。
        """
        t0 = time.time()
        was_ime = self.ime_shown()
        if was_ime:
            self.adb_shell("input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.8)
        self.adb_shell("input", "keyevent", "KEYCODE_BACK")
        self._log_action("back", f"ime={was_ime}, expect={expect}", t0)
        if expect is None:
            return True
        if self.wait_activity(expect, timeout=timeout):
            return True
        # 兜底：可能是 IME 判据失灵（候选栏等 mInputShown 漏报）或多层弹层
        if not silent:
            print(f"   ↩ 返回后未到达 {expect!r}（当前 {self.current_activity()}），补按一次")
        self.adb_shell("input", "keyevent", "KEYCODE_BACK")
        return self.wait_activity(expect, timeout=timeout)

    # ── 通用视觉排序（图库/相册选图）─────────────────────────────────
    # 通用能力：任何"从一堆缩略图里挑出目标类型图片"的场景都适用
    # （课程表导入、扫描识别、相册选图…）。与具体 App 无关，故放框架层。
    # 背景：183 反复选错图 BLOCKED，根因是只比"高/中/低"等级导致同级并列。

    @staticmethod
    def verdict_score(ans, *, positive=(), negative=(), vague=()):
        """把视觉判词转成可比较的分数（比只看"高/中/低"精细）。

        **教训（183 实测）**：这两句旧代码视为完全一样（都只取到等级"高"）——
            「高。…顶部**似有**横向表头」            ← 模糊
            「高。…顶部有"时间+**星期一至星期五**"表头」← 确切读出文字
        并列后排序退化成原序，选中模糊的那张 → App 判定不是课程表 → BLOCKED。
        所以必须把**确定度措辞**纳入排序。

        **另一个坑**：纯关键词计数会被**否定句**骗——
            「低。…**未呈现**…星期表头与网格」同样含关键词，计数和正例一样高。
        故否定词必须**优先重罚**，压过前面的"高"。

        positive: 目标类型的确切特征词（读到即加分，如"星期一""时间段"）
        negative: 否定词（默认内置通用否定词）
        vague:    模糊措辞（默认内置）
        """
        s = ans or ""
        if "高" in s:
            score = 30
        elif "中" in s:
            score = 20
        elif "低" in s:
            score = 10
        else:
            score = 5                      # 没给等级（含"视觉不可用"）→ 最保守
        # 默认否定/模糊词一律**通用**：不许出现具体 App 的文案
        #（分层边界硬规则 L78；曾误写"无课程表"，已移除——
        #  目标类型专属的否定词应由调用方经 negative= 传入）
        neg = negative or ("未见", "不是", "并非", "未呈现", "没有", "不含",
                           "无法确认")
        vag = vague or ("似有", "似乎", "可能", "疑似", "像是", "难以确认")
        if any(w in s for w in neg):
            score -= 40                    # 否定优先，压过前面的"高"
        if any(w in s for w in vag):
            score -= 8
        for w, bonus in (positive or ()):
            if w in s:
                score += bonus
        return score

    def rank_by_vision(self, nodes, prompt, positive=(), negative=(), vague=(),
                       limit=9, bounds_key="bounds_xy", verbose=True):
        """按视觉判词给节点排序（降序），用于"从缩略图里挑目标图"。

        对每个节点调 vision_ask(prompt, bounds=节点 bounds)，用
        verdict_score 打分后降序排。返回 [(node, ans), ...]。
        视觉不可用的节点按保守分（5）参与排序，不会崩链路。
        positive: [(特征词, 加分), ...] 传给 verdict_score。
        """
        scored = []
        for n in nodes[:limit]:
            try:
                ans = (self.vision_ask(prompt, bounds=n[bounds_key]) or "").strip()
            except Exception as e:
                ans = f"(视觉不可用:{e})"
            scored.append((n, ans, self.verdict_score(
                ans, positive=positive, negative=negative, vague=vague)))
        scored.sort(key=lambda it: -it[2])
        if verbose and scored:
            print("   🔍 视觉排序: " + " | ".join(
                f"候选{i+1}:{a[:36]}" for i, (_, a, _s) in enumerate(scored)))
        return [(n, a) for n, a, _s in scored]

    # ── App 私有导航辅助一律不放框架：入口长什么样、在哪、点完验证什么，
    #    都是具体 App 的知识（knowledge/<包名>.md）或 cases/<包名>/_flow.py 的职责。

    def _screen_size(self):
        raw = self._screencap_bytes()
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")

    def dismiss_first_use_dialogs(self, policy="allow", max_rounds=12, verbose=False):
        """
        处理首次使用弹窗直到主界面出现。
        策略：每轮只 dump 一次 UI 树，在 XML 里字符串匹配弹窗词，
        命中才点击（短超时）——避免旧版逐词 click_exists(timeout=1.2)
        的累计等待（最坏 12 词 × 1.2s ≈ 14s）。
        注意: Android 运行时权限弹窗约 8 秒自动消失，必须"检测即点"。
        调用方在启动 App 后调用时，首轮先等 ACTION_DELAY（界面渲染/首帧未就绪
        时 dump 会误判"无弹窗"）。
        """
        # 首轮前统一等待：启动/转场后界面未渲染完时，dump 会误判无弹窗
        time.sleep(ACTION_DELAY)
        for _ in range(max_rounds):
            hit = False
            # 1) 先做一次轻量 dump，字符串匹配（微秒级），命中才真正点击
            xml = self._dump()
            words = self._dialog_words(policy)
            for w in words:
                if f'text="{w}"' not in xml:
                    continue
                if self.d(text=w).click_exists(timeout=0.3):
                    if verbose:
                        self.record("INFO",
                                    f"弹窗已{'同意' if policy=='allow' else '拒绝'}: {w}")
                    time.sleep(ACTION_DELAY)
                    hit = True
                    break
            if not hit:
                # 2) 词表未命中：疑似未知弹窗 → AI 兜底（首启阶段同样适用）
                self._handle_unknown_dialog(xml)
            if not hit:
                return True   # 无弹窗，已进入主界面
        return False

    # ── 弹窗自动点击：主流程驱动，单连接，零并发 dump ────────────────
    def start_watchdog(self, policy="allow", interval=0.3, verbose=True):
        """
        启用弹窗自动点击：注册 u2 原生 watcher，由主流程每次 dump 后
        （_run_dialog_watchers）触发检查并点击，复用同一份已 dump 的 XML。
        无独立线程、无第二个 u2 连接、无并发 dump。
        policy: "allow"=点同意/允许 | "deny"=点拒绝
        """
        self._wd_enabled = True
        self._register_dialog_watchers(policy)
        if verbose:
            print(f"🛡️  弹窗自动点击已启用 (policy={policy})")

    def watchdog_policy(self, policy):
        """动态切换弹窗策略（allow=点同意/允许，deny=点拒绝）"""
        if self._wd_enabled:
            self._register_dialog_watchers(policy)
        else:
            self._wd_policy = policy
        print(f"🛡️  弹窗策略切换: {policy}")
        return self

    def watchdog_pause(self):
        """临时暂停弹窗自动点击（用于手动处理弹窗验证场景）"""
        self._wd_enabled = False
        print("🛡️  弹窗自动点击已暂停")

    def watchdog_resume(self):
        """恢复弹窗自动点击"""
        self._wd_enabled = True
        print("🛡️  弹窗自动点击已恢复")

    def stop_watchdog(self):
        """停用弹窗自动点击（保留注册，_run_dialog_watchers 不再触发）"""
        self._wd_enabled = False
        print("🛡️  弹窗自动点击已停用")

    def current_package(self):
        out = self._adb_run("shell", "dumpsys", "activity", "activities",
                            timeout=15).stdout
        m = re.search(r"topResumedActivity=ActivityRecord\{\S* u0 ([\w.]+)/", out)
        return m.group(1) if m else "unknown"

    # ── 断言 ────────────────────────────────────────────────────────
    def assert_equals(self, actual, expect, msg=""):
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg} 期望={expect!r} 实际={actual!r}")

    def assert_text(self, rid, expect, msg="文本断言"):
        v = self.read_rid(rid)
        actual = v["text"] if v else None
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg}: 期望={expect!r} 实际={actual!r}")

    def assert_switch(self, rid, expect, msg="开关状态"):
        v = self.read_rid(rid)
        actual = v["checked"] if v else None
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg}: 期望={expect} 实际={actual}")

    def assert_true(self, cond, msg):
        return self.record("PASS" if cond else "FAIL", msg)

    def assert_length_le(self, rid, limit, msg="长度上限"):
        v = self.read_rid(rid)
        actual = len(v["text"]) if v and v["text"] else 0
        return self.record("PASS" if actual <= limit else "FAIL",
                           f"{msg}: {limit} 实际={actual}")

    # ── 置灰断言（截图裁剪 + 颜色对比度）────────────────────────────
    def _region_contrast(self, bounds, scale=3):
        """计算按钮区域内文字与背景的对比度（0-255 差值）"""
        x1, y1, x2, y2 = bounds
        raw = self._adb_run("exec-out", "screencap", "-p", timeout=15,
                            text=False).stdout
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("L")
        crop = img.crop((x1, y1, x2, y2))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        px = list(crop.getdata())
        # 背景 = 众数附近的亮度；文字 = 与背景差异大的像素
        bg = sorted(px)[len(px) // 2]
        text_px = [p for p in px if abs(p - bg) > 40]
        if not text_px:
            return 0.0
        text_brightness = sum(text_px) / len(text_px)
        return abs(text_brightness - bg)

    def assert_grayed(self, rid, ref_contrast, msg="置灰断言", ratio=0.5):
        """
        断言按钮置灰: 当前对比度 < 参考对比度 * ratio
        ref_contrast: 按钮正常(可点击)态下的文字对比度
        """
        v = self.read_rid(rid)
        if not v or not v["bounds"]:
            return self.record("FAIL", f"{msg}: 元素不存在")
        cur = self._region_contrast(v["bounds"])
        grayed = cur < ref_contrast * ratio
        return self.record("PASS" if grayed else "FAIL",
                           f"{msg}: 对比度 {cur:.0f} vs 参考 {ref_contrast:.0f} "
                           f"→ {'置灰' if grayed else '未置灰'}")

    def contrast_of(self, rid):
        """获取元素当前文字对比度（供 assert_grayed 作参考）"""
        v = self.read_rid(rid)
        if not v or not v["bounds"]:
            return 0.0
        return self._region_contrast(v["bounds"])

    # ── 证据与辅助 ──────────────────────────────────────────────────
    def _screencap_bytes(self):
        """当前屏幕 PNG 字节（绑定本用例 serial）。截屏统一入口，
        供 _auto_screenshot / ocr / vision 复用，避免各自裸拼 adb。"""
        self.ensure_awake()   # 截屏前保活，避免截到锁屏界面
        return self._adb_run("exec-out", "screencap", "-p", timeout=15,
                             text=False).stdout

    def _log_action(self, action, detail=None, start=None):
        """记录一步 UI 操作及耗时。start 为操作开始前 time.time()。"""
        duration_ms = 0
        if start:
            duration_ms = int((time.time() - start) * 1000)
        if self._cur_step is not None:
            self._cur_step.setdefault("actions", []).append(
                {"action": action, "detail": detail, "duration_ms": duration_ms})
            if self._db is not None and self._db_step_id is not None:
                try:
                    self._db.add_step_action(self._db_step_id, action, detail, duration_ms)
                except Exception:
                    pass
        return duration_ms

    # 截图降载（§7.2 M3：总量 16.3MB → ≤6MB）
    # ⚠️ 2026-09-16 人确认：**每步仍要留图**，不许靠"少截图"换指标 —— 那会削弱
    # 证据链（原则 2），而 M3 的目的是"同样的证据更小"，不是"更少的证据"。
    # 因此：张数**不限**（SHOT_PER_STEP=0），体积只靠等比压缩达成。
    # 若压缩后仍超标，正解是继续调 SHOT_MAX_SIDE/编码，而不是丢截图。
    SHOT_MAX_SIDE = 1280    # 长边上限（0=不缩）；与 vision 侧 resize_for_vision 同口径
    SHOT_PER_STEP = 0       # 0=不限（见上）；保留该开关仅为压测/排查时临时收窄
    # 编码格式：**WebP 是 M3 达标的关键杠杆**。
    # 真机实测（2026-09-16，178 的 115 张 1280×802 截图，同尺寸同内容对比）：
    #     PNG     14.23MB  ← 只压分辨率（原方案）：离 ≤6MB 差 2.4×
    #     JPEG80  11.60MB  ← 换 JPEG 也**不够**（UI 截图里文字边缘多，JPEG 优势小）
    #     WebP75   3.69MB  ← ✅ 达标（且留 1.6× 余量）
    # 故默认 WebP。`jpg` / `png` 保留为可切换值（png = 无损兜底，逐像素比对时用）。
    # ⚠️ 换格式要同步认扩展名的两处 glob：`_count_screenshots`（本文件）与
    # `check_facts._grep_storage`（只认 .png 会让"M3 统计归零"+"事实核查误报"）。
    SHOT_FORMAT = "webp"
    SHOT_QUALITY = 75

    def _encode_shot(self, raw):
        """截图编码：长边压缩 + 格式转换。返回 `(bytes, 扩展名)`。

        两级降载（M3）：① 长边压到 `SHOT_MAX_SIDE`；② 编 JPEG（见类属性处的实测
        数据：只做 ① 时 178 是 14.2MB，离 ≤6MB 差 2.4×）。

        **编码失败一律回退原图 PNG** —— 优化不许让证据丢失（宁可大，不可没有）。
        """
        max_side = getattr(self, "SHOT_MAX_SIDE", 1280)
        fmt = str(getattr(self, "SHOT_FORMAT", "jpg") or "png").lower()
        try:
            from screenshot import ScreenImage, resize_for_vision
            # max_side=0 表示"不缩"：resize_for_vision 的判据是 `m <= max_side`，
            # 传 0 会被判成"要缩到 0"——所以这里换成一个极大的值表达"不缩"。
            si = resize_for_vision(ScreenImage.from_bytes(raw),
                                   max_side or 100000)
            if fmt in ("jpg", "jpeg", "webp"):
                buf = io.BytesIO()
                img = si.image.convert("RGB")
                if fmt == "webp":
                    img.save(buf, format="WEBP",
                             quality=int(getattr(self, "SHOT_QUALITY", 75)),
                             method=4)
                    return buf.getvalue(), "webp"
                img.save(buf, format="JPEG",
                         quality=int(getattr(self, "SHOT_QUALITY", 75)),
                         optimize=True)
                return buf.getvalue(), "jpg"
            return (si.png_bytes or raw), "png"
        except Exception as e:
            # 回退原图 PNG：优化失败只许"变大"，不许"丢证据"
            print(f"[截图降载] 编码失败，落原图 PNG: {e}")
            return raw, "png"

    def _auto_screenshot(self, label=None, add_to_step=True, force=False):
        """自动截图：操作/验证点统一入口。label 为 None 时用 'auto'。
        add_to_step=False 用于验证点截图（record 会单独在 result 中展示，
        不混入步骤级证据列表，避免报告重复）。

        M3 降载（§7.2）：**每步都留图**（人确认 2026-09-16），体积只靠压缩。
        · 张数默认**不限**（`SHOT_PER_STEP=0`）：证据链优先于指标。
          `step()` 仍重置 `_shot_in_step` 计数，供报告显示"每步几张"。
        · **长边 ≤SHOT_MAX_SIDE** 等比压缩 —— 16.3MB 的量级来自全分辨率 PNG，
          压缩后报告与人工排查仍够看。
        · `force=True`（FAIL 证据 / §5 工件包）永远截图，且不受任何配额影响。
        """
        step_cap = 0 if force else getattr(self, "SHOT_PER_STEP", 0)
        if add_to_step and self._cur_step is not None and step_cap:
            if getattr(self, "_shot_in_step", 0) >= step_cap:
                self._shot_skipped = getattr(self, "_shot_skipped", 0) + 1
                return None
        self._shot_idx += 1
        safe = re.sub(r'[\\/:*?"<>|]', "_", label or "auto")
        # 先编码再定文件名：扩展名由编码器决定（JPEG 是 M3 达标的关键杠杆）
        raw, ext = self._encode_shot(self._screencap_bytes())
        path = os.path.join(self.case_dir, f"{self._shot_idx:02d}_{safe}.{ext}")
        with open(path, "wb") as f:            # 显式关闭：不依赖 CPython 引用计数实现差异
            f.write(raw)
        if add_to_step and self._cur_step is not None:
            self._shot_in_step = getattr(self, "_shot_in_step", 0) + 1
            self._cur_step["evidences"].append(path)
            # 步骤级操作截图也入库，供 Web UI 展示
            if self._db is not None and self._db_step_id is not None:
                try:
                    self._db.add_step_evidence(self._db_step_id, path)
                except Exception:
                    pass
        print(f"   📷 {path}")
        return path

    def screenshot(self, label):
        """用户主动截图：与自动截图等价，但允许自定义 label"""
        t0 = time.time()
        path = self._auto_screenshot(label, add_to_step=True)
        self._log_action("screenshot", label, t0)
        return path

    def capture_toast(self, wait=1.0, label="toast", y_min=0, y_max=99999):
        """捕捉 Toast：动作后立即截屏定格 → OCR 读定格帧 → (文本列表, 截图路径)。
        Toast 是屏幕视觉元素且显示窗口短（~2-3.5s）。logcat 的 Toast 缓冲在
        多数 ROM 上不打印或格式不一，**不可靠 —— 禁止用 logcat 捕 toast**。
        正确姿势（本方法已封装）：
          1. 动作后立即调用（内部等 wait 秒让 toast 渲染，窗口内截屏最稳）
          2. 截屏落盘留证 + OCR 同一帧：旧实现对当前实屏再截一次，
             RapidOCR 首次冷加载 ~1s 后 toast 可能已消失 → 截图有 toast、
             OCR 结果却是空（断言用空结果误判 FAIL，证据却证明 toast 在）
          3. OCR 全屏 → 返回文本列表，toast 文案混在其中
        断言示例：
            texts, shot = t.capture_toast()
            ok = any("时间冲突" in s for s in texts)
            t.record("PASS" if ok else "FAIL", f"toast={texts}")
        OCR 混背景读不准半透明 toast 时，配视觉模型兜底：
            t.vision_ask("屏幕上是否有 toast 提示？内容是什么",
                         bounds=(0, y_min, W, y_max))
        y_min/y_max 可裁剪 OCR 区域（toast 通常在屏幕底部，可传 y_min=屏高*0.7 减噪）。
        """
        time.sleep(wait)                                    # 等 toast 渲染出来
        path = self._auto_screenshot(label, add_to_step=True)
        with open(path, "rb") as f:                         # OCR 读同一份定格帧
            raw = f.read()
        hits = self.ocr(y_min=y_min, y_max=y_max, image_bytes=raw)
        return [text for _, _, _, text in hits], path

    def screen_text(self):
        xml = self._dump()
        self._run_dialog_watchers(xml)
        nodes = _parse_nodes(xml)
        self._note_rids(nodes)
        return [n["text"] for n in nodes if n["text"]]

    # ── OCR（Canvas 内容读取）────────────────────────────────────────
    def ocr(self, y_min=0, y_max=99999, x_min=0, x_max=99999, image_bytes=None,
            below=None, above=None, region=None):
        """截屏 + rapidocr，返回 [(x, y, conf, text)]（原图像素坐标）。
        image_bytes：传入已定格的 PNG 字节时直接 OCR 它（capture_toast 用），
        不传则现场截屏。

        ⚠️ 调用方**不要写死像素范围**（换设备 / 换方向即失效）：范围应从元素 bounds
        或当次窗口尺寸派生。**三个派生入口**（§2.2 / §3.1，替代手写像素值）：

            t.ocr(region=t.region_of(RID_LIST))     # 由元素 bounds 直接派生
            t.ocr(below=RID_HEADER, above=RID_NAV)  # 两个 anchor 之间的区域

        anchor 取不到时**不缩范围**（退化为全屏）：提示错了最多慢一点，不会挂
        （与 §3.2「缓存命中前验证 + 降级」同一原则）。

        传了非默认范围却一条都没读到时会记 WARN ——「指定区域读空」是设备相关
        硬编码最典型的静默降级，必须可见。"""
        if region is not None:
            y_min, y_max, x_min, x_max = region
        if below is not None or above is not None:
            b = self.el_bounds(rid=below) if isinstance(below, str) else below
            if b:
                y_min = max(y_min, b[3])    # anchor 底边 → 区域起点在其下方
            b = self.el_bounds(rid=above) if isinstance(above, str) else above
            if b:
                y_max = min(y_max, b[1])    # anchor 顶边 → 区域终点在其上方
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        import numpy as np
        from PIL import Image
        raw = image_bytes if image_bytes is not None else self._screencap_bytes()
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        s = 1600 / max(w, h)
        img2 = img.resize((int(w * s), int(h * s)))
        res, _ = self._ocr(np.asarray(img2))
        out = []
        for box, text, conf in res or []:
            xs = [p[0] / s for p in box]
            ys = [p[1] / s for p in box]
            cx, cy = int(sum(xs) / 4), int(sum(ys) / 4)
            if y_min <= cy <= y_max and x_min <= cx <= x_max:
                out.append((cx, cy, float(conf), text))
        # 指定区域读空 → 可见化。
        # 旧行为：静默返回 []，调用方（如 175._ocr_numbers）把它当"OCR 没读到"记一条
        # INFO，报告里无人可见 —— 换设备后硬编码范围失效就是这样消失的。
        # image_bytes 非空 = 调用方在读一份**已定格的帧**（capture_toast），读空是
        # 合法结果（toast 已消失），不告警，避免制造噪音。
        if (not out and image_bytes is None
                and (y_min > 0 or y_max < 99999 or x_min > 0 or x_max < 99999)):
            try:
                self.record("WARN",
                            f"OCR 在指定区域读到 0 条"
                            f"（x {x_min}~{x_max}, y {y_min}~{y_max}）—— "
                            f"范围若是写死的像素值，换设备/换方向即失效")
            except Exception:
                pass
        return out

    def ocr_find(self, keyword, y_min=0, y_max=99999):
        """在 OCR 结果中找包含关键字的项，返回第一个 (x, y) 或 None"""
        for x, y, c, t in self.ocr(y_min, y_max):
            if keyword in t:
                return (x, y)
        return None

    # ── 探查缓存（批量探查 + 落盘复用）────────────────────────────────
    # 目的：生成用例阶段，同一页面只探一次；后续直接读缓存文件或 grep，
    # 避免"每步 dump + 每步试错"把首次生成拖到 20 分钟。
    # 缓存是纯文本（dump.xml / ocr.json / meta.json），可直接 grep、re 检索。
    def _probe_dir(self, label, pkg=None):
        """缓存目录：storage/probes/<包名>/<label>/。

        pkg=None 时取当前前台包（旧行为）。⚠️ 探查系统页（PhotoPicker、
        系统设置等）时前台包是系统包，缓存会散到 com.android.* 名下，
        与"目录名=被测包名"的约定分裂——这种场景应显式传 pkg=被测包名。"""
        if pkg is None:
            pkg = "unknown"
            try:
                pkg = self.d.app_current()["package"] or "unknown"
            except Exception:
                pass
        return os.path.join(PROBE_DIR, pkg, label)

    def _guard_exec_cache(self, api):
        """执行期（正式用例）禁用探查缓存：拿过期数据当结论 = 假 PASS。

        判据复用 `_should_record`（有 USER_INPUT = 用户口述的正式用例），
        不引入新的状态位；探查/补采/备数据脚本无 USER_INPUT → 不拦。
        `probe_page` 免检：它本就是"给正式用例里临时探一下的兜底"。

        **必须先 record("FAIL") 再 raise**：只 raise 不 record 的话，finish() 会按
        "目前所有断言都 PASS" 算出 PASS，而 run_case 的退出码是 1 —— 报告与退出码打架
        （run_case.py 注释里专门警告过的那种）。未命中拦截时静默返回。
        """
        if getattr(self, "_in_probe_page", False):
            return
        if not _should_record(getattr(self, "user_input", None), self.name):
            return
        self.record("FAIL",
                    f"执行期误用缓存: {api}() 拿过期数据当结论（假 PASS 风险）")
        raise ExecutionTimeCacheError(f"禁止在执行期间使用 {api}")

    def cached_dump(self, label, ttl=None, refresh=False, pkg=None):
        """取 UI 树，优先读缓存。
        ttl: 缓存有效期（秒），None=永不过期；refresh=True 强制重探。
        pkg: 归属包名（探查系统页时显式传被测包，避免缓存散到系统包名下）。

        ⚠️ 仅供探查期 / 写代码参考。**执行期（正式用例）调用会被
        _guard_exec_cache 拦下** —— 缓存里的数据属于上一次运行或另一台设备，
        拿它当结论就是假 PASS。probe_page 内部调用免检。
        """
        self._guard_exec_cache("cached_dump")
        d = self._probe_dir(label, pkg=pkg)
        fp = os.path.join(d, "dump.xml")
        if not refresh and os.path.isfile(fp):
            if ttl is None or (time.time() - os.path.getmtime(fp)) < ttl:
                with open(fp, encoding="utf-8") as f:
                    return f.read()
        prev = self.trace.ctx                # 语义上下文：本次 dump 归属该 label
        self.trace.set_ctx(f"probe:{label}")
        try:
            xml = self._dump()
        finally:
            self.trace.ctx = prev
        os.makedirs(d, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(xml)
        return xml

    def cached_ocr(self, label, y_min=0, y_max=99999, refresh=False, pkg=None):
        """取 OCR 结果，优先读缓存。返回 [(x, y, conf, text)]。

        ⚠️ 仅供探查期 / 写代码参考；执行期调用被 _guard_exec_cache 拦下
        （同 cached_dump）。probe_page 内部调用免检。
        """
        self._guard_exec_cache("cached_ocr")
        import json
        d = self._probe_dir(label, pkg=pkg)
        fp = os.path.join(d, "ocr.json")
        if not refresh and os.path.isfile(fp):
            with open(fp, encoding="utf-8") as f:
                return [(i[0], i[1], i[2], i[3]) for i in json.load(f)]
        res = self.ocr(y_min, y_max)
        os.makedirs(d, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return res

    def probe_page(self, label, ocr=False, ttl=None, refresh=False, pkg=None):
        """批量探查当前页：一次拿全 rid / text / bounds / 前台信息。

        返回结构化 dict，供生成用例的 agent 在内存里做匹配与规划，
        取代"点一步看一步"的单步试错。同时落盘供后续 grep 复用。
        pkg: 缓存归属包名。探查系统页（PhotoPicker 等）时前台包是系统包，
        必须显式传被测 App 包名（如 pkg="com.zui.calendar"），否则缓存
        散到系统包名下，后续按被测包名检索不到、只能重探真机。

        **自动开启采集会话**（2026-09-10 起）：调用本方法即视为"探查模式"，
        自动 set_trace() —— 之后每次 _dump() 的 UI 树快照与关键事件全部落盘
        storage/traces/<用例名>/<会话>/。此前该机制**从未被启用过**
        （storage/traces/ 从未存在），只因它要求调用方"记得调 set_trace()"；
        探查产物本该是默认行为，不该靠自觉。正式回归不调 probe_page，
        故零额外 IO、行为不变。
        （注：采集脚本 `_collect_*.py` 已在 TestCase 构造时自动开 trace，
          见 _maybe_auto_trace；此处是给"正式用例里临时探一下"的兜底。）
        """
        if getattr(self, "trace", None) is None or not self.trace.enabled:
            self.set_trace()          # 幂等：已开启则直接返回现有会话
        # 免检窗口：probe_page 是"给正式用例里临时探一下的兜底"（本方法 docstring
        # 明确允许在正式用例里调用），故其内部的 cached_* 不被 _guard_exec_cache 拦。
        self._in_probe_page = True
        try:
            xml = self.cached_dump(label, ttl=ttl, refresh=refresh, pkg=pkg)
        finally:
            self._in_probe_page = False
        nodes = [{
            "rid": n["rid"], "text": n["text"], "desc": n["desc"],
            "cls": n["cls"], "bounds": n["bounds"], "bounds_xy": n["bounds_xy"],
            "clickable": n["clickable"] == "true",
        } for n in _parse_nodes(xml)]
        # 前台包（记录用）与缓存归属包（参数 pkg）分离：探系统页时两者不同
        try:
            fg_pkg = self.d.app_current()["package"]
        except Exception:
            fg_pkg = None
        info = {
            "label": label,
            "package": fg_pkg,
            "texts": [n["text"] for n in nodes if n["text"]],
            "rids": sorted({n["rid"] for n in nodes if n["rid"]}),
            "nodes": nodes,
        }
        if ocr:
            self._in_probe_page = True
            try:
                info["ocr"] = self.cached_ocr(label, refresh=refresh, pkg=pkg)
            finally:
                self._in_probe_page = False
        # meta 落盘，便于检索
        import json
        d = self._probe_dir(label, pkg=pkg)
        os.makedirs(d, exist_ok=True)
        now = datetime.now().isoformat(timespec="seconds")
        meta_path = os.path.join(d, "meta.json")
        # accessed：**最后访问时间**，每次重采/重读刷新，作为超时清理的基准
        #（不是创建时间——写用例可能跨数小时，按创建时间会在使用中删掉
        #  正在查的缓存，183 从探查到验证通过跨 2 小时）。
        # created：首次采集时间，仅供追溯。
        created = now
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    created = json.load(f).get("created") or now
            except (OSError, ValueError):
                pass
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"label": label, "package": fg_pkg,
                       "created": created, "accessed": now,
                       "ts": now,
                       "texts": info["texts"], "rids": info["rids"]},
                      f, ensure_ascii=False, indent=1)
        return info

    @staticmethod
    def cleanup_probes(max_idle_min=None, verbose=True):
        """清理**超时未访问**的探查缓存（storage/probes/<包名>/<label>/）。

        规则（2026-09-10 讨论定稿）：读 meta.json 的 `accessed`（最后访问时间），
        距今超过 max_idle_min 分钟 → 删该 label 目录。缺 accessed 时退回看
        meta.json 的 mtime（老缓存没有该字段）。

        **为什么按时间自动删而不是靠 AI 记得删**：若落盘靠 AI 自觉、删除也靠
        AI 自觉，就是一个自觉弥补另一个自觉。物证：2026-09-10 当天
        D:\\dsh 下 15 个临时探针脚本全部未清理。
        **为什么是"最后访问"而非"创建"**：写用例可能跨数小时（183 探查→验证
        跨 2 小时），按创建时间会在使用中删掉正在查的缓存，反而逼着重跑真机。

        **max_idle_min 缺省跟随环境变量 `DSH_PROBES_MAXIDLE_MIN`**（未设置 = 30，
        与历史行为逐字节一致）；显式传数字则优先于环境变量。与 traces 侧
        `DSH_TRACE_MAXIDLE_MIN` 同一模式（trace_recorder.py:57-59）。
        **设 0（或负数）= 关闭清理**：调试期需长期保留同一批 probes 供离线预检
        （inventory.py verify）时用 —— 30 分钟会在写用例途中把缓存清掉，逼着
        回真机重探，是"改一点要整跑"的直接成因之一。
        ⚠️ 旧实现把 0 直接当阈值（cutoff = now → **删光所有缓存**），与 traces 侧
        "设 0 可关闭"语义相反，本次一并修正（回归保护见 tests 的
        TestCleanupProbesTtl）。

        返回 (删除数, 保留数)。由 run_case.py 启动时调用。
        """
        import json
        if max_idle_min is None:
            try:
                max_idle_min = int(os.environ.get("DSH_PROBES_MAXIDLE_MIN", "30") or 0)
            except ValueError:      # 非法值 → 回落 30（既不静默跳过清理，也不阻断执行）
                max_idle_min = 30
        if max_idle_min <= 0:
            return (0, 0)           # 0/负数 = 关闭清理（与 traces 侧同语义）
        if not os.path.isdir(PROBE_DIR):
            return (0, 0)
        cutoff = time.time() - max_idle_min * 60
        removed = kept = 0
        for pkg_name in os.listdir(PROBE_DIR):
            pkg_dir = os.path.join(PROBE_DIR, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue
            for label in os.listdir(pkg_dir):
                d = os.path.join(pkg_dir, label)
                if not os.path.isdir(d):
                    continue
                meta_path = os.path.join(d, "meta.json")
                stamp = None
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, encoding="utf-8") as f:
                            raw = json.load(f).get("accessed")
                        if raw:
                            stamp = datetime.fromisoformat(raw).timestamp()
                    except (OSError, ValueError):
                        stamp = None
                if stamp is None:
                    try:                      # 老缓存无 accessed → 退回文件 mtime
                        stamp = os.path.getmtime(meta_path)
                    except OSError:
                        stamp = None
                if stamp is not None and stamp < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    if verbose:
                        idle = int((time.time() - stamp) / 60)
                        print(f"   🧹 清理探查缓存（{idle} 分钟未访问）: {pkg_name}/{label}")
                    removed += 1
                else:
                    kept += 1
        return (removed, kept)

    @staticmethod
    def list_probes():
        """列出当前探查缓存（供 run_case.py 提示"还有旧的 X 份，要删吗"）。

        返回 [{"pkg","label","idle_min","accessed"}, ...]，按空闲时间降序。
        """
        import json
        out = []
        if not os.path.isdir(PROBE_DIR):
            return out
        for pkg_name in os.listdir(PROBE_DIR):
            pkg_dir = os.path.join(PROBE_DIR, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue
            for label in os.listdir(pkg_dir):
                d = os.path.join(pkg_dir, label)
                meta_path = os.path.join(d, "meta.json")
                if not os.path.isdir(d) or not os.path.isfile(meta_path):
                    continue
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        m = json.load(f)
                    ts = datetime.fromisoformat(m.get("accessed") or m["ts"])
                except (OSError, ValueError, KeyError):
                    continue
                out.append({"pkg": pkg_name, "label": label,
                            "idle_min": int((time.time() - ts.timestamp()) / 60),
                            "accessed": m.get("accessed") or m.get("ts")})
        out.sort(key=lambda r: -r["idle_min"])
        return out

    def find_nodes(self, label=None, rid_re=None, text_re=None,
                   cls_re=None, clickable=None, ttl=None, pkg=None):
        """在探查结果里按正则筛节点（不连设备时用缓存）。
        例: find_nodes("某页面", rid_re="switch_")
        pkg: 归属包名，与 probe_page 的 pkg 参数对应（探系统页时传被测包）。
        """
        xml = self.cached_dump(label, ttl=ttl, pkg=pkg) if label else self._dump()
        out = []
        for n in _parse_nodes(xml):
            d = {"rid": n["rid"], "text": n["text"],
                 "desc": n["desc"], "cls": n["cls"],
                 "bounds": n["bounds"], "bounds_xy": n["bounds_xy"],
                 "clickable": n["clickable"] == "true",
                 # enabled / checked（2026-09-11 补）：置灰与勾选态是常用断言
                 # 判据，_parse_nodes 已解析却在旧版被丢掉，导致用例只能拿到
                 # None、误判 FAIL（185 首跑 6 条误报就是这么来的）。
                 # 保留**原始字符串**（"true"/"false"）便于直接比较与打印。
                 "enabled": n.get("enabled"), "checked": n.get("checked")}
            if rid_re and not re.search(rid_re, d["rid"]):
                continue
            if text_re and not re.search(text_re, d["text"]):
                continue
            if cls_re and not re.search(cls_re, d["cls"]):
                continue
            if clickable is not None and d["clickable"] != clickable:
                continue
            out.append(d)
        return out

    def _compute_final_status(self):
        """用例最终结论（机器可消费的显式语义，不从摘要文本推断）：
        有 FAIL → FAIL；无 FAIL 有 BLOCKED → BLOCKED；两者皆无但有 WARN → WARN；
        其余 → PASS。规则确定性，优先级 FAIL > BLOCKED > WARN > PASS。"""
        counts = self._result_counts()
        if counts["FAIL"]:
            return "FAIL"
        if counts["BLOCKED"]:
            return "BLOCKED"
        if counts["WARN"]:
            return "WARN"
        return "PASS"

    def _result_counts(self):
        counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0, "BLOCKED": 0}
        for s in self.steps:
            for r in s["results"]:
                counts[r["result"]] = counts.get(r["result"], 0) + 1
        return counts

    def _emit_change_attribution(self):
        """版本门禁 + 双信号源归因（§4.1 / §10.2）。

        信号 1 = 版本（APK 替换）：run_case 启动时采集，经环境变量传来；
        信号 2 = 元素集合指纹（本轮 rid 集合 hash，服务端 / A-B 变更）。

        命中 `apk` / `config` 时记一条 WARN —— 这样报告里就能回答"用例行为变了，
        是因为版本变化还是其他"（§10.2 的「本次与历史的差异」节）。`script` /
        `layout` / `unchanged` 只打控制台，不记 WARN（见 version_gate 的取舍说明）。
        """
        sp = getattr(self, "script_path", None)
        db = getattr(self, "_db", None)
        if not sp or db is None:
            return
        import run_metrics as _rm
        import version_gate as _vg
        prev = db.latest_metrics(sp, exclude_id=getattr(self, "_db_case_id", None))
        static = _rm.load_static() or {}
        rids = sorted(getattr(self, "_rid_seen", None) or ())
        cur = {
            "script_hash": static.get("script_hash"),
            "app_version_name": static.get("app_version_name"),
            "app_version_code": static.get("app_version_code"),
            "rid_set_hash": _rm.rid_set_hash(rids),
            "device": getattr(self, "device_info", None),
        }
        lines, code = _vg.gate_warn_lines(prev, cur, static.get("card_version"))
        # 存下来给 _write_healing_log 用：APK 真的变了就要清掉该 App 的降级记录
        self._attribution_code = code
        # 完整明细（哪些 rid 新增/消失）——§10.2 要求报告给明细而不是只给 hash
        if code == "config" and prev.get("rid_set"):
            try:
                import json
                gone, new = _rm.rid_diff(json.loads(prev["rid_set"]), rids)
                print(f"[变更归因] 消失的 rid {len(gone or [])} 个、新增 "
                      f"{len(new or [])} 个（明细见 RunMetrics 的 rid_set 列）")
            except Exception:
                pass
        if lines:
            self.record("WARN", "版本/界面漂移（" + code + "）：" + "；".join(lines))
        elif code == "no_baseline":
            print("[变更归因] 首次运行，无可比基线 → 跳过对比（不标未归因）")
        else:
            print(f"[变更归因] {code}：与上次一致或无产品侧变更")

    def _case_package_from_script(self):
        """从用例脚本路径推断被测包名（cases/<包名>/<脚本>.py，目录名像包名才认）。

        判据与 db.backfill_package 一致：纯 ASCII、含点、无空白。
        推断不出（探查脚本无 script_path / 目录名不是包名）返回 None，
        由调用方回退到收尾前台包。"""
        sp = (getattr(self, "script_path", None) or "").replace("\\", "/")
        m = re.search(r"/cases/([^/]+)/", sp)
        if not m:
            return None
        cand = m.group(1)
        if re.match(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", cand):
            return cand
        return None

    # ── 报告 ────────────────────────────────────────────────────────
    def finish(self):
        global LAST_CASE
        # 幂等守卫：已正常 finish 过的用例（收尾代码再抛异常时 run_case.py
        # 的异常兜底会再调一次 finish），直接返回旧报告——重复执行会把刚
        # 生成的报告再备份一遍并二次写库。用例已完整跑完出报告，无需重做。
        if getattr(self, "_finished", False):
            return self._report_path
        os.makedirs(REPORT_DIR, exist_ok=True)
        # 报告文件名清洗：用例名含 :/\?* 等文件系统非法字符时不报错（与 _auto_screenshot 同款正则）
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", self.name)
        path = os.path.join(REPORT_DIR, f"{safe_name}_报告.md")
        # 正式报告名始终反映最近一次运行（重跑覆盖是既定语义，DB 里另有全量历史）。
        # 但覆盖前把旧报告备份成带时间戳的副本，杜绝"同名用例互相覆盖导致结果丢失"。
        if os.path.exists(path):
            try:
                import shutil
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak = os.path.join(REPORT_DIR, f"{safe_name}_{ts}_报告.md")
                shutil.copyfile(path, bak)
                print(f"[提示] 旧报告已备份为 {os.path.basename(bak)}", flush=True)
            except Exception as e:
                print(f"⚠️ 旧报告备份失败: {e}")
        # ── 版本门禁 + 变更归因（§4.1 双信号源 / §10.2 决策表）──────────
        # 位置刻意在 _compute_final_status **之前**：门禁命中要以 WARN 进入
        # 结论，否则报告与退出码上完全看不见（只剩一行控制台输出）。
        try:
            self._emit_change_attribution()
        except Exception as e:
            print(f"⚠️ [版本门禁] 检测失败（不阻断收尾）: {e}")
        # P5：降级留痕落盘（healing_log.json + count 达阈值的知识卡更新提案）
        try:
            self._write_healing_log()
        except Exception as e:
            print(f"⚠️ [healing] 落盘失败（不阻断收尾）: {e}")
        counts = self._result_counts()
        pass_n, fail_n = counts["PASS"], counts["FAIL"]
        warn_n, blocked_n, info_n = counts["WARN"], counts["BLOCKED"], counts["INFO"]
        total = pass_n + fail_n          # 仅 PASS/FAIL 计入断言统计
        self.final_status = self._compute_final_status()
        # 执行异常（run_case.py 捕获后设置 _fatal_error 再调 finish）：
        # 用例没跑完，断言统计再好看也不可信 → 结论按 ERROR 压过一切。
        if self._fatal_error is not None:
            self.final_status = "ERROR"
        # ── 清场：任何退出路径（正常 return / 抛异常 / CaseAbort）都执行 ──
        # 必须在漂移检测之前：清场动作本身会改设备状态，放后面会被 env_diff
        # 判成"用例污染环境"，反而制造假 WARN（清场是为了消除污染，不是制造）。
        self._run_cleanups()
        # ── 环境漂移检测：用例是否污染了设备环境 ─────────────────
        # 非 ERROR 时才检测（ERROR = 用例没跑完，环境状态不可信）。
        # WARN 不覆盖 FAIL/ERROR，只在 PASS 时升级为 WARN。
        if self._env_baseline and self.final_status not in ("ERROR",):
            try:
                diff = self.states.env_diff(self._env_baseline,
                                            ignore=self._env_ignore)
                if diff:
                    parts = [f"{k}: {v[0]!r}→{v[1]!r}" for k, v in diff.items()]
                    drift_msg = f"用例污染设备环境: {{{', '.join(parts)}}}"
                    self.record("WARN", drift_msg)
                    # PASS 用例因漂移降级为 WARN（不影响 CI 但提示维护者）
                    if self.final_status == "PASS":
                        self.final_status = "WARN"
            except Exception:
                pass   # 漂移检测失败不阻断报告生成
        # ── 方向变化检测：**刻意不吃 env_ignore** ──────────────────────
        # 为什么单独一条、且不能被 env_ignore 抑制：
        #   锁定态会被 App 冷启动解开（实测 pm_clear+launch_app 后
        #   accelerometer_rotation 由 0 变 1），设备随后可能随物理姿态翻转
        #   → 用例中途坐标系换了一次。此时断言**未必挂**（178 就靠"坐标全部
        #   现场派生"在横屏下照样 PASS），所以它是"结论可信"问题而非失败：
        #   结果对不代表过程稳。
        #   env_ignore 的语义是"用例自己合法改了环境"（旋屏用例），不能用来
        #   掩盖"环境被外部改写" —— 178 现在正是用 env_ignore 把这条静音的。
        # 为什么比较"起始 vs 结束"而不是"中间是否变过"：
        #   `snapshot_rotation`/`restore_rotation` 成对出现的合法转屏用例，
        #   结束时方向已还原 → 起始==结束 → 不报。只有**把设备留在另一个
        #   方向**的用例才报（= 119 事故与 178 的形态）。
        self._rotation_start = None
        self._rotation_end = None
        try:
            self._rotation_start = getattr(self, "_rotation_baseline", None)
            self._rotation_end = self.device_rotation()
            if (self._rotation_start is not None
                    and self._rotation_end is not None
                    and self._rotation_start != self._rotation_end):
                self.record(
                    "WARN",
                    f"用例中途方向变化: rotation {self._rotation_start}"
                    f"→{self._rotation_end}（"
                    f"{_rot_name(self._rotation_start)}→"
                    f"{_rot_name(self._rotation_end)}）"
                    f" —— 锁定态被外部改写，坐标系中途换过（结论可信性风险）")
                if self.final_status == "PASS":
                    self.final_status = "WARN"
        except Exception:
            pass   # 方向探测失败不阻断报告生成
        LAST_CASE = self                 # run_case.py 取最终结论定退出码
        # 包名同时入库：报告文件可能丢，库里的记录不会丢，
        # 重建报告时才能原样还原「被测 App」这一栏。
        # 取值优先级：脚本路径目录名（cases/<包名>/xx.py，长得像包名才认）
        # > 收尾前台包 —— 用例常停在 PhotoPicker 等系统页收尾，前台包可能
        # 根本不是被测 App（168 实测报成 com.android.providers.media.module）。
        package = self._case_package_from_script()
        if package is None:
            try:
                package = self.d.app_current().get("package") or None
            except Exception:
                package = None                   # 断连等异常不该挡住报告生成
        lines = [f"# 测试报告：{self.name}",
                 f"\n**测试日期**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 f"**设备**：{self.device_info}",
                 f"**被测 App**：{package or 'unknown'}",
                 f"**最终结论**：{self.final_status}",
                 f"**证据目录**：{self.case_dir}\n"]
        if self._fatal_error is not None:
            lines.append(f"\n> ⚠️ **执行异常终止**（用例未跑完，结论不可信）："
                         f"`{type(self._fatal_error).__name__}: {self._fatal_error}`\n")
        for s in self.steps:
            lines.append(f"\n## {s['name']}")
            for r in s["results"]:
                mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                        "BLOCKED": "⛔"}.get(r["result"], "❓")
                lines.append(f"- {mark} {r['detail']}")
                if r.get("state"):
                    lines.append(f"  - 状态: {r['state']}")
                if r.get("evidence"):
                    lines.append(f"  - 证据: `{r['evidence']}`")
            for ev in s["evidences"]:
                lines.append(f"  - 证据: `{ev}`")
        duration_sec = 0
        if self._case_start_time:
            duration_sec = round(time.time() - self._case_start_time, 1)
        summary = (f"✅ {pass_n} 通过 / ❌ {fail_n} 失败 / ⚠️ {warn_n} 警告 / "
                   f"⛔ {blocked_n} 阻塞 / ℹ️ {info_n} 记录 / 共 {total} 条断言")
        lines.append(f"\n---\n**汇总**: {summary} / 耗时 {duration_sec}s")
        # ── M4 口径：dump 次数 ÷ **断言/定位点数**（2026-09-16 人确认）──────
        # 分母**不是** `t.step()` 数：dump 的目的是"定位/断言前读一次屏"，而 step
        # 是**组织单位**、不是工作量单位。178 实测：8 个 step、45 条 record、
        # 120 次 dump → 按 step 是 15.0×（看起来严重超标）、按 record 是 2.7×
        # （真实水平）。口径混用会让指标完全失真，所以这里把分母写死在产出物里。
        n_points = total + warn_n + blocked_n + info_n
        dump_ratio = (self._dump_count / n_points) if n_points else 0.0
        lines.append(f"**UI 采集**: {self._dump_count} 次 dump"
                     f"（{dump_ratio:.1f}× 断言/定位点 {n_points}；M4 目标 ≤2×）")
        lines.append(f"**最终结论**: {self.final_status}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n📄 报告已生成: {path}")
        print(f"🏁 最终结论: {self.final_status}（{summary}）")
        print(f"📊 UI dump 次数: {self._dump_count}"
              f"（{dump_ratio:.1f}× 断言/定位点 {n_points}；M4 目标 ≤2×）")
        # 完成用例记录入库
        if self._db is not None and self._db_case_id is not None:
            finished_ok = False
            try:
                self._db.finish_case(
                    self._db_case_id, path, summary,
                    final_status=self.final_status, package=package)
                finished_ok = True
            except Exception as e:
                # 入库失败不再静默：记录丢失意味着 Web UI/追溯链断裂
                print(f"⚠️ [db] 用例完成状态入库失败: {e}")
            # 度量落库（append-only，保留趋势）：cases 表被 drop_previous_cases 删旧行，
            # 回答不了"这次改动有没有更快"；duration_sec 是唯一的性能口径。
            try:
                self._db.record_metrics(
                    script_path=self.script_path, device=self.device_info,
                    duration_sec=duration_sec,
                    ocr=getattr(self, "_ocr_count", 0),
                    derived=getattr(self, "_derived_clicks", 0),
                    dump=getattr(self, "_dump_count", 0),
                    rot_start=getattr(self, "_rotation_start", None),
                    rot_end=getattr(self, "_rotation_end", None),
                    **self._run_metrics_extra())
            except Exception as e:
                print(f"⚠️ [db] 度量入库失败: {e}")
            # ── 记录清理放在**结尾**，不在 start_case / __init__（2026-09-15 改）──
            #   在开头删 = 用例一"开始"就销毁上次的好记录 + 报告 + 截图；本次若被杀
            #   （套件超时 / Ctrl-C / 断连 / 用例崩）→ 什么都没剩。
            #   实测代价：一次被中断的套件把 168-177 共 8 个用例的记录清成空壳；
            #   178 的 09-11 基线也这样丢过一次（备份早于实战，救不回来）。
            #   在结尾删 = "用完整的新记录替换旧记录"；中断时旧记录留着（哪怕状态陈旧，
            #   也远好过没有）。finished_ok 守卫：本次入库没成功就不删旧的。
            #   ① 迭代清理：脚本被改过 → 被改动取代的旧记录是探索噪音
            #      （本次是套件记录时不动探索记录，见方法内守卫）
            #   ② 只留最新：同一用例（name + script_path）只留本次这一条
            if finished_ok:
                try:
                    self._db.cleanup_iterated_cases(
                        self.script_path, self.device_info, self._db_case_id)
                except Exception as e:
                    print(f"⚠️ [db] 迭代旧记录清理失败（不影响本次记录）: {e}")
                try:
                    self._db.drop_previous_cases(self.name, self.script_path,
                                                 keep_id=self._db_case_id)
                except Exception as e:
                    print(f"⚠️ [db] 旧记录清理失败（不影响本次记录）: {e}")
        self._finished = True
        self._report_path = path
        # 探针/探查用例不入库，其截图目录与报告只是调试中间产物，
        # 执行完即清理，避免长期占用本地工作区（用户规则）。
        if _is_probe_case(self.name):
            self._cleanup_probe_artifacts()
        return path

    # ── 探针产物清理 ────────────────────────────────────────────────
    def _cleanup_probe_artifacts(self):
        """探针/探查用例不入库，其截图目录与报告只是调试中间产物，执行完即清理，
        避免长期占用本地工作区（用户规则）。幂等：目录/文件已不存在也不报错。"""
        import shutil
        if self.case_dir and os.path.isdir(self.case_dir):
            try:
                shutil.rmtree(self.case_dir, ignore_errors=True)
                print(f"[清理] 已删除探针截图目录: {self.case_dir}")
            except Exception as e:
                print(f"⚠️ 探针截图目录清理失败（不影响主流程）: {e}")
        if self._report_path and os.path.exists(self._report_path):
            try:
                os.remove(self._report_path)
                print(f"[清理] 已删除探针报告: {self._report_path}")
            except Exception as e:
                print(f"⚠️ 探针报告清理失败（不影响主流程）: {e}")
