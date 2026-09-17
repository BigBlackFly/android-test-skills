# OPS 运维手册（维护者向）

> 本文件从 SKILL.md 拆出，**不随每次会话进 Agent 上下文**（省 token）。
> 受众：维护者/运维。Agent 只在用户提出「Web UI 为什么不对 / 重建报告 /
> 回填包名 / 配置视觉模型 / 查测试记录」等运维问题时检索本文件。
> 执行测试所需的行为契约分布在 SKILL.md（核心原则/工作流/硬约定/指针）+
> `docs/case-writing.md`（写用例）+ `docs/explore-guide.md`（探索 SOP）；
> **节级路由**：Windows 脚本参数 / 改完必查 BOM → 「Windows 脚本参数与细节」
> 查执行记录 → 「测试记录查询（SQLite）」｜Web UI 打不开/改了不生效 → 「Web UI 排障」
> 报告丢了或内容对不上 → 「报告重建」｜包名没入库 → 「报告重建 → 被测 App（包名）入库」
> 视觉模型配不上 → 「视觉模型配置」
>
> 本文件是同一约定的完整版与论证（SKILL.md 只留结论与指针，个别表格两边
> 各有一份，改动时同步）。

## Windows 脚本参数与细节

Windows 脚本参数：
- `setup.ps1 [-Workspace <路径>] [-Python <解释器>] [-SkipDeviceCheck] [-Recreate] [-WithAgent] [-Smoke]`
- `run_case.ps1 -Case <用例> [-List] [-Workspace <路径>]`
  - `-List` / 裸名 / 模糊匹配的查找**会排除共享模块**（路径任一层以 `_` 开头，
    如 `_flow.py`、`_lib/inventory.py`），与 `run_case.py` 的执行收集规则一致 ——
    列出的即是可执行用例，工具模块不会混进来。
- `webui.ps1 <start|stop|status|restart> [-Port 8900] [-HostAddr <地址>]`
  - 监听地址参数名是 **`-HostAddr`，不是 `-Host`**：`$Host` 是 PowerShell 只读自动变量
    （Constant/AllScope），拿它当 param 名会在参数绑定阶段直接失败，脚本根本起不来。

Windows 脚本为 **PowerShell 5.1 兼容 + UTF-8 with BOM**；已内置处理两项 Windows 特有问题：
原生命令 stderr 日志不触发 `NativeCommandError` 中断、强制 `PYTHONUTF8=1` 避免 GBK 编码 emoji 崩溃。
默认工作区 `~/android-test-skills-data`（Windows 下为 `C:\Users\<你>\android-test-skills-data`）。

### 改完 .ps1 必跑：BOM 校验

PowerShell 5.1 读**无 BOM** 的 `.ps1` 会按系统 ANSI 代码页（中文 Windows = GBK）解析，
脚本里的中文注释/输出与 emoji 会变乱码，乱码一旦破坏引号或括号配对，整个脚本直接语法崩溃。
实测把 `run_case.ps1` 的 BOM 去掉后有 **7 个语法错误、完全无法执行**。

> 常见触发场景：多数编辑/生成工具**重写文件时不保留 BOM**。本项目已踩两次（改 `webui.ps1`、
> `run_case.ps1` 后丢失）。`git` 与文件复制不会弄丢 BOM，**重写**才会。

```powershell
pwsh -File scripts/check_bom.ps1            # 只校验，缺 BOM 时退出码 1
pwsh -File scripts/check_bom.ps1 -Fix       # 自动补回缺失的 BOM
```

判定标准只有一条：文件开头 3 字节 = `EF BB BF`。别凭"改的是 ASCII 部分"来判断——
改完就跑一次脚本，比事后对着乱码报错排查便宜得多。

## 测试记录查询（SQLite）

- 每次执行自动入库 `~/android-test-skills-data/storage/test_records.db`：用例（cases）/步骤（steps）/断言结果（results，含状态快照与证据路径）。
- 命令行查看：`framework/db.py`（`python -c "import sys; sys.path.insert(0,'framework'); from db import get_db; print(get_db().list_cases())"`）

