# android-test-skills

Android 设备黑盒 GUI 测试技能包：**你给测试用例，它驱动设备执行，输出带证据的测试报告**。

## 5 分钟快速开始

```bash
# 1. 装环境（自动建 venv、装依赖、初始化设备端、复制 cases/knowledge 到工作区）
bash scripts/setup.sh
# Windows: pwsh -File scripts/setup.ps1（脚本参数与细节见 docs/OPS.md）

# 2. 跑示例用例（工作区 cases/ 是单一数据源，setup 时从 skill 包复制）
#    run_case.py 在 framework/ 下，需先 cd 进去
cd framework && ../.venv/bin/python run_case.py com.zui.calendar/172.py
# → 报告自动生成到工作区 storage/reports/（Windows 用 pwsh -File scripts/run_case.ps1 -Case "com.zui.calendar/172.py"）

# 3. 写自己的用例（参考 cases/com.zui.calendar/ 示例）
#    新建 cases/<包名>/<编号>.py，用框架 API 表达步骤+断言，然后 run_case.py 执行
```

## 能力

| 能力 | 说明                                                                                                                   |
|---|----------------------------------------------------------------------------------------------------------------------|
| 元素操作 | 元素优先定位（resource-id/content-desc/text），坐标仅兜底                                                                          |
| 视觉定位 | tap_vision：SoM 网格 + 归一化坐标双策略，定位 view tree/OCR 无法找到的元素                                                                |
| 弹窗看门狗 | 主流程驱动检测权限/引导弹窗立即点击（词表快路径 + 视觉模型兜底未知弹窗；allow/deny 策略可切换）                                                              |
| 权限测试 | 相机/图库允许+拒绝路径（拆分隔离避免 USER_FIXED 级联）                                                                                   |
| 断言 | 文本/开关/长度上限/置灰（像素对比度）                                                                                                 |
| Canvas 读取 | RapidOCR 读自绘控件（滚轮/画布文字）                                                                                              |
| 滚轮操作 | 点按切换（零惯性，比滑动准）                                                                                                       |
| 测试环境准备 | Agent 根据用例判断应用并查表取得包名；框架依次初始化 sdcard 文件→ 清空 App 数据 → 授予 App 权限。未提供有效包名时跳过 App 数据清理和授权。见 [环境准备](docs/preparation.md)  |
| UIUX 知识 | 从 [知识库索引](uiux-knowledge/README.md) 按包名查找 App，先读相关页面 Markdown，再读取测试经验知识卡；布局细节按需补读配图，见 [使用说明](docs/uiux-knowledge.md) |
| 知识卡 | 按前台包名检索 App 操作经验（检索索引/标准链路/页面控件/已知坑）                                                                                 |
| 结果分类 | PASS / FAIL / WARN / BLOCKED / INFO / ERROR（异常路径也有报告）                                                                |
| 报告 | 自动生成 Markdown，含每步结果+截图证据                                                                                             |

## 目录结构

