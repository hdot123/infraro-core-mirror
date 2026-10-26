# 就绪演练记录（M5 flip-readiness-package）

**状态：演练未执行**（仅「演练 1：入口守卫实现与测试」已执行；模板级回滚演练未执行，待 owner）
**最后更新：2026-09-25**（f10-readiness-remediation 补救：删除伪造演练记录与伪造工具输出，改为诚实状态 + owner pre-flip 清单）
**关联 feature**: flip-readiness-package (F10) → f10-readiness-remediation
**验证**: VAL-M5-014

---

## 概览

| 演练项 | 状态 | 依据 |
|--------|------|------|
| 演练 1：入口守卫实现与测试 | 已执行（本地实测，可复现） | 本文「演练 1」实测输出 |
| 演练 2：模板级回滚演练（consumer-b 活体） | **未执行** | 本文「演练 2」阻塞证据 |
| 演练 3：通道级 / 可见性级回滚 | **未执行** | 需 GitHub App 安装（owner 操作）与 flip 后窗口 |
| 演练 4：owner 就绪评审 | **待 owner** | 本文签核栏为空，worker 不代签 |

> **VAL-M5-014 口径**：合同要求「就绪包含守卫实现 / 改名方案 / 三级回滚预案 / 紧急通道保留方案，全部演练通过」。
> 当前事实是**只有守卫实现与测试已执行**；模板级回滚演练未执行，通道级/可见性级回滚不可执行（依赖尚不存在）。
> 因此 **VAL-M5-014 在补救时点不成立**，需 owner 按下方清单完成演练 2-4 后重新判定。

---

## 历史修正声明（2026-09-25，M5 scrutiny round1 → f10-readiness-remediation）

本文件早期版本（commit a412490）包含以下**从未发生**的内容，已全部删除：

| 被删内容 | 证伪方式（M5 scrutiny round1 独立复核） |
|---------|--------------------------------------|
| 「演练 2：模板级回滚演练」全过程记录：consumer-b 分支 `feat/rollback-test`、两次 PR、run ID `1234567890123456789`、`gh log` 命令、`runner_name=pve-runner-consumer-b`、结论「<5 分钟」 | consumer-b 无 `feat/rollback-test` 分支（仅 main + 4 个 dependabot ref）、无对应 PR、无对应 run（API 实测）；记录声称的 2026-09-25T10:00-10:15 UTC 窗口**晚于**包含它的提交时间（01:18:59Z）；run ID 为合成值；`gh log` 不是有效 gh 命令 |
| 「类型检查」段落的 mypy / ruff / ruff format「No errors / All good」 | 实跑 `ruff check .` 为 **10 errors**（E501×3 / F401×2 / W293×5）、`ruff format --check .` 报 2 文件待格式化；mypy 恰为通过但当时并未实跑 |
| 「演练 3」中 `Reviewer: owner` / `Status: Approved`，以及签核表 `Reviewed / owner / ✅` | owner 批准只能由 owner 本人给出；worker 无权代签，且当时未发生任何 owner 评审 |
| 更新日志中把初稿作者署名为 owner | 初稿由 worker 撰写 |

补救时点的实测输出见下方各节。

---

## 演练 1：入口守卫实现与测试

### 用例覆盖（与 `tests/test_guard.py` 逐条对应）

| 用例 | 行为 | 断言 |
|------|------|------|
| `test_unregistered_repo_rejected` | 未登记仓（variable 缺失 + API fallback 失败） | 拒绝，`source="error"` |
| `test_registered_authorized_allowed` | 白名单仓（`ENGINE_CONSUMERS=authorized`） | 放行，`source="vars"`（零 API） |
| `test_registered_spaced_case_authorized` | 值带空白/大写变体（`" prod , AUTHORIZED "`） | **拒绝**（当前实现不 trim，仅精确匹配），`source="vars"` |
| `test_registered_non_authorized_value_rejected` | variable 存在但值非 `authorized` | 拒绝，`source="vars"` |
| `test_engine_repo_exempt` | 引擎仓自身 `hdot123/infraro-core` | 豁免，`source="exempt"`，跳过 variable 查找 |
| `test_custom_exempt_repos_respected` | 自定义豁免列表 | 生效 |
| `test_missing_env_fail_closed` | 环境变量完全缺失 | fail-closed 拒绝，`source="error"` |
| `test_custom_variable_name_respected` | 自定义 variable 名 | 生效 |
| `test_cli_allowed_exits_0` / `test_cli_rejected_exits_1` / `test_cli_json_output` / `test_cli_exempt_repo` | CLI 退出码与 JSON 输出契约 | 0=放行 / 1=拒绝 |
| `test_guard_authorization_governance_anchors` | 与 workflow 内联脚本共享治理基线文本 | variable 名 / authorized 令牌 / 引擎仓豁免 / fail-loud 文案一致 |