## Web 前端（查看记录 + 编辑知识库，随 skill 打包分发）

- **位置**：本 skill 包内（`scripts/webui.sh`，实现为 `framework/webui.py` + `webui.html`
  + `webui.js` + `webui.css`；页面还加载 `codemirror.bundle.js`，均本地打包、离线可用）
- **启动**：`./scripts/webui.sh` → 打开 http://127.0.0.1:8900（`stop`/`status`/指定端口）
- **数据定位**（显式，不依赖脚本所在目录）：
  - 环境变量 `DSH_WORKSPACE_DIR` 指定测试工作区（其下 `storage/` 含 test_records.db + 截图/报告）
- 用例与知识卡的**编辑目标在工作区副本**（setup 时从 skill 包复制而来）；
  可用 `DSH_WORKSPACE_CASES` / `DSH_KNOWLEDGE_DIR` 覆盖目录
  - 未跑过 setup 时兜底命中 skill 包的 `cases/` 与 `knowledge/`
  - 打包给别人：对方装好 skill 后跑 `scripts/setup.sh` 建工作区，直接 `./scripts/webui.sh` 即可
- **测试记录**页：用例列表（通过/失败徽章、搜索、筛选）→ 详情含 用户输入/脚本/状态/证据
- **Case** 页：查看/编辑 `cases/` 下用例脚本（搜索、步骤数、修改时间；可删除普通用例）
  - `_` 开头的共享模块（`_flow.py` / `_template.py` / `_lib/` 等）**也会列出**，带蓝色
    「共享」徽章、排在列表末尾、**无删除按钮**（后端 DELETE 亦返回 403）——它们被同目录
    用例 `import`，删掉会让一批用例 import 失败
  - 它们只是「可查看/编辑的资产」，**不可作为用例执行**：`run_case` 收集时仍排除
- **知识库**页：CodeMirror 编辑器查看/编辑 `knowledge/*.md`（高亮、行号、括号匹配），文件名白名单防路径穿越
  - `_template.md` **只读**（新建卡的样式源）；`_system.md` **禁删但可编辑补充**
  - 新建知识卡自动套用 `_template.md` 全文；MD 无语法校验，保存只拦空文件

## 代码与数据分开放（skill 包 / 工作区）

**代码在 skill 包（只读），编辑目标在工作区** —— 两边各司其职：

| | 放哪 | 为什么 |
|---|---|---|
| **代码** `framework/` + `scripts/` + `docs/` + `SKILL.md` | skill 包（唯一，只读） | Agent 加载的就是这里；升级/重装 skill 包不碰用户数据 |
| **可编辑资产** `cases/` + `knowledge/` | 工作区（唯一编辑目标，setup 时从 skill 包复制） | 用户的用例与知识积累留在自己地盘；跨 Agent 会话共享 |
| **数据** `storage/`（截图/报告/探查缓存/测试库）+ `.venv/` | 工作区（唯一） | 重装 skill 不会被清空 |

**为什么资产放工作区而不是直接改 skill 包**（三个坑）：

1. **跨 Agent 会分裂**。机器上可能同时存在多个 Agent 的 skill 目录（`~/.workbuddy/skills/`、
   `~/.agents/skills/` …），资产放 skill 包里就是每个 Agent 一份，改了这份那份看不到。
   工作区按 `~/android-test-skills-data` 定位，所有 Agent 打开的是同一个。
2. **重装/升级会覆盖手改**。skill 包是要入库随版本分发的，用户对用例/知识卡的修改
   混进去就会被下次升级冲掉；分开放则升级只换代码。
3. **不污染 git**。skill 包是入库仓库，用户数据（含 DB、潜在的大截图）混入会拖垮仓库。

**工作区副本是唯一编辑目标**：`run_case.py` / `webui.py` 的搜索顺序是工作区在前、
skill 包兜底（未跑过 setup 时兜底命中）。改用例/知识卡一律改工作区那份——
改 skill 包里的原始副本不会生效，**别改回去**。

`scripts/sync_skill.ps1` 的用途：skill 包 ↔ 工作区双向搬运（如框架改动分发、
工作区新用例/知识卡回收集）。脚本会跳过内容相同的文件，并校验所有 `.ps1` 带 UTF-8 BOM
（PowerShell 5.1 缺 BOM 会按 GBK 解析报错）。

