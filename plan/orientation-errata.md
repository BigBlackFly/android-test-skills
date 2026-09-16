# 方向与屏幕几何：勘误 + 实测基线

> **性质**：对 `gaps-and-roadmap.md`（原始复盘，**保持原样不修改**）与
> `smart-skill-roadmap-v5.md` 中 **方向 / 屏幕几何相关事实**的勘误，
> 附 2026-09-14 的原始实测命令与输出。
>
> **不构成变更授权**：本文件只记录"实测是什么"。改动授权见 v5.0 动作表。
>
> **上游影响**：3 处勘误推翻了 A2.5 的立论 → **A2.5 已撤销**（见 v5.0）。
>
> **替代说明**：v5.0 的 changelog 记着「合并 gaps-recheck.md」——那份已随合并移除，
> 本文件是它的接续（同样的"勘误 + 实测基线"体例），不是同一份文件。

---

## 零、结论摘要

| # | 文档原说法 | 实测（2026-09-14） | 判定 |
|---|---|---|---|
| **E1** | 「TB323FU 自然方向即横屏」（`user_rotation=0` → `mRotation=ROTATION_90`） | `init=1904x3040` → **自然方向是竖屏**。原测量在 `accel=1` 下做的，此时 `user_rotation` **被系统忽略**，测到的是**设备物理姿态** | **测量错误** |
| **E2** | 缺口 6「`lock_portrait()` 方向语义错位」 | `accel=0 + user=0` → `cur=1904x3040` / `mRotation=0` = **竖屏** → **语义正确** | **立论不成立**（真问题在别处，见 §二） |
| **E3** | 「`wm size` 与实际窗口尺寸不符」（`wm size`=1904×3040 vs `mBounds`=3040×1904） | `wm size` = **自然尺寸**，`mBounds`/`cur=` = **当前尺寸**，转了 90° 本就该不同。且框架取屏幕尺寸走 `_screen_size()` 读**实测截图 PNG 头**（当前口径），`framework/` 里**没有任何地方调 `wm size`** | **不是缺陷**，是两个不同语义的量被当成了同一个 |
| **E4** | 「178 靠 `env_ignore` 屏蔽了 WARN」（v5.0 §一 能力 2） | ✅ **成立**：`178.py:156` 确有 `env_ignore=("accelerometer_rotation",)` | 成立 |
| **E5** | 「`178.py:157 t.lock_portrait()` 锁死方向」（v5.0 §一 能力 2） | ✅ 该行存在，但**没有"锁死"**：178 实际是**横屏跑完的**（证据见 §一.3） | 半对（行号对，结论错） |

---

## 一、原始实测证据（可复现）

设备：`51e8bf1c`（TB323FU，Android 17）。测试后设备状态已还原（`accel=1, user=0`）。

### 1. 自然尺寸 vs 当前尺寸

```
$ adb shell wm size
Physical size: 1904x3040              # ← 自然尺寸（竖屏：宽 < 高）

$ adb shell dumpsys window displays | (取 Display: 段)
Display: mDisplayId=0 (organized)
  init=1904x3040 440dpi mMinSizeOfResizeableTaskDp=220 cur=3040x1904 app=3040x1904
  overrideConfig={... winConfig={ mBounds=Rect(0, 0 - 3040, 1904)
    mDisplayRotation=ROTATION_90 ... mRotation=ROTATION_90}}
  mRotation=1 mDeferredRotationPauseCount=0
```

- `init=` = **自然**尺寸 → `1904x3040` → **自然方向是竖屏**
- `cur=` = **当前**尺寸 → `3040x1904` → 此刻是横屏（`mRotation=1` = 转了 90°）

### 2. 决定性实验：`lock_portrait()` 锁的到底是哪个方向

| 状态 | `cur=` | `mRotation` | 方向 |
|---|---|---|---|
| `accel=1, user=0`（初始） | 3040×1904 | 1 | 横屏 |
| **`accel=0, user=0`** ← `lock_portrait()` 的**全部**动作 | **1904×3040** | **0** | **竖屏 ✅** |
| `accel=0, user=1` | 3040×1904 | 1 | 横屏 |

```1459:1462:framework/test_framework.py
    def lock_portrait(self):
        """锁定竖屏（套件基线）。用例开头调用；结尾无需恢复。"""
        self.adb_shell("settings", "put", "system", "accelerometer_rotation", "0")
        self.adb_shell("settings", "put", "system", "user_rotation", "0")
```

