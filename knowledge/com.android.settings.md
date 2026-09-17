# 系统设置（ZUI Settings）

<!-- App 卡（MD 格式）。机器靠关键词检索 + 只读命中片段，不 parse 结构。 -->

- **app**: `com.android.settings`
- **name**: 系统设置 / ZUI Settings
- **验证版本**: 18.5.0.0166（versionName，2026-09-16 真机采集）
- **versionCode**: 18500166（2026-09-16 真机采集，TB323FU / Android 17）
- **最近验证**: 2026-09-09（双栏布局、deep link、导航条目属性 实测）

## 检索索引

设置、settings、com.android.settings、导航栏、内容区、双栏、单栏、竖屏、back、
返回、应用管理、恢复预装应用、更多选项、deep link、APPLICATION_SETTINGS、
壁纸和主题风格、WLAN、蓝牙、android:id/title

## 布局结构（核心：左右双栏，竖屏下切换显示）

- 设置分为**左右两部分**：左侧是**导航栏**（设置分类列表），右侧是**内容区**（选中分类的详情）。
- **8 寸平板竖屏下，屏幕同一时刻只显示导航 或 内容之一**（单栏模式切换），
  不像手机那样每级一个 activity——判断"当前在哪层"不能只看 activity，
  要看屏幕上是分类列表（导航）还是具体设置项（内容）。
- **在内容区按 BACK 回到导航栏**。导航页特征：出现 WLAN/蓝牙/个人热点 等
  分类列表；内容页特征：出现具体设置项/子列表。
- 导航栏**可上下滑动**查找设置项（应用管理等入口在首屏之下，需滚动）。

## 导航入口

- 打开设置: `am start -a android.settings.SETTINGS`
  - 注意：**该 intent 会恢复上次残留的子页（内容区）**，不保证落在导航页。
    可靠做法：先 `am force-stop com.android.settings` 再 start；落到内容区时按
    `input keyevent KEYCODE_BACK` 回导航页。
- **直达应用管理（推荐，跳过导航滚动查找）**:
  `am start -a android.settings.APPLICATION_SETTINGS` → 直接落在
  应用管理-所有应用列表（实测有效，列表含各应用与占用大小）。
- 导航分类条目定位: `text="<分类名>"` 的节点 rid=`android:id/title`，
  **title 本身 clickable=False**，点击目标是所在的行容器（可点父级）。
  竖屏实测分类项: WLAN / 蓝牙 / 个人热点 / 更多连接 / 壁纸和主题风格 /
  显示和亮度 / 声音和振动 / 通知和控制中心 / 生物识别和密码 / 安全和紧急情况…
  （应用管理 需滚动或走 deep link）
- 应用管理页右上角有**「更多选项」**溢出菜单
  （`el_bounds(desc="更多选项")`，cls=ImageButton）；「恢复预装应用」入口的
  固定路径见 `_system.md`「预装 APP 的卸载与恢复」节。

## 验证要点

- 判定"在导航页还是内容区"：看屏幕文字是分类列表（WLAN/蓝牙…）还是具体设置项。
- 从内容区回导航后，重新 dump 再定位（层级切换后节点树完全不同）。
- deep link 后 activity 可能读出 unknown，以 dump 到的页面文字为准。
