# 测试文件清理与媒体预置

清理用户使用设备过程中产生的文件，包括图片、视频、下载文件及测试残留。

每次 `TestCase` 初始化时自动执行一次 `t.reset_sdcard_files()`，清理上述文件，并预置测试图片和视频；正式用例、辅助脚本及重跑均执行。脚本无需重复调用。准备步骤不计入 `--stop-after` 的测试步骤额度。

测试用例明确要求“测试开始前，设备中不能有任何图片或视频文件”时，在 `TestCase` 初始化后调用 `t.clear_sdcard_files()`：仅清理上述文件，不预置测试资源。

```python
t = TestCase("设备无图片或视频文件", target_package="com.zui.gallery", user_input=USER_INPUT)
t.clear_sdcard_files()
```

两个接口成功均返回 `True`，仅记录动作；失败记录 `BLOCKED` 并中止执行。App 数据和权限准备见 [测试环境准备](preparation.md)。

## 清理范围

两个接口使用相同的清理范围：

| 位置 | 处理 |
|---|---|
| `/sdcard/Android` | 完整保留 |
| `/sdcard/` 下的 Alarms、Audiobooks、Download、DCIM、Documents、Music、Movies、Notifications、Podcasts、Pictures、Ringtones、Recordings | 清空内容（包含隐藏文件），保留/创建空目录 |
| 其余文件和目录 | 删除，包括 media-resources 中的预置及新增文件、录屏和临时文件 |

文件清理方法仅处理上述共享存储文件，保留 Android 目录，不处理 App 私有数据和云端照片；初始化流程还会单独清空目标 App 数据，见 [环境准备](preparation.md)。系统可能自动重建缓存文件，这不会导致文件准备失败。

## 预置素材

素材在 skill 根目录 [media-resources/](../media-resources/README.md)，不复制到用户工作区。`reset_sdcard_files()` 只读取其中 `images/` 和 `videos/` 的文件；README 不推送。目标为 `/sdcard/media-resources/images/` 和 `/sdcard/media-resources/videos/`，保留相对路径。

目前包含 4 张图片、2 段约 5 秒的视频，约 4.1 MB。调整素材应改 skill 中的源文件，并保留用例引用的名称；新素材必须是可读、非空的普通媒体文件，不支持链接目录或 `.nomedia`。

完整分发/安装必须携带 `media-resources/`。只复制 framework 代码的旧同步方式不会携带素材，缺素材时 `reset_sdcard_files()` 会在删除任何设备文件前中止。

## 使用条件与失败处理

- 支持 Android 11 及以上版本，操作当前 TestCase 关联设备的当前用户主共享存储。
- `reset_sdcard_files()` 所需的本地素材缺失、为空或不可读时，会在清理设备文件前中止。`clear_sdcard_files()` 本身不依赖本地素材；初始化时的自动重置仍需素材。
- 准备失败时，报告会记录原因并中止用例。预置过程中失败可能留下部分素材，修复原因后重新调用 `reset_sdcard_files()` 即可重新准备。
