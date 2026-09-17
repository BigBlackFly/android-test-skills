# 系统「打开方式」选择器（跨应用跳转中转站）

<!-- App 卡（MD 格式）。机器靠关键词检索 + 只读命中片段，不 parse 结构。

     ⚠️ 本卡原名 `com.android.intentresolver.md`（2026-09-16 修正）：
     卡的文件名 = 检索键 = **前台包名**。实测本机（TB323FU / ZUI / Android 17）
     该页面的前台包名是 `com.zui.resolver`（Activity
     `com.zui.resolver.ResolverActivity`），而 `com.android.intentresolver`
    在本机**根本没安装** —— 即旧文件名下这张卡**永远命中不到**（前台包名查
    不到卡 → 退到 `_system.md`），知识等于没写。
     其它机型源码见 `com.android.intentresolver`（AOSP 同名包）：若换了机型，
     按前台包名再建一张同名卡即可，内容可复用。 -->

- **app**: `com.zui.resolver`
- **name**: 系统「打开方式」选择器
- **验证版本**: 8.0.0.0153-2026.07.10-release（versionName）
- **versionCode**: 8000153（2026-09-16 真机采集，TB323FU / Android 17）
- **最近验证**: 2026-09-16（仅采集到包名/版本/Activity；界面项未逐条真机复核，仍是 stub）

## 界面结构

- 应用列表: 每行一个可打开当前内容的 App（图标 + 名称）
- 部分版本有「仅此一次 / 始终」选项

## 高效操作

- 点按目标 App 所在行即可选择
- 列表里找不到目标 App 时，先滑动列表再找
- 误开后可用 Back 返回原应用

## 验证要点

- 选择后前台包名应变为所选目标应用
- **判"是否在这个页面"要看包名**：`com.zui.resolver`（不是 `com.android.intentresolver`）
