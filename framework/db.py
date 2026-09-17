#!/usr/bin/env python3
"""
测试记录 SQLite 持久化：用例/步骤/断言结果/证据入库。
零依赖（标准库 sqlite3）。

数据库位置（按优先级）：
  1. 环境变量 DSH_WORKSPACE_DIR（测试工作区根，其下 storage/test_records.db）
  2. 默认 ~/android-test-skills-data/storage/test_records.db

显式定位而非相对路径推导：本模块可能在 skill 包或工作区任意位置被加载，
只有显式路径才能保证读的是同一个库。
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime


def default_test_dir() -> str:
    """测试工作区根目录：环境变量 > 默认 ~/android-test-skills-data。"""
    env = os.environ.get("DSH_WORKSPACE_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(os.path.expanduser("~"), "android-test-skills-data")


# ── 容错删除 ────────────────────────────────────────────────────────
# 判定标准是「磁盘上还在不在」，而不是「有没有抛异常」。
# 原因：某些运行环境会给 Python 注入「安全删除」shim，把 os.remove 改走
# 系统回收站。这类 shim 有两种误报：
#   1) 文件确实已经删掉了，但回收站二次确认返回 0x2（文件不存在）→ 照样抛异常；
#   2) 一次删除数量超过阈值时直接抛错要求人工确认（FAIL_CLOSED）。
# 两种情况下异常都不代表删除失败，只有「文件还在」才算失败。
def safe_remove(path) -> bool:
    """删除单个文件。返回是否删除成功（原本就不存在 → False，不算错误）。"""
    try:
        if not os.path.isfile(path):
            return False
    except OSError:
        return False
    try:
        os.remove(path)
        return True
    except Exception:
        try:
            if not os.path.exists(path):   # 抛了异常但文件没了 → 实际删成功
                return True
        except OSError:
            pass
        return False


def safe_rmtree(path) -> bool:
    """删除目录树。返回是否删除成功（原本就不存在 → False，不算错误）。"""
    import shutil
    try:
        if not os.path.isdir(path):
            return False
    except OSError:
        return False
    try:
        shutil.rmtree(path)
        return True
    except Exception:
        try:
            if not os.path.exists(path):
                return True
        except OSError:
            pass
        return False


def default_db_path() -> str:
    """测试记录数据库路径：<工作区>/storage/test_records.db（与截图/报告同区）。"""
    return os.path.join(default_test_dir(), "storage", "test_records.db")


def _migrate_legacy_db():
    """旧布局迁移：test_records.db 曾放在工作区根，统一挪进 storage/。

    新路径已存在 → 什么都不做（幂等）。旧文件搬不动（如被运行中的 Web UI
    锁住）→ 退化为复制：老连接继续用旧文件，新连接用副本——宁可暂时双份，
    也不能让新路径开出一个空库、历史记录"看起来丢了"。
    """
    import shutil
    old = os.path.join(default_test_dir(), "test_records.db")
    new = default_db_path()
    if not os.path.isfile(old) or os.path.isfile(new):
        return
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = old + suffix
            if os.path.isfile(src):
                shutil.move(src, new + suffix)
        print(f"[db] 已迁移旧库: {old} → {new}")
    except OSError as e:
        print(f"⚠️ [db] 旧库迁移失败，退化为复制（原文件保留）: {e}")
        try:
            shutil.copy2(old, new)
        except OSError as e2:
            print(f"⚠️ [db] 旧库复制也失败: {e2}（旧文件仍在 {old}，可手动迁移）")


DB_PATH = default_db_path()
_migrate_legacy_db()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  device TEXT,
  started_at TEXT,
  finished_at TEXT,
  report_path TEXT,
  summary TEXT,
  user_input TEXT,
  script_path TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL REFERENCES cases(id),
  name TEXT,
  ord INTEGER
);
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  result TEXT,
  detail TEXT,
  state_json TEXT,
  evidence TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS step_evidences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  evidence TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS step_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  action TEXT,
  detail TEXT,
  duration_ms INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS suites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  finished_at TEXT,
  filter TEXT,
  total INT,
  pass_n INT,
  fail_n INT,
  blocked_n INT,
  error_n INT,
  warn_n INT,
  report_path TEXT
);
-- 度量（append-only，刻意不被 drop_previous_cases 清理）：
--   cases 表同一用例只留最新一条，回答不了"这次改动有没有更快"；
--   本表按 script_path 累积，是趋势的唯一来源。
--   duration_sec = 墙钟耗时，**唯一的性能口径**；
--   ⚠️ dump_snaps 的**分母口径**（2026-09-16 人确认）：断言/定位点数 = results
--     表里该用例的**全部 record 条数**（PASS+FAIL+WARN+BLOCKED+INFO），
--     **不是** steps 表的行数 —— dump 的目的是"定位/断言前读一次屏"，而 step 是
--     组织单位、不是工作量单位。178 实测同一批数据：按 step 是 15.0×、按 record
--     是 2.7×，混用口径会让指标完全失真。M4 目标 ≤2×。
--   ocr_calls / derived_clicks / dump_snaps 是合规性计数器
--   （OCR 是否真跑 / 坐标是否从 bounds 派生 / dump 次数），
--   **不要拿 dump_snaps 当性能指标**（178 实测 116 次只占 2.6% 耗时）。
--   rotation_start / rotation_end = 用例起止的**真实屏幕旋转**（mRotation
--   0-3；0/2 竖、1/3 横）。两者不同 = 用例中途方向变过（锁定态被 App 冷启动
--   解开，实测 accelerometer_rotation 0→1），属「结论可信」维度的事件，
--   **不是性能指标**。存在理由：178 在横屏下照样 PASS（坐标全部现场派生），
--   但"过程稳不稳"必须可见 —— 本表回答"到底有几个用例中途方向变过"。
CREATE TABLE IF NOT EXISTS case_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  script_path TEXT NOT NULL,
  device TEXT,
  started_at TEXT NOT NULL,
  duration_sec REAL,
  ocr_calls INTEGER,
  derived_clicks INTEGER,
  dump_snaps INTEGER,
  rotation_start INTEGER,
  rotation_end INTEGER
);
CREATE INDEX IF NOT EXISTS idx_case_metrics_started ON case_metrics(started_at);
"""

