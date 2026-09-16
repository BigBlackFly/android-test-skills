# Smart Skill Roadmap v5.0 —— 合并版（以目标为导向）

> **本文档合并**：`smart-skill-roadmap.md`（v4.11，2026-09-14）+ `gaps-recheck.md`（2026-09-14 实测复核）。
> **上游不再改动，仅作依据引用**：
> - `gaps-and-roadmap.md`（2026-09-11）— 从 0 跑 178 的真实复盘，7 个缺口 + 阶段〇~五
> - `roadmap-to-A+.md`（2026-09-09）— 工程质量进阶（**已基本全部落地**）
>
> **变更管控**：涉及 `framework/`、`SKILL.md`、`docs/` 的改动，实施前须先出「原文 vs 修改后」提案经人确认。
> 本文档是"做什么"的共识，不是变更授权。
> **状态**：待审。**版本**：v5.0（取代 v4.11）

---

## 一、目标（先定清楚，因为所有分歧都从"目标没定义清"来）

**做一个聪明的、可靠的 Android 自动化测试 SKILL。**

拆成 **5 条可验收的能力**——这是本文档的骨架，后面所有动作都挂在某个能力下：

| # | 能力 | 一句话验收 | 现状（2026-09-14） |
|---|---|---|---|
| **1** | **结论可信** | 不假 PASS，**也不假 INFO**（隐藏的失败必须可见） | ⚠️ 178 全 PASS 且可信（断言走 rid）；**175 的 OCR 是"真跑但可能跑错地方"，且静默降级** |
| **2** | **跨设备成立** | 换设备 / 换方向重跑，断言结果不变 | ❌ 未验证 + 3 处活体违规（`175.py:64` / `179.py:281` / `183.py:234`） |
| **3** | **该问才问** | 20% 真卡住时停下求助；80% 常规路径安静执行 | ❌ 未实现。178 事故直接原因就是"结论已定还连改 4 版、多跑 4 次（22 分钟）" |
| **4** | **知识可复用** | 探索成果自动进执行；导航路径成体系；能续跑 | ❌ 探索→卡→执行 三段无通道；每个用例都付冷启动 |
| **5** | **成本可知** | 能回答"这次改动到底快了多少" | ❌ `case_metrics` 三列**无耗时**；178 的 09-11 基线已被销毁 |

**明确不是目标**：追求极致速度。
**但**——178 实测 311.5s 里 **69% 是 `time.sleep` + 截图 + 固定延时**，属"每次运行都在付"的成本，值得顺手处理（见 §三 能力 5）。

---

## 二、实测事实基线（唯一一次带 trace 的真机测量）

| 项 | 值 |
|---|---|
| 用例 / 设备 | `178.py` / `51e8bf1c`（TB323FU，Android 17） |
| 结果 | **PASS**（31 通过 / 0 失败 / 0 警告 / 0 阻塞 / 10 记录） |
| **墙钟** | **311.5s**（框架内部 307.0s） |
| **UI dump** | **116 次** |
| 截图 | **110 张 / 16.3 MB** |

### 2.1 时间预算

| 项 | 耗时 | 占比 | 依据 |
|---|---|---|---|
| `time.sleep`（45 处静态 + 函数重复调用） | **≈110s** | **35%** | 静态求和 81.2s 为下界 |
| 截图 110 张 | **56.3s** | **18.1%** | 实测 512ms/张 |
| tap 58 次（含 `ACTION_DELAY=1.0`×37） | **49.0s** | **15.7%** | DB `step_actions` |
| `ensure_awake`（throttle 3s × 2 条 adb） | 20~35s | 6~11% | 实测 343ms/次 |
| **dump 116 次** | **8.1s** | **2.6%** | 实测 69.8ms/次 |
| 其余 | ≈50s | 16% | 差额 |

### 2.2 实测单项成本

| 原语 | 实测 |
|---|---|
| `d.dump_hierarchy()` | **69.8 ms** |
| `adb exec-out screencap -p` | **512.2 ms**（213 KB） |
| 单条 JSON-RPC | **237.9 ms** |
| `ensure_awake()`（2 条 adb 子进程） | **342.9 ms** |

### 2.3 勘误（必须记录）

`gaps-and-roadmap.md:50` 写的「**UI dump 116 次 × 2.3s ≈ 267s（83%）**」**是错的**。
实测 dump = **69.8 ms/次 → 8.1s（2.6%）**，差 33 倍。

**根因**：把"相邻两次 dump 的时间戳之差"（均值 2.55s）当成了"单次 dump 的耗时"。间隔里装的是 sleep / 截图 / tap 延时。

**连锁影响**：该数字被 v4.11 继承为设计依据（`case_metrics` 选 `dump_snaps` 当列）——**见 §五 修订 R3**。

