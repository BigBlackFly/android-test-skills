---
name: android-test-skills
description: Android 设备黑盒 GUI 功能测试。用户给测试用例（步骤+预期），按本 skill 驱动已连接设备执行并输出带证据的测试报告。内置 uiautomator2 框架、Canvas OCR 读取、点按滚轮控制器、置灰像素断言、视觉定位（SoM 网格/归一化坐标双策略）、知识卡（按前台包名检索 App 操作经验）、PASS/FAIL/BLOCKED/ERROR 结果分类。
---

# Android 设备 GUI 测试（黑盒）

以"真人测试员"的方式在已连接的 Android 设备上执行用户给出的功能测试用例，输出结构化 Markdown 测试报告。

## 环境（先检查，缺啥跑安装脚本）

```bash
adb devices -l                      # 设备在线且已授权
# skill 包（只读）vs 工作区（可写）:
#   skill包/framework/*.py      测试框架与执行器（升级 skill 包即更新，不改工作区）
#   skill包/scripts/*.sh|ps1    安装/执行/运维脚本
#   工作区/cases/<包名>/*.py    用例（setup 时从 skill 包复制，之后编辑只改工作区副本）
#   工作区/knowledge/*.md       知识卡（同上，按前台包名检索加载 App 操作经验）
#   工作区/storage/             运行产物（截图/报告/探查缓存/测试库）
```

### 安装与运行

- 安装：`bash scripts/setup.sh`（Windows：`pwsh -File scripts/setup.ps1`）；依赖 `uiautomator2` + `rapidocr_onnxruntime`，
  Python 3.10+（`str | None` 语法要求）。默认工作区 `~/android-test-skills-data`
  （setup 会把 `cases/` 与 `knowledge/` 复制进工作区，**之后编辑一律改工作区副本**）
- 跑用例：`<工作区>/.venv/bin/python framework/run_case.py com.zui.calendar/172.py`
  （在 skill 包根目录执行；Windows：`pwsh -File scripts/run_case.ps1 -Case "com.zui.calendar/172.py"`）
- 单测（改 framework/ 后必跑，无需设备）：`python -m unittest discover -s tests -v`
  ——改的就是 skill 包里那份，不用再同步到工作区
- 安装细节/脚本参数/运维排障 → `docs/OPS.md`（人类快速开始 → README）

## 核心原则

1. **以用例（Case）为准**：用例的步骤与预期就是验收标准（spec）。执行后对比实际行为，**凡与 case 不符就是"不准"**——报告 FAIL 并说明差别（case 期望什么 / 实际是什么 / 差在哪），绝不反过来改 case 迁就实际行为。case 表述不清或客观上无法执行时才与用户确认。
2. **每步操作与验证点必须截图留证**：框架已自动为每个 `step()` 开始、`tap_*`/`input_*`/`clear_*` 等操作、`record()` 验证点截图。报告需附证据路径；状态类断言要同时记录实际状态值，不能只写 PASS/FAIL。
3. **元素优先定位（硬规则）**：resource-id > content-desc > text > 坐标。**App 自有控件已知 resource-id 时，禁止改用 content-desc/text 定位**——rid 跨版本稳定；desc 是开发者设置的无障碍语义标签，相对稳定；text 随版本/语言/灰度变化，仅限系统页/第三方页拿不到 rid/desc 的场景，或断言显示内容本身。坐标是最后手段（仅 Canvas/浮层等无元素场景），且必须从元素 bounds 推导（`el_bounds`），绝不写死。
4. **确定性优先**：UI 树能读到的用元素定位，绝不盲猜坐标；Canvas 盲区用 OCR；颜色/布局等像素与 UI 树都不可靠的场景用视觉模型（`vision_ask` / `assert_button_state_visual`）。
5. **每步验证**：操作后必须重读界面确认状态变化；断言用代码，不靠模型自述。
6. **如实分类**：与 case 不符=FAIL（说明差别）；环境/前置不满足=BLOCKED；case 与实际都成立但值得记录=WARN/INFO。
7. **默认允许数据清理**：用例以 `pm clear` 等重置 App 数据为前置时，视为测试流程的一部分，AI 不再逐次询问用户。执行前已确认测试设备上的 App 数据可被清理。
8. **信任脚本证据**：脚本执行成功、报告已生成、截图/日志已落盘，即为有效证据。AI 不另起一轮"独立复核"重复验证；若对结果有怀疑，应查看报告和截图，而非再跑一遍用例。
9. **动态事实必须有真机记录，禁止猜**：断言/定位引用的动态内容（toast 文案、弹窗文字、操作后的状态反馈）必须来自探索期的真机记录；记录里没有的，回探索补采触发一次，绝不凭用例文本或想象编造。用例文本描述的是"预期"，真机记录才是"事实"——断言以事实为准。
10. **目标资产必须可验证地选择，禁止盲点第一个**：从列表/网格中选"某个东西"（如图库里的课程表图）时，不能默认 dump 第一个匹配元素就是目标——共享状态（媒体库等）随时可能改变排序。正确姿势：视觉模型按**通用结构特征**排序候选（素材特征写进提示词反而是干扰）→ 逐候选预检 → 利用 App 自带失败信号（失败弹窗文案记入知识卡）做**双态等待**；确定性失败必须准确归因（如"未找到目标素材"），**禁止把确定性失败归因为超时/网络**。范式见 `docs/case-writing.md`「关键技术 → 图库/列表选资产」。