→ **`lock_portrait()` 语义正确**（E2 判定）。

### 3. 真问题：锁不持久，178 实际在横屏跑完

```
lock_portrait applied -> accel=0 user=0
  [locked]      cur=1904x3040  mRotation=0      ← 竖屏 ✅
launch com.zui.calendar, wait 8s
after launch -> accel=1 user=0                  ← ⚠️ App 把 accel 改回 1，锁被解开
  [after-launch] cur=1904x3040  mRotation=0     ← 8s 窗口内方向未随之翻转
```

**178 自己的报告给出了它实际跑在横屏的铁证**：

```
证据: ...\storage\screenshots\case_20260914_161616\02_点击坐标_2835_203.png
```

`tap(x, y)` 的证据命名规则是 `点击坐标_{x}_{y}`：

```1172:1173:framework/test_framework.py
            self._log_action("tap", f"x={x}, y={y}", t0)
            self._auto_screenshot(f"点击坐标_{x}_{y}")
```

**x=2835 > 1904（竖屏宽）** → 该次点击发生在**横屏**（宽 3040）。
即：`lock_portrait()` 之后，前置的 `_flow.restart_calendar`（`pm_clear` + `launch_app`）
解开了锁，设备弹回物理姿态（横屏），178 全程以横屏跑完 —— **仍然 PASS**，
因为它的坐标**全部现场派生**。

> **仍未定论的一点（诚实标注）**：上表 8s 窗口内方向**没有**跟着翻转（`mRotation` 仍 0）。
> 178 的横屏结论来自报告证据（§一.3 末），说明**翻转确实发生过**，但具体延迟
> 未测。需要一次针对性复现（设备横放 + 锁竖屏 + 冷启动 + 等 30s 后读 `mRotation`）
> 才能给出翻转时延。**不要**把"8s 未翻转"当成"不会翻转"。

### 4. 顺带实测：`u2` 的 `d.info` 在 Android 17 上不可用

```
$ python -c "import uiautomator2 as u2; u2.connect('51e8bf1c').info"
RPCUnknownError: -32001 java.lang.IllegalStateException:
  ApplicationSharedMemory not initialized
  ... at androidx.test.uiautomator.UiDevice.getDisplaySizeDp(UiDevice.java:312)
```

与 `framework/smoke.py:64` 的注释（「`d.info` 在 Android 15+ 可能崩」）一致。
**方向探测因此改用** `dumpsys window displays` 解析 `mRotation`（实测**约 0.21s** / 次）。

---

## 二、真问题是什么（缺口 6 的正确表述）

不是「锁错方向」，是 **「锁不持久 + 方向变化不可见」**：

| 环节 | 现状 |
|---|---|
| 写 | `lock_portrait()` 写得对（竖屏） |
| 保持 | ❌ App 冷启动把 `accelerometer_rotation` 改回 1 → 锁被解开 |
| 检测 | ✅ 框架**已经能检测到**（漂移检测报「用例污染设备环境 0→1」） |
| **暴露** | ❌ 178 用 `env_ignore` 把它**静音**了（而 `gaps-and-roadmap` 明确"不建议"这么做） |
| 区分 | ❌ 框架无法区分"用例自己改的"vs"App/系统改的" —— 这才是缺的能力 |

**为什么会用 `env_ignore`**：不静音的话，每个走冷启动的用例都会稳定报 WARN，
而 WARN 的来源**不是用例的错**。所以 178 的做法在**当时**是合理的权宜；
错的是框架没有提供"外部改写"这一独立语义。

---

## 三、已实施（2026-09-14）

| 动作 | 落点 | 验收 |
|---|---|---|
| `case_metrics` 加 `rotation_start` / `rotation_end`（真实 `mRotation`） | `framework/db.py` | 真实库已迁移；`health_check()['rotation_changed']` 可查 |
| `TestCase.device_rotation()`（解析 `dumpsys`，失败返 `None`） | `framework/test_framework.py` | 8 条单测（解析/语义/容错） |
| `finish()` 检测「起止方向不同」→ WARN，**不受 `env_ignore` 约束** | `framework/test_framework.py` | 3 条单测（含"转屏后还原不报"与"探测失败不报"） |
| `lock_portrait()` 文档改正：只保证"调用后立刻"，不保证"全程" | `framework/test_framework.py` | — |