---

## 三、五条能力 → 差距 → 动作

> 每条能力下列出**差距 → 动作 → 成本 → 审批 → 验收**。动作编号 `A<能力>.<序号>`，§四 按编号排批次。

### 能力 1：结论可信（不假 PASS，也不假 INFO）

**差距**——「运行 case 时用 OCR/视觉做真实检查」其实是 4 个独立要求，**v4.11 只做了第 1 条**：

| # | 要求 | 现状 | v4.11 |
|---|---|---|---|
| D1 | 走真调用、不读缓存 | `cached_ocr`/`cached_dump` 无守卫，但 `cases/` 里 **0 处**使用；**视觉链无缓存层**（`vision_provider.py` 只有实时 `ask`/`ask_json`） | ✅ 主线 A |
| D2 | **参数必须是当次派生的** | 无 lint、无守卫；`175.py:64 DLG_TOP, DLG_BOTTOM = 1350, 1870` 喂给 `t.ocr()` | ❌ 无（落进主线 C，**C 无承接阶段**） |
| D3 | **视觉给的位置是假设，落点前要与 dump 核对** | 只有 `tap_vision(verify=)`——那是**点之后**的可选验证且默认关闭 | ❌ 无 |
| D4 | **失败要可见，不能静默降级** | `175.py:92` 换设备后退化成空 list → 只记 INFO，不 FAIL | ❌ 无 |

**关键区分：`不读缓存 ≠ 真实检查`。**

| 失败模式 | 后果 | **可见性** | 现状 |
|---|---|---|---|
| 读探索期缓存 | 拿**旧设备**数据 → 可能假 PASS | 中 | ✅ v4.11 拦了 |
| **参数写死** | 拿**当次但错误区域**数据 → 静默读空 | **低** | ❌ 无 |
| **视觉不核对** | 模型幻觉坐标 → 点到错地方 | **低** | ❌ 无 |

**v4.11 花 0.5 天防了可见性最高的，花 0 天防了两个可见性最低的。**

**动作**

| 编号 | 动作 | 成本 | 层级 / 审批 | 验收 |
|---|---|---|---|---|
| **A1.1** | `ocr()` 在**指定区域读到 0 条结果**时 `record("WARN", ...)` | **S（~5 行）** | `framework/` ✅需 | 构造"区域写错"的用例 → 报告出现 WARN 而非静默 |
| **A1.2** | `ocr()` 增加 `rid=` / `bounds=` 参数（传 rid 自动取 bounds 再裁剪） | S | `framework/` ✅需 | `t.ocr(rid=...)` 可用；`docs/case-writing.md` 同步 |
| **A1.3** | `evals/lint_case.py` 补规则：`ocr`/`capture_toast`/`vision_ask(bounds=)`/`tap_vision(bounds=)` 的**字面量参数**；**并加模块级常量追踪**（`DLG_TOP = 1350` 也要抓） | S-M | `evals/` ⚠️**灰区** | `lint_case.py cases/ --baseline` 能报出 `175.py:64` |
| **A1.4** | `cached_ocr`/`cached_dump` 执行期守卫（record FAIL + raise，`probe_page` 免检） | S | `framework/` ✅需 | 单测 4 条（v4.11 阶段 1 原案） |
| **A1.5** | `tap_vision` 支持"**落点前**与当前 dump 的 bounds 核对" | M | `framework/` ✅需 | 构造幻觉坐标 → 落点被拒并记 WARN |

> **A1.3 为什么要"常量追踪"**：规则现只匹配 `tap_xy`（`lint_case.py:74`），且判据是 `_all_args_literal`（`:127`）——**参数全是 `ast.Constant` 才违规**。`t.ocr(DLG_TOP, DLG_BOTTOM)` 的参数是 `ast.Name` → **合法放行**。只扩规则名不做常量追踪，**照样抓不到 175**。
>
> **实测证据**：`python evals/lint_case.py cases/ --baseline` → `175.py` 显示 **✅ 通过**。

### 能力 2：跨设备成立

**差距**