## 工作流

1. **解析用例**：从用户消息提取 前置条件/操作步骤/预期结果。缺信息先问，不猜。
2. **先查复用，再动手探查**（首次生成慢的主因就是从零试探，务必按此顺序；
   边界判定 / 落盘 / 何时停下找人 → `docs/explore-model.md`；完整四步 SOP → `docs/explore-guide.md`）：
   1. 查 `knowledge/<前台包名>.md`，但**别整卡读进上下文**（App 卡 400 行 ≈ 6.5k tokens）。
      先用**交互类型**关键词定位小节（滚轮/时间选择器/弹窗/长按/拖动/键盘…）：
      Windows `Select-String -Path <卡> -Pattern '<词>'`｜*nix `grep -n '<词>' <卡>`｜
      或直接用 Agent 自带搜索工具 → 再**只读命中那 1–3 段**（按行号读片段，别整卡读）。
      「标准链路」命中就直接照步骤写，不要重新探路。只搜页面名/入口词必漏
      —— 交互类型词才是命中键（179 事故 → `docs/lessons.md`）；
   2. 查 `cases/<前台包名>/_flow.py` 有没有现成流程函数 —— 有就 `from _flow import ...`，不要复制粘贴；
   3. 查 `storage/probes/<包名>/<label>/` 缓存（按关键词检索 `dump.xml`/`ocr.json`/`meta.json`，**工具同上**）—— 有就不连设备；
   4. 用 `t.probe_page(label)` 采集**本用例真正要断言的页面**，一次性拿全，不要"点一步看一步"。

   **⚠️ 第 1、2 步与第 3、4 步回答的是两个不同问题，不是"先后/兜底"关系：**

   | 步骤 | 回答 | 命中了会怎样 |
   |---|---|---|
   | 1、2（复用） | **怎么到达**目标页面 | 能跑起来，但**不知道到达后有什么** |
   | 3、4（采集） | 到达后**有什么可断言** | 断言有据可依，不靠猜 |

   **复用命中 ≠ 可以跳过采集。** 复用只回答"怎么到达"，采集才回答"到达后有什么
   可断言"；跳过采集就是把猜测当依据（183 代价：3 个 FAIL / 8 分钟白耗，事故与
   探索期探针脚本规模量化 → `docs/lessons.md`、`docs/explore-model.md`）。
3. **写用例脚本**（参考 `cases/com.zui.calendar/172.py`；API 速查 + 定位规范 + 关键技术 → `docs/case-writing.md`）：新建到 `cases/<被测包名>/` 目录下（目录名 = 包名，与知识卡/探查缓存同键），用框架 API 表达步骤与断言。**必须把用户原始口述写进脚本顶部的 `USER_INPUT = """..."""` 常量**（run_case.py 提取后随执行结果一起入库，实现 需求→脚本→结果 全链路追溯）。前置链路优先复用同目录 `_flow.py`，本文件只留断言逻辑。

   **用例与辅助脚本的命名/入库规则（2026-09-11 人确认）：**

   | 类型 | 命名 | 入库 |
   |---|---|---|
   | **正式用例** | `<用例号>.py`，**纯数字** | ✅ 入库（须有 `USER_INPUT`） |
   | **辅助脚本** | **`_` 开头** + 描述性后缀（`_seed_186.py` / `_explore_186.py` / `_verify_186.py` / `_collect_185.py`） | ❌ **不入库** |

   **入库判据是 `USER_INPUT`，不是名字**：只有"用户口述的正式用例"才带 `USER_INPUT`，探查/补采/备数据/补验/框架自测天然没有。命名是**约定**（可能被违反），`USER_INPUT` 是**事实**（不可绕过）——旧前缀判定（`PROBE_`/`RECON_`）曾被中文脚本名绕过，产生 13 条脏记录（`docs/lessons.md`）。两层一起用：`_` 前缀给人看 + 防误执行，`USER_INPUT` 给机器判。
   **漏写 `USER_INPUT` 的正式用例**（纯数字名）→ run_case.py 打印醒目警告且不入库，别让它静默丢记录。
   **同一用例重复执行只留最新一条**（DB 记录 + 旧报告/截图一并清理）：失败历史多为"改脚本改出来的中间态"而非产品缺陷，真缺陷由当次报告承载，堆叠只会污染记录库。
