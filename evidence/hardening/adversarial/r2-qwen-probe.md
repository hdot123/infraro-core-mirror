# R2 Qwen 定点验证报告

**验证员**: Qwen (第二轮)  
**验证日期**: 2026-09-23  
**验证范围**: PR #141 代码争议定点复核（P1/P2/P3）  
**仓库**: ~/infraro-core

---

## P1 接缝锁裁定（D3）

### 问题描述
DS4 指出：若引擎代码把 conclusion 字段从 gh 输出中删掉（如改用非 --json 文本解析），现有测试是否会仍然全绿？

### 代码路径分析

**引擎代码**（`src/infra_core/engine/evolution_heartbeat.py:315-327`）：
```python
proc = subprocess.run(
    [
        "gh", "run", "list",
        *gh_repo_args(),
        "--workflow", workflow,
        "--limit", "5",
        "--json", "status,conclusion,createdAt",  # ← 显式请求 conclusion 字段
    ],
    capture_output=True,
    text=True,
    timeout=30,
)
```

**conclusion 使用处**（`:344`）：
```python
all_failed_streak = len(runs) >= CONSECUTIVE_FAILURE_STALENESS and all(
    run.get("conclusion") == "failure" for run in runs[:CONSECUTIVE_FAILURE_STALENESS]
)
```

### 测试 Mock 分析

**Mock 定义**（`tests/test_evolution_heartbeat.py:30-33`）：
```python
def _recent_run(hours_ago: float, conclusion: str = "success") -> dict:
    ts = (datetime.now(UTC) - timedelta(hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"status": "completed", "conclusion": conclusion, "createdAt": ts}
```

**关键观察**：
1. Mock **总是返回**包含 `conclusion` 字段的 dict，无论引擎代码实际请求什么
2. 引擎代码通过 `run.get("conclusion")` 读取（`:344`, `:358`），mock 数据始终包含该字段
3. **无任何测试断言** subprocess.run 的 `--json` 参数必须包含 `conclusion`

### 接缝漏洞场景

**假设**：引擎代码改为 `--json status,createdAt`（移除 conclusion）

**生产环境行为**：
- `gh run list --json status,createdAt` 返回的 dict 不含 `conclusion` 键
- `run.get("conclusion")` 返回 `None`
- `all_failed_streak` 计算：`None == "failure"` → `False` → 整个维度失效
- 仅依赖 createdAt 年龄判定，连续失败检测能力丧失

**测试环境行为**：
- Mock 仍返回 `{"status": "completed", "conclusion": "failure", ...}`
- `run.get("conclusion")` 返回 `"failure"`
- `all_failed_streak` 正常计算
- `test_scanner_three_consecutive_failures_stale` 等用例**仍然通过**

### 裁定结论

**DS4 发现成立**：测试套件**无法检测**引擎代码从 `--json` 参数中移除 `conclusion` 字段的行为。Mock 与生产 gh 命令之间存在接缝断裂。

**根因**：
- Mock 独立于引擎实际构造的 gh 命令参数
- 无契约测试验证 `--json` 参数完整性
- 测试断言的是"给定 conclusion 字段时的行为"，而非"必须请求 conclusion 字段"

**影响**：
- 若未来重构误删 `--json` 中的 `conclusion`，测试全绿但生产环境丢失连续失败检测能力
- 违反"测试应验证接口契约而非仅验证实现行为"原则

**修复建议**：
1. 添加契约测试：断言 subprocess.run 调用参数包含 `--json` 且值含 `conclusion`
2. 或在 Mock 中根据输入参数动态决定是否返回 conclusion 字段
3. 或添加集成测试（非 Mock）验证 gh 命令实际输出

---

## P2 字面值覆盖裁定（D4）

### 问题描述
1. 若 droid-review job 的 conclusion 为 `timed_out` 或 `cancelled` 持续发生，现行 liveness 是否永远判 alive？
2. Mission spec 要求的判定口径是什么？实现是否严格等于 spec？

