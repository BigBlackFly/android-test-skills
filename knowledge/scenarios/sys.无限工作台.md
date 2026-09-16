# 场景卡：Lenovo 无限工作台

<!-- 场景卡头部是机器解析区（states.py 用朴素"键: 值 / - 列表"规则读取），
     不要改成散文；机器只认下面这些键。 -->

场景: 无限工作台
判定方法: states.is_vision_mode()
判定命令: adb shell settings get system zui_ov_desktop_mode
判定说明: 1 = 开启，0 = 关闭

<!-- AI 检索用：按触发词检索 knowledge/scenarios/（Windows Select-String / *nix grep -rl） -->
触发词:
- 无限工作台
- 工作台
- vision
- desktop
- zui_ov_desktop_mode

## 进入方式

- 打开通知栏（下拉状态栏）
- 展开快速设置（再下拉一次，或直接点编辑/展开）
- 点击「无限工作台」磁贴
- 首次进入会有功能介绍弹窗 点击 "知道啦" 后才会真正进入

## 退出方式

- 再次进入快速设置，点击「无限工作台」磁贴关闭

## 注意事项

- 判定必须用 `settings get system zui_ov_desktop_mode`，不要用界面文字猜
