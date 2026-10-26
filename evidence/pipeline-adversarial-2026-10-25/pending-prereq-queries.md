# 待查前提 · 只读查询清单（2026-10-25）

供大哥过目；**未下令前不改仓、不关 PR、不出方案 v2**。

## 0. 本环境已闭合的一项（可不再占 Mac）

**ruleset `integration_id`（硬门 2 参数）——已一手读到**

```bash
gh api repos/hdot123/infraro-core/rulesets/23079535 \
  --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks'
```

实测（cloud agent，2026-10-25）：

| context | integration_id | 含义 |
|---------|----------------|------|
| quality-gate | **15368** | GitHub Actions |
| substrate-gate-suite | **15368** | GitHub Actions |
| droid-review | **15368** | GitHub Actions |

含义：病三若用「非 Actions 的 GitHub App」贴 check，默认**不满足**现行 ruleset 绑定；硬门 2 测 PR 必须同时验证「换绑 / 去 integration_id / 或改用 Actions 可进 rollup 的贴法」。对方对抗写「只读工具集读不到」——在本 agent 的 `gh` 上不成立；**参数已落地，门可执行**。

---

## 1. 仍待 Mac / 仓设置页：外置 Checks App（病三命门）

仓内现状（本环境只读）：

- 无 `APP_ID` / App private key secret 用于「向 PR head 贴 `droid-review`」的工作流引用
- 现有 `GO_GITHUB_RUN_TOKEN` = BYOM 网关凭证，**不是** GitHub App 安装令牌
- `droid-review.yml` mirror 仅 `checks:write` + 默认 `GITHUB_TOKEN`（Actions 身份）
- `gh api user/installations` / repo installation：**本 token 403/401，列不出安装列表**

请在 **Mac 且 PAT/owner 会话** 执行（只读）：

### 1.1 列出装在本仓/账号下的 GitHub Apps

```bash
# 用户可访问的 installation 列表
gh api user/installations --paginate \
  --jq '.installations[] | {id, app_id, app_slug, account:.account.login, repository_selection, permissions}'

# 若仓在个人名下，打开浏览器核对：
# https://github.com/settings/installations
# 以及 https://github.com/hdot123/infraro-core/settings/installations
```

**要回答的问题**：是否存在任一 App，其 permissions 含 `checks: write`（或 `checks: read`+能写的等价权限），且安装范围包含 `hdot123/infraro-core`？

### 1.2 若有候选 App：试探能否在测试 SHA 上创建 check-run

```bash
# 用 App JWT → installation token 后（勿把 key 贴进聊天）：
gh api -X POST repos/hdot123/infraro-core/check-runs \
  -H "Accept: application/vnd.github+json" \
  -f name='droid-review-app-probe' \
  -f head_sha='<某 open PR 的 head SHA>' \
  -f status='in_progress' \
  -f started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

然后：

```bash
gh api repos/hdot123/infraro-core/commits/<head_sha>/check-runs \
  --jq '.check_runs[] | select(.name|test("droid-review-app-probe")) | {id,name,app:.app.slug,app_id:.app.id,status}'

# GraphQL：是否进 statusCheckRollup（硬门 2 真验收）
gh api graphql -f query='
query($o:String!,$n:String!,$r:String!){
  repository(owner:$o,name:$n){
    ref(qualifiedName:$r){
      target{ ... on Commit {
        statusCheckRollup{ contexts{nodes{... on CheckRun{name,status,conclusion,checkSuite{app{databaseId,slug}}}}} }
      }}
    }
  }
}' -f o=hdot123 -f n=infraro-core -f r=refs/heads/<pr-branch>
```

**要回答的问题**：`app.databaseId` / `integration_id` 是否等于或可替换 ruleset 上的 **15368**？进不进 `statusCheckRollup`？

### 1.3 若无候选 App（预期默认）

记录一句结论即可：「仓无外置 Checks App；病三要么新建 App+改 ruleset 绑定，要么放弃『workflow 外 App』路径、另选载体」。  
**不要在未点头前创建 App。**

---

## 2. 对方分簇终判 · 本侧附议（摘要）

| 簇/门 | 对方终判 | 本侧 |
|-------|----------|------|
| 病一 | 改稿：PR 语境载体 / 组键塌缩 / 触发零守卫 | **附议**；并入 v2 |
| 病二 | 改稿：QG 非纯 API；先消等待再搬家；对齐 #444 | **附议**；QG 确有 checkout+ensure-jq |
| 病三 | 待查：外置 App 零一手证据，全案最先落实 | **附议**；见 §1 |
| 病四 | 改稿：并发2/FIFO介质/铸卡键 | **附议**；现行去重确为 rule_id+location 且只查 issue |
| 硬门 1 | 改稿：原生无「未齐」态；skip=通过 | **附议升级**（强于本侧仅 PATCH 出口措辞） |
| 硬门 2 | 待查 integration_id | **半闭合**：id=15368 已读；App 路径仍待 §1 |
| 硬门 3 / 哨兵 | 改稿：排队四属性；cron≪30min；防误伤排队 | **附议** |

## 3. 建议下一步（等点头）

1. 你过目本清单 → Mac 跑 §1（§0 已可跳过）  
2. 前提落实后 → 出方案 **v2 改稿**（病一载体、硬门1换构造、病四三要素、哨兵时限+心跳）+ 轻量复核  
3. **第 0 步止血仍单独下令**
