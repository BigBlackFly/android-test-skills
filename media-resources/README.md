# 内置测试媒体

每次 `TestCase` 初始化时，`t.reset_sdcard_files()` 自动将素材推送到 `/sdcard/media-resources/`，共 6 个文件、4,092,580 字节（约 4.1 MB）。本说明文件不推送。

| 文件 | 格式/尺寸 | 时长/音频 |
|---|---|---|
| [images/landscape_01.jpg](images/landscape_01.jpg) | JPG，700×437 | — |
| [images/landscape_02.jpg](images/landscape_02.jpg) | JPG，800×466 | — |
| [images/portrait_01.png](images/portrait_01.png) | PNG，291×349 | — |
| [images/portrait_02.png](images/portrait_02.png) | PNG，283×450 | — |
| [videos/sample_720p.mp4](videos/sample_720p.mp4) | MP4/H.264，1280×720 | 约 5 秒，无音轨 |
| [videos/sample_small.mp4](videos/sample_small.mp4) | MP4/H.264，176×144 | 5 秒，AAC 音轨 |

素材用于浏览、播放、编辑、删除和分享等通用测试。需要课程表、OCR 特定文字等业务内容的用例，应另行准备匹配的素材，不能假设这些通用图片满足业务前置条件。文件名供用例稳定引用，变更或删除时同步修改相关用例。
