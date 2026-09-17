"""生成 eval 的**正面样本**：按 P3b 规范写，判定器应全项通过。

不要求真机可跑（判定器是**静态**判定），但结构必须完整、且不踩任何诱导陷阱：
前置缺失走 BLOCKED、坐标运行时派生、等界面用 wait_*、选资产先排序、每步留证。
"""
USER_INPUT = """从图库导入课程表：进入图库、选课程表那张图、确认导入成功。"""

RID_GALLERY_ENTRY = "com.demo:id/gallery_entry"
RID_PICKER = "com.demo:id/picker"
RID_THUMB = "com.demo:id/thumb"
RID_SAVE = "com.demo:id/save"


def run(t):
    # 前置条件缺失 → BLOCKED（不记 FAIL，避免污染缺陷库，§10.3）
    t.block_unless(lambda: t.el_bounds(rid=RID_GALLERY_ENTRY) is not None,
                   "当前页面没有图库导入入口", probe="gallery_entry 未找到")

    with t.step("进入图库选图页"):
        t.tap_rid(RID_GALLERY_ENTRY, silent=True)
        t.wait_rid(RID_PICKER, timeout=10)
        t.record("PASS", "已进入系统图库选图页")

    with t.step("选课程表那张图（不盲点第一个）"):
        # 共享媒体库顺序会变 → 视觉排序 + 预检，而不是点第一个
        cand = t.locate(rid=RID_THUMB)
        nodes = [cand.node] if cand.node else []
        ranked = t.rank_by_vision(nodes, "哪张图是课程表",
                                  positive=["星期", "课表"])
        t.record("PASS" if ranked else "BLOCKED",
                 "候选已按视觉排序" if ranked else "没有可排序的候选缩略图")

    with t.step("确认导入并等待结果"):
        # 坐标从活体 bounds 派生（不写死），等界面用 wait_*
        b = t.el_bounds(rid=RID_SAVE)
        if b:
            t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
        t.wait_rid("com.demo:id/imported", timeout=15)
        t.record("PASS" if t.el_bounds(rid="com.demo:id/imported") else "FAIL",
                 "导入结果已出现")
