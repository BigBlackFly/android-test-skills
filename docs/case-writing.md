# 写用例指南：API 速查 + 定位规范 + 关键技术

> **何时读本文件**：按 SKILL.md 工作流第 3 步「写用例脚本」时打开。
> 返回 → [SKILL.md](../SKILL.md) 核心原则 / [explore-guide.md](explore-guide.md) 探索 SOP
>
> **节级路由（只读你要的那节，本文件 238 行别整读）**：
> 要用某个 API → 「框架 API 速查」｜定位/旋屏怎么写 → 「定位规范与旋屏约定」
> 弹窗/权限/Toast → 「关键技术 → 弹窗看门狗 / 系统弹窗与 Toast」
> 图上找东西、选素材 → 「关键技术 → 视觉定位 / 图库列表选资产」
> 坐标从哪来 → 「关键技术 → 坐标/方向类事实一律运行时标定」
> 判 PASS 还是 FAIL → 「结果分类」｜提交前自检 → 「注意事项」

## 框架 API 速查

```python
from test_framework import TestCase
t = TestCase("用例名")

t.step("步骤名")                 # 开启一个步骤
t.record("PASS|FAIL|WARN|INFO|BLOCKED", "说明")   # 记录结果
t.blocked("原因")                # 环境/前置阻塞
# ── 动作基元（tap_* / input_text / clear_text 统一契约）──
#   轮询定位（wait=5s）→ 执行 → 弹窗检查/截图留证。返回 bool：True=已执行，
#   False=超时未找到（默认记 WARN 兜底）。调用方有自己的 FAIL/BLOCKED 守卫
#   分支时传 silent=True，避免"守卫记录+WARN 兜底"双重记录
t.tap_el(rid=..., text=..., desc=..., observe=)  # tap_rid/tap_text/tap_desc 的统一入口
t.tap_rid("resource-id") / t.tap_text("文字") / t.tap_desc("content-desc")
                                     # observe=False 用于"触发后立即抓 toast"的动作
t.tap_text_re(r"正则", clickable=None)  # 文本正则点击（同样轮询定位+执行确认）
t.tap_xy(x, y, observe=)          # 坐标点击（坐标须从 el_bounds 推导）
t.tap_vision("目标描述",              # 视觉定位点击（最后手段：view tree + OCR 均无法定位时）
    bounds=None, crop_dialog=True,      # bounds 限定搜索区域；crop_dialog 自动裁剪弹窗区
    observe=True, silent=False,         # observe/silent 语义与 tap_el 一致
    timeout=30.0, verify="")            # verify 非空时点后再截图问视觉模型验证
                                        # 返回 bool；失败记 WARN 不抛 ERROR
t.el_bounds(rid/text/desc) -> (x1,y1,x2,y2)  # 元素取 bounds（坐标从元素推导）
t.locate(rid=, desc=, text=, cls=, clickable=, contains=)
                                     # 组合属性定位（新用例推荐入口，见「定位规范」节）
                                     #   返回 _Located: .exists/.wait(s)/.click()/.long_click()
                                     #   /.bounds/.center/.node；属性组合 = XPath 多条件与
t.long_press_xy(x, y, duration=1.0)  # ATX 坐标长按。launcher 长按菜单必须用它——
                                     #   input swipe 同坐标模拟长按弹不出菜单（实测）
t.wait_rid(rid, timeout=10) / t.wait_text(text, timeout=10)  # 条件等待元素/文字出现
t.wait_activity(substr, timeout=10) -> str   # 等前台 Activity，超时返回 ""
t.require_tap_text/rid/el(...)               # 必需操作：等不到记 FAIL 并抛 CaseAbort 中止用例
                                             # （轮询命中即在窗口内点击，无"先 wait 后 tap"竞态；
                                             #  tap_* 失败只记 WARN，链路关键步骤用 require_* 防假通过）
t.input_text("rid", "中文文本")   # 中文经 ADBKeyboard 输入；返回 bool（找不到记 WARN）
t.clear_text("rid")               # 同契约，返回 bool
t.read_rid("rid") -> {text, checked, enabled, selected, clickable, bounds}
t.assert_text(rid, expect) / t.assert_switch(rid, "true|false")
t.assert_length_le(rid, 20)      # 输入长度上限
t.vision_ask(prompt, rid=)       # 视觉模型问答（颜色/布局/OCR 盲区）
t.assert_button_state_visual(rid, "grayed"|"clickable")  # 视觉按钮状态断言
t.assert_visual(prompt, expect)  # 视觉断言：回答命中关键词
t.contrast_of(rid) / t.assert_grayed(rid, ref, ratio=0.6)  # 置灰像素断言（旧，视觉优先）
# ── 探查缓存（生成用例阶段用，避免重复 dump/OCR 拖慢首次生成）──
t.probe_page(label, ocr=False, ttl=None, refresh=False, pkg=None)
    # 批量探查当前页，一次返回 {package, texts, rids, nodes[rid/text/bounds/clickable]}
    # 同时落盘 storage/probes/<包名>/<label>/{dump.xml,ocr.json,meta.json}
    # pkg= 缓存归属包名（缺省取前台包名）；探系统页（权限选择器等 com.android.*）
    # 时必须传被测 App 包名，避免缓存散到系统包名下
t.cached_dump(label, ttl=None, refresh=False, pkg=None)   # 取 UI 树，优先读缓存
t.cached_ocr(label, y_min, y_max, refresh=False, pkg=None)  # 取 OCR 结果，优先读缓存
t.find_nodes(label=None, rid_re=, text_re=, cls_re=, clickable=, pkg=)
    # 按正则筛节点；传 label 走缓存（不连设备），不传则实时 dump
t.screenshot("名称")              # 截图留证
t.capture_toast(wait=1.0, y_min=) -> (texts, shot)  # 动作后立即调：截屏定格→OCR 读 toast
                                     # 返回 (全屏文本列表, 截图路径)；读不准配 vision_ask 兜底
t.ocr(y_min, y_max) -> [(x,y,conf,text)]   # Canvas 内容读取
t.ocr_find(keyword, y_min, y_max) / t.first_clickable(y_min, y_max)
t.adb_shell("cmd", "args")        # adb shell 直通（绑定本用例 serial；cases 层禁止裸 subprocess adb）
t.current_package() / t.current_activity()  # 前台包名/完整 Activity
t.finish() -> 报告路径
```