| 缺口 | 证据 |
|---|---|
| 框架**零标定能力** | `framework/` grep `calibrat\|标定\|聚类` 仅 1 处无关注释；`179.calibrate_picker` 仍是用例私有 |
| 代码里 3 处活体违规 | `175.py:64`（①绝对常量）、`179.py:281 b[0]+61`（②固定偏移）、`183.py:203,234`（③文档污染 "411~1646，屏幕高 1904"） |
| 知识卡 10 处机型标注 | `knowledge/com.zui.calendar.md` 内 `TB323FU` **10 次**（`gaps-and-roadmap` 阶段三验收标准是 **0**） |
| 方向语义错位 → **2026-09-14 勘误**（见 `plan/orientation-errata.md`） | ~~`wm size`=1904×3040 但 `mBounds`=3040×1904~~ —— 这是**自然尺寸 vs 当前尺寸**，转了 90° 本就该不同，**不是缺陷**；~~`user_rotation=0` → ROTATION_90~~ —— 那次在 `accel=1` 下测，`user_rotation` **被系统忽略**，测到的是物理姿态；`lock_portrait()` 实测锁出**竖屏**（`mRotation=0`/`cur=1904x3040`）**语义正确**。**真问题：锁不持久** —— App 冷启动把 `accelerometer_rotation` 改回 **1**（实测 0→1），178 用 `env_ignore` 静音了它 → **已由 A2.6 解** |
| **M4 真验收**（表述修正） | 178 **实际是横屏跑完的**（报告证据 `02_点击坐标_2835_203.png`，x=2835 > 竖屏宽 1904），并未被"锁死"；`178.py:176-179` 的"横屏"注释与现场**一致**。M4 的"换方向重跑 178"保留，并升级为**方向无关性的通用验收**（横/竖屏各跑一次，断言结果一致） |

**动作**

| 编号 | 动作 | 成本 | 层级 / 审批 | 验收 |
|---|---|---|---|---|
| **A2.1** | `knowledge/_template.md`：沉淀**三问→四问**（加第 0 问"换设备/换方向还成立吗"）＋**删「行尾打机型戳 `[机型/版本]`」教学** | S | `knowledge/` ❌免 | 模板不再教人写设备戳 |
| **A2.2** | 3 张卡头部删设备字段（calendar / launcher / settings） | S | `knowledge/` ❌免 | `[TB323FU` 在头部出现 0 次 |
| **A2.3** | 把 `179.calibrate_picker()` 提到 `_flow.py`；`175.py:64` 改为现场标定；`179.py:281` 升级为从 bounds 实时算 | M | `cases/` ❌免 | 175/179 真机重跑 PASS 不变 |
| **A2.4** | **换方向重跑 178**（横/竖屏各一次）。原表述"解 `178.py` 的 `lock_portrait()` 与注释矛盾"的前提已被实测推翻 —— 178 本来就跑在横屏，注释与现场一致 | S | `cases/` ❌免 | 两个方向下断言结果一致 |
| ~~**A2.5**~~ | ~~`lock_portrait()` 方向语义方案选定（A 按自然方向判断 / B 冷启动后重新锁定 / C 现状+各用例 env_ignore）~~ **已撤销（2026-09-14）**：三方案都在解一个**不存在**的问题 —— 实测 `lock_portrait()`（`accel=0`+`user=0`）锁出的就是竖屏（`mRotation=0` / `cur=1904x3040`），**语义没错**。真问题是「锁不持久」，由 **A2.6** 承接。详见 `plan/orientation-errata.md` | — | — | — |
| **A2.6** | **锁定持久性 + 方向变化可见**：`case_metrics` 记 `rotation_start`/`rotation_end`（真实 `mRotation`）；`finish()` 检测"起止方向不同" → WARN，且**不受 `env_ignore` 约束**（178 的静音路径被封） | S | `framework/` ✅需 | **✅ 已完成（2026-09-14）**：真实库已迁移；`health_check()['rotation_changed']` 可查；单测 212 → **237**（本次共 +25：方向 13 + BOM 2 + lint 规则 10） |

### 能力 3：该问才问

**差距**

- **「协作准则」从未写进 `SKILL.md`**：grep 全仓，"协作准则"只命中 `gaps-and-roadmap.md` 自身。**178 的 22 分钟浪费的直接原因就是缺这条**，且 `gaps-and-roadmap` 自己定性为"应最先做"。
- Ask 机制未实现。**但验收对象错了**：v4.11 要求"在 178 或 179 上连跑 2 轮 → 触发器真的响"，而实测两者都**全 PASS（31/0、8/0）** → **永不触发**。库里唯一 FAIL 的是 **`177`（13 通过 / 1 失败）**。

**动作**

| 编号 | 动作 | 成本 | 层级 / 审批 | 验收 |
|---|---|---|---|---|
| **A3.1** | `SKILL.md` 加「协作准则」（"结论已确定先汇报" / "疑似框架问题应上报" / "改法≥2 种列选项问人" / "连续 2 次尝试未收敛立即停下"） | **S（30min）** | `SKILL.md` ✅**需** | 下一次实战中，AI 在结论确定后主动停下汇报 |
| **A3.2** | Ask 步骤级触发器 + 答案复用（= v4.11 阶段 2 原案） | **L（2d）** | `framework/` ✅需 | 验收对象**改为 177**；单测模拟 2 轮 FAIL |

### 能力 4：知识可复用

**差距**

