# 图库 App 知识索引

[返回知识库](../../README.md) · [按功能查资料](functions.md) · [跨页导航](navigation.md) · [来源与覆盖](sources.md) · [版本及未决事项](limitations.md)

## App 身份

| 项目 | 内容 |
|---|---|
| App ID | gallery |
| 名称/候选别名 | 图库、相册、Gallery |
| Android包名 | com.zui.gallery（项目中由用户指定，运行时核对） |
| App版本 | 未提供，不能仅凭通用别名认定实际应用或版本 |
| 资料范围 | 本次压缩包的19张设计图，含子目录补充稿 |
| 平台 | Android；主要为横向平板，部分小窗/分屏/桌面场景 |
| 适用限制 | 多份设计并非单一版本；AI搜索/智能分类稿限定LLSI Pro、18.0上 |

## 按页面加载

| 知识ID | 页面 | 主要知识 |
|---|---|---|
| gallery.home | [图库首页](pages/home/README.md) | 分栏、设置入口、导航、日月视图 |
| gallery.all_photos | [所有照片](pages/all_photos/README.md) | 照片网格、多选、添加到、移动复制 |
| gallery.all_albums | [所有相册](pages/all_albums/README.md) | 相册列表、多选、删除重命名、拖动排序 |
| gallery.system_albums | [系统相册](pages/system_albums/README.md) | 相机拍摄、视频、收藏、截屏、录屏、下载、空态 |
| gallery.my_albums | [我的相册](pages/my_albums/README.md) | 新建、添加照片、重命名、用户相册管理 |
| gallery.private_album | [隐私相册](pages/private_album/README.md) | 密码验证、移入移出、隐私分享、永久删除、隐藏入口 |
| gallery.recently_deleted | [最近删除](pages/recently_deleted/README.md) | 回收站、恢复、剩余天数、永久删除、云端删除 |
| gallery.photo_detail | [照片详情页](pages/photo_detail/README.md) | 图片查看、缩放、清屏、详细信息、OCR、文档扫描 |
| gallery.video_detail | [视频详情页](pages/video_detail/README.md) | 播放、暂停、倍速、快进、截图、视频编辑 |
| gallery.ocr | [OCR／全图识别](pages/ocr/README.md) | 文字识别、选字、笔操作、抠图、清屏识别 |
| gallery.settings | [图库设置](pages/settings/README.md) | 隐藏隐私相册、自动同步、WLAN、同步相册 |
| gallery.widgets | [图库小部件与照片管理](pages/widgets/README.md) | 桌面、widget、20张、锁定轮播、照片管理 |
| gallery.external_picker | [应用外图片选择页](pages/external_picker/README.md) | 其他App、选照片、返回调用方、单选 |
| gallery.smart_albums | [智能分类相册](pages/smart_albums/README.md) | 文档、地点、更多、人物暂不支持 |
| gallery.ai_search | [图库AI搜索](pages/ai_search/README.md) | 自然语言、文字、分类、时间、地点、搜索历史、无结果 |

## 跨页面主题（按需加载）

| 知识ID | 主题 | 适用任务 |
|---|---|---|
| gallery.cloud_sync | [云同步](features/cloud_sync/README.md) | 备份、下载上传、账号登录、云空间不足 |
| gallery.drag_drop | [全局图片拖动](features/drag_drop/README.md) | 长按、多选拖动、跨应用、跟手缩略图 |
| gallery.lifecycle | [图库卸载、停用与恢复引导](features/lifecycle/README.md) | 卸载挽留、恢复安装、启用、系统调用 |
| gallery.cta | [CTA与首次授权弹窗](features/cta/README.md) | CTA、授权、隐私协议、首次进入 |

## 推荐阅读方式

先在功能索引找到当前任务，读取入口页和目标页；仅涉及跨页操作才读导航文件。页面已给出局部图片，不必读取全部原图。设置知识来自云同步和隐私相册设计，不是假定存在单独设置原稿。OCR依据画布实际内容归档，保留原文件名映射。