### 条件等待（P1b 新增；全部"命中即停、超时有界"）

```python
t.wait_rid(rid, timeout=10)              # 等出现
t.wait_text(text, timeout=10)            # 等出现（**精确匹配**：n.text == text）
t.wait_activity(substr, timeout=10)      # 等 Activity；命中返回完整名，超时返回 ""
t.wait_gone(rid=..., timeout=10)         # 等**消失**（P1b；删除类断言的另一半）
t.scroll_to_rid(rid, timeout=12, max_swipes=6, direction="up")   # 循环 swipe+查树（P1b）
```

- `wait_gone` 无判据时**抛错** —— 不许静默返回 True（那会变成假 PASS）。
- `wait_text` 是**精确匹配**；要子串/正则自己用 `screen_text()` 判，或用 `tap_text_re`。
- `scroll_to_rid` 解决"rid 根本不在树上"（小屏折叠 / 长列表懒加载）：这时 `wait_rid`
  **等多久都没用**，必须滚（定位失效四归因见 SKILL.md §2.4）。
  `swipe(x1,y1,x2,y2,dur)` 也已上提到框架（别再手写 `t.d.swipe` / `input swipe`）。

### 区域派生（禁止写死像素，§3.1）

```python
t.ocr(region=t.region_of(RID_LIST))      # 元素 bounds → OCR 区间
t.ocr(below=RID_HEADER, above=RID_NAV)   # 两个 anchor 之间的区域
t.region_of(rid)                         # → (y_min, y_max, x_min, x_max) 或 None
```

anchor 取不到时**不缩范围**（退化全屏）：提示错了最多慢一点，不会挂。

### 一份 dump 查多个目标（M4）

```python
xml = t.dump_snapshot()                  # 短 TTL（1.5s）内复用，避免 N 次 dump
t.el_bounds(rid=A, xml=xml) or t.el_bounds(text=B, xml=xml)
```

`el_bounds` 的优先级是 **rid > desc > text**，且三级在**同一份 dump** 内完成
（§4.2 硬约束：跨 dump 重找会让"相似元素"的身份保证断裂 → 报告写"自愈成功"却点了
别的元素）。给了 rid 却靠 desc/text 命中时会留**降级留痕**，进报告与 `healing_log.json`。

**定位正确性的四条保证**（2026-09-16 补；rid 优先不变，补的是"取到的到底对不对"）：