| 缺口 | 现状 |
|---|---|
| 三段式无通道（探索→卡→执行） | ❌ 仍存在 |
| 无覆盖度/地图 | ⚠️ `traces/` 已启用 + 新增 `cases/_lib/inventory.py`（页面库存 + 离线预检）；但**无"边/图"、无覆盖度**，且 `probes/` 被 30 分钟规则清空 → 本次 `inventory.py` **无可读输入** |
| 路径不成体系 | ❌ `goto_*` 无前置检查、无 `[从]/[到]/[成本]` 元信息；`skip_if_ready` 仍只挂在 `goto_图库导入_基本信息确认页` 一个函数上 |
| **无续跑能力** | ❌ `goto_课程表空状态(t, pm_clear=True)` 无条件 `restart_calendar(pm_clear=True)` → **每个用例每次跑都付冷启动**（**2026-09-14 实测：完整前置 35.7s，跳过 0.6s** —— 原引用的"约 100s"偏大 3 倍） |

**动作**

| 编号 | 动作 | 成本 | 层级 / 审批 | 验收 |
|---|---|---|---|---|
| **A4.1** | `goto_*` 加**前置状态检查** | M | `cases/_flow.py` ❌免 | ✅ **已实现**（2026-09-14）：`skip_if_ready` 参数 + `_on_课程表空状态` / `_on_手动创建页` / `_on_确认页` 三个检查函数；**默认 `False`**（保持既有语义，10 个调用点零变化）。**实测：完整前置 35.7s → 跳过 0.6s，省 35.1s**（验收线 <5s ✅；报告 `storage/reports/_verify_skip_ready_报告.md`）<br>⚠️ **触发窗口 = 设备恰好停在该 `goto_*` 的目标页**。172/176 跑完停在别的页 → 加了也不会触发（`_on_确认页` 判据是页面后置条件，但用例已走过该页）→ **未加到任何用例上**；178 会触发（停在编辑页）但**必须保持 `False`**（断言默认值）。<br>📌 **待提案**：把它做成环境变量开关（`DSH_SKIP_IF_READY=1`）供调试循环用，而非逐用例改代码 |
| **A4.2** | 2-3 个带机器可读元信息（`[从]/[到]/[成本]/[前提]`）的样板方法 | M | `cases/_flow.py` ❌免 | AI 能据此选最优边 |
| **A4.3** | 探索地图（可查询的"页面 × 边"图 + 覆盖度查询） | **L** | 独立立项 | 能列出"已探明页 / 未探明页" |

### 能力 5：成本可知

**差距**

- v4.11 `case_metrics` 三列（`dump_snaps`/`ocr_calls`/`derived_clicks`）**无耗时列**。实测对照：

| 列 | 服务 | 178 实测 | 判定 |
|---|---|---|---|
| `derived_clicks` | 主线 C（坐标派生比例） | ~58 | ✅ **合规指标，保留** |
| `ocr_calls` | 主线 A（"执行期真跑 OCR"） | **0** | ✅ **合规指标，保留** |
| `dump_snaps` | 无主线；被自称"我的改动有没有减少 dump" | 116 → **8.1s（2.6%）** | ❌ **指不到时间** |
| **（无 duration）** | — | **311.5s** | ❌ 改前 vs 改后无法比较 |

- **基线已损失一次**：178 的 09-11 记录被本次复核运行的 `drop_previous_cases`（`db.py:601`，`remove_artifacts=True`）连同报告与截图删除；唯一备份（09-11 12:40）早于 178 实战（~17:05），救不回来。

**动作**

| 编号 | 动作 | 成本 | 层级 / 审批 | 验收 |
|---|---|---|---|---|
| **A5.1** | `case_metrics` 增加一列 **`duration_sec REAL`** | **S（1 行 + 1 列）** | `framework/` ✅需 | 跑 2 次同用例 → 趋势可查、可比 |
| **A5.2** | `dump_snaps` 保留为"dump 次数"记录，**但不再作为性能指标解读** | — | 文档 | §五 R3 同步更正 |
| **A5.3** | 配置**性能优化开关**（见 A5.4-A5.6，全部需审批） | — | — | 见下 |
| A5.4 | `ACTION_DELAY` 1.0 → 0.3s（37 次 observe 动作） | S | `framework/` ✅需 | ~26s |
| A5.5 | `record()` 改为仅 FAIL/WARN 截图（41 张） | S | `framework/` ✅需 | ~21s；**与 SKILL 原则 2「必须截图留证」冲突，需先改文档原则** |
| A5.6 | `ensure_awake` throttle 3 → 15s | S | `framework/` ✅需 | ~15-28s |
| A5.7 | `_flow.py` / 用例内固定 `sleep` → 轮询；`_scroll_top` 5 滑 → 到顶即停 | M | `cases/` ❌**免** | ~42s |

---

## 四、执行顺序（四批）