4. **执行**：`python run_case.py <用例>.py`
5. **出报告**：框架自动生成 `storage/reports/<用例>_报告.md`，把结论汇报给用户。
6. **沉淀**：新探明的链路/坑，回写 `knowledge/<包名>.md` 的对应小节——**更新既有小节，勿追加带日期/用例号的新小节**；写卡前过沉淀四问（换设备还成立吗/会搜到吗/防错吗/能下沉吗，约定见 `knowledge/_template.md` 头部注释）；大版本真机回归通过后**整卡重写**（GC：删过时条目、合并按会话堆出来的段落、刷新「最近验证」，卡体积收敛而非只增不减）。≥2 个用例共享的流程函数补进 `cases/<包名>/_flow.py`（一次性链路留在用例文件里，不提取）。

## 变更管控（硬规则，优先级高于本文件其他一切规则）

**所有对框架（`framework/`）与 SKILL.md 内部的修改，必须先经人确认，Agent 不得自行变更。**

- **无例外**：新增功能、修 bug、重构、参数调整、甚至一行的改动，一律先向人呈现「原文 vs 修改后」对比 + 改动理由，**明确确认后**才能落盘。禁止"顺手修了"、"趁跑用例间隙改了"。
- **禁止静默添加功能**：Agent 认为框架缺能力时，只能以**提案**形式提出（现状/问题/建议改法/影响面），由人决定做不做。即使 Agent 100% 确定是 bug，也只有"报告权"，没有"修复权"。
- **目的**：防止架构被随意演进导致 SKILL 前后行为不一致——框架的每一处变化都必须在人可知、可追溯的前提下发生。
- **变更记录**：每次被确认的框架/SKILL.md 修改，随执行在报告或对话中注明「改了什么、为什么、经谁确认」，保证事后可回溯。
- **同步约束**：修改同步到专家包副本属于既定流程的机械复制，不算新修改；但**只允许同步已被确认过的内容**，不得借同步夹带未确认改动。
- **管控范围**：需人确认的 = `framework/` 全部 + 本文件（SKILL.md）+ 子文档（`docs/case-writing.md` / `docs/explore-guide.md`）。**自由迭代**（2026-09-08 人已确认）= `cases/<包名>/` 用例与 `_flow.py`（执行层）、`knowledge/*.md` 知识卡（探查产出，写错会由用例失败自然暴露并修正）。灰区归属有争议时停下来问人，不自行推断。
- **改 `.ps1` 后必查 UTF-8 BOM**（硬要求）：PowerShell 5.1 读无 BOM 的 `.ps1` 会按 GBK 解析，中文/emoji 变乱码并直接语法崩溃。多数编辑/生成工具**重写文件时不保留 BOM**（git/复制不会丢，重写才会；事故记录 → `docs/lessons.md`）。因此每次修改 `scripts/*.ps1` 后必须跑：
  `pwsh -File scripts/check_bom.ps1`（有问题再 `-Fix` 自动补回）。
  **判定标准只有一条**：文件开头 3 字节必须是 `EF BB BF`——不要凭"我刚改的是 ASCII 部分"来判断，也**不要**为了躲这个要求去把脚本改成全英文（那会把易校验的不变量换成"永远不能有非 ASCII 字符"这种防不住的约束）。

## 协作准则（硬规则）

**不知道或无法决定的，问人，而不是自作主张。**

| 场景 | 正确做法 |
|---|---|
| 测试结论已确定（PASS/FAIL 不再变），剩余仅为环境噪音 | **先汇报**，由人决定是否处理，不自行连试多版 |
| 发现疑似框架层问题（非用例脚本问题） | **上报**，不自行修改框架 |
| 分层归属有争议（该进 `framework/` 还是 `_flow.py`） | **问人**，不自行推断 |
| 同一问题改法 ≥2 种且各有取舍 | **列选项问人**，不自行选定 |
| 要做超出当前任务范围的改进 | **先提案**，不「顺手做了」 |

