# 场景卡：勿扰模式（Zen Mode）

<!-- 场景卡头部是机器解析区（states.py 用朴素"键: 值 / - 列表"规则读取），
     不要改成散文；机器只认下面这些键。 -->

场景: 勿扰模式
判定方法: states.is_dnd_mode()
判定命令: settings get global zen_mode
判定说明: 0=关闭，非0=开启（1=仅允许重要通知，2=完全静音）

<!-- AI 检索用：按触发词检索 knowledge/scenarios/（Windows Select-String / *nix grep -rl） -->
触发词:
- 勿扰模式
- 勿扰
- 免打扰
- dnd
- zen_mode

## 进入方式

- 脚本: `adb shell cmd notification set_dnd on`
- 界面: 下拉快速设置 → 点击「勿扰」磁贴。首次进入有功能介绍弹窗需要点击 "知道啦" 后才会进入。

## 退出方式

- 脚本: `adb shell cmd notification set_dnd off`
- 界面: 下拉快速设置 → 再次点击「勿扰」磁贴

## 注意事项

- 判定用 `settings get global zen_mode`，不要用界面文字猜
- 切换勿扰不能用 `settings put global zen_mode` —— 写得进去但会被 ZenModeController 立刻覆盖，回读恒为 0；必须用 `cmd notification set_dnd`
- 读是准的：zen_mode 会跟 set_dnd 联动（off=0 / on=2）
- 定时规则（如"睡眠"）触发时也计入勿扰，判定前先确认没有定时规则生效