> **排序原则**：① 先做对准**已发生事故**的；② 先做**执行层（免审批）**的；③ 每步可独立验收、可独立叫停；④ **押后**对着未观测问题的、以及依赖真机多轮验证的

### P0 —— 今天，2 小时，零风险

| # | 动作 | 编号 | 审批 |
|---|---|---|---|
| 1 | **修 CI**：`183.py:257` 的 `time.sleep(4)` 补注释 → `lint_case.py` 退出码 0 | — | ❌ 免（`cases/`） |
| 2 | `_template.md` 沉淀四问 + 删机型戳教学 | A2.1 | ❌ 免 |
| 3 | 3 张卡头部删设备字段 | A2.2 | ❌ 免 |
| 4 | `SKILL.md` 加「协作准则」 | A3.1 | ✅ **需** |

> **为什么是这 4 件**：它们**全部对准已发生的事故**（178 的 22 分钟 / 卡片污染 / CI 红），成本合计 2 小时，其中 3 件免审批。`gaps-and-roadmap` 3 天前就定性「阶段〇应最先做」，一直没做。

> **顺手发现**：`evals/lint_case.py cases/ --baseline` 在**已提交代码**上退出码 **1**（`183.py:257 [bare_sleep]`），而 CI 挂的就是这条命令（`.github/workflows/test.yml:18`）→ **CI 目前是红的**。地基红了，后面所有门禁都会被无视。

### P1 —— 本周，2-3 天

| # | 动作 | 编号 | 审批 | 收益 |
|---|---|---|---|---|
| 5 | `ocr()` 指定区域读空 → WARN | **A1.1** | ✅需 | **把"静默降级"变可见**（~5 行，性价比最高） |
| 6 | `case_metrics` 加 `duration_sec` | **A5.1** | ✅需 | 让后续每次改动**可验证** |
| 7 | `goto_*` 前置状态检查 | **A4.1** | — | ✅ **已完成**：机制就位 + 实测 35.7s → 0.6s；默认 False，未加到用例（见 A4.1 说明） |
| 8 | `lint_case.py` 补 OCR/视觉规则 + 常量追踪 | **A1.3** | ⚠️灰区 | 抓 D2 |
| 9 | `cached_*` 执行期守卫 | **A1.4** | ✅需 | 防假 PASS（防未然） |

### P2 —— 2 周内

| # | 动作 | 编号 | 审批 | 收益 |
|---|---|---|---|---|
| 10 | `ocr()` 加 `rid=`/`bounds=`（API 引导） | A1.2 | ✅需 | 让"派生"比"写死"省事 |
| 11 | 标定落 `_flow.py` + 清 `175.py:64` / `179.py:281` | A2.3 | ❌免 | 清 ①② |
| 12 | **换方向重跑 178** | A2.4 | ❌免 | **能力 2 的唯一真验收** |
| 13 | `tap_vision` 落点前核对 | A1.5 | ✅需 | 防幻觉坐标 |
| 14 | `_flow.py`/用例 sleep 轮询化 + `_scroll_top` | A5.7 | ❌免 | ~42s |

### P3 —— 押后（等条件）

| 动作 | 编号 | 等什么 |
|---|---|---|
| Ask 触发器（2d） | A3.2 | 等"同一步骤连续 2 轮 FAIL"**真被观测到**；真要做，验收改指 **177** |
| 探索地图 | A4.3 | 等 A4.1/A4.2 把"边"的表示形式定下来 |
| 方向语义方案 | A2.5 | 等人在 A/B/C 里选定 |
| `ACTION_DELAY` / 截图策略 / `ensure_awake` | A5.4-A5.6 | 等 A5.1 的耗时基线建立后，才有依据谈取舍 |
| 路径元信息 | A4.2 | 等 A4.1 有 2-3 个样板跑通 |

---

## 五、对 v4.11 的四处修订（供追溯）

| # | v4.11 原状 | 修订 | 理由 |
|---|---|---|---|
| **R1** | 主线 C（判据不过时）**无承接阶段**；§六 第一步写「**阶段 1 完成** → 把 `calibrate_picker` 提到 `_flow.py`」，而"阶段 1"在 v4.11 里 = 缓存零容忍，**与标定无关**；真正做标定的是 `gaps-and-roadmap` 阶段二，**v4.11 没把它列进阶段表** | 主线 C 归入**能力 2**，动作 A2.3 / A2.4 给它承接阶段；§六 的依赖链更正为 A2.3 | 否则 §六 永远无法启动；主线 C 只剩"清卡"，而实测的 C 类违规**在代码里（`175.py:64`）不在卡里** |
| **R2** | 「协作准则写进 SKILL.md」（30min，直击 178 的 22min）**被完全删除**；「前置状态检查」（省 100s/次）**被完全删除** | 回填为 **A3.1** 与 **A4.1** | 成本高的（Ask 2d）留下、成本低的删掉——而删掉的这两项才对已发生的事故 |
| **R3** | `case_metrics` 三列含 `dump_snaps`，自称"回答'我的改动有没有减少 dump'" | **加 `duration_sec`**（A5.1）；`dump_snaps` 降级为普通记录（A5.2） | dump 实测占 **2.6%**，原始依据错 33 倍（§2.3）；无耗时列则**改前 vs 改后无法比较** |
| **R4** | M2 验收「在真实用例 **178 或 179** 上连跑 2 轮 → 触发器真的响」 | 改指 **177** | 178 全 PASS（31/0）、179 全 PASS（8/0）→ **永不触发**；库里唯一 FAIL 是 177 |

