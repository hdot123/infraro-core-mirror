# 受控通道对比研究（修正版）

**生成日期**: 2026-09-24  
**Feature ID**: `channel-research-poc-redo`  
**Milestone**: M3-channel-research  
**状态**: 研究完成（PoC 未执行，待验证）  
**依据**: 官方 GitHub 文档分析 + M3 scrutiny 验证

---

## 执行摘要

本研究对比分析 4 种受控访问 infraro-core 引擎的候选通道，按 8 条标准逐一评估。关键事实：

- PyPI `infra-core` 名已被他人占用，`infraro-core` 可用（引擎包现名 infra-core）
- 公开仓调私有 reusable workflow 受到 GitHub 平台限制（公开仓只能访问公开 workflow）
- GitHub App token 与 GITHUB_TOKEN 权限模型不同，App token 需显式安装授权

**研究方法**：
1. 文档研究：查阅 GitHub 官方文档（OIDC、trusted publishing、reusable workflow 访问规则、GitHub Packages 权限）
2. **PoC 验证：未执行/待验证** - 由于系统限制（Droid-Shield 秘密检测、远程仓库推送阻断），OIDC token 交换与 Fork PR 权限边界的实操验证暂无法执行。平台能力验证依赖官方文档与现有用户报告。
3. 8 标准矩阵：逐候选逐标准填「满足/不满足/部分+论证」

> **「待所有者拍板」**：本研究产出评估矩阵与建议排序，**不构成技术方案选定**。最终通道选择需所有者基于业务需求（如公开访问范围、安全边际、维护成本）裁定。无显式拍板，则不执行任何通道切换。

> **PoC 状态说明**：原始研究文档的 PoC 段（`runs/123`/`runs/456`/`runs/789`）经 scrutiny 验证为虚构（`channel-research-poc-2026-09-24.md:128-196`），已移除。修正版文档只保留文档研究得出的矩阵结论，PoC 相关断言需等待实际执行验证。

---

## 4 候选通道概述

| 候选 | 架构简述 | 适用场景 | 主要依赖 |
|------|---------|---------|---------|
| **① PyPI OIDC trusted publishing** | 公开仓 workflow 通过 OIDC 向 PyPI 申请短时 API token → 匿名拉取引擎包 | 匿名公众可下载的开源引擎包 | PyPI trusted publishing + GitHub OIDC |
| **② 私有 reusable workflow + GitHub App token** | 公开仓 workflow 调用私有仓 reusable workflow → App token 认证到 infraro-core | 公开仓需私有限制访问（Fork PR 无法触发） | reusable workflow十字仓库访问 + GitHub App |
| **③ GitHub Packages 私有包** | infraro-core 发布到 GitHub Packages (private) → 公开仓通过 App token 拉取 | 仅 GitHub 生态用户，寻求单平台统一包管理 | GitHub Packages + App token |
| **④ 4A：公开仓→hosted runner→OIDC身份证明→私有受控入口→scheduler白名单校验→GitHub App短时token→infraro-core** | 公开仓触发调度 → OIDC证明运行方身份 → 自建轻量入口服务校验 scheduler 白名单 + repo_property claim → Issuer 以 App 身份签发短时 token → 访问 infraro-core | 零长期 PAT、细粒度白名单控制、可撤销单仓、Fork PR 无效 | OIDC + self-hosted runner 非必需 + scheduler 白名单 + GitHub App |

> **注**：③④ 中提及的「App token」非 GitHub App 本身，而是入口服务以 App 身份签发的短时访问 token（类似 `id-token: write` 的 OIDC token 但由自建 Issuer 发行）。目标是「无长期 PAT」「可撤销单仓」「Fork PR 无私有权限」。

---

## 8 标准矩阵

### 标准说明

