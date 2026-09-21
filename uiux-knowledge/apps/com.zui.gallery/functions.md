# 图库功能发现索引

[返回App](README.md)

下表是资料加载建议，不是固定测试脚本。多选是否操作照片还是操作相册，应先区分。

| 用户意图/检索词 | 入口或前提 | 应加载资料 | 关键区别 |
|---|---|---|---|
| 找页面、折叠导航、日/月切换 | 首页分栏；所有照片网格 | [图库首页](pages/home/README.md)、[所有照片](pages/all_photos/README.md) | 默认视图未知 |
| 选择多张照片、全选 | 媒体网格多选 | [所有照片](pages/all_photos/README.md) | 不是所有相册的相册多选 |
| 移动/复制照片到相册 | 媒体已选→添加到 | [所有照片](pages/all_photos/README.md)、[我的相册](pages/my_albums/README.md) | 先选照片与先建相册是不同起点 |
| 新建空相册、重命名 | 左侧新建或所有相册+ | [我的相册](pages/my_albums/README.md)、[所有相册](pages/all_albums/README.md) | 重命名同名校验已明确 |
| 删除相册、排序相册 | 所有相册可编辑项 | [所有相册](pages/all_albums/README.md)、[我的相册](pages/my_albums/README.md) | 系统相册不可编辑；删除原文件语义未明确 |
| 找相机拍摄/视频/收藏/截屏/录屏/下载 | 系统相册分组 | [系统相册](pages/system_albums/README.md) | 部分入口为空时隐藏 |
| 看图、清屏、缩放、详细信息 | 点照片缩略图 | [照片详情页](pages/photo_detail/README.md) | 隐私/最近删除详情工具不同 |
| 识别文字、笔选字、抠图、文档扫描 | 照片详情 | [照片详情页](pages/photo_detail/README.md)、[OCR／全图识别](pages/ocr/README.md) | 部分能力依外部文档/上线排期 |
| 播放视频、倍速、截图、剪辑 | 点视频 | [视频详情页](pages/video_detail/README.md) | 首次暂停；退出重进倍率重置 |
| 打开隐私相册、验证身份 | 更多相册；入口未隐藏 | [隐私相册](pages/private_album/README.md)、[图库设置](pages/settings/README.md) | 返回前台重新验证 |
| 移入/移出隐私、解密分享 | 普通多选/详情或隐私相册 | [所有照片](pages/all_photos/README.md)、[隐私相册](pages/private_album/README.md) | 100项为特定动作检查；删除例外 |
| 恢复照片、永久删除 | 最近删除 | [最近删除](pages/recently_deleted/README.md) | 恢复细节未完整定义 |
| 删除云端照片 | 云同步已开且涉及云端 | [最近删除](pages/recently_deleted/README.md)、[云同步](features/cloud_sync/README.md) | 需区分本地与云端影响 |
| 开启备份、仅Wi-Fi同步、云空间 | 设置或照片浮窗 | [图库设置](pages/settings/README.md)、[云同步](features/cloud_sync/README.md) | WLAN开关有LTE适用说明 |
| 自动分类、文档/地点相册 | 所有相册智能分类卡片等 | [所有相册](pages/all_albums/README.md)、[智能分类相册](pages/smart_albums/README.md) | 人物暂不支持 |
| 自然语言搜图、文字/日期搜索 | 左侧搜索 | [图库AI搜索](pages/ai_search/README.md)、[智能分类相册](pages/smart_albums/README.md) | 依版本；离线与账号条件未逐项定义 |
| 照片拖到另一应用 | 分屏相册媒体列表 | [全局图片拖动](features/drag_drop/README.md)、[所有照片](pages/all_photos/README.md) | 目标App接收结果未提供 |
| 桌面照片、轮播、锁定、20张上限 | widget | [图库小部件与照片管理](pages/widgets/README.md) | 移出管理列表不等同删除原文件 |
| 在其他App选择一张照片 | 调用图库选择器 | [应用外图片选择页](pages/external_picker/README.md) | 点图返回调用方，而非浏览详情 |
| 图库卸载后调用、停用后恢复 | 系统应用调用图库 | [图库卸载、停用与恢复引导](features/lifecycle/README.md)、[应用外图片选择页](pages/external_picker/README.md) | 第三方不触发同样的恢复提示 |
| 首次授权、隐私协议 | 首次进入图库 | [图库首页](pages/home/README.md)、[CTA与首次授权弹窗](features/cta/README.md) | CTA与系统权限不能简单合并 |