**保留不动（v4.11 站得住的部分）**：
- 2/8 边界的定位（`§一`）——干净的**正确性坐标系**
- 从 v3.x 砍到 v4.0 的简化（9 阶段/8 触发器/6 指标/11 天 → 3 阶段/1 触发器/3.5 天）——实测支持
- **主线 A 的理由是"假 PASS"（正确性），不是性能**——理由站得住
- `derived_clicks` / `ocr_calls` 两列——真合规指标

---

## 六、明确不做（防范围蔓延）

| 不做 | 原因 |
|---|---|
| ❌ 把 v4.11 推倒重写 | 它不是"方案错"，是"坐标系错位"；§五 四处修订即可 |
| ❌ 现在做 Ask（2d） | 对准一个**未被观测到**的失败模式；真要做，验收改指 177 |
| ❌ 现在做探索地图（L） | 等"边"的表示形式（A4.1/A4.2）定下来再立项 |
| ❌ 用 `dump_snaps` 衡量性能 | 实测占 2.6%；性能只看 `duration_sec` |
| ❌ 靠 `env_ignore` 掩盖方向语义 | `gaps-and-roadmap` 明确不建议（会破坏"中途意外转屏可捕获"）；178 现在正是这么做的 → A2.5 要解来源 |
| ❌ 动 `record()` 截图策略前不改 SKILL 原则 2 | 两者直接冲突，先改文档再改代码 |
| ❌ 改 `gaps-and-roadmap.md` / `roadmap-to-A+.md` | 前者是原始复盘（保持原样），后者已完成 |

---

## 七、里程碑

| 里程碑 | 验收 | 依赖 |
|---|---|---|
| **M0**（今天） | CI 绿；`_template.md` 含沉淀四问且无机型戳教学；3 张卡头部无设备字段；`SKILL.md` 含「协作准则」 | — |
| **M1**（本周） | `ocr()` 区域读空记 WARN；`case_metrics.duration_sec` 可查趋势；`goto_*` 前置检查使"已在目标页"耗时 <5s；`lint_case.py` 能报出 `175.py:64`；`cached_*` 执行期守卫单测 4 条通过 | M0 |
| **M2**（2 周内） | `t.ocr(rid=)` 可用；175/179 改现场标定后真机 PASS 不变；**换方向重跑 178 断言不变**；`tap_vision` 幻觉坐标被拒 | M1 |
| **M3** | 178 墙钟相比 311.5s 下降（用 `duration_sec` 证明）；探索地图可列"已探明/未探明" | M2 + A5.4-A5.7 审批通过 |

---

## 八、复核方法（可复现）

```bash
# 1) 带 trace 跑用例（正式用例默认不开 trace）
DSH_TRACE=1 python framework/run_case.py <工作区>/cases/com.zui.calendar/178.py
#    末尾打印「📊 UI dump 次数」与「耗时 Xs」

# 2) dump 时间线：storage/traces/<用例名>/<会话>/index.json
#    {seq, ts, src, bytes}；相邻 ts 之差 = 间隔（≠ dump 耗时，见 §2.3）

# 3) 动作耗时：storage/test_records.db → step_actions.duration_ms

# 4) 静态 lint（CI 同款命令）
python evals/lint_case.py cases/ --baseline

# 5) 旋屏语义一次性取证
#    ⚠️ 本块的**推断**已被 plan/orientation-errata.md 勘误：`wm size` 是自然尺寸（非当前）、
#    `user_rotation=0` 在 accel=1 时被忽略 —— 命令仍可跑，但别照本块的结论下判断
adb shell wm size
adb shell settings get system accelerometer_rotation
adb shell settings get system user_rotation
adb shell dumpsys window displays | Select-String -Pattern 'mBounds=[^}]*'
```

---

## 九、变更控制