**"起止比较"而非"中间是否变过"的理由**：`snapshot_rotation`/`restore_rotation`
成对出现的**合法**转屏用例结束时方向已还原 → 起始==结束 → 不报；
只有**把设备留在另一个方向**的用例才报（= 119 事故与 178 的形态）。

`rotation_changed` 的**首次真实读数（2026-09-14 17:05，重跑 178）**：

```
cases:        id=75 联想日历_178  final_status=WARN      ← 改前是 PASS
case_metrics: rotation_start=0（竖屏） → rotation_end=1（横屏），duration_sec=302.0
报告:         ⚠️ 用例中途方向变化: rotation 0→1（竖屏→横屏）—— 锁定态被外部改写，
                坐标系中途换过（结论可信性风险）
汇总:         ✅ 31 通过 / ❌ 0 失败 / ⚠️ 0 警告  →  最终结论 WARN
health_check: {'duration_p50': 302.0, 'p90': 310.2, 'rotation_changed': 1, 'samples': 2}
```

**这是整个论断的活体证据**：178 在**横屏**下 31 条断言**全部通过**（0 失败），
但方向确实从竖屏变成了横屏 —— 这件事在改前**完全不可见**（被 `env_ignore` 静音）。

新语义是「**结果对不代表过程稳**」：断言不降级（还是 31/0），只把**结论**
从 PASS 标成 WARN，让"过程不稳"这件事第一次有了出口。

**成本**：302.0s vs 改前 310.2s —— 两次 `dumpsys` 约 +0.4s，可忽略。

---

## 四、复核方法（5 条可复现命令）

```bash
# 1) 自然尺寸 vs 当前尺寸
adb shell wm size
adb shell dumpsys window displays | Select-String -Pattern 'Display: mDisplayId' -Context 0,3

# 2) lock_portrait 的真实效果（做完务必还原：accel=1 user=0）
adb shell settings put system accelerometer_rotation 0
adb shell settings put system user_rotation 0
sleep 3; adb shell dumpsys window displays | Select-String -Pattern 'mRotation='
adb shell settings put system accelerometer_rotation 1
adb shell settings put system user_rotation 0

# 3) 冷启动是否解开锁定
adb shell monkey -p com.zui.calendar -c android.intent.category.LAUNCHER 1
sleep 8; adb shell settings get system accelerometer_rotation    # 期望看到 1

# 4) 178 实际跑在哪个方向（看证据文件名里的 x 坐标）
python framework/db.py  # 或直接看 storage/reports/联想日历_178_报告.md 的 证据 行

# 5) 方向变化可见性
python -c "import sys; sys.path.insert(0,'framework'); import db; print(db.RecordDB().health_check())"
```

---

## 五、连带影响（哪些既有结论要改）

| 文档 | 原内容 | 应改为 |
|---|---|---|
| `smart-skill-roadmap-v5.md` A2.5 | 三方案（A 按自然方向判断 / B 冷启动后重新锁定 / C 现状+env_ignore）选一个 | **撤销**。三方案都在解不存在的问题；真问题已由 A2.6 承接（已实施） |
| `smart-skill-roadmap-v5.md` A2.4 | 「解 `178.py` 的 `lock_portrait()` 与注释矛盾」 | 前提被推翻：178 的"横屏"注释与现场**一致**。改为**换方向重跑 178**（方向无关性验收） |
| `smart-skill-roadmap-v5.md` §一 能力 2 | 「方向语义错位」「唯一真验收被挡住」 | 按本文件 §零 勘误 |
| `gaps-and-roadmap.md` §一 相关行 | 同上 | **保持原样**（原始复盘，不修改）——勘误以本文件为准 |

## 六、"方向无关性"的正确定义（供验收引用）

> **方向之所以成为干扰，只有一个原因：坐标来自"某一次观测"，而不是"本次观测"。**
> 收敛到 ①活体 `rid`/`desc`/`text` ②活体 OCR/视觉，方向即被消掉。

因此 `lock_portrait()` 的定位是 **"对硬坐标的补偿机制，目标态是删除"**，
而不是"方向保证"。它的删除前提是两条都做到：

1. **坐标全部现场派生**（当前违规：`175.py:64`、`179.py:281`、`_set_time_tap.py:138`、
   知识卡 10 处机型戳）
2. **视口无关读取**（元素不在当前视口时要"滚到可见再读"，而不是依赖
   "首屏应该有 N 项" —— 178 的"先读值再滑动"血泪教训属此类，**它不是坐标问题**）