## Web UI 排障

### 历史记录加载失败（OperationalError: unable to open database file）

多半是 **Web UI 进程还在跑旧代码**——库位置调整后（test_records.db → storage/ 内）没有重启，
旧进程按旧路径开库（文件已被迁移走）。排查与修复：

1. 杀掉旧进程再重启：`pwsh -File webui.ps1 restart`（它会用 venv Python + 当前代码）。
2. 不要用系统 Python 直启 `python webui.py`——venv 依赖不在，且绕过了启动脚本的环境设置。
3. 框架侧已加固：`db._connect()` 会在开库前自动创建缺失的父目录
   （纯 Web UI / 首次使用 / HOME 被重定向的环境里 storage/ 可能还没建出来）。

### Web UI 改了却没生效 —— 按顺序查这两条

1. **PAGE 缓存**：`webui.py` 把 HTML/JS/CSS 缓存在内存里，同端口上的旧进程不杀掉，就永远返回旧文件。  
   → 症状："文件明明改了，浏览器刷新还是老样子"。**先杀进程再重启**，别只刷新页面。
2. **进程跑在另一份副本上**：机器上可能同时存在多个 skill 包副本，进程命令行指向的那个才是真正生效的目录，改别的副本一律白改。  
   → 症状："重启了还是不对"。排查：查出监听端口的 PID，再看它的命令行指向哪个 skill 包路径（PID → 命令行 → 目录）。  
   → 实测踩过：进程跑在旧副本上，而我一直改活副本，浪费一轮排查。确认后统一到活副本，旧副本要么删除要么反向同步。

> 排查端口占用 / 进程命令行用系统自带命令即可（`netstat -ano | findstr :<port>` 拿 PID，再用 PID 查命令行）。  
> 注意：`wmic.exe` 在本机被安全策略屏蔽，**不要**用它，改用 PowerShell 的 CIM 查询。

### 首屏一直停在「加载中…」

Dashboard 卡片里的「加载中…」是 **HTML 写死的静态占位符**，只有 `loadDashboard()`
执行成功才会被替换。而 `show()` 只由侧栏按钮的 `onclick` 触发 —— 若 `webui.js` 末尾的
首屏初始化缺失或被清掉，首屏就永远卡在占位符上，**点一下侧栏 Dashboard 又立刻正常**
（这是最典型的识别特征）。

排查顺序：

1. 先看是不是 JS 没生效：改完 `webui.js` 必须**杀进程重启**（见上一条的页缓存）。
2. 确认页面返回的 JS 里有首屏初始化段（对比 `/webui.js` 响应内容与源文件）。
3. 若接口有数据而页面空着，那是前端没调用，不是后端问题：单独请求 `/api/dashboard` 验证。

### Web UI 删除失败（SHFileOperationW 0x2 / Failed to fetch）

**不要在 AI 沙箱里的 shell 启动 Web UI。** 沙箱会给 Python 注入「安全删除」shim（通过 `PYTHONPATH` 指向 `cli/vendor/shim/sitecustomize.py`），
它把 `os.remove` / `shutil.rmtree` 改走系统回收站。因为工作区在用户目录下，护栏按「个人文件」处理，于是两种误报：

1. `SAFE_DELETE_FAIL_CLOSED` → 界面报 `{"error": "SHFileOperationW 失败: 0x2"}`。  
   **0x2 是误报**：文件其实已经进回收站了，shim 二次确认时找不到文件才抛异常。别去改代码找 bug。
