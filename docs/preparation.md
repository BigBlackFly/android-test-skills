# 测试环境准备

每次 `TestCase` 初始化必须在测试步骤前依次完成：

1. 初始化设备 sdcard：清理用户文件并预置测试图片、视频。
2. 接收 Agent 确认并传入的目标包名。
3. 清空目标 App 数据。
4. 批量授予目标 App 权限。

顺序固定；未传入有效包名时，仅跳过第 3、4 项。正式用例、辅助脚本、套件执行及重跑均适用；准备步骤不占用 `--stop-after` 额度。用例无需重复调用准备接口。

文件清理是指清理用户使用设备过程中产生的文件，包括图片、视频、下载文件及测试残留。

| 功能 | 接口 |
|---|---|
| 清理 App 数据 | 初始化自动执行；后续调用 `t.pm_clear(pkg)` 成功后也会重新授权 |
| 批量授权 | 清数据后自动调用 `t.grant_permissions(pkg)` |
| 清理上述文件，并预置测试图片和视频 | `t.reset_sdcard_files()`，用例初始化时自动执行 |
| 仅清理上述文件，不预置测试资源 | `t.clear_sdcard_files()`，仅用于明确要求测试开始前设备中没有图片或视频文件的用例 |

用例 `run()` 示例：

```python
PKG = "com.zui.gallery"  # Agent 根据用例内容确定应用，查表后填写包名
t = TestCase("图库测试", target_package=PKG, user_input=USER_INPUT)
t.step("启动图库")
t.launch_app(t.target_package)
```

测试用例明确要求“测试开始前，设备中不能有任何图片或视频文件”时，在 `TestCase` 初始化后、启动 App 前调用 `t.clear_sdcard_files()`。

## 职责边界

[app_packages.py](../framework/preparation/app_packages.py) 仅提供 `APP_NAME_PACKAGE_MAP` 常量表。Agent 根据测试用例判断待测应用，查询对应包名，并将结果写入 `TestCase(..., target_package="com.zui.gallery")`。

框架只接收包名并校验格式，不匹配应用名称，不从用例名称、描述、目录或前台应用推断目标。`target_package` 不接受应用名称或 Activity 组件。Agent 无法确定应用或查不到包名时，传 `None` 或省略参数。

未提供有效包名时，记录 INFO，跳过 App 数据清理与权限授予，继续执行测试；`target_package` 和 `permissions_granted` 保持 `None`。文件准备失败仍中止。已确定包名后，清空 App 数据失败记 FAIL 并中止；授权失败仅记 WARN，继续测试。

## 批量授权

`t.grant_permissions(pkg)` 作用于当前设备、当前 Android 用户的目标 App，处理运行时权限、已映射的特殊权限及 AppOps。

- **返回值**：成功为 `True`；失败为 `False`，记录 `WARN`，不终止用例。
- **AppOps**：查询结果中的 `allow`、`ignore` 不参与补全；特殊权限映射独立执行，可覆盖 `ignore`。
- **未覆盖项**：未映射权限及保留的 `ignore` 项记入动作详情。
- **适用条件**：设备支持批量授权。权限行为测试在自动准备完成后，按用例要求撤销权限或调整授权状态。

App 引导、登录和页面导航由 `_flow.py` 处理。文件清理边界见 [文件清理与媒体预置](reset-sdcard-files.md)。

## 真机验收

```text
python scripts/check_preparation_device.py --device SERIAL --package com.zui.gallery
```

命令会实际清理设备使用过程中产生的用户文件、预置测试图片和视频，并清除目标 App 数据后授权。必须指定测试设备及目标包名；结束时保留预置资源。

验收覆盖准备顺序、显式包名传递、真实 App 历史数据清理、文件与权限状态、再次清数据后恢复权限，以及未传包名、仅有目录信息或误传应用名称时跳过 App 准备并继续执行，以及清数据失败和授权失败的处理。结果保存到 `storage/preparation-device/<时间>/result.json`，验收结论为 PASS、WARN 或 FAIL；目标 App 部分授权未生效时记 WARN，继续检查实际权限及后续执行。退出码为 0 表示 PASS/WARN，1 表示 FAIL。对不存在的包进行授权是预期失败场景，以 `result.json` 区分预期告警和真实授权问题。

该脚本直接连接真实设备。也可以通过专项测试调用：

```powershell
$env:DSH_TEST_DEVICE = 'SERIAL'
$env:DSH_TEST_PACKAGE = 'com.zui.gallery'
python -m unittest discover -s tests -p test_case_preparation.py -v
```

未指定设备或设备不可用时，专项测试明确跳过，不使用模拟 ADB。本地素材校验和权限文本解析可独立执行，不操作设备。