### 引擎代码分析

**字面值匹配**（`evolution_heartbeat.py:344`）：
```python
run.get("conclusion") == "failure"  # ← 严格等于 "failure"
```

**GitHub Actions 完整 conclusion 枚举**（官方文档）：
- `success` - 成功完成
- `failure` - 失败
- `cancelled` - 被取消
- `timed_out` - 超时
- `action_required` - 需要人工干预
- `neutral` - 中性退出
- `skipped` - 跳过
- `stale` - 过时（已弃用）

### 场景分析

**场景 1**：droid-review job 连续 3 次 `conclusion=timed_out`
- `run.get("conclusion")` 返回 `"timed_out"`
- `"timed_out" == "failure"` → `False`
- `all_failed_streak` → `False`
- 仅依赖 createdAt 年龄判定
- 若 runs 新鲜（< threshold），判定 `alive=True`

**场景 2**：droid-review job 连续 3 次 `conclusion=cancelled`
- `"cancelled" == "failure"` → `False`
- 同上，判定 `alive=True`

**场景 3**：droid-review job 连续 3 次 `conclusion=action_required`
- `"action_required" == "failure"` → `False`
- 同上，判定 `alive=True`

### 代码库对比

**其他位置的 conclusion 处理**：

1. **auto-merge.yml:115**（action.yml）：
```yaml
select(.conclusion == "failure" or .conclusion == "cancelled" or .conclusion == "timed_out" or .conclusion == "action_required")
```
→ 将 `failure/cancelled/timed_out/action_required` 统一视为失败类

2. **actions-budget-guard.yml:9-10**：
```yaml
# conclusion ∈ {failure, startup_failure}（cancelled 是人工行为，不计失败）
```
→ 区分 `cancelled`（人工）与 `failure/startup_failure`（系统）

3. **quality-gate.yml:127**：
```bash
if [ "$ROW" = "completed/failure" ] || [ "$ROW" = "completed/timed_out" ]
```
→ 将 `failure/timed_out` 统一处理

**观察**：代码库内部对 conclusion 字面值覆盖**不一致**：
- auto-merge 宽泛匹配（含 cancelled/timed_out/action_required）
- budget-guard 窄匹配（仅 failure/startup_failure，排除 cancelled）
- quality-gate 中等匹配（failure + timed_out）
- **heartbeat liveness 最窄**（仅 failure）

### Mission Spec 对比

**Mission.md Feature 2 原文**：
> heartbeat-conclusion-liveness: 连续 3 次 failure→stale；1-2 次失败仍在宽限内 alive

**Spec 口径**：
- 明确使用 "failure" 字面值
- 未提及 `timed_out`/`cancelled` 等其他失败类 conclusion
- "失败" 一词在中文语境可能泛指所有非成功结论，但 spec 未明确定义

**实现与 Spec 对比**：
- 实现严格匹配 spec 字面值：`== "failure"`
- 实现**未扩展**到其他失败类 conclusion
- 从字面合规性看，实现等于 spec

**潜在问题**：
- Spec 可能存在语义模糊："failure" 是指 GitHub Actions 的 `failure` conclusion，还是泛指"失败"？
- 若 spec 意图是"连续 3 次非成功结论 → stale"，则实现**过窄**
- 若 spec 意图是"连续 3 次字面 failure conclusion → stale"，则实现**准确**

### 裁定结论

**D4 问题 1**：**成立**。若 droid-review job 持续 `timed_out` 或 `cancelled`，liveness 判定永远 alive（因 `!= "failure"`）。

**D4 问题 2**：
- Mission spec 字面口径为 "failure"，实现严格匹配 spec
- 但 spec 本身可能存在语义模糊（"failure" 是字面值还是泛指）
- 代码库其他位置对失败类 conclusion 的覆盖宽于 heartbeat liveness
- **建议**：澄清 spec 意图——是否应将 `timed_out`/`action_required` 纳入"失败"范畴（`cancelled` 可能需单独处理，因其常为人工行为）