- 本文档是"做什么"的共识，**不构成变更授权**。
- **改动流程**：AI 提提案（原文 vs 修改后）→ 人 review → 合入。
- **未经人确认前**：不实施任何 `framework/` / `SKILL.md` / `docs/` 改动。
- **自由迭代**（已确认）：`cases/<包名>/` 用例与 `_flow.py`、`knowledge/*.md`。
- **灰区**：`evals/` 未列入管控范围（管控 = `framework/` + `SKILL.md` + `docs/case-writing.md` / `explore-guide.md`）→ 按 SKILL「灰区归属有争议时停下来问人」，**A1.3 需先确认**。
- **每次迭代**：更新 `version` 与 changelog。

```
# Smart Skill Roadmap
version: 5.0
updated: 2026-09-14
owner: AI + 人（共同维护）
changelog:
  - v5.1 (2026-09-14): **方向勘误**（3 处立论被实测推翻 → **A2.5 撤销**，新增 A2.6 并已实施）；
          真问题重定义为「锁不持久 + 方向变化不可见」；A2.4 前提修正（178 本就跑在横屏）；
          **P1-8 走 B**：lint 规则 5/6/7 生效（pixel_literal / pixel_const / pixel_offset）+ `# noqa` 留痕出口；
          顺带修 `lint_case.py` 跨盘符崩溃；单测 212 → 237。勘误与原始证据见 `plan/orientation-errata.md`
  - v5.0: 合并 smart-skill-roadmap.md(v4.11) + gaps-recheck.md；
          目标重定义为 5 条可验收能力（结论可信 / 跨设备成立 / 该问才问 / 知识可复用 / 成本可知）；
          新增「真实检查」D1-D4 框架（v4.11 只做了 D1）；
          四处修订 R1-R4（主线 C 补承接 / 回填协作准则+前置检查 / case_metrics 加 duration_sec / M2 验收改指 177）；
          实测基线入档（178 = 311.5s，dump 占比勘误 83% → 2.6%）；执行顺序改为四批 P0-P3。
  - 依据: gaps-and-roadmap.md(2026-09-11 原始复盘，保持原样) / roadmap-to-A+.md(2026-09-09，已完成)
```

---

## 附录 A：派生程度阶梯（坐标设备无关性分级）

> 来源：原 `smart-skill-roadmap.md` §六。用于判定与消除 A2.3 要处理的违规。

| 级 | 描述 | 例子 | 处置 |
|---|---|---|---|
| **①** | 绝对像素常量 | `175.py:64 DLG_TOP, DLG_BOTTOM = 1350, 1870` | **立即消除** |
| **②** | 固定偏移 | `179.py:281 b[0] + 61, b[1] + 31` | 升级为 ④ 或写明来源 |
| **③** | 固定比例 | `int(h * 0.30)` | 可接受，写明基准 |
| **④** | 实时派生 | `_flow.py:638 cy_line = (panel[1] + panel[3]) // 2` | ✅ 正确 |
| **⑤** | 现场测量 / 聚类 | `179.py:49-82 calibrate_picker()` | ✅ 最佳 |

**铁律**：标定结果**只在本次运行内有效**，禁止写进知识卡——写进去 = 换了个名字的 cache。
知识卡只存**标定方法**（"用 `calibrate_picker()` 取边界"），不存**标定结果**（"边界是 1350,1870"）。

---

## 附录 B：实施要点（原 v4.11 阶段 1/2/3 的硬契约，勿丢）

### A1.4 缓存守卫 —— 三个非显然的约束

1. 异常类**必须继承 `CaseAbort`**，不能是 `RuntimeError`：后者走 `is_abort=False` → `_fatal_error` → 结论压成 **ERROR**、退出码 **3**；前者是 **FAIL(1)**、报告正常出。
2. **`raise` 之前必须先 `record("FAIL", ...)`**：只继承不 record 的话，`finish()` 会按"目前所有断言都 PASS"算出 **PASS**，而退出码是 **1** —— 正是 `run_case.py:346-349` 注释里警告过的「报告/退出码打架」。
3. `probe_page` **免检**：它是"给正式用例里临时探一下的兜底"，需 `_in_probe_page` 置位；否则它内部调 `cached_dump` 会自杀。

```python
class ExecutionTimeCacheError(CaseAbort):
    """执行期误用缓存 API —— 必须 raise 阻断，否则拿过期数据当结论 = 假 PASS。"""

def cached_ocr(self, ...):
    if _should_record(self.user_input, self.name) and not getattr(self, "_in_probe_page", False):
        self.record("FAIL", "执行期误用缓存: cached_ocr() 拿过期数据当结论")   # 先记
        raise ExecutionTimeCacheError("禁止在执行期间使用缓存 OCR")             # 再抛

def probe_page(self, ...):
    self._in_probe_page = True
    try:
        return self.cached_dump(...)
    finally:
        self._in_probe_page = False
```