| 情形 | 行为 | 为什么 |
|---|---|---|
| 同一 rid 命中多个（容器 + 子控件） | 优先 `clickable=true`；无则可点击则取**面积最小**；仍并列标 `ambiguous` 并留痕 | "取树序第一个"常点到容器（可能被拦截，或点到错误位置） |
| rid 命中但 **bounds 为空**（出屏/折叠/未布局） | **返回 None 且绝不降级** → 用 `scroll_to_rid` 滚，或报"找不到" | 退到 text 会命中一个同名乱入的无关节点并"成功"点到错的东西 —— **比找不到更危险**（报告还是绿的） |
| rid 只回本地名（无包名前缀） | 精确匹配优先，本地名兼容作第二级 | 跨包同名节点不许抢先命中 |
| 断言 | **永不降级** | 降级链只用于"找要点哪个"，不参与"判断结果对不对" |

`present_no_bounds` 会打印 `↕️ <rid>: 在 UI 树上但无可交互 bounds（出屏/折叠）→ 需要滚动`。
看到它就说明**该用 `scroll_to_rid`**，而不是去改定位属性（改成 text 只会点到别的元素）。

### 前置条件不满足 → BLOCKED（不是 FAIL）

```python
t.block_unless(lambda: t.el_bounds(rid=RID_LIST), "需先有一条课程表")
t.require_tap_rid(RID_SAVE, on_absent="BLOCKED")     # 默认仍是 FAIL
```

为什么要分：`require_*` 找不到元素有两种成因 —— App 真坏了（FAIL）vs 前置条件缺失
（BLOCKED）。混成同一个会让缺陷库被环境噪音污染，且"配置差异除外"永远无法机械判定。
退出码：FAIL=1、BLOCKED=2。

## 定位规范与旋屏约定（硬约定）

**定位优先级：resource-id > content-desc > text > 坐标**，落成两条实现路径：

- **组合属性定位 `t.locate(...)`**（新用例默认入口）：多属性 AND，等价于 XPath
  多条件组合。单属性语义太弱——如 desc="日历" 同时命中桌面图标/widget/长按菜单，
  text="日历" 命中列表项容器+名称标签+其他页面同名节点；叠加 cls / clickable /
  rid 后收缩到唯一目标。实测组合（TB323FU）：
  `locate(desc="卸载", cls="android.widget.ImageView", clickable=True)`（长按菜单图标项）、
  `locate(desc="日历", clickable=True)`（所有应用列表项容器）。
  约束：每个属性都必须能回答"为什么它必须成立"，答不上来的不加（过度约束 =
  系统改版即失效）；**禁止 bounds / 位置索引**（布局一改全崩，且属性变化不携带
  任何业务含义）。
- **遍历读真实文字**（列表/菜单条目、无法预设文案时）：`t.find_nodes()` 拿
  text/desc/clickable/bounds_xy，按真实文字匹配后点 bounds 中心。**不得硬编码
  想当然的界面文案**——曾把恢复入口硬编码成"恢复预装应用"，实际入口真实文字
  是"恢复"，导致定位失败设备滞留卸载态。先遍历读、再匹配，命中的真实文字
  写进 record 留证。
- 弹窗按钮（无 rid 无 desc）是 text 的合理例外，同样走遍历读真实文字匹配。

**旋屏约定：套件基线 = 竖屏锁定**

- 所有用例开头 `t.lock_portrait()`，结尾**不还原**自动旋转——基线即竖屏锁定，
  "不动"就是正确的现场。教训：119 曾在 finally 写死"还原自动旋转"，设备立马
  转横屏，下一个脚本坐标系全错、长按点进状态栏拉下通知面板。
- 只有真正中途转屏的用例才 `t.snapshot_rotation()`（转屏前快照）+
  `t.restore_rotation(snap)`（finally），恢复"进用例时的状态"，而非盲目开自动旋转。

## 系统级操作与通用前置条件

跨 App 通用的设备操作（用例前置条件常用）：

| 场景 | 命令/方法 |
|---|---|
| **首次使用/重置状态** | `t.pm_clear("包名")` = `adb shell pm clear 包名`（清数据回首次引导） |
| 强制停止 App | `t.force_stop(pkg)` |
| 启动 App | `t.launch_app(pkg)`（monkey LAUNCHER 入口，绑本用例 serial） |
| 读系统属性（判断模式） | `t.getprop("ro.build.type")` / `t.getprop("persist.sys.xxx")` |
| 读写系统设置 | `t.settings_get("global", key)` / `t.settings_put("secure", key, value)` |
| 判断设备网络 | `t.has_network()`（解析需要联网时先检查，无网则 BLOCKED） |
| 授予运行时权限 | `t.grant_permission(pkg, "android.permission.X")` |
| 前台包/Activity | `t.current_package()` |
| 锁定竖屏（套件基线） | `t.lock_portrait()`（用例开头调用，结尾不还原，见「定位规范与旋屏约定」节） |
| 中途转屏的用例 | `t.snapshot_rotation()`（转屏前）→ `finally: t.restore_rotation(snap)` |

