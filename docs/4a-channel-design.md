# 4A 通道设计文档（M5 flip-readiness-package）

**状态：设计完成（待 owner 建 GitHub App）**  
**最后更新：2026-09-25**  
**参考：M3 通道研究结论（4A 候选排名第一，待 owner 拍板）；FLIP-GO 尚未写入（flip 未获批准）**

---

## 概览

4A 通道是公开仓访问受限私有引擎的受控通道，架构为：

```
公开消费仓（GitHub-hosted runner）──┐
                                   ├──▶ GitHub OIDC → GitHub App token → 私有受控入口
                                   │        ↓                  ↓
                                   │   ENGINE_CONSUMERS    authorized.yaml（真源）
                                   │        ↓                  ↓
                                   ▼    infraro-core（私有引擎 + 入口守卫）
```

### 核心特性

| 特性 | 说明 |
|------|------|
| 无长期 PAT | 仅依赖 OIDC + GitHub App short-lived token |
| 未登记仓不可用 | ENGINE_CONSUMERS variable 必须为 `authorized` |
| Fork PR 无法访问 | fork head repo 不在 authorized.yaml 授权集 |
| 单仓可撤销 | authorized.yaml 删除一行 = 该仓即时失效 |
| 不依赖 self-hosted | 公开仓走 GitHub-hosted runner |
| 接入协议长期稳定 | GitHub OIDC + App token 平台级保证 |

---

## 通道管道（Pipeline）

### A面：身份证明（GitHub OIDC）

1. **workflow 触发**：`pull_request/push/release` 等 `workflow_dispatch`/`workflow_call` 事件
2. **GitHub OIDC 令牌**：`github.token` 是短时 JWT，含 `sub`（repo）和 `actor`（user）等声明
3. **JWT 结构关键字段**：
   ```json
   {
     "iss": "https://token.actions.githubusercontent.com",
     "sub": "repo:hdot123/infraro-core:ref:refs/heads/main",
     "actor": "hdot123",
     "aud": "hdot123/infraro-core"
   }
   ```
4. **观众校验**：`aud` 必须匹配 `hdot123/infraro-core`（防止 token 复用）

### B面：私有受控入口（GitHub App Installation Token）

1. **超时短时 token（TTL = 1 小时）**：
   - GitHub AppInstallation API 返回 `expires_at=now+1h`
   - 过期后自动失效，无需主动撤销

2. **单仓可撤销**（差异化约束）：
   - GitHub App 安装是仓级（installation）而非账号级
   - 从 authorized.yaml 删除仓条目 → 下次 workflow 触发时，App 不再有该仓访问权限
   - 即时生效：无需等待 token 过期

3. **权限范围**：仅授予 `Contents: Read-only` + `Metadata: Read-only`
   - Content Read：读取 repo 源码（workflow 文件、.evolution/config.yml）
   - Metadata Only：获取 App Installation token（无写权限）

### C面：白名单校验（authorized.yaml 为真源）

1. **真源位置**：`hdot123/scheduler/.github/authorized.yaml`
2. **格式**：
   ```yaml
   # 唯一登记与对账入口；不构成匿名拉取的技术闸门
   authorized:
     - consumer-a
     - consumer-b
     - infraro
   ```
3. **校验逻辑**：
   - 工程侧读取 authorized.yaml（gh CLI / REST API）
   - 比对 `github.repository` 或 `REPO` 变量
   - 存在且值为 `authorized` → 放行；缺失/非 `authorized` → fail-closed

### D面：引擎调用链

1. **生产执勤门禁（当前事实）**：reusable workflow 内的**内联 bash gate**——
   `authorization-gate` job 读 `ENGINE_CONSUMERS` variable → 比对 `authorized`，异常 fail-closed。
   这是今天真正拦住未授权调用仓的机制。

2. **`src/infra_core/guard.py` 的定位（当前事实）**：守卫模块 + 13 个契约测试已落地并通过，
   但**当前零生产调用方**——没有任何 workflow 或 action `uses` / 调用它。
   （这不是安全洞：内联 bash gate 仍在执勤。两者治理基线——variable 名 / authorized 令牌 /
   引擎仓豁免 / fail-loud 文案——由 `tests/test_guard.py` 的文本锚定契约测试锁定一致。）