**可执行的验收**：同一用例在 `accel=0/user=0`（竖屏）与 `accel=0/user=1`（横屏）
下各跑一次，**断言结果必须一致**。这是 M4「换方向重跑 178」的通用化版

---

## 七、A2.4 实测结果（2026-09-14）：结论与预期相反

跑四腿（`wm fixed-to-user-rotation` + `user_rotation` 可强制方向；**每腿后已还原**）：

| 腿 | 条件 | 结果 | `rotation` 读数 |
|---|---|---|---|
| A | 自然横屏，**无** wm 标志 | **31 通过 / 0 失败** ✅ | 0→1（变过） |
| B | 强制**竖屏** + wm 标志 | 30 / **1** ❌ | 0→0 |
| C | 强制**横屏** + wm 标志（对照） | 30 / **1** ❌ | 0→0 |
| D | 套件内自然跑（第 4 次） | 30 / **1** ❌ | 0→0 |

**判定：方向不是那个变量。** B（竖屏）与 C（横屏）**差在方向、"坏"得一样**；A 与 C
**方向相同、"好"与"坏"不同**。三个 FAIL 的唯一共同点是「不是当天第一次跑」。

**被触发的断言一直是同一条**：

```
❌ 课间休息上端回绕: 期望回绕到5分钟，实际 '30分钟'
```

→ 它的形态是**滚轮按档位点按后的回绕**，落在"从 bounds 派生的点按位置是否刚好压住那一格"
的边际上。**行为是 flaky（同用例/同设备/同环境，1 天 5 次跑出 2 通过 3 失败），不是方向问题。**

> **A2.4 的原本目的（换方向重跑 178）没能证明 178 方向相关**；但它意外暴露了
> **一条不稳定断言** —— 这属于"结论可信"维度，比方向问题更该先修。
> 验收方式也不该是"两个方向各跑一次"（1 次采样分辨不出 flaky），而应是
> **同条件下重复 N 次**。方法上的教训记在这里。

---

## 八、全量基线（2026-09-14，16 个用例）

| 指标 | 值 |
|---|---|
| 用例数 / 度量行 | 16 / 21（178 跑了 4 次） |
| 耗时 p50 / p90 | **142.5s** / 310.2s |
| 墙钟合计（21 行） | ≈ 3470s ≈ 58 分钟 |
| `dump_snaps` p90 | 430 |
| `ocr_calls` p90 | 0 |
| **`rotation_changed`** | **1** |

**16 个用例的读数：只有 178 出现过一次方向变化（0→1）。**

⚠️ **但这个"1"必须谨慎解读**：方向变化只在「**设备物理姿态与锁定态相反**」时发生 ——
当天第一次 178（17:05）时设备是横屏姿态 → `lock_portrait()` 锁成竖屏 → 冷启动解锁 →
弹回横屏 = 变化；**之后设备一直停在被锁出的竖屏姿态**，后续所有运行（含套件内的 178）
`rotation` 都是 0→0。

→ 所以 `rotation_changed` 测的是「**当前设备姿态下的暴露面**」，**不是"代码里的缺陷密度"**。
要拿后者，需要在**横屏姿态**下再跑一次全量（或接受"1 是下界"这个说法）。

**本轮的其它结果（同一批运行）**：

| 用例 | 结论 | 备注 |
|---|---|---|
| 119 / 167 / 183 | PASS（5, 8, 20 条断言） | — |
| 178 | FAIL 30/1 | 上述 flaky 断言 |
| 182 | FAIL 19/1 | — |
| 179 | FAIL 1/1（496.6s） | 仅 2 条断言 → **前置未满足即中止** |
| 185 / 186 | FAIL 0/1 + 1 阻塞（~20s） | 前置要"**已有 ≥2 个课程表**"，全量套件里前序用例 `pm_clear` 过 → 环境不满足 |

**两个套件级发现**：

1. **185/186 不能直接进全量套件**：它们依赖"已有 ≥2 个课程表"的前置数据，需要**备数据**
   步骤（与 v5.0 的 A4.x 相关）。
2. **被中断的运行会留下脏记录**：中途杀掉套件后，`cases` 表留下 **8 条 `final_status=NULL`**
   的行（id 96-103，`start_case` 插了行但 `finish_case` 没跑到），另有一条
   `suites` 行（id=1）字段全空。清理工具已存在：`scripts/cleanup_records.py`。
本。