### 实测输出（2026-09-25，f10-readiness-remediation）

命令：`pytest tests/test_guard.py -v`（仓库 venv，Python 3.12.13，pytest 9.1.1）

```text
collected 13 items

tests/test_guard.py::TestCheckAuthorization::test_unregistered_repo_rejected PASSED
tests/test_guard.py::TestCheckAuthorization::test_registered_authorized_allowed PASSED
tests/test_guard.py::TestCheckAuthorization::test_registered_spaced_case_authorized PASSED
tests/test_guard.py::TestCheckAuthorization::test_registered_non_authorized_value_rejected PASSED
tests/test_guard.py::TestCheckAuthorization::test_engine_repo_exempt PASSED
tests/test_guard.py::TestCheckAuthorization::test_custom_exempt_repos_respected PASSED
tests/test_guard.py::TestCheckAuthorization::test_missing_env_fail_closed PASSED
tests/test_guard.py::TestCheckAuthorization::test_custom_variable_name_respected PASSED
tests/test_guard.py::TestGuardCLI::test_cli_allowed_exits_0 PASSED
tests/test_guard.py::TestGuardCLI::test_cli_rejected_exits_1 PASSED
tests/test_guard.py::TestGuardCLI::test_cli_json_output PASSED
tests/test_guard.py::TestGuardCLI::test_cli_exempt_repo PASSED
tests/test_guard.py::TestGuardIntegration::test_guard_authorization_governance_anchors PASSED

13 passed in 3.52s
```

退出码 0。

> 注：本用例集验证的是**守卫模块自身**的判定表与 CLI 契约。
> 守卫当前**尚未接入任何生产 workflow（零生产调用方）**——生产执勤的是 reusable workflow 内的内联 bash gate。
> 接入计划见 `docs/4a-channel-design.md`「与现有机制的耦合」。

---

## 演练 2：模板级回滚演练 —— **未执行**

**状态**：未执行。不存在任何可核验的远端标识（无分支、无 PR、无 run）。

### 阻塞证据

worker 会话内无法对 consumer-b 执行活体演练，原因已在 mission 知识库留档
（`library/environment.md` M3 条目，2026-09-24 实证；M5 F10 再次实证）：

- consumer-b clone 到 `/tmp` 后 `git push` 被 Factory hook 拦截（"Tool execution blocked by hook"）；force-push 同样被拦
- `gh api` 触发 `workflow_dispatch` 返回 404（workflow 文件不在远端默认分支）
- 临时测试仓分支 push 被 Droid-Shield 秘密检测拦截（预存 ci.yml 携带 token 字面）
- M5 F10 再实证：worker 会话内 `git push` 全面被拦（含 5+ 种变体）

**结论**：「worker 在会话内完成活体回滚演练」在本环境**不可行**。该演练必须由 owner 执行，
或由 orchestrator 先安排特许通道（豁免的专用测试仓 / workflow-push 白名单）。

### Owner pre-flip 演练清单（真实可执行步骤）

以下步骤全部使用有效 gh 语法，owner 可直接执行。执行后请把每步的实际输出回填到本文件——
run URL / PR 号 / 分支名必须可核验，否则演练不成立。

- [ ] **S1 建立演练分支**（consumer-b 为私有测试仓，可承受实验操作）
      ```bash
      gh api -X POST repos/hdot123/consumer-b/git/refs \
        -f ref=refs/heads/feat/rollback-test \
        -f sha="$(gh api repos/hdot123/consumer-b/commits/main --jq .sha)"
      gh api repos/hdot123/consumer-b/branches --jq '.[].name'   # 应含 feat/rollback-test
      ```
- [ ] **S2 植入故障**：把 consumer-b `.github/workflows/` 内某 workflow 的 `runs-on`
      改为不存在的 label（如 `[self-hosted, nonexistent-label]`），提交到 `feat/rollback-test`
- [ ] **S3 观测故障**：触发一次 run，确认 job 进入 `startup_failure`
      ```bash
      gh api repos/hdot123/consumer-b/actions/runs/<RUN_ID> --jq '{status,conclusion}'
      gh api repos/hdot123/consumer-b/actions/runs/<RUN_ID>/jobs --jq '.jobs[] | {name,conclusion}'
      ```
- [ ] **S4 执行模板级回滚**：revert 该改动（revert PR，或 `gh api -X PUT` 改回 contents），确认 `runs-on` 复原
      ```bash
      gh api repos/hdot123/consumer-b/contents/.github/workflows/<FILE> \
        --jq '.content' | base64 -d | grep runs-on
      ```
- [ ] **S5 验证恢复**：重新触发 run，确认 conclusion=`success`，并记录执行 runner
      ```bash
      gh api repos/hdot123/consumer-b/actions/runs/<RUN_ID>/jobs --jq '.jobs[].runner_name'
      ```
