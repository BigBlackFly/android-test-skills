"""生成 eval 的**反面样本**：故意同时踩多个诱导陷阱（§八 P3b）。

⚠️ 不要当用例跑。它存在的唯一意义是让 `judge_generation.py` 的判据能被回归验证
（判定器自己也会静默失效：判据写错 → 永远 PASS → 评测看起来全绿）。

踩到的坑（判定器应逐条报 FAIL）：
  ① 无 USER_INPUT        → 不入库 + 执行期缓存守卫失效（三重静默）
  ② 写死坐标 + 固定偏移   → 换设备/换方向即失效
  ③ 长裸 sleep（>3s 无注释）→ 等界面不用 wait_*（M2 要消灭的对象）
  ④ 盲点第一个缩略图      → 共享媒体库顺序会变，"第一个"≠ 目标
  ⑤ 抄任务描述里的文案    → 真机记录里没有这句（编造）
  ⑥ 无 step/record       → 没有证据链

注：**≤3s 的 settle sleep 是合规的**（case-writing.md 的约定），所以这里用 8s
并且不写注释 —— 否则这条判据测不出东西。
"""
import time


def run(t):
    time.sleep(8)
    t.tap_xy(1520, 940)
    b = t.el_bounds(rid="com.demo:id/thumb")
    t.tap_xy(b[0] + 61, b[1] + 40)
    t.tap_text("保存成功，已同步到云端")
    t.tap_rid("com.demo:id/save")