**用例前置处理套路**：
1. 用例要求"首次使用"状态 → `t.pm_clear(包名)` 再启动
2. 用例依赖网络/系统模式 → 先 `t.has_network()` / `t.getprop(...)` 判断，不满足标 BLOCKED（环境原因），不硬跑
3. App 特定前置（如"已导入图片并解析"）→ 写在用例脚本开头按步骤执行

## 关键技术

### 弹窗看门狗（权限/引导弹窗自动处理）
- `t.start_watchdog(policy="allow|deny")`：启用弹窗自动点击（u2 原生 watcher + 主流程驱动，单连接、零并发 dump）
- 普通动作（点击/输入）后的弹窗检查窗口默认 3 轮（~1.5s）；**首启/授权/安装/弹窗级联等高风险动作后必须显式调 `t.observe_dialogs(rounds=10)` 开长窗口**——窗口期漏掉的弹窗仍会被后续 wait_* 轮询兜底，但高风险场景不要依赖兜底
- `t.watchdog_policy("deny")` 动态切换策略；`t.watchdog_pause()/resume()`；`t.stop_watchdog()`
- 规则：**Android 运行时权限弹窗约 6-8 秒自动消失**，必须检测即点
- Android 14+ 照片权限按钮是 **"选择照片"/"全部允许"**；相机是"仅在使用时允许/仅本次使用时允许/拒绝"
- **两层处理**：
  1. **词表快路径**（毫秒级）：`DIALOG_GUIDE_WORDS`/`DIALOG_ALLOW_WORDS`/`DIALOG_DENY_WORDS` 命中 → u2 watcher 点击
  2. **AI 慢路径**（秒级，兜底未知弹窗）：词表未命中但 UI 树有可点击文本 → 视觉模型识别弹窗（标题/按钮/动作建议/置信度）→ 按建议点击。保护：节流 8s + 每动作窗口最多 3 次 + 置信度 ≥0.7 + 动作与策略一致（allow 策略不点拒绝类）；破坏性操作 AI 倾向点"取消"（已实测：系统"删除应用数据"弹窗 AI 正确点"取消"）
- 词表外的新弹窗：AI 处理过一次后自动学习（按钮文字并入 `framework/dialog_words.json` 对应词表，重复自动去重），下次走快路径；也可手动编辑该文件

### 系统弹窗/Toast
- **模态弹窗 ≠ Toast，处理方式完全不同**：弹窗有按钮、不点不散，用 `screen_text()` 判断后
  点按钮关闭；toast 无按钮、自动消失（窗口 4s/7s 档——AOSP `ToastPresenter`
  `SHORT_DURATION_TIMEOUT=4000 / LONG_DURATION_TIMEOUT=7000`），只能截屏定格抓取。
  别把「图片内容不是课程表」这类弹窗当 toast 找（找不到按钮、也不会自动消失）
- Toast：**截屏 + OCR，禁止用 logcat**（Toast 缓冲在多数 ROM 上不打印或格式不一，不可靠）。
  Toast 是视觉元素、显示窗口短、不会一直在屏——**动作后立即**调
  `texts, shot = t.capture_toast()`（内部等 1s 让 toast 渲染 → 截图落盘留证 → OCR 返回文本列表），
  断言 `any("目标文案" in s for s in texts)`；错过窗口 toast 就没了，只能重做触发动作
- **触发动作用 `observe=False`**：`tap_xy/tap_text/tap_rid(..., observe=False)` 跳过点击后的
  弹窗检查窗口(~1.5s)+自动截图——那条链会占满 toast 显示窗口，capture_toast 必抓空
  （179 的 AB 隔离实验实锤）。普通动作保持默认 observe=True（弹窗保护不丢失）
- 半透明 toast OCR 混背景读不准时，视觉模型兜底：`t.vision_ask("屏幕上是否有 toast？内容是什么")`
- 探索期（采集脚本）每个关键动作后就地 capture_toast 落盘，见 [explore-guide.md](explore-guide.md) SOP ①——
  断言里的 toast 文案必须来自这些记录，不是用例期现抓