3. **接入计划（M6+，非本 mission 交付）**：把内联 bash gate 收敛为对 `guard.py` 的 CLI 调用
   （`python -m infra_core.guard --repo <repo>`），消除两处判定表漂移风险。
   接入前需评估：每仓 venv/依赖安装成本、`gh` 可用性、以及 reusable workflow 的启动开销。

4. **零 PAT 策略**：全程仅 `$GITHUB_TOKEN` + 短时 App token

---

## 部署面建议

### 引擎侧（infraro-core）

**暂不部署**——引擎仓本身固定为 `public`，所有 entry point 均为 public workflow。

**部署条件**（后期评估）：
- 公开仓消费量显著上升
- 需要更强的引擎逻辑保护（防逆向研究）
- GitHub App token 模式稳定运行 ≥30 天

**部署形态**：
1. **infraro-core 转 private**（flip 执行）：`gh repo edit hdot123/infraro-core --visibility private`
2. **入口守卫前置**：`droid-review-shards.yml` / `auto-merge-pipeline.yml` / `branch-cleanup.yml` 三处守卫 job 独立于 reusable workflow
3. **反向不可见**：public workflow 不可见其内部实现

### GitHub App 建立（owner 账号操作）

**待 owner 执行**（本设计文档仅记录操作步骤）：

1. **创建 GitHub App**：
   - URL: `https://github.com/settings/apps/new`
   - Name: `infraro-private-entries`
   - Description: `Private access gate for infraro-core engine`
   - Homepage: `https://github.com/hdot123/infraro-core`
   - Git ignore: `.evolution/**`, `.github/**`
   - Webhook: disabled（仅需要 App token）
   - Repository permissions:
     - `Contents`: Read-only
     - `Metadata`: Read-only（implicit）
   - Subscribe to events: `Pull request`, `Push`, `Release`

2. **安装到仓**：
   - App Settings → Install App → 仅安装 `infraro-core`
   - 限制：仅限 org 成员（public depot 需额外授权）

3. **生成 Installation Token**：
   ```bash
   # 使用 1Password 存储 Private Key
   gh api \
     -X POST \
     -H "Accept: application/vnd.github+json" \
     -H "Authorization: Bearer $APP_JWT" \
     /app/installations/$INSTALLATION_ID/access_tokens
   ```

### 调用方（consumer repo）

**预期消费仓模板**（infraro 仓 `docs/onboarding/templates/`）：

```yaml
# .github/workflows/droid-review.yml
name: Droid Auto Review
on:
  pull_request:
    branches: [main]

jobs:
  droid-review:
    runs-on: ubuntu-latest  # 公开仓走 hosted
    permissions:
      actions: read  # 读 ENGINE_CONSUMERS
    env:
      ENGINE_CONSUMERS: ${{ vars.ENGINE_CONSUMERS || '' }}
    steps:
      - name: assume-role
        uses: hdot123/infraro-core/.github/actions/assume-role@v0.18.9

      - name: droid-review
        uses: hdot123/infraro-core/.github/workflows/droid-review-shards.yml@v0.18.9
```

**`assume-role` composite action**：

1. 通过 GitHub App 安装 token 获取 `access_token`
2. 校验 `ENGINE_CONSUMERS == authorized`
3. 输出 token 到 `${{ steps.assume.outputs.GITHUB_TOKEN }}`

---

## 公开仓接入协议形态

### 声明层零改动承诺

**承诺内容**：公开仓模板 `droid-review.thin-caller.yml` 无需修改，仅复制到消费仓即可。

**落法**：
1. `runs-on: ubuntu-latest` → 保持不变（公开仓；不改 self-hosted）
2. `permissions.actions: read` → 保持不变（零改动）
3. `ENGINE_CONSUMERS: ${{ vars.ENGINE_CONSUMERS || '' }}` → 保持不变（零改动）

**实际生效路径**：
1. 公开仓 `gh variable set ENGINE_CONSUMERS --body authorized -R org/repo`
2. workflow 运行时，`vars.ENGINE_CONSUMERS` → `${{ vars.ENGINE_CONSUMERS }}` → `authorized`
3. 守卫校验通过 → 后续 job 启动

### 消费仓模板差异（仅 variable）