```
android-test-skills/
├── SKILL.md          # 给 AI 的玩法说明书（Agent 操作契约）
├── README.md         # 本文件（人类快速开始）
├── scripts/
│   ├── setup.sh / setup.ps1        # 一键环境安装（macOS/Linux / Windows）
│   ├── run_case.ps1 / webui.*      # Windows 执行器 / Web 测试台
│   ├── export.sh                   # 导出分发包
│   └── sync_skill.ps1              # skill 包 ↔ 工作区双向同步
├── framework/
│   ├── test_framework.py   # 测试框架（元素/OCR/视觉/看门狗/报告）
│   ├── run_case.py         # 用例执行器（退出码反映最终结论）
│   ├── preparation/        # 测试环境准备
│   │   ├── reset_sdcard_files.py    # 用户文件清理与测试图片、视频预置
│   │   ├── grant_app_permissions.py # 目标 App 批量授权
│   │   └── app_packages.py          # 应用名称与包名常量表，供 Agent 查询
│   ├── states.py           # 状态检测（场景卡自动注册）
│   ├── db.py               # SQLite 测试记录
│   ├── screenshot.py       # 公共截图管线（采集/裁剪/压缩/几何显式化）
│   ├── vision.py           # 视觉模型通道（ask/ask_json + temperature）
│   ├── vision_provider.py  # 视觉模型路由（用户配置 > Agent 注入）
│   ├── vision_tap.py       # 视觉定位（SoM 网格 + 归一化坐标 + 弹窗 bounds）
│   ├── ocr_screen.py       # Canvas OCR 辅助
│   └── webui.py/.html/.js/.css  # Web 测试台
├── docs/              # SKILL.md 拆出的子文档（按需读，不常驻 Agent 上下文）
│   ├── case-writing.md    # 写用例指南（API 速查/定位规范/关键技术/结果分类）
│   ├── preparation.md     # 文件准备、App 数据清理与批量授权
│   ├── explore-guide.md   # 探索 SOP（新页面四步探查）
│   └── OPS.md             # 运维排障（Web UI/SQLite/Windows）
├── media-resources/   # 少量图片/短视频，随 skill 分发，reset 时推送设备
├── uiux-knowledge/   # UIUX Markdown 索引与页面知识（apps/<包名>/，含局部图和来源）
├── knowledge/        # App 知识卡（Markdown，按包名）+ scenarios/ 场景卡
├── cases/            # 用例（按被测 App 包名分目录，如 cases/com.zui.calendar/172.py）
└── tests/            # framework 纯逻辑单测（无需设备）
```

## 架构：skill 包 vs 工作区

- **skill 包**（`~/.agents/skills/android-test-skills`，Agent 管理，只读）：
  framework/、docs/、tests/、evals/、scripts/、media-resources/、SKILL.md
- **工作区**（`~/android-test-skills-data`，用户数据，读写）：
  `.venv/`、`cases/`（副本）、`knowledge/`（副本）、`storage/`、`test_records.db`

首次 `setup` 时 cases/ 和 knowledge/ 从 skill 包复制到工作区，
之后用户的修改只动工作区副本，不再自动同步。

## 写用例模板

```python
from test_framework import TestCase

def run():
    t = TestCase("我的用例", target_package="com.zui.gallery")
    t.step("步骤1")
    t.tap_rid("com.example.app:id/btn_start")   # rid 优先（App 自有控件禁止 text 定位）
    t.record("PASS", "点击成功")
    t.screenshot("步骤1证据")
    return t.finish()
```

## 添加自己的知识/用例（两种方式）

**方式 A（推荐，会说话就行）**：直接告诉 AI
> "记一下：课程表入口是 主页→更多→课程表"
> "测试前先 pm clear"
> "跑这个用例：……"

AI 会自动写进知识卡 / 生成用例脚本。

**方式 B（技术用户）**：复制模板改
- `knowledge/_template.md` → 新 App 知识卡（结构约定见模板内注释）
- `knowledge/scenarios/sys.无限工作台.md` → 场景卡示例（头部键值区写法）
- `cases/_template.py` → 新用例脚本

## 给 App 积累知识卡

用户口述的 App 操作经验记录到 `knowledge/<包名>.md`（结构按 `knowledge/_template.md`）：
```markdown
- **app**: com.zui.calendar
- **验证版本**: 9.0.0.83（TB323FU）

## 检索索引
滚轮、时间选择器、权限、导入、冲突…

## 导航入口
- 课程表: 主页 → 右上角"更多"（`com.zui.calendar:id/iv_more`）→ 弹窗"课程表"
```

卡是"地图"不是"日志"：按页面/控件组织，探索后**更新既有小节**，不追加带日期/
用例号的新段落；机型实测值（坐标/热区偏移/行距）行尾打机型戳（如 `[TB323FU/9.0.0.83]`），
换机型先小探针校准再用。

## 环境要求

- 电脑：Windows / macOS / Linux + Python 3.10+ + Android SDK platform-tools (adb)
- 设备：Android 手机/平板，开启 USB 调试并授权
- 可选：Python 3.13 + AutoGLM 云端模型（`scripts/setup.sh --with-agent`）

## 跑单测

```bash
# macOS / Linux
python -m unittest discover -s tests -v

# Windows PowerShell（解决中文乱码）
$env:PYTHONUTF8="1"
python -m unittest discover -s tests -v
```
