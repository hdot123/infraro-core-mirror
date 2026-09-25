# 紧急通道保留方案（M5 flip-readiness-package）

**状态：设计稿（未实施；含待 owner 决策项）**
**最后更新：2026-09-25**（f10-readiness-remediation：私有仓 archive URL 方案已证伪，改为公开镜像仓设计）
**关联 feature**: flip-readiness-package (F10) → f10-readiness-remediation
**验证**: VAL-M5-014

---

## 概览

flip 后（infraro-core 转 PRIVATE），外部**匿名 git 消费者**（未登记仓、fork PR、临时测试仓）会断链。
本方案定义 flip 期间面向匿名消费者的只读拉取缓冲。

---

## 问题定义：为什么私有仓的 archive URL 不可用

早期版本（commit a412490）提出的方案是「flip 后匿名消费者改用
`https://github.com/hdot123/infraro-core/archive/refs/tags/vX.Y.Z.tar.gz`」。

**该方案不成立**：

- GitHub 的 `archive/refs/tags/...` 与 `archive/refs/heads/...` 端点对私有仓**要求认证**；
  匿名请求（无 token）返回 **404**（GitHub 对无权限仓一律以 404 隐藏存在性）。
- 该文档早期版本自己的 Scenario A 已展示这一失败（`Repository not found`），
  却仍把同一私有仓的 archive URL 当作替代方案——自相矛盾。
- `pip install` 直接指向私有仓 archive URL 同样失败（403/404）。

**结论**：任何落在 `hdot123/infraro-core`（PRIVATE）上的匿名 URL 都不可用。
匿名缓冲必须落在**另一个公开仓库**上。

---

## 设计：公开镜像仓承接匿名拉取缓冲

### 形态

```
hdot123/infraro-core (PRIVATE，真源)
        │  只读同步（GitHub App 短时 token，仅 contents:read）
        ▼
hdot123/infraro-core-mirror (PUBLIC，镜像仓)
        │  只读 release archive / tarball（匿名可拉）
        ▼
匿名消费者（未登记仓 / 临时测试仓 / fork PR）
```

### 同步机制

1. 镜像仓内放置定时 workflow（或复用 scheduler 的 reconcile 通道），
   以 GitHub App 短时 token（`contents:read`，仅限 infraro-core）拉取真源
2. 真源每次发布 release tag 时，镜像仓把对应源码快照打成 release asset / tarball 并发布
3. 镜像仓**只承载只读制品**：不注册 runner、不执行 reusable workflow、不持有消费仓凭据
4. 窗口期 N 天：镜像仓只保留最近 N 天的 release，过期制品删除（默认建议 N=7，理由见下）

### 为什么这是安全的

| 风险 | 缓解 |
|------|------|
| 引擎实现泄露 | 镜像内容 = 已发布 release 的源码快照，与 flip 前公开状态等价；不含未发布分支/内部文档 |
| 镜像仓被当作引擎入口 | 镜像仓不注册 runner、不执行 reusable workflow；它只是制品分发面 |
| 撤销粒度 | 删除镜像仓对应 release asset 即断开该版本；真源删 tag 后镜像随窗口过期 |
| 长期凭据 | 同步用 App 短时 token（TTL 1 小时），不落长期 PAT |

### 保留时间策略

| 项目 | 时间 | 依据 |
|------|------|------|
| 镜像 release（当前版本） | N = 7 天（建议值，待 D2 拍板） | GitHub Actions cache TTL 默认 7 天；PR review cycle 3-5 天 |
| 镜像 release（历史版本） | 随窗口过期删除 | 控制公开面暴露窗口 |
| 真源 release tag | 永久（私有，需授权） | 真源完整性 |

---

## 待 owner 决策（未定）

以下为**设计留白，必须由 owner 拍板**，worker 不代为决定：

