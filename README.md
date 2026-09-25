# infraro-core

组织级演进引擎：自进化/审计/门禁体系的共享基础设施。

## 定位

infraro-core 是从 memory-core 抽离的组织级共享引擎，提供：

- **四层体系**（Evolution Core / Governance / Rule Packs / Delivery）：Evolution Core 核心能力（scanner、utils、adapters、heartbeat、self-audit、version-sync）+ Governance 治理判定（`governance.py` + `substrate/gates/`）+ Rule Packs 规则包（memory pack：daily-audit、layout-audit、hygiene、error-patterns 等）+ Delivery 交付编排（workflows / actions / shell / webhook 脚本 / droid-review 分片发布 / 锚点助手与 anchor_gate 等寄居件）。权威归属表与路径清单见 [`docs/architecture.md` §1.1](docs/architecture.md)
- **webhook 脚本**：manifest + trigger 家族（生产同步源）
- **CI/CD**：reusable workflows + composite actions
- **CLI**：infra-cli 统一入口
- **发版公告链路**：引擎发版时自动广播升级公告，消费仓自动接单开 pin-bump PR（详见[发版公告与下游自动接单](#发版公告与下游自动接单)）

## 旧世界引擎层下线状态

- org 旧引擎仓（hdot123-org / infra-core，运行时拼接构造的冻结仓）已于 2026-09-18 正式下线：archived=true，18/19 workflow 已停用（仅 Dependency Graph 内置件保留，GitHub 不可禁用）。
- 全部能力已迁移到新世界双仓：引擎仓 hdot123/infraro-core + 声明仓 hdot123/infraro。
- 交付标准详见声明仓 docs/delivery-standard.md（三层交付模型 + 机械验收门）。
- 终局处置记录见 substrate/infra-core-termination-record.md。

## 安装

要求 Python 3.12（`requires-python = "==3.12.*"` 锁定）。

### 获取 Python 3.12

```bash
# 方式一：Homebrew
brew install python@3.12

# 方式二：uv（推荐的 Python 版本管理器）
uv python install 3.12
```

### 安装 infraro-core

```bash
# 从源码安装（开发模式）
pip install -e .

# 从 GitHub 安装（公开仓库，免认证；v0.18.7 为当前已发布 tag）
pip install git+https://github.com/hdot123/infraro-core.git@v0.18.7

# 安装开发依赖
pip install -e ".[dev]"
```

## 使用

### CLI 命令

```bash
# 查看帮助
infra-cli --help

# 扫描仓库（只读模式，不创建 issue）
infra-cli scan --report-only --repo-root /path/to/repo

# 审计（尚未实现，cli.py 当前固定 return 1）
infra-cli audit --target /path/to/project

# 版本同步
infra-cli version-sweep --target /path/to/project

# venv 环境工具组（创建独立 venv 并安装依赖）
infra-cli venv create --path .venv --extras dev
```

独立审计入口（随包安装提供）：`infra-self-audit`、`infra-daily-audit`、`infra-layout-audit`、`infra-hygiene-audit`、`infra-error-patterns`。

### 规则包

消费仓通过 `.evolution/config.yml` 声明使用的规则包：

```yaml
rule_packs:
  - pack: memory

audit_tools:
  - name: consistency_check
    command: "memory-consistency-check --json"
    output_format: json
```

### 消费仓接入

新仓库接入引擎只需三件事：复制 thin-caller workflow 模板、声明 `.evolution/config.yml`、配置 secrets，引擎版本由 workflow 引用（SHA 级真源 job.workflow_sha）决定，经 pip install git+ 直接交付，非 Python 仓通过条件安装守卫自动跳过。详见[消费仓接入指南](docs/onboarding/consumer-onboarding.md)，thin-caller 模板位于 `docs/onboarding/templates/`。


## 发版公告与下游自动接单

infraro-core 发版时，通过两条触发面（推送 + 轮询）自动广播升级公告，消费仓（`engineConsumer: true`）自动接单开 pin-bump PR，CI → auto-merge 闭环。

**链路要素**：
- **推送触发面**：`release-announce.yml` workflow（on: release published）POST 到 Mac 侧 webhook → `trigger-release.sh` 派发 droid session
- **轮询触发面**（默认）：`poll-releases.sh` + launchd 定时轮询 GitHub Releases API，发现新 release 后调用 `trigger-release.sh`
- **接单 skill**：`release-gateway` skill 指导 droid 升级消费仓 infraro-core pin（pyproject git+、workflow @tag、测试断言、文档等）
- **幂等保证**：per-tag 锁文件，重复触发零副作用
- **双触发面共存**：推送 + 轮询经幂等锁天然共存，推送面可休眠（secret 缺失时优雅跳过）

**消费仓接入**：在 `~/.factory/config/repositories.yml` 中添加 `engineConsumer: true` 标记即可自动接收升级公告。

详细架构：[`docs/architecture.md` §7 发版公告链路](docs/architecture.md) § C9 轮询触发面；消费仓接入指南：[`docs/onboarding/consumer-onboarding.md`](docs/onboarding/consumer-onboarding.md)。组织治理规范与「批准即执行」变更 playbook：[`docs/governance/`](docs/governance/)（org-governance-spec.md / change-playbook.md / source-of-truth.md / templates/ / correction-plan.md），供下一个 mission 执行。

## 架构

```
infraro-core/
├── src/infra_core/
│   ├── engine/          # 自进化引擎单源（scanner/utils/adapters/heartbeat/self-audit/version-sync/锚点助手）
│   ├── packs/           # 规则包（memory 等，经 entry points 发现；memory 为平铺模块 + 门面）
│   ├── shell/           # shell 辅助层（auto-merge/branch-cleanup 家族源码）
│   ├── governance.py    # 治理自检（受保护路径修改判定，fail-closed）
│   └── cli.py           # infra-cli 统一入口
├── actions/             # composite actions（auto-merge / branch-cleanup / droid-review-aggregate / governance-check）
├── .github/workflows/   # reusable workflows + CI/QA 门禁
├── webhook-scripts/     # webhook 脚本（生产同步源）
└── tests/               # 测试套件
```

## 开发

### 版本对齐说明

CI 通过 `runner-tools.toml` 固定工具版本（见 `[tools]` 段）。本地开发建议与 CI 对齐：

```bash
# ruff：CI 钉 0.16.3，本地建议安装同版本
pip install ruff==0.16.3

# actionlint / shellcheck：CI 使用宿主二进制，本地需单独安装
brew install actionlint shellcheck
```

版本不一致可能导致本地通过但 CI 失败（或反之），以 CI 为准。

### 本地开发流程

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check .
ruff format --check .

# GitHub Actions 检查
actionlint
```

## 治理自检 dry-run

governance 门禁（workflow `Evolution Governance` + composite action `actions/governance-check`）的判定核心可本地模拟验证（fail-closed、路径感知）：

```bash
# 非 owner 修改受保护路径 → 退出码 1（拒绝）
python -m infra_core.governance --author someone-else --files .evolution/config.yml

# 非 owner 修改普通文件 → 退出码 0（放行）
python -m infra_core.governance --author someone-else --files README.md

# owner 修改受保护路径 → 退出码 0（放行）
python -m infra_core.governance --author hdot123 --files .evolution/config.yml
```

## 命名契约

以下字符串构成隐式契约网络，任何一处改动会静默杀死 auto-merge/watchdog：

- workflow 名：`Droid Auto Review`、`Evolution Governance`、`CI`、`QA`
- check 名：`droid-review`（job key）、`Block non-owner governance modifications`（governance job 显示名）
- artifact 前缀：`droid-review-debug-`
- workflow 文件名：`evolution-scan.yml`

详见 [architecture.md](docs/architecture.md)。

## License

MIT

## 防线现状（2026-09 审计防线加固）

本仓库已完成四项审计防线加固，

- **接口门（Gate1）fail-closed**：模板 / tags 不可验证时显式红而非软通过（`substrate/gates/gate1_interface.py:306,327-332`）；
- **heartbeat liveness conclusion 维度**：conclusion ∈ {failure, timed_out, cancelled} 连续 3 次判 stale，streak 告警穿透 self-heal 抑制链（`src/infra_core/engine/evolution_heartbeat.py` streak 判定+穿透）；
- **零红聚合时序**：ci-ok 内零红聚合在 droid-review 轮询之后执行（`.github/workflows/ci.yml` ci-ok step-order + `TestCiOkStepOrder`）；
- **Core↛Delivery 反向 import**：由 AST 契约测试强制（`tests/test_core_delivery_import_contract.py`）。

## CI / 分支保护 / 治理基线（2026-09 现状）

### CI 结构

CI 门由可复用 workflow 组成，聚合为显式 check：

- `quality-gate`：外层聚合门，把 ci / qa / substrate 三路结论收敛为单一 required check
- `ci-ok`：pytest（`-rA` 逐测试结果行）、集成 / e2e / guards / health-check 聚合门（内部已轮询 droid-review）
- `qa-ok`：QA 门聚合（正确处理 schedule-only job 的 skipped 状态）
- `substrate-gate-suite`：substrate 五门聚合（job 显示名即 required context）

release-please 发版链路经 `setup-venv` composite action 使用 uv relock，保证发版 PR 与 tag 的锁文件一致性。

### 分支保护硬化

main 分支 ruleset（active，作用域 `~DEFAULT_BRANCH`）：

- required checks = `quality-gate` + `droid-review` + `substrate-gate-suite`，strict（要求分支最新）
- pull_request：squash-only、0 审批（自动化骨干仓）+ unattributed 变更额外审批、review 线程必须解决
- `non_fast_forward`（禁强推）、`deletion`（禁删分支）、`required_linear_history`；bypass actors 为空——owner 亦无豁免
- fork PR 由 droid-review fail-closed 身份检查拦截（见下文 Fork PR 政策）

### 治理基线（2026-09 账号级全量施工）

- **GITHUB_TOKEN 默认权限 `read`**，且禁止 GITHUB_TOKEN 审批 PR；workflow 级 write 已下放到 job 级
- **allowed actions 收敛为 `selected`**：github-owned + verified 放行，叠加 `hdot123/*` 白名单；本仓额外保留 `googleapis/*`（release-please 链路依赖）
- **secret scanning + push protection 已启用**（公开仓可用；私有仓受计划限制，见治理报告 blocked-by-plan 清单）
- **`production` environment**：发版与公告链路 job 走 required reviewer 审批门（branch main + tag `v*`）
- **CODEOWNERS + Dependabot**：owner 兜底 + 敏感路径；依赖更新覆盖 github-actions / pip（本仓）
- **预算守卫**：`.github/workflows/actions-budget-guard.yml` 每周汇总各仓 runs 分钟数与失败率，超阈值开 automation Issue
- 账号级 27 仓（Tier A/B/C）已按同一基线施工，本仓为参照实现；其余仓为同构副本

### droid-review 自定义模型（go-github + dynamic routing）

BYOM 链路（droid exec AI 评审）已同步至 main @ a17ad26 的最新配置（服役 PR #137 合入）。新链路基于 CF AI Gateway 的 go-github worker + 动态路由，完全脱离旧 NVIDIA Kong 内网。

- **baseUrl**：`https://gh.lumivane.dpdns.org/compat`（CF AI Gateway go-github 网关 + 动态路由）
- **模型**：`dynamic/droid-review`（主模型 deepseek-v4.1-flash@opencode-go，备用 Workers AI），模型 id `custom:DeepSeek-V4.1-Flash-Go`（不含内部拓扑字符串，避免 substrate gate3 泄露检查）
- **认证三要素**：`cf-aig-authorization: Bearer <GO_GITHUB_RUN_TOKEN>` + `Authorization: Bearer <OPENCODE_GO_KEY>` + `x-opencode-session`（上游必需）
- **密钥位置**：1Password vault `sever`（条目「OpenCode go / API Key」对应 OPENCODE_GO_KEY；「Cloudflare AI Gateway / lumivane / 调用凭证」对应 GO_GITHUB_RUN_TOKEN）；仓库/runner-hub secrets 注入，文档零明文密钥
- **手动分片重试**：`droid-review-shards.yml`（workflow_dispatch）接受同两个 secret（`opencode-go-key` / `go-github-run-token`）

## Fork PR 政策（droid-review）

自 v0.15.2 起（PR #250），droid-review 链对 `pull_request_target` 事件做 fail-closed 身份检查：PR head 来自 fork（`head.repo` != 本仓）或 `head.repo` 缺失时，AI 审查直接失败退出，防止 fork 经 AGENTS.md 注入指令操纵审查。同仓分支 PR 不受影响；维护者可通过 `workflow_dispatch` 手动触发审查.

<!-- Variables updated: 2026-09-22 -->