| 项目 | 公开仓模板 | 私有仓模板 |
|------|-----------|-----------|
| `runs-on` | `ubuntu-latest` | `[self-hosted, pve-linux]` |
| `permissions.actions` | `read` | `read`（相同） |
| `ENGINE_CONSUMERS` | `authorized` | `authorized`（相同） |
| `runner 注册` | none（GitHub-hosted） | `pve-runner-<repo>`（专属） |

**差异仅在 runner 标签和 runner 注册，引擎侧 transparent**

---

## 回滚面对照（与三回滚预案联动）

| 回滚级 | 影响面 | 条件 | 操作 |
|--------|--------|------|------|
| 模板级 | consumer-a/b thin-caller | 模板失效 | `git revert` 仓模板 PR |
| 通道级 | 4A App installation | App token 异常 | `gh api -X DELETE /app/installations/$id` |
| 可见性级 | infraro-core visibility | flip 后重建 public | `gh repo edit hdot123/infraro-core --visibility public` |

---

## 与现有机制的耦合

### Sharding workflow（droid-review-shards.yml）

- **守卫位置**：`setup` job（`needs: authorization-gate`）
- **授权判定（当前）**：`authorization-gate` job 内联 bash 脚本（生产执勤路径）
- **共享逻辑（计划）**：`infraro-core/src/infra_core/guard.py` —— **当前零生产调用方**；
  治理基线一致性由 `tests/test_guard.py::test_guard_authorization_governance_anchors`
  的文本锚定契约测试锁定。CLI 收口属 **M6+ 接入计划**，尚未实施。

### Audit workflow（engine-consumer-audit.yml）

- **每周扫描**：`cron: 0 2 * * 1`（周一 02:00 UTC）
- **扫描面**：
  - `gh api /users/$OWNER/repos`（公开仓发现）
  - `gh api /user/repos`（token 可见仓发现）
  - `git/trees/{branch}?recursive=1`（workflow 文件扫描）
  - `actions/variables/ENGINE_CONSUMERS`（variable 对照）
- **违规判定**：引用 infra-core reusable workflow + variable ≠ `authorized`
- **Issue alert**：幂等标题 `Engine consumer audit: unauthorized engine references`

---

## 证书（Certificate）

- **Signed-off-by**: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>
- **Reviewers**: none（设计文档，需 owner 评审）
- **Approvers**: owner（GitHub App 创建授权）

---

## 附录

### A. JWT 示例（OIDC）

```bash
# 获取 GitHub OIDC token（workflow 内）
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://token.actions.githubusercontent.com/.well-known/jwks

# 解码 JWT payload
echo "eyJpc3MiOiJodHRwczovL3Rva2VuLmFjdGlvbnMuaW5zdGFuY2VzLmdpdGh1Yi5jb20ifQ==" | base64 -d
# {"iss":"https://token.actions.githubusercontent.com"}
```

### B. App Installation Token 请求

```bash
# 生成 JWT（GitHub App 私钥签名）
APP_ID=123456
PRIVATE_KEY=$(cat app-private-key.pem)
JWT=$(python3 -c "import jwt,datetime; print(jwt.encode({'iss': $APP_ID, 'iat': int(datetime.utcnow().timestamp()), 'exp': int(datetime.utcnow().timestamp())+600}, '$PRIVATE_KEY', algorithm='RS256'))")

# 获取 installation_id（已安装该 app 的仓）
gh api \
  -H "Authorization: Bearer $JWT" \
  -H "Accept: application/vnd.github+json" \
  /user/installations

# 获取 access_token
gh api \
  -X POST \
  -H "Authorization: Bearer $JWT" \
  /app/installations/$INSTALLATION_ID/access_tokens
```

### C. authorized.yaml 与 var 面关系

| 源 | 真源地位 | 单仓可撤销 | runner 绑定 |
|----|---------|-----------|-----------|
| `authorized.yaml` | ✅ | ✅ | ❌ |
| repo variable `ENGINE_CONSUMERS` | ❌（派生） | ❌（需手动清） | ❌ |

**结论**：`authorized.yaml` 是唯一真源；variable 是 engine consumer 面的缓存/镜像（由 `whitelist-auto` workflow 铺设）。

---

## 更新日志

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-09-25 | v0.1 | 初稿：4A 通道设计 | owner |