# 旧库迁移：为早期建的表补列（幂等；列已存在时 ALTER 抛错，忽略即可）
_MIGRATIONS = [
    "ALTER TABLE cases ADD COLUMN user_input TEXT",
    "ALTER TABLE cases ADD COLUMN script_path TEXT",
    # final_status：用例最终结论（PASS/FAIL/BLOCKED/WARN/ERROR），由框架显式写入。
    # 老库没有此列时，列表查询回退到摘要文本推断（仅兼容历史数据，新数据不再推断）。
    "ALTER TABLE cases ADD COLUMN final_status TEXT",
    # package：被测 App 包名。报告丢了可以重建，但包名只写在报告里 ——
    # 不入库的话重建出来的报告这一栏就是空的。
    "ALTER TABLE cases ADD COLUMN package TEXT",
    # suite_id：套件 runner 关联（run_suite.py 插入 suites 行后透传给子进程）。
    # 单跑时该列为 NULL，不影响现有逻辑。
    "ALTER TABLE cases ADD COLUMN suite_id INTEGER",
    # case_metrics 方向列：回答"16 个用例里到底有几个跑的过程中方向变过"。
    # 老库没有这两列 → NULL，health_check 按"未知"处理（不误计为变化）。
    "ALTER TABLE case_metrics ADD COLUMN rotation_start INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN rotation_end INTEGER",
    # ── RunMetrics 增量列（P0a，plan §7.1）────────────────────────────
    # 为什么加在 case_metrics 上而不是新开 run_metrics 表：本表**已经是**
    # append-only 且刻意不被 drop_previous_cases 清理（见建表注释），
    # 天然满足 §7.1 对基线"不受保留策略影响"的要求；趋势也只有一处可查。
    # 列名与 run_metrics.EXTRA_COLS 一致（白名单单一来源）。
    "ALTER TABLE case_metrics ADD COLUMN package TEXT",
    "ALTER TABLE case_metrics ADD COLUMN script_hash TEXT",
    "ALTER TABLE case_metrics ADD COLUMN app_version_name TEXT",
    "ALTER TABLE case_metrics ADD COLUMN app_version_code TEXT",
    "ALTER TABLE case_metrics ADD COLUMN sleep_static_sec REAL",
    # 含 _flow.py/_lib 的口径：防止"把 sleep 挪进 _flow.py 让 M2 变绿"（§7.2 M2）
    "ALTER TABLE case_metrics ADD COLUMN sleep_static_sec_with_flow REAL",
    # P5：自愈/降级命中次数（§7.1 质量维度）
    "ALTER TABLE case_metrics ADD COLUMN healing_hits INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN wait_calls INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN wait_sec REAL",
    "ALTER TABLE case_metrics ADD COLUMN screenshots INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN screenshot_bytes INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN rid_set TEXT",
    "ALTER TABLE case_metrics ADD COLUMN rid_set_hash TEXT",
    "ALTER TABLE case_metrics ADD COLUMN gate_lint_errors INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN gate_check_facts_suspects INTEGER",
    "ALTER TABLE case_metrics ADD COLUMN gate_checked_words INTEGER",
]


def _extra_metric_cols():
    """RunMetrics 增量列白名单（单一来源：run_metrics.EXTRA_COLS）。

    做成函数而不是模块级常量：run_metrics 与 db 同在 framework/ 下，正常都能
    导入；万一不可用（裁剪/单文件环境）→ 返回空元组，退化成"只写基础列"，
    总比记录整条写不进去好。
    """
    try:
        from run_metrics import EXTRA_COLS
        return EXTRA_COLS
    except Exception:
        return ()


def _artifact_roots():
    """运行产物目录（截图/报告）——文件读写/删除只允许落在这两棵子树里。"""
    base = os.path.join(default_test_dir(), "storage")
    return [os.path.realpath(os.path.join(base, "screenshots")),
            os.path.realpath(os.path.join(base, "reports"))]