**判据**：连续 2 次以上的「尝试性修改 + 全流程验证」未收敛 → **立即停下问人**。

## 分层边界（硬规则）

- **`framework/`（TestCase/states）**：与任何 App 无关的原子能力——定位点击、输入、OCR、视觉断言、弹窗看门狗、记录报告。**禁止出现**具体 App 的入口/控件/业务校验（如某 App 的菜单项文字、resource-id、页面特征）。测任何新 App 框架零改动。
- **`knowledge/<包名>.md`**：App 知识——入口在哪、按钮叫什么、resource-id、标准链路、坑。
- **`cases/<包名>/`**：该 App 的用例与 `_flow.py`（可复用流程函数：导航、前置链路；用例只写断言）。目录名就是包名——与 `knowledge/<包名>.md`、`storage/probes/<包名>/` 同键，一个键三处复用，Agent 拿到前台包名即可机械拼出全部路径。`_` 开头的文件/目录（`_flow.py`/`_set_time_tap.py`/`_template.py`/`_lib/`/`_seed_*.py`/`_explore_*.py`）是共享模块或辅助脚本、**不是用例**：`run_case` 收集时排除（不会被当用例执行）；Web UI 的 Case 页**会列出**它们（打「共享」标记、排在末尾、无删除按钮），因为它们是需要人工维护的资产，要能查看和编辑——**「在列表里展示」与「可作为用例执行」是两件事**，别因看到就以为能跑、能删。

**临时探查/备数据/补验脚本一律 `_` 开头**（如 `_explore_186.py`、`_seed_186.py`、`_verify_186.py`）：**不要**用中文描述名（`探查_186_xxx.py` / `备数据_186c.py`）—— 既不合 `_` 约定、又会绕过旧入库判据（事故 → `docs/lessons.md`）。写完用完即删。
- **`framework/states.py`**：只留跨 App 通用的状态判定；App/机型级状态写到 `knowledge/scenarios/*.md` 场景卡（写「判定命令」即自动注册，不改框架代码）。

### 已上提到框架的通用能力（别在 `_flow.py` 里重复实现）

这几条都是**跨 App 的平台级行为**，已验证通用，直接从框架调：

| 能力 | API | 为什么通用 | 踩坑来源 |
|---|---|---|---|
| 返回键 | `t.back(expect=None)` | **IME 弹起时第一次 BACK 只收输入法，不触发返回**；未弹起时多按会退过头。所以不能无脑按两次，必须先判断 | 183 连续 3 次 BLOCKED |
| IME 状态 | `t.ime_shown()` | 读 `dumpsys input_method` 的 `mInputShown` | 同上 |
| 视觉判词评分 | `t.verdict_score(ans, positive=[...])` | 只比"高/中/低"会**同级并列**→排序退化成原序→选错图。必须看确定度措辞 | 183 反复选错图 |
| 按视觉排序 | `t.rank_by_vision(nodes, prompt, positive=[...])` | 「从一堆缩略图里挑目标类型图片」任何 App 都有（相册导入/扫描识别） | 同上 |

`verdict_score` 的通用部分是**否定优先重罚 + 模糊措辞降权**；
目标类型专属特征词（如"星期表头"）必须由调用方经 `positive=` 传入，
**不许写进框架默认值**（分层边界硬规则）。

### 改动归属检查（每次改代码前先过这一问）

**主框架只做通用修改，禁止针对特定场景/App 定制**。写任何新能力前先问：这段代码离开日历（或当前被测 App）还成立吗？

- **成立（通用机制）** → 可进 `framework/`，但 App 特定的部分必须**参数化**，由调用方传入：
  - 提示词（视觉判定问什么）、成功/失败信号文本（弹窗文案）、resource-id、入口路径——一律作为参数，框架内不许出现任何具体 App 的文案/rid/包名常量。
  - 例：`vision_ask(prompt, bounds=...)`、`wait_text(text)` 是通用 API；"等『图片内容不是课程表』弹窗"不是——它只能出现在调用方代码里。
- **不成立（App 特有产经）** → 归宿只有两处：
  - **知识卡 `knowledge/<包名>.md`**：App 事实类——入口、控件 rid、弹窗/Toast 文案、页面特征、坑、解析时长等参数默认值；
  - **`cases/<包名>/_flow.py`**：App 流程类——导航链路、前置步骤、带 App 特定提示词的选图/预检/双态等待组合逻辑。