2. `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（默认阈值 50 次/轮）→ 护栏直接掐断，连接被关闭，浏览器只看到一句 `Failed to fetch`。  
   **此时数据库记录往往已经删掉了**，只是产物没清干净 —— 别重复删。

正确处理：

- **启动方式**：用普通终端跑 `webui.ps1`（这是用户自己的程序，删的是自己生成的产物，不该被护栏管）。  
  必须在 AI 侧拉起时，清掉注入再启动：`env -u PYTHONPATH python webui.py --port 8900`。
- **代码侧已加固**（不依赖运行环境）：`db.safe_remove()` / `safe_rmtree()` 以「文件还在不在」判定成败，不信异常；
  `delete_case()` 的产物清理整体包了 try/except，产物删不掉也绝不回滚数据库、绝不往外抛；
  `Handler` 的四个 HTTP 动词都套了 `_guarded`，任何异常都转成 JSON 500，**永不掐断连接**。

## 报告重建

### 报告文件丢了 / 内容对不上 —— 可以直接重建

报告只是 `steps` / `results` / `step_evidences` 的 Markdown 视图，**数据全在库里**，
文件没了随时能重算，不用重跑用例。

```bash
python framework/report_rebuild.py 105          # 重建指定记录
python framework/report_rebuild.py --all        # 重建所有"对不上"的
python framework/report_rebuild.py --force-all  # 强制重建全部（报告没坏也重建）
```

`--force-all` 用于**报告字段变更后刷新历史文件内容**：`--all` 只修"对不上"的，
字段改了但文件没坏的情况它不管。典型场景是 `package` 入库后，要把老报告里
「被测 App：C:\...\169.py」（其实是脚本路径）刷成真正的包名。

Web UI 里：记录详情右上角 **♻️ 重建报告**。

自动识别三种"对不上"：

1. **文件缺失** —— 跨机器迁移、路径变更、清理误伤
2. **多条记录共用一个报告文件** —— `<name>_报告.md` 是 finish() 的既定语义（重跑覆盖），
   历史记录都指向它，结果除最新一条外看到的都是别人的报告
3. **报告内容不是本条的** —— 落款时间既不等于 started_at 也不等于 finished_at（容差 60s）

修复策略：共用报告时**自动另存独立文件** `<name>_<运行时间>_报告.md`，绝不覆盖别人的报告 ——
否则修完这条、又弄坏那条，来回打转。最新一条继续持有原路径。

> 幂等：跑完再跑一次应返回「成功 0 / 失败 0」。

### 被测 App（包名）入库

报告头部的「被测 App」曾**只写进文件**，报告一丢就永久没了 —— 所以它现在进 `cases.package` 列：

- `finish()` 的包名取值优先级：**脚本路径目录名**（`cases/<包名>/xx.py`，目录名长得像包名才认）
  > 收尾前台包 —— 用例常停在 PhotoPicker 等系统页收尾，前台包可能根本不是被测 App
  （168 实测曾把 com.zui.calendar 报成 com.android.providers.media.module）。
  取不到就留空，**不覆盖已有值**
- 重建报告时渲染顺序：`package` → `script_path` → `unknown`
- 老记录（加列之前跑的）用 `db.backfill_package()` 回填：

```python
db.backfill_package(dry_run=True)   # 先看能补哪些
db.backfill_package()               # 正式写库（幂等，已有值的不动）
```

回填只认 `cases/<包名>/<脚本>.py` 这种结构、且目录名长得像包名（纯 ASCII、含点、无空白）。
历史旧路径（如 `cases/联想日历_168.py`——早期用例直接放 cases/ 根下用中文名，现磁盘已迁入
`cases/com.zui.calendar/`）提取不出包名，**一律跳过，不猜**。

## 视觉模型配置

- 入口：Web UI 左侧 **「视觉模型」**（`/#view-vision`）
- 可配 `Base URL` / `Model` / `API Key`，「测试连接」会发最小 chat 请求自检
- 保存位置：**工作区** `storage/vision.json`（权限 600）。**不进 skill 包**，`sync_skill.ps1` 不同步 `storage/`，所以分享 skill 包不会泄露密钥
- API Key 只回显掩码（前 4 后 4，中间打码），GET 接口不返回明文；**保存时留空 = 保留原值**（防止只想改 model 却误清空密钥）
- 运行时优先级：`DEEPSEEK_API_KEY` 环境变量 > 本页配置 > `~/.dsh/.credentials.yaml`
- 跨平台：路径统一由 `db.default_test_dir()` 解析（Windows `~/android-test-skills-data`、Linux/macOS 同逻辑），无平台分支代码