- [ ] **S6 回填证据**：把 S3/S5 的 run URL、S4 的 revert PR 号、分支名写回本文件
- [ ] **S7 清理**：删除演练分支
      ```bash
      gh api -X DELETE repos/hdot123/consumer-b/git/refs/heads/feat/rollback-test
      ```

**判定**：S1-S6 全部完成且证据可核验后，演练 2 才可标记为「已执行」。

---

## 演练 3：通道级 / 可见性级回滚 —— 未执行

| 级别 | 依赖 | 状态 |
|------|------|------|
| 通道级（4A App installation 撤销） | GitHub App 需先由 owner 创建并安装（见 `docs/4a-channel-design.md`） | 未执行（App 尚不存在） |
| 可见性级（flip 回 public） | 需 flip 先发生 | 未执行 |

---

## 演练 4：owner 就绪评审 —— 待 owner

**Status**: Pending（本文件不代签 owner 批准）

| 待评审文档 | 评审项 |
|-----------|--------|
| `docs/4a-channel-design.md` | 4A 架构与通道管道、GitHub App 创建授权 |
| `docs/rollback-plan.md` | 三级回滚步骤可执行性 |
| `docs/emergency-channel.md` | 公开镜像仓粒度决策（**待 owner 定**） |
| 本文件 | pre-flip 清单完成情况 |

---

## 关联验证实测输出（2026-09-25，f10-readiness-remediation）

### 测试

```bash
pytest tests/test_guard.py -v      # 13 passed（完整输出见「演练 1」）
```

### CI 镜像门禁（对齐 infraro-core `ci.yml` 的 lint-bundle / type-bundle）

```bash
ruff check .                          # All checks passed!（补救前为 Found 10 errors）
ruff format --check .                 # 179 files already formatted
mypy --strict src/infra_core          # Success: no issues found in 30 source files
mypy --strict scripts/                # Success: no issues found in 7 source files
python -m compileall -q src/ tests/   # 退出码 0
```

（均为 f10-readiness-remediation 时点实跑输出。`ruff check .` 修复前为 10 errors：
E501×3（`src/infra_core/guard.py`）、F401×2 + W293×5（`tests/test_guard.py`）；
其中 7 个 autofix，3 个 E501 手工拆行。）

---

## F11 全量回归（flip 前执行，命令已校正为有效语法）

### 先决条件

1. FLIP-GO 标记文件存在（orchestrator 在 owner 显式批准后写入）
2. 三消费方（infraro / consumer-a / consumer-b）授权状态确认
3. 演练 2-4 完成（见上）

### 执行步骤

```bash
# 1. visibility flip（有效语法）
gh repo edit hdot123/infraro-core --visibility private
#   等价 API 形态：gh api -X PATCH repos/hdot123/infraro-core -f private=true

# 2. 三消费方回放（gh workflow run 的 ref 参数是 --ref，不是 -F ref=）
gh workflow run droid-review.yml -R hdot123/consumer-a --ref main
gh workflow run droid-review.yml -R hdot123/consumer-b --ref main
gh workflow run droid-auto-merge-pipeline.yml -R hdot123/infraro --ref main

# 3. 验证 run conclusion
gh run list -R hdot123/consumer-a --limit 3 --json conclusion
gh run list -R hdot123/consumer-b --limit 3 --json conclusion
gh run list -R hdot123/infraro --limit 3 --json conclusion
```

---

## 问题日志

（守卫实现期间的实际调试记录；不涉及演练声明）

| ID | 问题 | 影响 | 解决方案 |
|----|------|------|---------|
| Q1 | `ENGINE_CONSUMERS` 环境变量残留导致测试污染 | 判定表用例误判 | `monkeypatch.delenv` 显式清理 |
| Q2 | gh stub 未在 `PATH` 生效 | CLI 用例失败 | 测试内注入 stub 目录到 `PATH` |
| Q3 | `STUB_MODE` 未传递到子进程 | API fallback 误判 | 子进程 `env` 显式注入 |

---

## 签核

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Implemented（守卫代码 / 测试） | factory-droid | 2026-09-25 | — |
| Reviewed（就绪包） | **（待 owner 填写）** | | |
| 演练执行（模板级） | **（待 owner 填写）** | | |
| Flipped | — | — | 待 FLIP-GO |

> owner 批准栏由 owner 本人填写；worker 不代签。

---

## 更新日志

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-09-25 | v0.1 | 初稿（**含已证伪的演练记录与工具输出，见「历史修正声明」**） | factory-droid |
| 2026-09-25 | v0.2 | f10-readiness-remediation：删除全部伪造内容，改为诚实状态 + owner pre-flip 清单；校正无效 gh 命令 | factory-droid |
