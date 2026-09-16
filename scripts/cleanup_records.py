#!/usr/bin/env python3
"""一次性清理记录库（用户 2026-09-11 定）：
  1. 删除**无 USER_INPUT** 的记录（探查/补采/备数据/补验/框架自测等辅助脚本）
     —— 连同子表 + 磁盘产物（旧报告/截图）
  2. 同一用例（同 name + script_path）只留**最新**一条，历史连子表一起删

用法:
  python cleanup_records.py            # 预演（只报告，不写库）
  python cleanup_records.py --apply    # 实际执行
"""
import os
import shutil
import sqlite3
import sys
import time

STORAGE = os.environ.get("DSH_STORAGE_DIR") or os.path.join(
    os.environ.get("DSH_WORKSPACE_DIR",
                   os.path.expanduser("~/android-test-skills-data")), "storage")
DB_PATH = os.path.join(STORAGE, "test_records.db")

SUBTABLES = ("results", "step_evidences", "step_actions")   # 都按 step_id 挂 steps


def _case_ids_for(sql, args=()):
    conn = sqlite3.connect(DB_PATH)
    try:
        return [r[0] for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _count_sub(cid):
    """统计一条 case 名下的子表行数（steps 按 case_id，其余按 step_id 挂 steps）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        out = {t: conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE step_id IN"
            f" (SELECT id FROM steps WHERE case_id=?)", (cid,)).fetchone()[0]
            for t in SUBTABLES}
        out["steps"] = conn.execute(
            "SELECT COUNT(*) FROM steps WHERE case_id=?", (cid,)).fetchone()[0]
        return out
    finally:
        conn.close()


def _delete(cid, remove_artifacts=True):
    """删一条记录（含子表 + 产物）。复刻 db.delete_case 的逻辑，独立实现
    以免脚本依赖 framework 的导入路径。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT report_path, name FROM cases WHERE id=?", (cid,))
    row = cur.fetchone()
    report_path = row[0] if row else None
    ev = []
    cur.execute("SELECT evidence FROM step_evidences WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?)", (cid,))
    ev += [r[0] for r in cur.fetchall() if r[0]]
    cur.execute("SELECT evidence FROM results WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?) AND evidence IS NOT NULL",
                (cid,))
    ev += [r[0] for r in cur.fetchall() if r[0]]
    for t in ("results", "step_evidences", "step_actions"):
        cur.execute(f"DELETE FROM {t} WHERE step_id IN"
                    f" (SELECT id FROM steps WHERE case_id=?)", (cid,))
    cur.execute("DELETE FROM steps WHERE case_id=?", (cid,))
    cur.execute("DELETE FROM cases WHERE id=?", (cid,))
    conn.commit()
    conn.close()

    if not remove_artifacts:
        return 0
    n = 0
    # 报告 + 同前缀时间戳备份
    if report_path and os.path.isfile(report_path):
        base = os.path.basename(report_path)
        stem = base[:-len("_报告.md")] if base.endswith("_报告.md") else base
        d = os.path.dirname(report_path)
        for f in os.listdir(d):
            if f.startswith(stem) and f.endswith("_报告.md"):
                try:
                    os.remove(os.path.join(d, f))
                    n += 1
                except OSError:
                    pass
    # 证据截图所在目录（若已无其它记录引用）
    dirs = {os.path.dirname(p) for p in ev if p}
    conn = sqlite3.connect(DB_PATH)
    for dd in dirs:
        still = 0
        for t, col in (("step_evidences", "evidence"), ("results", "evidence")):
            still += conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE {col} LIKE ?",
                (os.path.join(dd, "%"),)).fetchone()[0]
        if still == 0 and os.path.isdir(dd):
            shutil.rmtree(dd, ignore_errors=True)
            n += 1
    conn.close()
    return n


