# 测试前读取 UIUX 知识

执行一组用例时，先通过宿主文件读取工具把相关 UIUX Markdown 正文读入当前 Agent 上下文，再读取 `knowledge/<包名>.md` 的测试经验、探索和执行用例。

## 入口与组织

从 skill 包内 [uiux-knowledge/README.md](../uiux-knowledge/README.md) 开始，按已确认包名定位 App。App 目录统一使用 Android 包名；图库入口为 `uiux-knowledge/apps/com.zui.gallery/README.md`，与 `knowledge/com.zui.gallery.md` 使用同一包名。

```text
uiux-knowledge/
  README.md                    # App / 包名映射和加载规则
  apps/<包名>/
    README.md                  # 页面与跨页主题索引
    functions.md               # 按任务、功能和检索词发现资料
    navigation.md              # 跨页导航
    limitations.md             # 版本、歧义与未确定事项
    sources.md                 # 原稿来源与覆盖
    pages/<page_id>/README.md   # 页面规则；同级 images/ 放局部图
    features/<topic_id>/README.md
    sources/                   # 原始设计稿
```

## 阅读流程

1. 解析本组用例涉及的 App 和目标功能，读总索引、相关 App 索引和功能索引。跨 App 用例分别定位相关资料；包名未知时先确认。
2. 读取当前页、目标页、必要中间页的 Markdown 正文与适用限制；跨页操作补读 navigation.md 和对应 features/。只读索引或搜索命中行不等于读过完整相关规则。
3. 图标、布局或状态细节不明确时，按 Markdown 所在目录解析相对图片路径，使用宿主图片工具打开对应局部图；需要时再查看 overview 或 sources/ 原稿。读取 Markdown 中的 `![...](...)` 只是读取链接，不自动把图片送入模型。
4. 基于已读设计理解入口、状态和约束，然后进入原有知识卡/流程复用、探查、用例编写与执行步骤。同组复用上下文；涉及新页面、资料变化或上下文丢失时补读。

例如验证图库删除/恢复：先在 `apps/com.zui.gallery/functions.md` 定位相关规则，再读 `pages/recently_deleted/README.md` 及操作起点页面；不能从恢复按钮存在推断资料未说明的恢复落点或提示文案。

## 使用边界

- Markdown 直接通过宿主文件工具读取，无需调用专用 Python 预读脚本或另外配置视觉 API。文字作为工具结果进入当前会话；图片仍要求宿主和当前模型具备相应能力。
- UIUX 保存设计预期；`knowledge/` 保存测试操作经验。二者都不能替代当前设备证据或擅自改变用户的验收标准。包名映射来自项目约定，安装版本和机型仍须核对。
- 资料中的缺口、条件化能力、历史标记与占位图必须保留；正文不是逐字 OCR 副本，不能声称 Markdown 已覆盖全部图像细节。
- 文件缺失、输出截断或图片无法读取时如实说明。需要的资料不存在时先记录缺口；影响操作或验收的关键缺口应先澄清。不要为读文字资料自动调用外部视觉服务。
- 这是 Agent 的准备流程。直接执行 `run_case.py` / `run_suite.py` 不会自动读取 UIUX，也不会把这些资料注入独立的视觉定位请求。