### 视觉定位（tap_vision）

适用：view tree（rid/text/desc）与 OCR 都无法定位的元素（Canvas/色盘/无文字图标/WebView 私有控件）。

- **策略选择**：`tap_strategy` 在 Web UI「视觉模型」页配置（`auto/som/coordinate`）
  - `som`（默认）：SoM 网格——外扩画布画行列标签，模型报格子引用，纯索引换算格子中心
  - `coordinate`：归一化坐标——专用 GUI/grounding 模型直接输出 0-1000 比例
- **弹窗裁剪**：`crop_dialog=True` 自动从 UI 树找弹窗容器（Panel 类）裁剪搜索区；`bounds=(...)` 显式指定
- **verify**：非空时点击后再截图问视觉模型验证（如 `"目标图标是否已被选中"`），未通过则返回 False
- **失败行为**：返回 False + 记 WARN（不抛 ERROR）；`silent=True` 时不记 WARN

### 图库/列表选资产（视觉排序 + 预检 + 双态等待）

从网格/列表选"内容匹配的目标"（图库里的课程表图、文件选择器里的素材等）时的标准范式
（实现范例：`cases/com.zui.calendar/_flow.py → _rank_thumbs/_ensure_grid`）：

1. **枚举候选**：`find_nodes(rid_re=...)` 拿全部可见条目——不要盲点第一个
   （同 rid 节点的顺序 = 界面排序 = 共享状态，随时会变）。
2. **视觉排序**：逐条目裁剪送 `vision_ask`，提示词只写**通用结构特征**（表格网格/
   表头/单元格文字），不写素材颜色标题等具体特征（遇到其他风格的同类资产会误杀）。
3. **大图预检（便宜闸门）**：点入详情/裁剪页后图片变大，先 `vision_ask` 确认再触发
   下一步（解析/上传）——不是目标就返回换候选，不消耗下游轮次，也绕开失败后的
   不确定状态导航。
4. **双态等待**：等"成功信号"或"App 失败信号"（失败弹窗文案必须记入知识卡），两者
   出现任一立即终止本轮；弹窗可能被看门狗抢先点掉，所以"失败弹窗文本出现"和
   "已退回列表"都算命中失败分支（0.5s 级轮询抗竞争）。
5. **候选上限**：最多试 3 张，用尽 BLOCKED 并**准确归因**（如"未找到课程表图片"），
   禁止归因为超时/网络——归因错误会让下次排查方向全错。
- 返回列表时**先查是否已回列表，再按 BACK**：失败弹窗点掉后可能已自动回列表，
  盲按 BACK 会直接退出选择器。

### 坐标/方向类事实一律运行时标定
- 知识卡/探查缓存里的坐标、行距、列 x 都是**探查线索**，禁止跨运行直接复用：方向（横竖屏）、
  分辨率、App 版本一变就全错（179 写死坐标后，锁定态被冷启动解开、设备翻回横屏 → 坐标全偏）。
  **卡里本不该有这些值**（见 `knowledge/_template.md`：设备实测值不写进卡）——卡里若还留着，
  就是待清除的探查线索，别当运行时事实引用
- 正确姿势（179 `calibrate_picker` 范式）：运行时 OCR/dump 现场标定——列按 x 聚类、行按 y 聚类
  去重（同一行 OCR 出多个相邻 y 会让 step 中位数塌成 1）、step 取相邻行距中位数；
  标定结果只在本次运行内有效，不回写卡里当"事实"
- 视觉模型给出的位置 = **假设**：落点前必须与当前 UI dump 的元素 bounds 核对，对不上就重标定
- 用例开始先读一次当次方向/分辨率，**不做任何方向假设**。**读法必须分清语义**：
  - **当前**尺寸/方向 → `t.d.window_size()`，或框架 `t._screen_size()`（取实测截图的 PNG 头）
  - `wm size` 给的是**自然尺寸**（不随旋转变），只能用来判断"这台设备的自然方向是竖是横"；
    **拿它当当前尺寸用就会错**（实测本机型 `wm size`=1904×3040，当前却是 3040×1904）
- 滑动/区域 OCR 的坐标从当次窗口尺寸推导（175 `_swipe_up` 范式），不写死像素

### 输入
- 中文：`input_text`（u2 send_keys 自动切 ADBKeyboard）
- 清空：`clear_text`

## 结果分类