- **升级路径**：先在某个 App 的 `_flow.py` 里跑通；当第二个 App 需要同一机制时，把通用骨架上提到框架（App 特定部分变成它的参数），而不是复制粘贴到第二个 `_flow.py`。只有一份实现，App 差异全部走参数与知识卡。
- **自检方法**：在 `framework/*.py` 里检索你自己 App 的关键词（Windows `Select-String -Path framework\*.py -Pattern 'zui|课表'`；*nix `grep -n 'zui\|课表' framework/*.py`）应只命中注释/docstring 示例，命中逻辑代码即违规。

## 写用例 / 探索 → 子文档

- **写用例**（API 速查 / 定位规范与旋屏约定 / 关键技术 / 结果分类 / 注意事项）→ [`docs/case-writing.md`](docs/case-writing.md)
  - 工作流第 3 步「写用例脚本」时打开，含框架全部 API 签名 + 弹窗看门狗 / Canvas / 视觉定位 / Toast / 坐标标定等关键技术
- **探索 SOP**（新页面四步探查：真机采集 → 离线库存 → 逐句预检 → 真机终验）→ [`docs/explore-guide.md`](docs/explore-guide.md)
  - 工作流第 2 步「先查复用/探查」时打开
- **运维排障**（Web UI 排障 / 报告重建 / 包名回填 / SQLite 查询 / Windows 脚本）→ [`docs/OPS.md`](docs/OPS.md)
- **事故复盘**（某条规则为什么存在：179 / 183 / 13 条脏记录 / BOM / 数据源事故）→ [`docs/lessons.md`](docs/lessons.md)
  - 只在"想知道规则来历"时读；平时遵守 SKILL.md 的规则即可，不必读它

## 测试记录（SQLite）与 Web 前端

- 每次执行自动入库 `~/android-test-skills-data/storage/test_records.db`（用例/步骤/断言结果，含状态快照与证据路径）
- Web 测试台：`./scripts/webui.sh`（Windows：`pwsh -File scripts/webui.ps1 start`）→ http://127.0.0.1:8900
  - **测试记录**页：用例列表 → 详情含 用户输入/脚本/状态/证据
  - **知识库**页：编辑 `knowledge/*.md`（`_template.md` 只读；`_system.md` 禁删可编辑）
  - **视觉模型**页：配置凭据（未配置时视觉断言降级 WARN，见下）
- 记录查询命令行 / Web UI 排障 / 报告重建 / 包名回填 → `docs/OPS.md`

### 代码与数据分开放（skill 包 / 工作区）

**代码在 skill 包（只读），编辑目标在工作区** —— 两边各司其职：

| | 放哪 | 为什么 |
|---|---|---|
| **代码** `framework/` + `scripts/` + `docs/` + `SKILL.md` | skill 包（唯一，只读） | Agent 加载的就是这里；升级/重装 skill 包不碰用户数据 |
| **可编辑资产** `cases/` + `knowledge/` | 工作区（唯一编辑目标，setup 时从 skill 包复制） | 用户的用例与知识积累留在自己地盘，跨会话共享 |
| **数据** `storage/`（截图/报告/探查缓存/测试库）+ `.venv/` | 工作区（唯一） | 重装 skill 包不会被清空 |

**工作区副本是唯一编辑目标**：`run_case.py` 的搜索顺序是工作区在前、skill 包兜底
（未跑过 setup 时兜底命中 skill 包）。改用例/知识卡一律改工作区那份——
改 skill 包里的原始副本不会生效，**别改回去**
（首次复制与同步说明 → `docs/OPS.md`）。

### 视觉模型配置
入口：Web UI 左侧「视觉模型」页（或设 `DEEPSEEK_API_KEY` 环境变量）。
- **tap_strategy**：同页下拉框可选 `auto/som/coordinate`（auto 按模型名启发；som 通用多模态；coordinate 专用 GUI 模型）。配置保存位置/掩码/优先级等细节 → `docs/OPS.md`；密钥只存工作区，不进 skill 包。

## 知识卡（App 操作经验）

知识分两类，**先判断属于哪类，再决定放哪**：

| 类型 | 放哪 | 判断标准 |
|---|---|---|
| **可执行判断** | `framework/states.py` | 能用一条命令确定性地测出 True/False |
| **经验知识** | `knowledge/*.md` | 需要人传授的操作路径、坑、注意事项 |