| # | 决策点 | 选项 | 影响 |
|---|--------|------|------|
| D1 | **镜像仓粒度** | (a) 单镜像仓承载全部版本；(b) 每 major 一个镜像仓；(c) 每版本一个镜像仓 | 决定撤销粒度与仓数量：粒度越细撤销越精准，维护面越大 |
| D2 | 窗口期 N | 7 / 14 / 30 天 | 决定匿名可达窗口与暴露面 |
| D3 | 镜像内容范围 | (a) 仅 release tarball；(b) tarball + 顶层文档 | 决定匿名可读面 |
| D4 | 同步触发 | (a) 真源 release 事件驱动；(b) 定时 reconcile | 决定制品滞后窗口 |
| D5 | 镜像仓命名与归属 | `infraro-core-mirror` / 其他 | 命名即公开承诺，改名有外部成本 |

**在上述决策完成前，本方案不进入实施**（不建仓、不配同步、不发布制品）。

---

## Flip 后断链场景

### Scenario A：匿名消费者（未登记仓）

```bash
# flip 后直连真源：失败（预期）
git clone https://github.com/hdot123/infraro-core.git
# remote: Repository not found.

# 私有仓 archive URL：同样失败（404，认证要求）
curl -sI https://github.com/hdot123/infraro-core/archive/refs/tags/v0.18.9.tar.gz | head -1
# HTTP/2 404

# 镜像仓（设计目标）：匿名可用
curl -sIL https://github.com/hdot123/infraro-core-mirror/releases/download/v0.18.9/infraro-core-v0.18.9.tar.gz | head -1
# HTTP/2 200
```

> 注：镜像仓尚未建立（见「待 owner 决策」），上方 200 是**设计目标**，不是当前实测结果。

### Scenario B：Fork PR 触发

- fork PR 的 head repo 无权读取私有引擎仓 → `uses:` step 失败（`Repository not found`）
- **缓冲方案**：按平台安全原则 fail-closed，不做额外处理；owner review 后走源仓 PR

### Scenario C：release 版本 pin

```toml
# 真源（私有）：需要授权，匿名不可用
dependencies = ["infra-core @ git+https://github.com/hdot123/infraro-core.git@v0.18.9"]

# 镜像仓（公开，匿名可用）——设计目标
dependencies = ["infra-core @ https://github.com/hdot123/infraro-core-mirror/releases/download/v0.18.9/infraro-core-v0.18.9.tar.gz"]
```

**注意**：PEP 610 `direct_url.json` 的 `vcs_info` 变为 `archive_info`（不可回退到 git ref）。

---

## 消费方路径对照（flip 后）

| 消费方 | 可见性 | 可用路径 |
|--------|--------|---------|
| infraro | public | 自身仓 + 镜像仓制品 |
| consumer-a | private | authorized.yaml 登记 + 4A 通道（App token） |
| consumer-b | private | 同上 |
| 未登记仓 / 匿名 | N/A | 仅镜像仓只读制品（无引擎能力） |

---

## 与三级回滚预案的联动

| 回滚级 | 缓冲制品 | 修复路径 |
|--------|---------|---------|
| 模板级 | 镜像仓内对应版本 workflow 文件 | `git checkout vX.Y.Z -- .github/workflows/` |
| 通道级 | 镜像仓内对应版本 actions | 安装旧版 App |
| 可见性级 | 镜像仓 tarball | `curl -L <mirror>/releases/download/vX.Y.Z/<asset>.tar.gz` |

---

## 监控（设计目标，未实施）

| 项 | 阈值 | 观测方式 |
|----|------|---------|
| 镜像仓制品 404 | > 0 | 匿名 curl 探针（定时） |
| 同步滞后 | > 24h | 对比真源最新 tag 与镜像最新 release |
| 真源可见性 | 非预期值 | `gh api /repos/hdot123/infraro-core --jq .visibility` |

---

## 未实施声明

本文件为**设计稿**。截至 2026-09-25：

- 镜像仓不存在（未创建）
- 同步 workflow 不存在
- 制品未发布
- 决策点 D1-D5 待 owner

---

## 更新日志

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-09-25 | v0.1 | 初稿（**私有仓 archive URL 方案，已证伪**） | factory-droid |
| 2026-09-25 | v0.2 | f10-readiness-remediation：改为公开镜像仓设计；标注 D1-D5 待 owner；删除无效 gh 命令与不可核验声明 | factory-droid |