**风险等级**：
- 若 spec 意图为宽泛"失败"：实现存在盲区，`timed_out` 持续发生时无法检测
- 若 spec 意图为字面 "failure"：实现正确，但需确认该口径是否足够

---

## P3 时间线钉死（D2 辅助）

### 时间戳收集

1. **最后一个代码提交**（`a4d2ae3`）：
```bash
$ git show -s --format='%ci %s' a4d2ae3
2026-09-22 22:15:48 +0800 ci(engine): 零红聚合移序至 droid-review 轮询之后 + step-order 契约锁
```
**时间**：`2026-09-22 22:15:48 +0800`

2. **User-testing synthesis.json mtime**：
```bash
$ stat -f '%Sm' synthesis.json
Sep 22 23:22:15 2026
```
**时间**：`2026-09-22 23:22:15`（未标注时区，推断为本地时间 +0800）

3. **README 提交**（`527a435`）：
```bash
$ git show -s --format='%ci %s' 527a435
2026-09-22 23:28:57 +0800 docs(readme): add 防线现状（2026-09 审计防线加固）
```
**时间**：`2026-09-22 23:28:57 +0800`

### 时间线排序

```
22:15:48  最后一个代码提交（a4d2ae3）
    ↓
23:22:15  User-testing synthesis.json 生成
    ↓
23:28:57  README 提交（527a435）
```

**时间差**：
- 最后代码提交 → synthesis 生成：**1h 06m 27s**
- synthesis 生成 → README 提交：**6m 42s**
- 最后代码提交 → README 提交：**1h 13m 09s**

### 裁定结论

**README 提交时序**：README 提交（23:28:57）发生在 user-testing synthesis 产出（23:22:15）**之后**，时间差约 6 分 42 秒。

**D2 争议验证**：
- 若争议点是"README 是否在 user-testing 之前提交"：**否定**，README 在 synthesis 之后提交
- 若争议点是"代码提交后是否有足够时间进行 user-testing"：**肯定**，最后代码提交到 synthesis 生成有 1h+ 间隔
- 若争议点是"README 提交是否仓促"：从时间差看，synthesis 后 6m42s 提交 README，时间窗口合理

**结论**：时间线顺序正确，README 提交在 user-testing synthesis 产出之后，符合"先验证后文档"的预期流程。

---

## 综合结论

### P1 接缝锁
**DS4 发现成立**。测试套件无法检测引擎代码从 `--json` 参数中移除 `conclusion` 字段的行为，Mock 与生产 gh 命令之间存在接缝断裂。

### P2 字面值覆盖
**D4 问题 1 成立**。`timed_out`/`cancelled` 持续发生时 liveness 永远判 alive（因代码仅匹配 `== "failure"`）。**D4 问题 2 部分成立**：实现严格匹配 spec 字面口径，但 spec 本身存在语义模糊，代码库内部对其他失败类 conclusion 的覆盖宽于 heartbeat liveness，建议澄清 spec 意图。

### P3 时间线
**README 提交时序正确**。README 提交发生在 user-testing synthesis 产出之后（时间差 6m42s），符合预期流程。

### 整体评估
- **P1**：测试设计缺陷，需补强契约测试
- **P2**：实现与 spec 字面合规，但 spec 可能需澄清，且与代码库其他位置的 conclusion 处理口径不一致
- **P3**：无争议，时序正确

**建议行动**：
1. P1：添加契约测试验证 `--json` 参数完整性
2. P2：澄清 mission spec 中 "failure" 的语义（字面值 vs 泛指失败），并考虑是否将 `timed_out` 纳入检测范围
3. P3：无需行动

---

**报告路径**：`~/infraro-core/evidence/hardening/adversarial/r2-qwen-probe.md`
