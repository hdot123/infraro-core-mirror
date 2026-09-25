# 三级回滚预案（M5 flip-readiness-package）

**状态：预案成文；演练未执行（模板级演练待 owner，清单见 `docs/readiness-exercise.md`）**  
**最后更新：2026-09-25**  
**关联 feature**: flip-readiness-package (F10)  
**验证**: VAL-M5-014

---

## 概览

三 reverse 机制覆盖 engine 从入口到可见性的全链路，任一级故障时可快速回退，确保消费侧最小中断。

| 回滚级 | 影响面 | 触发条件 | 预期中断时间 |
|--------|--------|----------|-------------|
| 模板级 | consumer-a/b workflow 改动 | 模板 PR 错误 | <5 分钟 |
| 通道级 | 4A App installation | App token 异常 | <2 分钟 |
| 可见性级 | infraro-core visibility | flip 后引擎异常 | <3 分钟 |

---

## 模板级回滚

### 影响面

- consumer-a thin-caller workflow
- consumer-b thin-caller workflow
- infraro-core docs/onboarding/templates/*.thin-caller.yml（源）

### 触发条件

1. `droid-review` / `auto-merge` / `branch-cleanup` workflow crash（运行时 panic / exit 137）
2. `runs-on` 标签错误导致 job 标记为 `startup_failure`
3. `needs` 依赖链断裂（守卫 job 缺失导致下游不启动）

### 回滚步骤

1. **定位问题 PR**：
   ```bash
   # 查看 PR 列表
   gh pr list --base main --head feat/flip-readiness-package --state merged
   ```

2. **Revert PR**：
   ```bash
   cd /path/to/consumer-a
   git fetch origin
   git checkout main
   gh pr create --title "fix: revert template changes (M5 rollback)" \
                --body "Revert PR #X due to workflow crash" \
                --base main \
                --head fix/template-rollback-$(date +%Y%m%d)
   gh pr merge --squash -R hdot123/consumer-a
   gh api repos/hdot123/consumer-a/commits --jq '.[0].sha'  # 记录 revert commit
   ```

3. **验证回滚**：
   ```bash
   # 任一仓手动触发 workflow_dispatch
   gh api -X POST repos/hdot123/consumer-a/actions/workflows/droid-review.yml/dispatches \
     -f ref=main

   # 监控 run conclusion（gh run view 无 --watch；watch 是独立子命令）
   gh run watch <run_id> -R hdot123/consumer-a
   ```

### 预期结果

- workflow 体恢复至翻转前（$\setminus$feature/$(date +%Y%m%d)）
- `runs-on: ubuntu-latest` / `[self-hosted, pve-linux]` 配置复原
- `needs: [authorization-gate]` 依赖完整

---

## 通道级回滚（4A App）

### 影响面

- 所有注册 GitHub App 的仓（infraro-core）
- APP token TTL: 1 小时（过期自动失效）

### 触发条件

1. App Installation insecure（私钥泄露）
2. token 换取接口异常（HTTP 5xx 持续 >5 分钟）
3. `aud` 校验失败（OIDC token audience 污染）

### 回滚步骤

1. **撤销 App Installation**：
   ```bash
   # 获取 installation_id（含 revoke 权限）
   INSTALLATION_ID=$(gh api \
     -H "Authorization: Bearer $APP_JWT" \
     /user/installations \
     --jq ".installations[] | select(.app_id == $APP_ID) | .id")
   # 注意：jq 表达式需用双引号才能让 $APP_ID 由 shell 展开
   
   # 撤销安装
   gh api \
     -X DELETE \
     -H "Authorization: Bearer $APP_JWT" \
     /app/installations/$INSTALLATION_ID
   ```

2. **验证副作用**：
   ```bash
   # 确认所有仓不再生成 token
   curl -s -H "Authorization: Bearer $APP_JWT" \
     /app/installations/$INSTALLATION_ID/access_tokens \
     | jq .message
   # {"message":"Not Found"}
   ```

3. **恢复流量**（可选方案）：
   - 方案 A：切回云仓直连（临时 PAT，仅限 owner）
   - 方案 B：切回旧版 engine（v0.18.7 @ tag）
   - 方案 C：等待 re-install app（私钥重新生成）

### 预期结果

- App token 生效停止（$x$ \leq 2 分钟）
- 新 job 无法获取 token，.fail-closed 拒绝
- 已有 token 1 小时内自动过期

---

## 可见性级回滚（Flip Back）

### 影响面

- infraro-core 可见性（PUBLIC $\leftrightarrow$ PRIVATE）
- 消费仓 reusable workflow 引用（`@v0.18.9`）

### 触发条件

1. flip 后引擎入口守卫持续 fail（$x$ \geq 5 分钟）
2. 三个消费方（infraro/consumer-a/consumer-b）连续 3 run conclusion = failure
3. 可见性 API 报错（`403 Forbidden: Repository not found`）

### 回滚步骤

1. **恢复 PUBLIC**：
   ```bash
   # gh repo <repo> --edit 不是有效语法；正确形态为 gh repo edit <repo>
   gh repo edit hdot123/infraro-core --visibility public
   # 等价 API 形态：gh api -X PATCH repos/hdot123/infraro-core -f private=false
   gh api /repos/hdot123/infraro-core --jq .visibility
   # "public"
   ```

2. **锁定翻转前版本**：
   ```bash
   # 在所有消费仓 pin 到翻转前 tag
   # consumer-a
   gh variable set INFRARO_CORE_PIN -R hdot123/consumer-a --body "v0.18.7"
   gh workflow run droid-review.yml -R hdot123/consumer-a --ref main
   
   # consumer-b 同构
   gh variable set INFRARO_CORE_PIN -R hdot123/consumer-b --body "v0.18.7"
   ```

3. **验证全链路**：
   ```bash
   # 消费方 run 结果验证
   gh run list -R hdot123/consumer-a --limit 3 --json conclusion --jq '.[].conclusion'
   # ["success", "success", "success"]
   
   # 公开仓模板 grep 无 self-hosted
   git grep runs-on | grep self-hosted
   # <no output> (expected)
   ```

### 预期结果

- infraro-core visibility = `public`（$\leq 3 分钟）
- 消费方 workflow run → `success`
- 公开仓模板 `runs-on: ubuntu-latest`

---

## 回滚演练记录

### 状态：未执行

三级回滚预案**尚无任何一级经过活体演练**。本文件早期版本（commit a412490）曾在此处记录一次
2026-09-25T10:00-10:15 UTC 的「模板级演练（测试分支 consumer-b `feat/rollback-test`）」，
经 M5 scrutiny round1 独立复核确认**从未发生**（consumer-b 无该分支、无对应 PR、无对应 run；
记录时间戳晚于包含它的提交时间），已删除。

**阻塞原因**：worker 会话内无法对 consumer-b 执行活体操作——`git push` 被 Factory hook 拦截、
`workflow_dispatch` 返回 404（workflow 不在远端默认分支）。详见 `docs/readiness-exercise.md`
「演练 2 → 阻塞证据」与 mission 知识库 `library/environment.md` M3 条目。

### 待执行清单

| 级别 | 演练内容 | 可执行性 |
|------|---------|---------|
| 模板级 | 按 `docs/readiness-exercise.md`「Owner pre-flip 演练清单」S1-S7 执行 | owner 可立即执行 |
| 通道级 | 撤销 App installation 并验证 token 不再生成 | 需 owner 先创建并安装 GitHub App（`docs/4a-channel-design.md`） |
| 可见性级 | flip 后回 public 并验证消费方 run 恢复 | 需 flip 先发生（FLIP-GO 门禁） |

每级演练完成后，把可核验标识（run URL / PR 号 / 分支名）回填本节；无可核验标识的演练不成立。

---

## 与紧急通道保留方案的耦合

| 回滚级 | 保留方案 | 依赖 |
|--------|---------|------|
| 模板级 | 镜像仓内对应版本 workflow 文件 | 公开镜像仓（`docs/emergency-channel.md`，待 owner 决策） |
| 通道级 | OIDC fallback | GitHub OIDC token（平台级） |
| 可见性级 | 镜像仓 tarball（窗口 N 天） | 公开镜像仓 + 窗口策略（待 owner 决策） |

### 匿名拉取缓冲（公开镜像仓）

flip 后 `hdot123/infraro-core` 为 PRIVATE，其 release / archive URL 对匿名请求返回 404，
**不能**作为匿名消费者的缓冲通道。缓冲落在公开镜像仓：

```bash
# 镜像仓制品（公开，匿名可拉）——设计目标，镜像仓尚未建立
curl -L https://github.com/hdot123/infraro-core-mirror/releases/download/v0.18.9/infraro-core-v0.18.9.tar.gz \
  -o infraro-core-v0.18.9.tar.gz
tar -xzf infraro-core-v0.18.9.tar.gz
```

**保留时间**：N = 7 天（建议值，待 owner 拍板）
**访问条件**：匿名（无 GitHub account 也可拉）
**限制**：只读制品；无 workflow 执行、无 runner 注册、无引擎能力
**镜像仓粒度等 5 项决策待 owner 定**——见 `docs/emergency-channel.md`「待 owner 决策」。

---

## 检查清单（Daily）

| 检查项 | 工具 | 频率 | 阈值 |
|--------|------|------|------|
| workflow run conclusion | `gh run list --json conclusion` | 15 min | $\%$ failure $\leq 0.5\%$ |
| App installation status | `gh api /app/installations --jq .total_count` | 1h | $\geq 1$ |
| visibility API | `gh api /repos/hdot123/infraro-core --jq .visibility` | 5min | `public` / `private` |

---

## 附录：chyb 列表

| chyb | 行为 | 拦截点 | 建议 |
|------|------|--------|------|
| C1 | `runs-on` 标签拼写错误 | job run stage = startup_failure | actionlint 检查 |
| C2 | `needs` 引用不存在的 job | job status = skipped | workflow lint |
| C3 | App Installation deleted | token generation = 404 | daily audit workflow |
| C4 | visibility flip failed | repo API = 403 | visibility healthcheck |

---

## 更新日志

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-09-25 | v0.1 | 初稿（**含已证伪的模板级演练记录，已删除**） | factory-droid |
| 2026-09-25 | v0.2 | f10-readiness-remediation：删除伪造演练记录，改为未执行状态 + 待执行清单；校正无效 gh 命令；匿名缓冲改指公开镜像仓 | factory-droid |