| 标准编号 | 标准描述 | 检查要点 |
|---------|---------|---------|
| S1 | 公开仓可访问 | 公开仓（public repo）无需额外授权即可触发接入通道 |
| S2 | 无长期 PAT | 通道中不使用长期有效的 personal access token（≤24 小时 token 或 OIDC 临时 token 可接受） |
| S3 | 未登记仓不可用 | 未在 scheduler 白名单登记的仓（或等效授权机制）无法获得引擎访问权 |
| S4 | Fork PR 无私有权限 | Fork PR 触发的 workflow 无法获得私有资源（如 reusable workflow、App token、私有包） |
| S5 | 引擎实现不泄露 | 引擎源码不因通道机制而暴露给未授权方（如匿名拉取不会附带源码） |
| S6 | 可撤销单仓 | 单仓授权可独立撤销（不影响其他仓），如移出白名单、撤销 App 安装 |
| S7 | 不依赖 self-hosted | 不强制要求自建 runner（hosted runner 可用则不强制部署 self-hosted） |
| S8 | 接入协议长期稳定 | 使用 GitHub 官方长期支持协议（如 OIDC、GITHUB_TOKEN），非实验性功能 |

---

### 矩阵评估（文档研究，依赖 GitHub 官方文档）

#### 候选①：PyPI OIDC trusted publishing（匿名制品）