**计数器注入点**（`_derived_clicks` 曾在 v4.2 漏计）：至少 `_tap_unified`（最常用路径）/ `_Located.click` / `tap_text_re`；注意 `tap_vision` 内部会调 `tap_xy`，**别双计**。

### A3.2 Ask —— 关键契约

- **表**：`asks(key TEXT UNIQUE, case_name, answer, status /*pending|answered*/, last_result, last_seen_at)`
- **挂载点 = `step()` 末（步骤级）**，覆盖率 100%（所有 `record()` 都在某个 step 里）。
  **不是**挂在 `assert_*` 上——真实用例几乎不用：全仓 `t.assert_*` 仅 10 处（真实使用 **1 处**），而 `record("PASS" if ... else "FAIL")` 有 **246 处**。
- **开跑前扫描上一轮**（`_check_prev_run_steps`）才能覆盖"最后一步 FAIL"（179 Step4 场景）。
- **key** = `f"{用例名}::{step 名}"`；`__prev_run__` 行带**失败集合指纹**（MD5 前 8 位），集合变化才重新问；答案回填到各 step 的 key，避免"问两遍"。
- **报告/DB/退出码三处一致**：ask 触发后 `finish()` 里 `if self._ask_pending: self.final_status = "BLOCKED"`（与 `_fatal_error` 同款 override）——因为 **FAIL 优先级高于 BLOCKED**，只 `record("BLOCKED")` 没用。
- **`run_case.py` 出口**：`except AskRequired` → `tc.finish()` **包 try** → `sys.exit(2)`（finish 抛异常时也必须走到 exit 2，否则退出码变成 1）。
- **身份约定**：**用例名 + step 名是触发器身份**，改脚本时不要改它们（改名 → key 变 → 计数归零，静默失效）。
- **验收对象**：**177**（见 R4）。

### A5.1 度量 —— SQL 与用法

```sql
CREATE TABLE IF NOT EXISTS case_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_path TEXT NOT NULL,
    device TEXT,
    started_at TEXT NOT NULL,          -- ISO 字符串（与 cases 一致）
    duration_sec REAL,                 -- ← v5.0 新增
    ocr_calls INTEGER,
    derived_clicks INTEGER,
    dump_snaps INTEGER
);
CREATE INDEX IF NOT EXISTS idx_case_metrics_started ON case_metrics(started_at);
```

- **必须 append-only**：`cases` 表被 `drop_previous_cases` 删旧行 → **没有趋势**。
- 写入点在 `finish()`；`started_at` 必须写 `datetime.now().isoformat(timespec="seconds")`，**不能传 `time.time()` 的 float**（否则与 `cases.started_at` 字符串排序混排）。
- `duration_sec` 的源已存在：`test_framework.py:2290`
  `duration_sec = round(time.time() - self._case_start_time, 1)` —— **只是没入库**。
- `health_check()` 放 `framework/db.py`（与 `flaky_stats:350` / `card_freshness:377` 同层），**不是** `cases/_lib/`。
- `framework/ask.py` CLI（约 10 行）：`python ask.py answer <key> <choice>`。

---

## 附录 C：截图构成 + 证据索引

### 110 张截图的来源（全部 512ms/张，合计 56.3s）

| 来源 | 张数 | 成本 |
|---|---|---|
| `record()` 每条断言一张 | **41** | 21.0s |
| `tap()` 每次点击一张 | 34 | 17.4s |
| 用例手写 `screenshot()` | 26 | 13.3s |
| `step()` 每步开始一张 | 8 | 4.1s |
| `input()` | 1 | 0.5s |

→ **83 / 110（75%）是框架自动产生的证据截图**，其中 `record()` 的 41 张价值最低（截的是"断言执行时的屏幕"，而断言是 `'50分钟' == '50分钟'` 这类文本比较）。

**这是 A5.5 的量化依据，但它与 `SKILL.md` 核心原则 2「每步操作与验证点必须截图留证」直接冲突 → 必须先改文档原则，再改代码。**

### 证据位置

| 内容 | 路径 |
|---|---|
| 178 报告（2026-09-14） | `storage/reports/联想日历_178_报告.md` |
| 178 运行日志 | `storage/logs/trace178.out.log` |
| 178 trace 会话（116 份 dump + index.json） | `storage/traces/联想日历_178/20260914_143541_693/` |
| 178 截图（110 张） | `storage/screenshots/case_20260914_143541/` |
| DB 备份（09-11 12:40，含 13 条脏记录） | `storage/test_records.db.bak_20260911_130218` |

> **已被销毁的证据**：178 的 2026-09-11 执行记录 + 报告 + 截图 —— 被 2026-09-14 复核运行的
> `drop_previous_cases`（`db.py:601`，`remove_artifacts=True`）连带删除，备份早于该次实战，无法恢复。