**"怎么检测某个状态"一律写进 states.py，不要写成知识卡。**
写成知识卡 = 每次要进 AI 上下文（占 token）还可能被猜错；
写成代码 = 上下文成本为零、结果确定、能直接当断言用。

```python
t.states.is_vision_mode()                              # 是否处于无限工作台
t.assert_true(t.states.is_vision_mode(), "应处于无限工作台")
python framework/states.py --list                      # 列出全部可用检测
```

### 新增状态检测：只改一处

**在场景卡里写「判定命令」就会自动注册同名方法，不必再改 states.py。**

```markdown
# knowledge/scenarios/sys.勿扰模式.md
场景: 勿扰模式
触发词:
- 勿扰模式
判定方法: states.is_dnd_mode()          # ← 方法名由此推导
判定命令: settings get global zen_mode  # ← 自动注册 is_dnd_mode() / raw_dnd_mode()
判定说明: 0=关闭，非0=开启
```

保存后 `t.states.is_dnd_mode()` 直接可用，`--list` 里也会出现。
（`adb shell` 前缀可写可不写，两种写法都能解析。）

注：写「判定命令」前先确认这条命令**真的能反映状态**。
例如勿扰不能用 `settings put global zen_mode` 来切换（会被系统覆盖，恒为 0），
得用 `cmd notification set_dnd on|off`；但**读** `zen_mode` 是准的（0 / 2）。

只在**判定逻辑特殊**（要组合多个条件、要额外解释含义）时才手写方法：
在 `states.py` 用 `@state(...)` 装饰（带 desc / triggers / vendor），
手写方法优先级更高，不会被场景卡覆盖。
（自动注册的生成机制与容错细节见 `framework/states.py` 模块 docstring）

### 知识卡分两种

- **App 卡** `knowledge/<包名>.md`：检索索引/标准链路/页面控件/已知坑/验证要点
  （结构约定见 `knowledge/_template.md` 头部注释）
  - 测试前先查目标 App 的知识卡；用户口述的 App 知识要**记录进知识卡**
- **场景卡** `knowledge/scenarios/sys.<场景>.md`：跨 App 的操作场景（进入/退出/注意事项）
  - 头部键值区（场景/触发词/判定命令）是机器解析区，保持朴素「键: 值 / - 列表」写法
- 未知包名时用 `_system.md`（系统弹窗/权限，常驻，保持精简）

### 场景卡懒加载（重要）

`knowledge/scenarios/` 会越攒越多，**不要一次性读全部**（同 App 卡：按需读、只读命中的那张）。

场景卡自带 `触发词:` 字段 —— 按触发词检索 `knowledge/scenarios/`，命中哪张读哪张
（Windows `Select-String -Path 'knowledge\scenarios\*.md' -Pattern '<触发词>'`；
*nix `grep -rl '<触发词>' knowledge/scenarios/`）。未命中说明还没有该场景的知识（可新建）。
根因：全量读取会让上下文随场景数线性膨胀，而单次用例通常只涉及 1-2 个场景。

### 场景卡目录查找顺序

`states.py` 自动注册场景卡时按以下顺序找 `knowledge/scenarios/`，先命中先用
（同名卡以先扫到的为准，因此工作区的卡可覆盖 skill 包的同名卡）：

1. `DSH_SCENARIOS_DIR` —— 显式指定的场景卡目录（自测用）
2. `$DSH_WORKSPACE_DIR/knowledge/scenarios/` —— 测试工作区（本地改的卡先生效）
3. `$DSH_SKILL_DIR/knowledge/scenarios/` —— 环境变量指定的 skill 包位置
   （`run_case.ps1` / `webui.ps1` 启动时自动设置，也支持自定义安装位置）
4. `~/.agents/skills/android-test-skills/knowledge/scenarios/` —— 默认安装位置
5. states.py 自身所在包（即本 skill 包）的 `knowledge/scenarios/`

## 使用者如何添加知识/用例

使用者口述（"记一下：课程表入口是 主页→更多→课程表"、"跑这个用例：<步骤+预期>"）→
AI 结构化落盘：知识写入 `knowledge/<包名>.md` 对应小节（更新而非追加），
用例按 `cases/_template.py` 生成。技术用户可复制模板自行编辑
（`knowledge/_template.md` / `knowledge/scenarios/sys.无限工作台.md` / `cases/_template.py`，
详细指引 → README）。

**AI 侧的职责**：凡使用者口述了新的 App 知识/系统操作，主动写入对应知识卡/文档，让知识沉淀可复用。
