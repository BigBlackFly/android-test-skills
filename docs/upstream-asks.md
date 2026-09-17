# 与 App 团队的协作请求（上游联动）

> **这是什么**：我们这边（测试基建）需要 App 团队配合的三件事。整理成"要什么 /
> 为什么 / 怎么验证"，便于直接拿去沟通。
>
> **为什么值得提**：我们的自动化质量**有上限**，上限由上游的两个东西决定 ——
> ① 元素标识稳不稳定（rid / testTag）；② 测试时环境可不可控（弹窗、数据、
> profile）。这两件事我们**无法在测试侧解决**，只能绕（绕的成本以"探索期分钟数"
> 和"flaky 率"的形式真实发生）。
>
> **验收物**：App 团队**书面确认 ≥3 个核心链路的 rid 稳定性承诺**（形式不限：
> 邮件/需求单/群消息都可以，只要可追溯）。

---

## 请求 1：核心链路 rid 的稳定性承诺

**要什么**：至少 3 个核心链路（日历的「课程表入口 / 时间选择器 / 导入流程」等）
的 `resource-id` 纳入**回归只读约定** —— 改名/重构需通知测试侧。

**为什么**：rid 是我们唯一"跨版本稳定"的定位依据。定位优先级
（`resource-id > content-desc > text > 坐标`）不是偏好问题，而是**稳定性排序**：

| 定位依据 | 谁控制 | 会因什么而变 |
|---|---|---|
| `resource-id` | **App 团队** | 重构 / 改名（可承诺） |
| `content-desc` | App 团队（无障碍语义） | 无障碍描述调整 |
| `text` | 产品/文案/多语言/灰度 | **随时变** |
| 坐标 | 无 | 换设备 / 换方向 / 换字号 |

我们已经有机制**量化"rid 失效"这件事**（`healing_log.json`）：同一
`(Activity, rid)` 连续降级 ≥3 次即生成知识卡更新提案 —— 那是比版本号比更精确的
漂移信号，可以直接拿这份数据去找对应团队。

**怎么验证**：跑一段时间后看 `healing_log.json` 的累计降级次数（目标：核心链路
rid 的降级次数**趋近 0**）。

**附**：如果 App 是 Compose / 自绘 UI（rid 天然不稳），退而求其次的方案是
`Modifier.testTag`（Compose 的 `testTag` 会映射到 `resource-id`，等价可用）。

---

## 请求 2：测试 profile 可控

**要什么**：可切换的"测试 profile" —— 关掉首次使用引导 / 运营弹窗 / 灰度开关，
并允许固定测试数据（如"空课程表"的稳定初始态）。

**为什么**：现在这些弹窗我们靠**看门狗 + 词表**硬扛（`dismiss_first_use_dialogs`、
`observe_dialogs`）。能扛住，但代价是可测量的：

- **探索期成本**：首次启动的三个弹框（权限说明 / 通知权限 / 课程表上新）顺序固定
  但**文案随版本变** → 每次大版本都要重新核对词表；
- **flaky 来源**：弹窗与看门狗抢点击是竞态（实测"点空"过），属"环境抖动"而非缺陷。
  我们的 FAIL 复跑机制就是为区分这两者而加的（首跑 FAIL、复跑 PASS → 标
  `FLAKY`，不计入阻断）。

**怎么验证**：`FLAKY` 用例数下降；`suite_*_报告.md` 的「结论一致率」节 ≥90%。

---

## 请求 3：配置层 DSL（**独立立项，不在此批范围**）

**要什么**：不是"给我们一个配置接口"，而是**配置本身的版本化与可断言化**：
如果 App 的行为由远端配置驱动，那"配置改了"必须**可被测试侧观测**。

**为什么**：这是当前自动化最大的盲区。我们已经能识别"**版本没变但界面变了**"
（`version_gate.classify` 的 `config` 归因：元素集合指纹变了而 versionCode/
versionName 没变）—— 但我们只能**报告**"疑似服务端/配置变更"，无法进一步定位是
**哪个开关**。

**为什么独立立项**：它需要 App 侧配合定义"配置快照接口"（哪怕只是一个能 dump
当前生效开关集合的 debug 接口）。这属于**产品能力**，不是测试脚本能解决的，
也不该塞进本批改造里假装完成。

**验收物**：一份接口约定（谁提供、什么格式、怎么鉴权），以及测试侧能用它做
"配置指纹"的那一天。在此之前，我们靠**元素集合指纹**做近似归因。

---

## 附：可直接跑的数据报表（拿去沟通用）

以下 SQL 直接对 `~/android-test-skills-data/storage/test_records.db` 执行。
**用例热度**决定"该优先保哪些链路的 rid"——按被用例覆盖的次数排序，比拍脑袋准。

```sql
-- ① 用例覆盖热度：哪些链路最值得保 rid（按库里的用例数排）
SELECT script_path, COUNT(*) AS runs,
       MIN(started_at) AS first_run, MAX(started_at) AS last_run
FROM case_metrics
GROUP BY script_path
ORDER BY runs DESC;

-- ② 稳定性排行：逐用例的结论分布（FAIL 多的优先查）
SELECT c.script_path,
       SUM(r.result = 'PASS')    AS pass_n,
       SUM(r.result = 'FAIL')    AS fail_n,
       SUM(r.result = 'BLOCKED') AS blocked_n,
       SUM(r.result = 'WARN')    AS warn_n
FROM cases c
JOIN steps s ON s.case_id = c.id
JOIN results r ON r.step_id = s.id
WHERE c.script_path IS NOT NULL
GROUP BY c.script_path
ORDER BY fail_n DESC, blocked_n DESC;

-- ③ 性能与合规趋势（墙钟是唯一性能口径，见 case_metrics 表注释）
SELECT script_path, started_at, duration_sec, ocr_calls, derived_clicks,
       dump_snaps, sleep_static_sec, sleep_static_sec_with_flow,
       wait_calls, wait_sec, healing_hits
FROM case_metrics
ORDER BY started_at DESC
LIMIT 50;

-- ④ 方向中途变化的用例（"结论可信"维度，不是性能指标）
SELECT script_path, started_at, rotation_start, rotation_end
FROM case_metrics
WHERE rotation_start IS NOT NULL AND rotation_end IS NOT NULL
  AND rotation_start <> rotation_end;
```

**④ 的读法**：`rotation_start <> rotation_end` = 用例中途屏幕方向变过。实测
178 在横屏下照样 PASS（因为坐标全部现场派生），但"过程稳不稳"必须可见 ——
这份清单回答"到底有几个用例中途方向变过"。**不是**性能指标。