def main():
    apply_ = "--apply" in sys.argv
    if not os.path.isfile(DB_PATH):
        print(f"❌ 找不到数据库: {DB_PATH}")
        return 1
    print(f"数据库: {DB_PATH}")
    print(f"模式: {'★ 实际执行' if apply_ else '预演（不写库）'}\n")

    # ── 备份 ──────────────────────────────────────────────
    if apply_:
        bak = f"{DB_PATH}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(DB_PATH, bak)
        print(f"已备份: {bak}\n")

    conn = sqlite3.connect(DB_PATH)
    total_before = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    conn.close()
    print(f"清理前 cases: {total_before} 条\n")

    # ── ③ 空记录：被中断的运行（有 cases 行、无任何 steps）────
    # 形态：`start_case` 插了行，但进程被杀 → `finish_case` 从没跑到 →
    # final_status 为 NULL 且没有任何子表。
    # ⚠️ 这类行**不能靠②的"留最新"清掉** —— 它们恰恰是 id 最大的那批。
    #    实测（2026-09-14）：一次被中断的套件留下 8 条 NULL 行，全都比同名用例
    #    已完成的记录新；单跑②会把 8 条好记录删掉、把 8 条空壳留下。
    #    所以本规则必须排在②**之前**，且其 id 要进 junk_set。
    # 判据**只认"没有 final_status"**：跑完的用例必然有结论（`finish_case` 一定写），
    # 没结论 = 这次没跑完 → 它的记录与报告都不可信。
    # ⚠️ 别拿"无 steps"当判据（本规则第一版就是这么写的，实测漏判）：
    #    被杀的运行时**往往已经写了若干 steps**（183 被杀时已写 40 行）；
    #    ③ 漏掉它之后，② 的"留最新"反而把**上一次的好记录删掉、把空壳留下**
    #    —— 2026-09-15 实测踩到一次，所以判据改为只认 final_status。
    # ⚠️ 别在用例**正在跑**的时候执行本脚本：会把在跑那条（还没有结论）一起删掉。
    interrupted = []
    conn = sqlite3.connect(DB_PATH)
    for cid, name, fs in conn.execute(
            "SELECT id, name, final_status FROM cases ORDER BY id").fetchall():
        if fs is not None and str(fs).strip():
            continue                       # 有结论 = 跑完过
        interrupted.append((cid, name))
    conn.close()
    print(f"【③ 未跑完的记录（无 final_status）】{len(interrupted)} 条")
    for cid, nm in interrupted:
        print(f"   #{cid:<4} {nm:<28}")
    if apply_:
        files = sum(_delete(cid) for cid, _ in interrupted)
        print(f"   → 已删除 {len(interrupted)} 条记录，连带清理 {files} 个产物文件/目录")

    # ── ① 无 USER_INPUT 的辅助脚本记录 ────────────────────
    junk = _case_ids_for(
        "SELECT id FROM cases WHERE user_input IS NULL OR TRIM(user_input)=''"
        " ORDER BY id")
    print(f"【① 无 USER_INPUT（辅助脚本，完全不入库）】{len(junk)} 条")
    for cid in junk:
        name = _case_ids_for("SELECT name FROM cases WHERE id=?", (cid,))
        nm = name[0] if name else "?"
        c = _count_sub(cid)
        print(f"   #{cid:<4} {nm:<28} 子表={c}")
    if apply_:
        files = sum(_delete(cid) for cid in junk)
        print(f"   → 已删除 {len(junk)} 条记录，连带清理 {files} 个产物文件/目录")

    # ── ② 同用例重复执行，只留最新 ────────────────────────
    # 注意：① 里已删的 id 不能重复统计/删除（如 补采D_185_置灰逻辑 既无
    # USER_INPUT 又是重复执行，会同时命中两条规则）。
    junk_set = set(junk) | {cid for cid, _ in interrupted}
    conn = sqlite3.connect(DB_PATH)
    groups = conn.execute(
        "SELECT name, script_path, COUNT(*) n FROM cases"
        " GROUP BY name, script_path HAVING n > 1 ORDER BY name").fetchall()
    conn.close()
    dups = []
    for name, sp, n in groups:
        ids = _case_ids_for(
            "SELECT id FROM cases WHERE name=? AND IFNULL(script_path,'')=?"
            " ORDER BY id", (name, sp or ""))
        dups += [i for i in ids[:-1] if i not in junk_set]   # 排除①已处理的
    print(f"\n【② 同一用例重复执行，只留最新】{len(groups)} 组 → 删 {len(dups)} 条"
          f"（已排除①中处理的 {len(junk_set)} 条）")
    for name, sp, n in groups:
        print(f"   {name:<28} {n} 次 → 留最新")
    if apply_:
        files = sum(_delete(cid) for cid in dups)
        print(f"   → 已删除 {len(dups)} 条记录，连带清理 {files} 个产物文件/目录")

    # ── 汇总 ──────────────────────────────────────────────
    if apply_:
        conn = sqlite3.connect(DB_PATH)
        after = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        print(f"\n{'='*60}")
        print(f"清理后 cases: {after} 条（原 {total_before}，删 {total_before-after}）")
        for t in ("steps", "results", "step_evidences", "step_actions"):
            nn = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"   {t}: {nn} 行")
        print(f"\n保留的用例记录:")
        for r in conn.execute("SELECT id, name, final_status FROM cases"
                              " ORDER BY id"):
            print(f"   #{r[0]:<4} {r[1]:<24} {r[2]}")
        conn.close()
    else:
        print(f"\n（预演结束。加 --apply 实际执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
