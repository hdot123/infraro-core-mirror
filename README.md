# infraro-core-mirror

**Official public read-only mirror of `hdot123/infraro-core`.**
**`hdot123/infraro-core` 的官方公开镜像（只读）。**

本仓是引擎仓 `hdot123/infraro-core` 的官方公开镜像，只承载**已发布的 release 快照**。
真源（source of truth）是 `hdot123/infraro-core`；该仓转私有后，匿名或未授权的消费者
只能通过本镜像获取已发布制品。

## 只读声明

- 本仓为**只读镜像**：不接受 issue、不接受 pull request（Issues 已关闭，合并策略已收窄）。
- 本仓**不注册 runner**、**不作为 reusable workflow 的执行入口**、**不持有任何消费方凭据**。
- 本仓**不含开发分支历史**（浅镜像）：每个 release 只对应一个快照提交，没有真源的提交历史。
- 本仓内容全部由机械同步 workflow 生成，零手工内容。

## 内容与布局

| 位置 | 内容 |
|------|------|
| `main` | 本说明 + 机械同步 workflow（本仓唯一自行维护的内容） |
| 分支 `release/<tag>` | 该 tag 的源码快照（单个提交） |
| tag `<tag>` | 指向对应快照提交，`git+https://…@<tag>` 可直接安装 |
| GitHub Releases | 与快照一致的源码制品 `infraro-core-<tag>.tar.gz` |

## 安装与引用（匿名可用）

```bash
pip install "git+https://github.com/hdot123/infraro-core-mirror.git@v0.18.9"
```

```bash
curl -L -O https://github.com/hdot123/infraro-core-mirror/releases/download/v0.18.9/infraro-core-v0.18.9.tar.gz
```

```yaml
# 消费仓的 reusable workflow 锚点
uses: hdot123/infraro-core-mirror/.github/workflows/evolution-scan.yml@v0.18.9
```

## 同步机制（零手工内容）

`.github/workflows/sync-release.yml` 机械同步真源的 release：

- **触发面**：`workflow_dispatch`（手动/补跑）、`repository_dispatch`（真源 release 事件转发）、
  `schedule`（每 6 小时轮询一次，自动跟进新 release）
- **过程**：读取真源 release 元数据 → 拉取源码 tarball 与 release 制品 → 机械变换 →
  提交快照并打 tag → 以同一 tag 发布镜像 release
- **幂等**：同一 tag 已镜像时自动跳过（`force=true` 可强制重同步）
- **零手工内容**：release notes 取自真源 release body，制品由快照生成

### 机械变换（与真源的唯一差异）

`scripts/mirror_snapshot.py` 在提交前对快照做三类**确定性**变换（可重复运行、结果一致）：

1. **公开暴露脱敏**：私有网段 IPv4 地址 → `[REDACTED-IP]`；内部主机名 → `[REDACTED-HOST]`；
   本地主目录路径 → `/Users/[USER]/`、`/home/[USER]/`。GitHub 托管 runner 的家目录
   （`/Users/runner`、`/home/runner`）保留——那是平台路径，不是本地主机路径。
2. **可消费面过滤**：`.github/workflows/` 下**只保留声明 `workflow_call` 的可复用 workflow**
   （即消费者能用 `uses:` 锚定的那些）；真源自身的 CI / 发版 / 运维 workflow 不进入镜像
   ——只读镜像里它们是死代码，且不属于「被消费面」。
3. **自引用改指**：镜像自身可执行面（`.github/workflows/**`、`actions/**`）内对真源仓的引用
   改指本镜像，commit-SHA pin 重新 pin 到被镜像的 release tag。否则锚定本镜像的消费者
   仍会去拉私有真源（且真源的 commit SHA 在本仓并不存在）。

文档、变更日志与脚本面**不改写**，继续指向真源。

### 同步权限边界（GitHub 平台规则）

GitHub 禁止 Actions 默认的 `GITHUB_TOKEN`（App 安装令牌）创建或更新 `.github/workflows/**`
下的文件。因此：

- 同步 workflow 默认以 `GITHUB_TOKEN` 推送；当快照引入 workflow 面变更时，推送会被平台拒绝，
  job **fail-loud** 报错并指向修复方式（不会静默发布残缺快照）。
- 配置仓库 secret `MIRROR_PUSH_TOKEN`（带 `contents:write` + `workflows:write` 的细粒度令牌
  或 App 安装令牌）后，workflow 面随 release 自动刷新。
- 同一个 tag 的重同步（`force=true`）不引入 workflow 面变更，因此无需该令牌即可通过。

## 权威性与支持

- 真源：`hdot123/infraro-core`（私有）。本镜像只保证「与真源对应 release 一致的快照」。
- 本仓不提供支持渠道；问题走真源仓的治理流程。
- 许可证随真源：MIT。
