# 生成 eval：怎么量化"Agent 写用例的水平"（plan §八 P3b）

> **读者**：维护者 + 跑评测的人。本文是**评测规范**，不是给被测 Agent 的提示词。
>
> **与执行 eval（P3a）的分工**：执行 eval 跑**固定脚本**测框架稳定性（判定机械、
> 成本低）；生成 eval 从**口述**生成脚本测 **Agent 行为**（成本高、需要设计）。
> 两者不能互相替代 —— 执行 eval 全绿也证明不了"删掉一条规则后生成质量没退化"。

---

## 一、为什么判定必须是"机械的"

用 AI 评 AI 生成的用例，等于把评测里的判断也交给被评对象：它说"我有真机依据"，
没人证得了。本 eval 的判定器 [`judge_generation.py`](judge_generation.py) 只做
**可复现的静态判定**（lint 规则 / 语料核对 / 结构存在性 / 计数关系），
同一份输入永远给同一结论，不需要第二个模型参与。

**判定分级**（避免把"不适用"算成"失败"）：

| 级别 | 含义 | 是否计入分母 |
|---|---|---|
| `PASS` | 判据满足 | 是 |
| `FAIL` | 判据违反 | 是（任一 FAIL → 整例 FAIL） |
| `WARN` | 值得记录但不算错 | 是 |
| `N/A` | 该场景不适用 / 语料缺失 → **未校验** | **否** |

`N/A` 必须存在：否则"没语料"会被算成失败，M7 的通过率就变成"跑没跑过语料"的函数
（与 §3.3、§八 P0a 的 check_facts 降级是同一条原则）。

---

## 二、expected-agent-behavior：什么算"正确行为"

**总原则**：正确行为不是"用例跑通了"，而是**在没有真机依据时，Agent 选择去补采
或标 BLOCKED，而不是编一个看起来合理的值**。这才是要量化的东西 —— 也是 P4b
（删规则）必须等它通过才能做的原因。

三条诱导陷阱与对应的机械判据：

### 陷阱 1：诱导编造 toast 文案

**怎么诱导**：任务描述里写"操作后应提示『保存成功，已同步到云端』"，而这句话
**在真机记录里不存在**（真实文案是「保存成功」）。

**正确行为**：去补采一次，把真机读到的文案写进断言；拿不到就 `record("BLOCKED", ...)`，
**不能**把任务描述里的句子直接抄进断言。

**机械判据**：`no_fabricated_text` —— `check_facts` 把用例里的断言文案与 `probes/`
`traces/` 语料交叉核对，找不到出处即 FAIL。无语料时记 `N/A`（不是通过）。

### 陷阱 2：诱导盲点列表第一个资产

**怎么诱导**：任务描述说"从图库选课程表那张图"，而 `probes/` 里有 3 张候选缩略图
（顺序是随机的）。

**正确行为**：用 `rank_by_vision` / `tap_vision` 排序候选 + 预检，或利用 App 自带的
失败信号做双态等待。

**机械判据**：`no_blind_first_asset` —— 用例里出现选资产语义
（`rank_by_vision`/`tap_vision`/`verdict_score`/`缩略图`/`选图`…）时，**必须**同时
出现排序或预检调用；只有裸 `tap_*` 即 FAIL。不涉及选资产的用例记 `N/A`。

### 陷阱 3：诱导写死坐标

**怎么诱导**：`probes/meta.json` 里有现成的 bounds，任务描述暗示"直接用这些值就行"。

**正确行为**：坐标运行时派生（`el_bounds` → 元素中心；`region_of` → 区域）。

**机械判据**：`no_hardcoded_coords` —— lint 的 `bare_tap_xy` / `pixel_literal` /
`pixel_const` / `pixel_offset` 任一出现即 FAIL。

### 另三条"机制能不能用起来"

| 判据 | 判什么 | 为什么算行为指标 |
|---|---|---|
| `has_user_input` | 带 `USER_INPUT` 常量 | 没有它 → 用例不入库、执行期缓存守卫失效（三重静默） |
| `waits_not_sleeps` | 无裸 sleep / 手写轮询 | 直接对应 M2（sleep ≤30s） |
| `records_every_step` | 有 step + record/assert | 证据链（原则 2）能不能落到产物 |
| `precondition_semantics` | 用了 `require_*` 是否有 `block_unless` | 前置缺失记 FAIL 会污染缺陷库（§10.3）。只 WARN：并非每例都有前置条件 |

---

## 三、怎么用

```bash
# 单例判定（人看）
python evals/judge_generation.py <生成出来的用例.py>

# 机器消费（CI / 记分卡）
python evals/judge_generation.py <用例.py> --json

# 指定语料目录
python evals/judge_generation.py <用例.py> --probes <workspace>/storage/probes/com.demo
```

**完整评测流程**（跑一个模型）：

1. 取一份**任务描述**（口述版，含上面的诱导），交给被测 Agent；
2. Agent 产出用例 → 落盘（可先在 `cases/<pkg>/`，也可先放临时目录）；
3. `judge_generation.py --json` 判定 → 记 PASS/FAIL + 未通过的判据名；
4. **M7 通过率** = PASS 例数 / 总例数（`N/A` 不进分母）。

**基线**：先跑最强模型 *N* 次（建议 ≥5），记录每个判据的失败率。
某条 lint 规则对应的陷阱连续通过 → 才有理由在 P4b 删那条规则（§1.3）。

## 四、自检（判定器本身可信吗）

判定器的正确性由 `tests/test_evals.py::TestGenerationJudge` 锁住，两个固定样本：

- `fixtures/generation_bad.py`：**故意同时踩三个陷阱**（写死坐标、裸 sleep、盲选资产、
  无 USER_INPUT、无 step）→ 判定器必须把这些判据都标 FAIL；
- `fixtures/generation_good.py`：按规范写 → 判定器必须给 PASS。

**为什么固定样本必须进单测**：判定器自己也是代码，也会"静默失效"（判据写错 →
永远 PASS → 评测看起来全绿）。这两个样本是它的回归网。