| 标准 | 评估 | 论证 |
|------|------|------|
| S1 公开仓可访问 | **满足** | OIDC trusted publishing 无仓库可见性要求；公开仓 workflow 可直接触发发布（[Configuring OpenID Connect in PyPI](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-pypi)） |
| S2 无长期 PAT | **满足** | 使用短时 OIDC token 换取 PyPI API token，无长期 PAT 存储 |
| S3 未登记仓不可用 | **部分满足** | PyPI trusted publishing 使用 `owner/repo/workflow` 三元组授权（[PyPI trusted publishing](https://docs.pypi.org/manage-project/publishing/)），未登记仓无法发布；**但匿名拉取无访问控制**（见 S4 论证） |
| S4 Fork PR 无私有权限 | **部分不满足** | Fork PR workflow 可触发 trusted publishing（只要 Fork 属于同一 owner），**但匿名拉取任何人都可进行**（无 Fork Owned 相关限制） |
| S5 引擎实现不泄露 | **部分不满足** | PyPI 匿名可拉取包意味着任何人可下载 wheel，**源码可能通过逆向或包内注释泄露**；若包为纯编译产物则满足，但定义不明 |
| S6 可撤销单仓 | **满足** | PyPI 允许单独撤销某 `owner/repo/workflow` 的 trusted publisher |
| S7 不依赖 self-hosted | **满足** | 仅依赖 GitHub-hosted runner |
| S8 接入协议长期稳定 | **满足** | OIDC + trusted publishing 为 GitHub 官方长期支持功能 |

**候选①小结**：zerro-PAT 设计简洁，但「匿名可拉取」与「引擎实现不泄露」存在语义张力；若引擎需保持源码封闭，此候选不适用。

**观察**：S3/S4/S5 验证依据 PyPI 文档与 GitHub OIDC 文档交叉引用；**PoC 未执行/待验证**（无实际 release 运行可复核）。

---

#### 候选②：私有 reusable workflow + GitHub App token

| 标准 | 评估 | 论证 |
|------|------|------|
| S1 公开仓可访问 | **部分不满足** | GitHub 允许公开仓调用私有 reusable workflow **但需要显式授权**；公开仓默认不可见私有Reusable（[Reusing workflow configurations](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflows)） |
| S2 无长期 PAT | **满足** | 使用 GITHUB_TOKEN 或短时 App token（由 workflow 调用侧传入，非长期存储） |
| S3 未登记仓不可用 | **部分满足** | 未在 App installation 白名单中的仓无法触发 workflow；**但 App 安装白名单与 scheduler 白名单需二元维护** |
| S4 Fork PR 无私有权限 | **满足** | Fork PR workflow 默认无权访问父仓 secret，需显式 `secrets: inherit`（[Reusing workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)），**默认不inherit则无法获得 App token** |
| S5 引擎实现不泄露 | **满足** | 引擎调用通过受控入口（如 `/engine` endpoint），不依赖公开源码 |
| S6 可撤销单仓 | **满足** | GitHub App installation 可单独移除某仓（Settings → Installed GitHub Apps） |
| S7 不依赖 self-hosted | **满足** | host workflow 可跑在 hosted runner |
| S8 接入协议长期稳定 | **满足** | reusable workflow 与 App token 为 GitHub 官方长期支持功能 |

**候选②小结**：Fork PR 权限控制稳健，但 S1「公开仓可访问」需显式 App installation 授权，与「匿名公开访问」语义不完全匹配；需维护 App 白名单+scheduler 白名单双轨。

**观察**：S1/S4 验证依据 reusable workflow 官方文档；**PoC 未执行/待验证**（无实际 workflow_dispatch 运行可复核）。

---

#### 候选③：GitHub Packages 私有包

| 标准 | 评估 | 论证 |
|------|------|------|
| S1 公开仓可访问 | **部分不满足** | 私有包默认不可访问；需配置 GitHub Actions access（[About permissions for GitHub Packages](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages)），或使用 App token |
| S2 无长期 PAT | **部分满足** | 使用 `GITHUB_TOKEN` 时满足（但仅限本仓）；**跨仓私有包需 personal access token**（classic），存在长期 PAT 风险 |
| S3 未登记仓不可用 | **满足** | GitHub Packages 权限模型支持 granular read/write/admin（需 explicit permission grant），未授权仓无法 pull |
| S4 Fork PR 无私有权限 | **满足** | Fork PR 默认无权访问父仓 GITHUB_TOKEN 以外的 secret；App token 同理（需显式 installation） |
| S5 引擎实现不泄露 | **满足** | 包为二进制 wheel，不暴露源码 |
| S6 可撤销单仓 | **满足** | 移除 package permission 即可撤销单仓访问权 |
| S7 不依赖 self-hosted | **满足** | 仅依赖 hosted runner |
| S8 接入协议长期稳定 | **部分不满足** | GitHub Packages 为长期支持功能，但**跨仓访问需 personal access token (classic)**（不是长期推荐方案，[Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)） |

**候选③小结**：GITHUB_TOKEN 本仓访问无长期 PAT，但跨仓访问需 token，与「无长期 PAT」目标部分冲突；适合「仅内部 org 内仓访问」场景。

**观察**：S2/S8 验证依据 GitHub Packages 官方文档；**PoC 未执行/待验证**（无实际 fetch 运行可复核）。

---

#### 候选④：4A 链（公开仓→hosted runner→OIDC身份证明→私有受控入口→scheduler白名单校验→GitHub App短时token→infraro-core）

| 标准 | 评估 | 论证 |
|------|------|------|
| S1 公开仓可访问 | **满足** | 公开仓 workflow 可正常触发（待 scheduler 白名单校验） |
| S2 无长期 PAT | **满足** | 全链路使用短时 OIDC token + 入口服务以 App 身份签发的短时 token（≤24 小时） |
| S3 未登记仓不可用 | **满足** | 入口服务校验 `repo_property_*` claim + scheduler 白名单（`ENGINE_CONSUMERS` 变量铺设）；未登记仓被拒 |
| S4 Fork PR 无私有权限 | **满足** | Fork PR workflow 的 OIDC token `sub` claim 为 `repo:<fork-owner>/<fork-repo>:...`，入口服务查 scheduler 白名单失败；**GITHUB_TOKEN 在 Fork PR 中为只读权限（[About secrets](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-secrets)）** |
| S5 引擎实现不泄露 | **满足** | 引擎调用路径：入口服务 → `/engine` endpoint → infraro-core；引擎实现不暴露给公开仓 |
| S6 可撤销单仓 | **满足** | 撤销方式 1：从 scheduler 白名单移除；撤销方式 2：入口服务撤销某仓的 App token 签发权限 |
| S7 不依赖 self-hosted | **满足** | 第一跳可使用 GitHub-hosted runner（公开仓必用），only scheduler 侧需 self-hosted |
| S8 接入协议长期稳定 | **满足** | OIDC 与 GitHub Actions 为长期支持协议；scheduler 白名单为已落地机制（whitelist-auto.yml） |

**候选④小结**：8 标准全部满足；架构复杂度较高（需自建轻量入口服务 + scheduler 白名单集成），但零长期 PAT、Fork PR 无效、引擎实现不泄露、可撤销单仓全部达成。

**观察**：S2/S4 验证依据 GitHub OIDC 文档与 secrets 官方说明；**PoC 未执行/待验证**（需在实际环境执行 OIDC token 交换与白名单校验）。

---

## 排序依据（去除 PoC 依赖）

### 决策框架

排序仅依赖**文档研究得出的 8 标准矩阵结论**，不引用任何 PoC 验证结果。核心权衡点：

1. **安全性维度**（权重 40%）：S2-S5、S8（零长期 PAT、引擎不泄露、协议稳定）
2. **可行性维度**（权重 30%）：S1-S3、S7（公开可访问、未登记不可用、不依赖 self-hosted）
3. **可维护性维度**（权重 30%）：S6（单仓撤销）、标准化程度

### 候选④（4A 链）- **优先推荐**

- **优势**：8 标准全部满足；Fork PR 无效；引擎实现不泄露；零长期 PAT；scheduler 白名单已落地可复用
- **劣势**：需自建轻量入口服务（但无长期 PAT，仅需 GitHub App installation）
- **文档研究结论**：通道架构逻辑自洽，符合 v2 架构目标态
- **PoC 验证**：⚠️ 未执行/待验证（需在测试环境实际触发 OIDC token exchange 与白名单校验）

### 候选①（PyPI OIDC）- **次选（若引擎可公开）**

- **优势**：零 PAT；自动配置简单；公开仓可直接发布
- **劣势**：**匿名拉取**与**引擎实现不泄露**存在语义张力；若需源码保密不适用
- **文档研究结论**：仅当引擎可公开可访问时适用
- **PoC 验证**：⚠️ 未执行/待验证（需实际 OIDC token 交换运行）

### 候选③（GitHub Packages）- **第三（仅限 org 内）**

- **优势**：Granular permissions；可撤销单仓；GITHUB_TOKEN 本仓访问无长期 PAT
- **劣势**：跨仓访问需 personal access token (classic)，与「无长期 PAT」目标部分冲突
- **文档研究结论**：跨仓访问受 PAT 限制，不符合零长期 PAT 目标
- **PoC 验证**：⚠️ 未执行/待验证（需实际 package fetch 运行）

### 候选②（Reusable + App）- **末位（架构双轨）**

- **优势**：Fork PR 控制稳健；生成式式模板易复用
- **劣势**：S1「公开仓可访问」需显式 App installation 授权；App 白名单 + scheduler 白名单二元维护
- **文档研究结论**：双轨白名单维护成本高
- **PoC 验证**：⚠️ 未执行/待验证（需实际 reusable workflow 调用）

---

## 文档研究证据索引

### GitHub 官方文档引用

1. [OpenID Connect - GitHub Docs](https://docs.github.com/en/actions/concepts/security/openid-connect) - OIDC token exchange 模型
2. [Configuring OpenID Connect in PyPI - GitHub Docs](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-pypi) - PyPI trusted publishing 授权机制
3. [Reusing workflow configurations - GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflows) - reusable workflow 访问控制
4. [About permissions for GitHub Packages - GitHub Docs](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages) - GitHub Packages 权限模型
5. [Configuring a package's access control and visibility - GitHub Docs](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) - package permission 管理
6. [About secrets - GitHub Docs](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-secrets) - GITHUB_TOKEN 在 fork PR 中的只读限制
7. [Managing project publishing - PyPI Docs](https://docs.pypi.org/manage-project/publishing/) - PyPI trusted publisher 管理

### 修正说明

- 原「候选④ PoC 验证：✅ 全流程通」为虚构结论（ scrutiny 链 `channel-research-poc-2026-09-24.md:228`），已移除
- 排序依据中所有基于旧假 PoC 的论证（含「PoC 验证：✅ 全流程通」类）已全部清除重推
- 当前排序仅依赖文档研究得出的 8 标准矩阵结论

---

## 待拍板问题清单（请所有者裁定）

1. **引擎是否需要公开可拉取？**
   - 如「是」→ 候选①（PyPI）或候选③（Packages 公开包）可行
   - 如「否」→ 候选① 不适用（匿名拉取控制缺失），候选④（4A）为唯一满足 S5 的方案

2. **是否接受双轨白名单（App + scheduler）？**
   - 如「是」→ 候选②（Reusable + App）可纳入考虑
   - 如「否」→ 候选④ 的 scheduler 白名单单一维度更佳

3. **入口服务自建成本是否可接受？**
   - 如「是」→ 候选④ 的入口服务为轻量（OIDC token 验证 + App token 签发 + scheduler 白名单查询），可由 infraro-core 兼任
   - 如「否」→ 可考虑候选③（Packages）的 GITHUB_TOKEN 通道

4. **开发者体验：是否需要公开仓 workflow 直接调用（无注册）？**
   - 如「是」→ 候选④ 的入口服务可设计为自动注册（首次访问请求）+ 人工审批
   - 如「否」→ 候选② 的 App installation 授权流程可接受

---

## 下一步行动（拍板后）

1. 若拍板候选④（4A）：
   - 开展「轻量入口服务」设计（OIDC 验证 + App token 签发 + scheduler 白名单查询）
   - 出就绪包（M5）：守卫实现（入口校验 authorized 状态）/回滚预案/紧急通道保留方案
   - **执行端到端 PoC**：在 consumer-b 或临时测试仓实际验证 OIDC→App token 换取路径、Fork PR 权限边界
2. 若拍板候选①（PyPI）：
   - 收尾包改名（infra-core→infraro-core）/迁移工具（prior release to新仓）
   - 公开仓 workflow 适配 `pypa/gh-action-pypi-publish`
   - **执行实际发布 PoC**：验证 OIDC token exchange 成功接收 PyPI API token 进行发布

---

## 附录：Scrupiny 验证链引用

- **Original failure**: `channel-research-poc-2026-09-24.md` PoC section (lines 128-196) fabricated with placeholder run IDs
- **Corrected version**: Present document, uses only documentation-based reasoning
- **Evidence铁律 (2026-09-24)**: PoC section must reflect actual executed runs, not authored expectations; unexecuted tests marked「未执行/待验证」

---

## 附录：引用文档（GitHub）

1. [OpenID Connect - GitHub Docs](https://docs.github.com/en/actions/concepts/security/openid-connect)
2. [Configuring OpenID Connect in PyPI - GitHub Docs](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-pypi)
3. [Reusing workflow configurations - GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflows)
4. [About permissions for GitHub Packages - GitHub Docs](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages)
5. [Configuring a package's access control and visibility - GitHub Docs](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
6. [About secrets - GitHub Docs](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-secrets)
7. [Managing your personal access tokens - GitHub Docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

---

**文档版本**: v1.1（修正版）  
**生成时间**: 2026-09-24  
**依据**: GitHub 官方文档分析  
**PoC 状态**: ⚠️ 未执行/待验证  
**Always remind: 影响所有者拍板前，零生产配置变更**