| 类型 | 含义 |
|---|---|
| ✅ PASS | 符合预期 |
| ❌ FAIL | 不符合预期（产品缺陷或环境，注明原因） |
| ⚠️ WARN | 需人工确认/非致命异常 |
| ⛔ BLOCKED | 环境/前置不满足，无法执行 |
| ℹ️ INFO | 记录性信息 |

**权限测试注意**：拒绝后权限变 USER_FIXED，系统不再弹窗 —— 每个权限行为（允许/拒绝）拆成独立用例、各自 pm_clear，避免级联失败

**用例最终结论（final_status）与进程退出码**：框架显式计算最终结论（规则确定：
FAIL > BLOCKED > WARN > PASS），写入报告头部/汇总、SQLite `cases.final_status` 列
（不再从摘要文本推断）和进程退出码：

| 退出码 | 含义 |
|---|---|
| 0 | PASS / WARN（WARN 需人工看，但不阻断 CI） |
| 1 | FAIL（含 require_* 必需操作失败中止，即 CaseAbort） |
| 2 | BLOCKED（环境/前置不满足） |
| 3 | ERROR（脚本/框架/设备异常） |

只有 BLOCKED 的用例最终结论是 BLOCKED，**不会**显示为 PASS。

**异常路径也有报告**：脚本中途抛异常时 run_case.py 会捕获、设置 `_fatal_error` 并补调
`finish()`——结论压成 ERROR（报告头部附异常信息）照常入库出报告，不会"崩了但 DB 悬挂"。
CaseAbort 例外：它是 require_* 已记 FAIL 的正常中止，按 FAIL 走退出码 1。

## 注意事项

- **禁止裸 `time.sleep` 等界面**：等页面/元素出现一律用条件等待 `t.wait_rid` / `t.wait_text` / `t.wait_activity`（轮询期间顺带驱动看门狗）。`sleep` 只允许用于非 UI 的 settle（进程退出、服务启动等），且必须写注释说明在等什么。连续两个裸 sleep 是明确的坏味道。
- **既有链路 sleep 属 settle 型可保留**：`cases/<包名>/_flow.py` 等既有链路里的固定 sleep（如冷启动 4s、页面转场 3s）是无元素信号的 settle 等待，数值来自真机实测调优（PhotoPicker 首次冷启动、图片解析等场景没有可条件等待的 rid）。复用这些链路时保持原值；改数值需真机回归。新写代码仍遵守上一条：优先条件等待，sleep 必须带注释。
- **每步操作后统一等待 ≥1s 再截图或 dump UI**：任何点击/输入/启动/返回等操作后，先 `time.sleep(ACTION_DELAY)`（=1s）等界面动画/转场稳定，再截图或 dump_hierarchy；否则会因界面未渲染完而误判（如首启权限页还没出现就 dump，误认为"无弹窗"）。AI 写用例脚本时必须遵守，禁止"操作后立即 dump/截图"。
- **多设备**：`TestCase(device_id=None)` 时要求恰好一台已授权设备；零台/多台会在启动时直接报错。多台时必须 `TestCase("名", device_id="serial")` 显式指定——框架内所有 adb/u2 操作都绑定同一 serial，操作、断言、截图证据不会跨设备分家。报告与 SQLite 记录 serial/型号/Android 版本/屏幕尺寸。
- **屏幕锁屏（已自动防护）**：长用例执行中设备可能因休眠超时被锁屏，导致后续 adb/u2 交互打到 keyguard、dump 读不到 App 节点 → 元素定位失败、用例误判 FAIL/BLOCKED。框架已内置两层防护：① `TestCase.__init__` 执行 `svc power stayon true`，USB 供电期间屏幕常亮、从源头不锁屏；② 每次读屏(`_dump`)/截屏(`_screencap_bytes`)前自动调用 `ensure_awake()`（3s 节流）兜底唤醒+解 keyguard。**注意**：`stayon` 仅 USB 供电时生效；若设备拔线用电池跑、或设了安全锁屏(PIN/图案)，仍可能锁屏——此时需人工解锁或保持供电。
- `d.info` 在 Android 15+ 可能崩溃：用 `app_current`/`dump_hierarchy` 替代
- 坐标以 u2 dump bounds 为准；选择器等系统 UI 布局可能变化 → 用 `first_clickable`/OCR 动态定位，不用写死坐标
- 无法操作时（设备离线/无网络等）：如实标注 BLOCKED 并说明原因，不编造结果
- 测试设备数据可被修改：告知用户副作用，不擅自恢复