def is_artifact_path(path):
    """path 是否位于运行产物目录内（realpath 解符号链接 + commonpath 校验）。"""
    if not path or not os.path.isabs(path):
        return False
    try:
        full = os.path.realpath(path)
    except OSError:
        return False
    for root in _artifact_roots():
        try:
            if os.path.commonpath([full, root]) == root:
                return True
        except ValueError:       # 跨盘符（Windows）
            continue
    return False


class RecordDB:
    """线程安全的测试记录库（单连接 + 锁）。"""

    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        # 线程本地连接：测试框架和 Web UI 可能在不同线程使用同一实例，
        # 每个线程独立持有连接可彻底规避 "SQLite objects created in one thread" 错误。
        self._local = threading.local()

    def _connect(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            # sqlite 无法在不存在的目录里建库（CANTOPEN: unable to open
            # database file）。库在 storage/ 子目录下，而 storage/ 只有用例
            # 跑过才会创建——纯 Web UI / 首次使用 / HOME 被重定向的环境里
            # 它可能不存在，这里兜底建目录。
            parent = os.path.dirname(os.path.abspath(self.path))
            if not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            # timeout=10：并发写（Web UI + run_case 同时写库）锁冲突时最多等
            # 10s 再抛 database is locked，而不是立即失败丢记录。
            # WAL：写前日志，读写并发能力更好（读不阻塞写）。
            self._local.conn = sqlite3.connect(self.path, check_same_thread=False,
                                               timeout=10)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.executescript(_SCHEMA)
            # 幂等迁移：旧库补列（列已存在时 ALTER 抛错，忽略即可）
            for stmt in _MIGRATIONS:
                try:
                    self._local.conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            self._local.conn.commit()
        return self._local.conn

    def close(self):
        """关闭当前线程的连接。
        ⚠️ threading.local 每个线程各持一个连接，本方法只关当前线程的；
        其它线程的连接随各自线程退出由 GC 回收（无显式 close 时机）。"""
        with self._lock:
            if hasattr(self._local, 'conn') and self._local.conn is not None:
                self._local.conn.close()
                self._local.conn = None

    # ── cases ────────────────────────────────────────────────────────
    def start_case(self, name, device, started_at=None, user_input=None, script_path=None,
                   suite_id=None):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "INSERT INTO cases (name, device, started_at, user_input, script_path, suite_id)"
                " VALUES (?,?,?,?,?,?)",
                (name, device, started_at or datetime.now().isoformat(timespec="seconds"),
                 user_input, script_path, suite_id))
            new_id = cur.lastrowid
            self._local.conn.commit()
        # ⚠️ 「脚本被改过 → 旧记录是探索噪音」的迭代清理**刻意不在这里**
        #    （2026-09-15 改）：在 start_case 里清 = 用例一开始就把上次记录**连同
        #    报告与截图**删掉，本次若被杀（套件超时 / Ctrl-C / 断连 / 用例崩）→
        #    两边都不剩。已挪到 cleanup_iterated_cases()，由 finish_case 之后调用。
        return new_id

    def finish_case(self, case_id, report_path, summary, final_status=None,
                    finished_at=None, package=None):
        """收尾一条记录。

        package 是被测 App 包名 —— 以前只写进报告文件，报告丢了就跟着丢；
        现在入库，重建报告时能原样还原。
        """
        with self._lock:
            if package:
                self._connect().execute(
                    "UPDATE cases SET finished_at=?, report_path=?, summary=?,"
                    " final_status=?, package=? WHERE id=?",
                    (finished_at or datetime.now().isoformat(timespec="seconds"),
                     report_path, summary, final_status, package, case_id))
            else:
                # 没拿到包名就别把已有的覆盖成 NULL
                self._connect().execute(
                    "UPDATE cases SET finished_at=?, report_path=?, summary=?,"
                    " final_status=? WHERE id=?",
                    (finished_at or datetime.now().isoformat(timespec="seconds"),
                     report_path, summary, final_status, case_id))
            self._local.conn.commit()

    def backfill_package(self, dry_run=False):
        """给历史记录补 package 列。

        背景：package 列是后加的，加之前跑的记录该列为空。好在 script_path
        是 `.../cases/<包名>/<脚本>.py` 结构，目录名就是包名，可以直接反推。

        只认「长得像包名」的目录名（纯 ASCII、含点、无空白），像
        `cases/联想日历_168.py` 这种不是包名的一律跳过 —— 宁可留空也不猜。

        dry_run=True 时只报告不写库。返回 [(case_id, package), ...]。
        """
        # com.zui.calendar / com.tencent.mm 这类：段首小写字母，段内字母数字下划线
        pkg_re = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("SELECT id, script_path, package FROM cases")
            rows = cur.fetchall()
            filled = []
            for r in rows:
                # 用索引取值：连接未必设了 row_factory，元组下标最稳
                cid, script_path, package = r[0], r[1], r[2]
                if (package or "").strip():
                    continue                       # 已有值，不动
                sp = (script_path or "").replace("\\", "/")
                # 取 cases/ 之后的第一段
                m = re.search(r"/cases/([^/]+)/", sp)
                if not m:
                    continue
                cand = m.group(1)
                if not pkg_re.match(cand):
                    continue                       # 不是包名形态，跳过
                filled.append((cid, cand))
                if not dry_run:
                    self._connect().execute(
                        "UPDATE cases SET package=? WHERE id=?", (cand, cid))
            if not dry_run and filled:
                self._local.conn.commit()
            return filled

    # ── flakiness 查询 ──────────────────────────────────────────────
    def case_history(self, script_path, limit=20):
        """同一用例脚本的近期执行历史（新→旧）。

        返回 [{id, final_status, started_at, finished_at, report_path,
               duration_seconds}]，供前端历史时间线渲染。
        """
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "SELECT id, final_status, started_at, finished_at, report_path,"
                "  ROUND((julianday(finished_at)-julianday(started_at))*86400,1) AS dur"
                " FROM cases"
                " WHERE script_path=? AND final_status IS NOT NULL AND final_status <> ''"
                " ORDER BY id DESC LIMIT ?",
                (script_path, limit))
            cols = ["id", "final_status", "started_at", "finished_at",
                    "report_path", "duration_seconds"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def flaky_stats(self, min_runs=5):
        """全量用例的通过率统计。

        返回 [{script_path, package, runs, pass_rate, flaky}]。
        flaky 判定：runs >= min_runs 且 0.2 < pass_rate < 0.8。
        通过口径：final_status IN ('PASS','WARN')——WARN 退出码 0、不阻断 CI。
        """
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "SELECT script_path, package, COUNT(*) AS runs,"
                "  SUM(CASE WHEN final_status IN ('PASS','WARN') THEN 1 ELSE 0 END)"
                "   *1.0/COUNT(*) AS pass_rate"
                " FROM cases"
                " WHERE script_path IS NOT NULL AND final_status IS NOT NULL"
                "   AND final_status <> ''"
                " GROUP BY script_path")
            rows = cur.fetchall()
            result = []
            for sp, pkg, runs, rate in rows:
                rate = round(rate, 4) if rate else 0.0
                flaky = (runs >= min_runs and 0.2 < rate < 0.8)
                result.append({
                    "script_path": sp, "package": pkg,
                    "runs": runs, "pass_rate": rate, "flaky": flaky})
            return result

    def card_freshness(self):
        """每包的最近验证时间（知识卡新鲜度）。

        返回 [{package, last_pass_at, last_pass_id, last_run_at, last_run_id, runs}]。
        验证定义：final_status='PASS' 的执行（WARN 不算验证通过）。
        """
        with self._lock:
            cur = self._connect().cursor()
            # 最近 PASS
            cur.execute(
                "SELECT package, MAX(started_at), "
                "  (SELECT id FROM cases c2"
                "   WHERE c2.package = c1.package AND c2.final_status = 'PASS'"
                "   ORDER BY c2.started_at DESC LIMIT 1) AS pass_id"
                " FROM cases c1"
                " WHERE final_status = 'PASS' AND package IS NOT NULL"
                " GROUP BY package")
            pass_rows = {pkg: {"last_pass_at": at, "last_pass_id": pid}
                         for pkg, at, pid in cur.fetchall()}
            # 最近执行（任何状态）
            cur.execute(
                "SELECT package, MAX(started_at), "
                "  (SELECT id FROM cases c2"
                "   WHERE c2.package = c1.package AND c2.final_status IS NOT NULL"
                "   ORDER BY c2.started_at DESC LIMIT 1) AS run_id,"
                "  COUNT(*) AS runs"
                " FROM cases c1"
                " WHERE package IS NOT NULL AND final_status IS NOT NULL"
                "   AND final_status <> ''"
                " GROUP BY package")
            result = []
            for pkg, last_run_at, run_id, runs in cur.fetchall():
                pi = pass_rows.get(pkg, {})
                result.append({
                    "package": pkg,
                    "last_pass_at": pi.get("last_pass_at"),
                    "last_pass_id": pi.get("last_pass_id"),
                    "last_run_at": last_run_at,
                    "last_run_id": run_id,
                    "runs": runs,
                })
            return result

    # ── 度量（append-only）───────────────────────────────────────────
    def record_metrics(self, script_path, device, duration_sec,
                       ocr=0, derived=0, dump=0, rot_start=None, rot_end=None,
                       **extra):
        """写入一条用例度量；失败不抛（度量不该阻断用例收尾）。

        - script_path: 用例脚本路径（趋势按它聚合；直跑脚本时可能为 None → 跳过）
        - duration_sec: 墙钟耗时秒数（**唯一性能口径**）
        - ocr/derived/dump: 合规性计数器，含义见 _SCHEMA 注释
        - rot_start/rot_end: 用例起止的**真实屏幕旋转**（mRotation 0-3）。
          两者不同 = 用例中途方向变过 —— 是「结论可信」事件，**不是性能指标**。
          探测失败/老库补列时为 None，**不要拿 None 当 0**（None 与 0 混同会把
          "未知"误报成"竖屏→横屏"）。
        - extra: RunMetrics 增量列（P0a）。键名必须 ∈ run_metrics.EXTRA_COLS，
          **未知键静默忽略**——拼错的键直接进 SQL 会报 no such column，
          导致整条记录（含耗时）一起丢失，比"少记一列"严重得多。
        - started_at 在**这里**取 now 的 ISO 字符串，不接受 time.time() 的 float：
          否则 ORDER BY started_at 会退化成 float 排序，与 cases.started_at 混排。
        """
        if not script_path:
            return None
        cols = [c for c in _extra_metric_cols() if c in extra]
        names = ", ".join(cols)
        marks = ",".join("?" * len(cols))
        sql = ("INSERT INTO case_metrics (script_path, device, started_at,"
               " duration_sec, ocr_calls, derived_clicks, dump_snaps,"
               " rotation_start, rotation_end"
               + (", " + names if names else "") + ")"
               " VALUES (?,?,?,?,?,?,?,?,?" + ("," + marks if marks else "") + ")")
        with self._lock:
            self._connect().execute(
                sql,
                (script_path, device,
                 datetime.now().isoformat(timespec="seconds"),
                 duration_sec, ocr, derived, dump, rot_start, rot_end)
                + tuple(extra[c] for c in cols))
            self._local.conn.commit()
        return True

    def latest_metrics(self, script_path, exclude_id=None):
        """同一用例**最近一条**度量（变更归因 / 指纹对比的基线）。

        返回 dict（id / started_at / script_hash / rid_set / rid_set_hash /
        app_version_name / app_version_code / duration_sec…）或 {}（无历史）。

        为什么不从 cases 子表取：那里被 `drop_previous_cases` 删得只剩本次
        （"同用例只留最新一条"策略），拿不到"上次"。本表 append-only → 能取到。
        """
        if not script_path:
            return {}
        sql = ("SELECT id, started_at, script_hash, rid_set, rid_set_hash,"
               " app_version_name, app_version_code, duration_sec, device"
               " FROM case_metrics WHERE script_path=?")
        args = [script_path]
        if exclude_id is not None:
            sql += " AND id<>?"
            args.append(exclude_id)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._lock:
            row = self._connect().execute(sql, args).fetchone()
        if not row:
            return {}
        keys = ("id", "started_at", "script_hash", "rid_set", "rid_set_hash",
                "app_version_name", "app_version_code", "duration_sec", "device")
        return dict(zip(keys, row))

    def health_check(self, limit=100):
        """用例健康度聚合（与 flaky_stats / card_freshness 同层）。

        从 append-only 的 case_metrics 读趋势（**不是** cases 表——它只留最新一条）。
        返回 {duration_p50, duration_p90, ocr_p90, dump_p90, samples,
              rotation_changed}。

        `rotation_changed` = 最近 limit 条里"起止方向不同"的条数 —— 直接回答
        "有几个用例跑的过程中方向变过"（= 锁定态被 App 冷启动解开的暴露面）。
        起止任一为 None（老库补列 / 探测失败）→ **不计入**，避免把"未知"当"变了"。
        """
        with self._lock:
            rows = self._connect().execute(
                "SELECT duration_sec, ocr_calls, dump_snaps,"
                " rotation_start, rotation_end FROM case_metrics"
                " ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        if not rows:
            return {"duration_p50": 0, "duration_p90": 0,
                    "ocr_p90": 0, "dump_p90": 0, "samples": 0,
                    "rotation_changed": 0}

        def _pct(vals, q):
            vals = sorted(v or 0 for v in vals)
            return vals[min(len(vals) - 1, int(round((len(vals) - 1) * q)))]

        return {
            "duration_p50": _pct([r[0] for r in rows], 0.5),
            "duration_p90": _pct([r[0] for r in rows], 0.9),
            "ocr_p90": _pct([r[1] for r in rows], 0.9),
            "dump_p90": _pct([r[2] for r in rows], 0.9),
            "rotation_changed": sum(1 for r in rows
                                    if r[3] is not None and r[4] is not None
                                    and r[3] != r[4]),
            "samples": len(rows),
        }

    # ── suites ──────────────────────────────────────────────────────
    def start_suite(self, filter_desc, total):
        """插入套件记录，返回 suite_id。子进程通过 DSH_SUITE_ID 环境变量关联。"""
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "INSERT INTO suites (started_at, filter, total) VALUES (?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), filter_desc, total))
            self._local.conn.commit()
            return cur.lastrowid

    def finish_suite(self, suite_id, pass_n, fail_n, blocked_n, error_n, warn_n,
                     report_path, finished_at=None):
        with self._lock:
            self._connect().execute(
                "UPDATE suites SET finished_at=?, pass_n=?, fail_n=?, blocked_n=?,"
                " error_n=?, warn_n=?, report_path=? WHERE id=?",
                (finished_at or datetime.now().isoformat(timespec="seconds"),
                 pass_n, fail_n, blocked_n, error_n, warn_n, report_path, suite_id))
            self._local.conn.commit()

    # ── steps ────────────────────────────────────────────────────────
    def add_step(self, case_id, name, ord_):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("INSERT INTO steps (case_id, name, ord) VALUES (?,?,?)",
                        (case_id, name, ord_))
            self._local.conn.commit()
            return cur.lastrowid

    # ── results ──────────────────────────────────────────────────────
    def add_step_evidence(self, step_id, evidence):
        with self._lock:
            self._connect().execute(
                "INSERT INTO step_evidences (step_id, evidence, created_at) VALUES (?,?,?)",
                (step_id, evidence, datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    def add_step_action(self, step_id, action, detail=None, duration_ms=0):
        with self._lock:
            self._connect().execute(
                "INSERT INTO step_actions (step_id, action, detail, duration_ms, created_at)"
                " VALUES (?,?,?,?,?)",
                (step_id, action, detail, duration_ms,
                 datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    def add_result(self, step_id, result, detail, state=None, evidence=None):
        with self._lock:
            self._connect().execute(
                "INSERT INTO results (step_id, result, detail, state_json, evidence, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (step_id, result, detail,
                 json.dumps(state, ensure_ascii=False) if state else None,
                 evidence, datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    # ── 查询（供前端/报告用）────────────────────────────────────────
    def list_cases(self, limit=50, include_internal=False):
        """列出测试记录。

        默认隐藏内部记录（名称以 _ 开头：__probe__ / __states_probe__ 等
        框架探针与调试用例），它们是执行过程的中间数据，不是真用例。
        include_internal=True 时全量返回（命令行排查用）。

        迭代噪音由 start_case() 的 mtime 检测自动清理，这里不再去重。
        """
        with self._lock:
            cur = self._connect().cursor()
            sql = ("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                   " user_input, script_path, package,"
                   " CASE WHEN started_at IS NOT NULL AND finished_at IS NOT NULL THEN"
                   "  ROUND((julianday(finished_at) - julianday(started_at)) * 86400, 1)"
                   " ELSE NULL END as duration_seconds,"
                   # 新数据读 final_status 列；老数据（该列为空）回退摘要文本推断
                   " CASE WHEN final_status IS NOT NULL AND final_status <> ''"
                   "      THEN final_status"
                   "      WHEN summary LIKE '% 0 失败%' THEN 'PASS'"
                   "      WHEN summary LIKE '% 失败%' THEN 'FAIL'"
                   "      ELSE 'UNKNOWN' END as status"
                   " FROM cases")
            params = []
            if not include_internal:
                sql += " WHERE name NOT LIKE '\\_%' ESCAPE '\\'"
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            cols = ["id", "name", "device", "started_at", "finished_at",
                    "report_path", "summary", "user_input", "script_path",
                    "package", "duration_seconds", "status"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def cleanup_iterated_cases(self, script_path, device, exclude_id):
        """清理同脚本的迭代旧记录（脚本被改过 → 旧记录是探索噪音）。

        判定规则：
          script mtime > 旧记录 started_at  →  脚本被改过了  →  迭代  →  删
          script mtime <= 旧记录 started_at →  脚本没动    →  有意复跑 →  留

        条件叠加（B+C）：
          - 同 script_path + 同 device（换设备保留）
          - suite_id IS NULL（套件记录保留）
          - 本次（exclude_id）也必须是非套件记录才清（套件跑动时不动探索记录）

        ⚠️ 调用时机（2026-09-15 改，与 drop_previous_cases 同一原则）：
          只能在**本次记录完整落库之后**调用（`finish_case` 之后），不能在
          `start_case` 里。以前在 start_case 里清 = 用例一开始就把上次记录连同
          报告与截图删掉；本次若被杀（套件超时 / Ctrl-C / 断连 / 用例崩）→
          两边都不剩 —— 实测 168-177 共 8 个用例就是这样被清成空壳的。

        返回删除的记录数（脚本不存在等情况下为 0）；失败不抛
        （记录清理不该阻断用例执行）。
        """
        # 脚本文件不存在（可能被删了）或 mtime 取不到 → 不清理
        try:
            mtime = os.path.getmtime(script_path)
        except OSError:
            return 0
        mtime_iso = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")

        with self._lock:
            cur = self._connect().cursor()
            cur.execute("SELECT suite_id FROM cases WHERE id=?", (exclude_id,))
            row = cur.fetchone()
            # 本次记录不存在，或本身是套件记录 → 不动探索记录
            if row is None or row[0] is not None:
                return 0
            cur.execute(
                "SELECT id, started_at FROM cases"
                " WHERE script_path=? AND device=? AND suite_id IS NULL AND id!=?"
                " ORDER BY id DESC",
                (script_path, device, exclude_id))
            old_ids = []
            for oid, sat in cur.fetchall():
                # 脚本 mtime > 旧记录开始时间 → 脚本在上次跑后被改过
                if (sat or "") < mtime_iso:
                    old_ids.append(oid)

        # 逐条复用 delete_case 的级联删除 + 产物清理逻辑
        removed = 0
        for oid in old_ids:
            try:
                self.delete_case(oid, remove_artifacts=True)
                removed += 1
            except Exception:
                pass  # 清理失败不阻塞主流程
        return removed

    def delete_case(self, case_id, remove_artifacts=False):
        """删除用例记录，级联删除 steps/results/evidences/actions。

        remove_artifacts=True 时连带删除磁盘产物：
        - 报告（report_path）及其同前缀的时间戳备份（<name>_<ts>_报告.md）
        - 证据截图（results.evidence / step_evidences.evidence 引用的文件）
        - 截图目录：该 case 引用截图所在的 case_* 目录若不再被其它记录
          引用，整目录删除
        返回删除的文件/目录数（失败不阻塞，记录仍会删除）。
        """
        removed = 0
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            # 收集磁盘产物路径（删库行之前取，否则查不到）
            cur.execute("SELECT report_path FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            report_path = row[0] if row else None
            cur.execute(
                "SELECT evidence FROM step_evidences WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            evidence_paths = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(
                "SELECT evidence FROM results WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?) AND evidence IS NOT NULL",
                (case_id,))
            evidence_paths += [r[0] for r in cur.fetchall() if r[0]]
            # 删库行
            cur.execute("DELETE FROM results WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM step_evidences WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM step_actions WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM steps WHERE case_id=?", (case_id,))
            cur.execute("DELETE FROM cases WHERE id=?", (case_id,))
            conn.commit()

        if not remove_artifacts:
            return 0

        # ── 磁盘产物删除（锁外执行，文件 IO 不阻塞其它记录操作）──
        # 数据库行已经删掉了：产物清理只是顺手打扫，**任何失败都不能回滚 /
        # 不能往上抛**——否则调用方（Web UI）会把整次删除判成失败，甚至因为
        # 未捕获异常把连接掐断，前端只看到一句无头无脑的 "Failed to fetch"。
        try:
            removed += self._remove_artifacts(evidence_paths, report_path, case_id)
        except Exception:
            pass
        return removed

    def drop_previous_cases(self, name, script_path=None, keep_id=None,
                            remove_artifacts=True):
        """删除**同一用例**的历史执行记录，只留最新一次（用户 2026-09-11 定）。

        为什么需要：同一个用例脚本反复跑（改一版跑一次、复跑验证稳定性）会
        每次都插一条 cases，导致记录库里同一用例几十条、且旧的多是中间 FAIL
        状态，人看记录时噪声盖过结论。子表（steps/results/step_evidences/
        step_actions）按 case_id 挂着，只删 cases 行会留孤儿数据。

        - name        用例名（cases.name）
        - script_path 用例脚本路径：给了就精确匹配，避免误删"同名不同包"
        - keep_id     保留的记录 id（通常是刚 start_case 出来的本次记录）
        - remove_artifacts=True 时连带清理旧报告与截图（复用 delete_case）

        返回删除的记录数。失败不抛（记录清理不该阻断用例执行）。
        """
        removed = 0
        try:
            with self._lock:
                conn = self._connect()
                sql = "SELECT id FROM cases WHERE name=?"
                args = [name]
                if script_path:
                    sql += " AND script_path=?"
                    args.append(script_path)
                ids = [r[0] for r in conn.execute(sql, args).fetchall()]
            for cid in ids:
                if keep_id is not None and cid == keep_id:
                    continue
                self.delete_case(cid, remove_artifacts=remove_artifacts)
                removed += 1
        except Exception:
            pass          # 清理失败不影响本次记录写入
        return removed

    def _remove_artifacts(self, evidence_paths, report_path, case_id):
        """删除一次执行的磁盘产物（截图目录 + 报告及同前缀备份），返回删除数。"""
        removed = 0
        shot_dirs = set()
        for p in evidence_paths:
            if not is_artifact_path(p):   # 只删运行产物目录内的文件
                continue
            parent = os.path.dirname(os.path.abspath(p))
            # case_* 目录（一次执行一个目录）按目录删，其它散文件按文件删
            if os.path.basename(parent).startswith("case_"):
                shot_dirs.add(parent)
            elif safe_remove(p):
                removed += 1
        for d in shot_dirs:
            if not is_artifact_path(d):   # 整目录删除前同样过白名单
                continue
            # 不再被其它记录引用 → 整目录删；仍被引用 → 只删本 case 引用的文件
            if self._dir_referenced_elsewhere(d, exclude_case=case_id):
                for p in {e for e in evidence_paths
                          if os.path.dirname(os.path.abspath(e)) == d}:
                    if safe_remove(p):
                        removed += 1
            else:
                if safe_rmtree(d):
                    removed += 1
        # 报告：主报告 + 同前缀时间戳备份（联想日历_168_20260902_151054_报告.md）
        if report_path:
            rp = os.path.abspath(report_path)
            rdir = os.path.dirname(rp)
            base = os.path.basename(rp)
            stem = base[:-len("_报告.md")] if base.endswith("_报告.md") else base
            candidates = {rp}
            try:
                for f in os.listdir(rdir):
                    if f.startswith(stem) and f.endswith("_报告.md"):
                        candidates.add(os.path.join(rdir, f))
            except OSError:
                pass
            for p in candidates:
                if not is_artifact_path(p):   # 只删运行产物目录内的文件
                    continue
                # ⚠️ 仍被别的记录引用 → 不删。报告名 `<name>_报告.md` 是**共享**的
                #    （finish() 的既定语义：重跑覆盖），而上面的 stem 前缀匹配会
                #    命中**本次刚写的**那份报告 —— 清理一旦挪到 finish() 之后，
                #    旧记录的产物清理就把当前报告一起删了。
                #    2026-09-15 实测：183 记录在、报告消失（含时间戳备份，0 个残留）。
                if self._report_referenced_elsewhere(p, case_id):
                    continue
                if safe_remove(p):
                    removed += 1
        return removed

    def _report_referenced_elsewhere(self, report_path, exclude_case):
        """报告是否仍被别的记录引用（同名用例重跑共用同一份报告名）。

        在 delete_case 的锁外调用，内部短暂重新加锁 —— 与
        _dir_referenced_elsewhere（截图目录共享时不能整目录删）同一套路数。
        **库行已删掉之后**才调用：此时"本次保留的那条"仍在库里，会被正确
        识别为"还被引用"，从而保住本次报告。
        """
        with self._lock:
            row = self._connect().execute(
                "SELECT COUNT(*) FROM cases WHERE report_path=? AND id!=?",
                (report_path, exclude_case)).fetchone()
        return bool(row and row[0])

    def _dir_referenced_elsewhere(self, shot_dir, exclude_case):
        """检查截图目录是否被除 exclude_case 外的其它记录引用。

        在 delete_case 的锁外调用，内部短暂重新加锁。
        """
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            like = shot_dir.rstrip(os.sep) + os.sep + "%"
            cur.execute(
                "SELECT COUNT(*) FROM step_evidences WHERE evidence LIKE ?"
                " AND step_id IN (SELECT id FROM steps WHERE case_id != ?)",
                (like, exclude_case))
            n1 = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM results WHERE evidence LIKE ?"
                " AND step_id IN (SELECT id FROM steps WHERE case_id != ?)",
                (like, exclude_case))
            n2 = cur.fetchone()[0]
            return (n1 + n2) > 0

    def get_case(self, case_id):
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                        " user_input, script_path, final_status, package"
                        " FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            if not row:
                return None
            case = dict(zip(["id", "name", "device", "started_at", "finished_at",
                             "report_path", "summary", "user_input", "script_path",
                             "final_status", "package"], row))
            cur.execute("SELECT id, name, ord FROM steps WHERE case_id=? ORDER BY ord", (case_id,))
            steps = []
            for sid, sname, sord in cur.fetchall():
                cur.execute("SELECT result, detail, state_json, evidence FROM results"
                            " WHERE step_id=? ORDER BY id", (sid,))
                res_rows = cur.fetchall()
                results = []
                for r, d, st, ev in res_rows:
                    entry = {"result": r, "detail": d}
                    if st:
                        try:
                            entry["state"] = json.loads(st)
                        except ValueError:
                            pass
                    if ev:
                        entry["evidence"] = ev
                    results.append(entry)
                cur.execute("SELECT evidence FROM step_evidences"
                            " WHERE step_id=? ORDER BY id", (sid,))
                evidences = [row[0] for row in cur.fetchall() if row[0]]
                cur.execute("SELECT action, detail, duration_ms, created_at FROM step_actions"
                            " WHERE step_id=? ORDER BY id", (sid,))
                actions = [{"action": a, "detail": d, "duration_ms": dur, "created_at": ca}
                           for a, d, dur, ca in cur.fetchall()]
                steps.append({"id": sid, "name": sname, "results": results,
                              "evidences": evidences, "actions": actions})
            case["steps"] = steps
            return case


# 全局单例（供框架懒加载）
_db_singleton = None


def get_db():
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = RecordDB()
    return _db_singleton


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="测试记录数据库 CLI")
    parser.add_argument("--stale-cards", type=int, metavar="DAYS",
                        help="列出超过 N 天未验证的知识卡")
    parser.add_argument("--health", action="store_true",
                        help="打印用例度量聚合（耗时 P50/P90 + 合规计数）")
    args = parser.parse_args()

    db = get_db()
    if args.health:
        h = db.health_check()
        if not h["samples"]:
            print("📊 暂无度量数据（case_metrics 为空）")
        else:
            print(f"📊 样本 {h['samples']} 条 / "
                  f"耗时 P50={h['duration_p50']}s P90={h['duration_p90']}s / "
                  f"OCR P90={h['ocr_p90']} / dump P90={h['dump_p90']}")
        raise SystemExit(0)
    if args.stale_cards is not None:
        from datetime import datetime as _dt, timedelta
        cutoff = (_dt.now() - timedelta(days=args.stale_cards)).isoformat(
            timespec="seconds")
        rows = db.card_freshness()
        stale = [r for r in rows
                 if not r["last_pass_at"] or r["last_pass_at"] < cutoff]
        never = [r for r in rows if not r["last_pass_at"]]
        expired = [r for r in rows
                   if r["last_pass_at"] and r["last_pass_at"] < cutoff]
        if never:
            print(f"🔴 从未验证 ({len(never)}):")
            for r in never:
                print(f"  {r['package']} ({r['runs']} 次执行)")
        if expired:
            print(f"🟡 超过 {args.stale_cards} 天未验证 ({len(expired)}):")
            for r in expired:
                print(f"  {r['package']} 最后 PASS: {r['last_pass_at']}")
        if not stale:
            print(f"✅ 全部知识卡在 {args.stale_cards} 天内有验证记录")
    else:
        for c in db.list_cases(5):
            print(f"#{c['id']} {c['name']} {c['started_at']} {c['summary']}")
