# R3 GLM-5.3 终稿独立复核（A5 验证 + 最终问题总表 + 修复预研）

- 复核员：GLM-5.3（终稿收敛轮，独立于 R1/R2 四模型、Fable 5.1 与并行 DS4 红队）
- 日期：2026-09-23
- 方法：全程只读（git/grep/cat/gh 只读、handoff JSON 解析）；**未执行任何测试、未修改任何仓库文件**；本报告为唯一写入物
- 独立性声明：所有结论基于本次亲手取证的第一手材料（源码 file:line、handoff JSON 原文、`substrate/gate0-exemptions.md`、`mission.md`、`gh run list` 实测）。复核中途目录内出现了并行 DS4 产物 `r3-ds4-final-attack.md`，其内容未被采信；本报告涉及的重叠发现（gate_common Template 表头、mission.md 错误前提）均在其写入前已由本复核员独立取证完成（handoff 清点脚本 → `gate_common.py:184` 实读 → `gate0-exemptions.md:22` 实读 → `mission.md:51` grep），分级独立给出
- 证据基准：本地 HEAD `527a435`，`git diff --stat HEAD origin/main` 为空（与 `origin/main` = `bc810b7` 树逐字节一致），全部 file:line 直接适用于 main
- 限制声明：本地 KB 决策文档（`memory/kb/decisions/architecture-evolution-goal-correction.md`）不在本次允许读列表内，其「态一」原文采信 final-synthesis §三 A5 行与 Fable T3-1 的多方一致引用，未做第一手复读

---

## Y1 A5 独立验证

### Y1.0 场景设定与前置事实

A5 攻击的场景：**evolution-scan workflow 持续被触发、每次 run 都以 conclusion=failure 告终**（P1 liveness conclusion 防线的目标场景）。前置事实（第一手）：

- `evolution-scan.yml:66`：`workflow_dispatch: {}` 存在 → self-heal dispatch 会被 GitHub API 接受（结构性常态）
- `evolution-scan.yml:68` 注释：cron 为 13/43 分钟双窗（每 30 分钟），分钟位避开 ：00/:30 → 只要触发面活着，最新 run 的 createdAt 必在 ~30 分钟内
- `gh run list --workflow evolution-scan.yml --limit 100` 实测直方图：`failure: 6, success: 83`，最近 30 次全 success，零 timed_out/cancelled → 场景当前未发生，但 failure 先例真实存在（6 次），且 `gate0-exemptions.md:20-29` 登记的 6 个 registered template break 全为 run 级失败类——「workflow 长期跑但长期失败」是本仓有登记记录的真实故障类，非理论构造

### Y1.1 控制流推演（八步，全部 file:line 本次亲手核）

**Step 1 — liveness 判定**（`_check_workflow_liveness`，`evolution_heartbeat.py:285`）：

- `:318-328`：`gh run list --limit 5 --json status,conclusion,createdAt`
- `:343-345`：`all_failed_streak = len(runs) >= 3 and all(run.get("conclusion") == "failure" for run in runs[:3])` → 场景下 **True**
- `:361-370`：循环内遇到最新 run（age ≤ 2h threshold，双窗 cron 保证），streak 命中 → `alive=False`，message「Scanner stale: last 3 runs concluded failure」，**提前 return**

关键结构性事实：`alive=False` 时 `hours_since_last_run` 是**最新一次运行的年龄**（分钟级 fresh），不是「最后一次成功运行的年龄」。这是整条抑制链的第一块多米诺。

**Step 2 — main() self-heal dispatch**（`:846-870`）：

- `:851` else 分支（alive=False）→ `:854` `dispatch_accepted, dispatch_error = trigger_scanner_dispatch()`
- `:396-431` dispatch 实现：`gh workflow run`，`:424-431` 只要 returncode==0 即返回 `(True, None)`
- dispatch 被接受 ≠ 那次 run 会成功——API 接受 dispatch 不预检 workflow 健康度。**恰恰在「scanner 病得只会失败」的场景里 dispatch 依然成功**（除非 token 失效/网络故障，那是另一类故障）

**Step 3 — age < 8h**（`:855-870`）：

- `stale_hours = hours_since_last_run` = 分钟级 fresh age < `SCANNER_SEVERE_STALENESS_HOURS = 8`（`:46`）
- `:867-870` else 分支：打印「Self-heal dispatch accepted; suppressing scanner_stale alert」，severe 分支（`:859-865`）不可达

**(a) 项核验——「3 连 failure 与 age 必 <8h」的因果**：**成立，且因果是双向锁定的**。streak 判定对象是 `runs[:3]`（最新 3 次），全 failure 隐含「最近 3 次都被触发」；而触发面活着（cron 双窗）+ heartbeat 每 2h tick 自身的 dispatch 都会制造 fresh run 进入 run history → 下一 tick 探测到的最新 age 结构性 < 8h。两个独立机制（cron 触发 + 自愈 dispatch）共同把 age 永续压在阈值下。

- dispatch 成功才重置 age 吗？精确表述：**dispatch 本身不重置当前 tick 的 liveness 数据；dispatch 制造的新 run 进入 run history，在下一 tick 重置 age**。同一 tick 内 age 就是探测时点的实测值
- dispatch 失败/限频时？→ `dispatch_accepted=False` → `:903` `stale_alertable = True and (True or ...)` = **True** → `:919` 告警路径走通。**这是「持续失败」场景下唯一能产生告警的子场景——它要求 self-heal 自身先坏（token 失效/权限漂移/网络故障）**。观测性寄生于治疗机制的故障，属自举倒挂

**Step 4 — severe_outage / stale_alertable**（`:900-903`）：

- `:900-902` `severe_outage = (not alive)=True and (measured_hours is not None and > 8)` = False（fresh age 非 None 且 < 8）
- `:903` `stale_alertable = True and (False or False)` = **False**

**Step 5 — effective_liveness 覆盖**（`:906-907`）：

- `:906` `effective_liveness = {"alive": alive or dispatch_accepted and not severe_outage}`——Python 运算符优先级 `and` > `or`，即 `alive or (dispatch_accepted and not severe_outage)` = False or (True and True) = **True**
- `:907` `compute_current_anomalies`（`:535-546`）以 alive=True → **scanner_stale 不进入 current_anomalies**
- 注意覆盖范围：effective_liveness 只作用于 scanner_stale 维度；coverage 维度（issues_without_pr）用真实数据计算，不受影响

**Step 6 — 既有告警关闭**（`:911-917` → `resolve_cleared_alerts` `:613-743`）：

- `:548-565` `list_open_alert_issues`：按 `--label evolution-heartbeat`（ALERT_LABEL）查 open issues → **作用域第一步圈定**
- `:520-533` `extract_recorded_anomalies`：从 body 解析两个 marker 常量（`:60-62`）；无可解析 marker 的 issue 在 `:694-696` 被跳过，不关
- `:678-686` 关闭条件：recorded − current = 全部 cleared 且无交集
- **(b) 项核验——关闭面范围**：正确限定。关闭只影响「挂 evolution-heartbeat 标签 **且** body 可解析出 recorded anomalies **且** recorded 全部维度都已消失」的 issue。纯 scanner_stale 告警（最常见形态）在本场景被关；**含 issues_without_pr 维度且该维度未清的告警不关**（current 含真实 coverage 异常，recorded∩current 非空 → 不满足关闭条件）；其他标签的告警零影响。另有 fail-closed 侧保护（`:624-627` coverage_data_ok=False 时跳过全部关闭）。**结论：(b) 无缺陷——Fable「可能关闭既有告警」对纯 scanner_stale 告警成立，但不误伤 coverage 维度存续的告警**

**Step 7 — main() 返回码**（`:919-940`）：

- `stale_alertable=False` 且 coverage 无 missing → `:919` 条件 False → `:938-940` `write_monitor_heartbeat(0)` + 「All checks passed」+ **return 0**。heartbeat workflow 保持绿
- 附带推演：即便 heartbeat 是 required check，返 0 也不红；实际上它是独立 scheduled workflow，不在 `ci.yml` 的 ci-ok 聚合链内。**双重确认决策文档「态一：CI 内以红/绿判定阻断」对 liveness 不成立**——「态一」的可执行判据（该面失效时某 required check 必红）在此场景两条都不满足

**Step 8 — 每 tick 永续闭环**：

- Step 2 的 dispatch 制造新的 scanner run（dispatch 触发型）；病根不可自愈时（如 workflow 配置坏、依赖 secret 缺失——dispatch 无法修复的失败类）该 run 再次 failure → 成为最新 → 下一 tick `runs[:3]` 依然全 failure → alive=False → dispatch → **循环自锁**。cron 双窗也在独立制造 fresh failure run。抑制条件被防线自身的自愈动作（+cron）永续满足

**(c) 项核验——逃生门穷举**（main() 后所有影响告警路径的变量）：

| # | 潜在逃生门 | 位置 | 在目标场景的可达性 |
|---|---|---|---|
| 1 | dispatch 被拒 → `not dispatch_accepted`=True | `:903` | 不可达（dispatch 被 API 接受是结构性常态）；且语义倒挂——dispatch 也坏（token 失效）才告警，scanner 本身坏而 API 活着不告警 |
| 2 | age > 8h → severe_outage=True 穿透 | `:900-902` | 目标场景结构性不可达（Step 3 双机制压制）；但见 Y1.2 的交叉场景 |
| 3 | coverage_data_ok=False | `:624-627` | 只挡关闭，不产生新告警，不影响 stale_alertable |
| 4 | issues_without_pr > 0 | `:919` | 旁路告警存在但 body 只含 coverage 维度，对「scanner 停摆」无效且依赖无关维度巧合 |

**无「dispatch 连续失败 N 次后不再抑制」的跨 tick 计数器**——代码无任何本地跨 tick 状态文件记录 dispatch 失败史；唯一跨 tick 状态是 GitHub 上的 issue 与 run history，而后者正被 dispatch 自身刷新。**(c) 答案：没有逃生门。**

### Y1.2 对 Fable 论断的精确化（不改变结论，修复测试需知）

Fable T3-1 第 6 点称「抑制条件被永续满足，severe_outage 路径在此场景下**结构上**不可达」。精确限定如下：

- **「持续触发 + 持续失败」**（P1 目标场景）：age 被 cron 双窗 + 自身 dispatch 双机制永续压制 → severe 不可达，**告警永寂**。Fable 论断完全成立
- **「停触发 + 历史全失败」**：cron load-shed 停掉触发后，最新 run 持续老化；age > 2h 后 streak 分支（`:361-370`）不再命中（要求 age ≤ threshold 才进），落到 `:371-383` 纯 age 判 stale；age 跨过 8h → `severe_outage=True` → `:903` 穿透 → **告警出现**（延迟最坏 ~8h + heartbeat 2h tick 粒度），且 `:906` effective alive=False → 不误关既有告警

即：`(streak AND age<8h)` 永寂，`(streak AND age>8h)` 可穿透。**修复的 main() 级测试必须双场景都锁**，只锁前者会留下交叉场景回归面。

### Y1.3 终裁

**与 Fable 一致：A5 升 major，成立。** 八步控制流全部独立复现，(a)(b)(c) 三项特别核验均有第一手答案。最终表述：

> 在「scanner 持续被触发且每次都 conclusion=failure」这一 P1 conclusion 防线的目标场景中：alive=False → dispatch 被接受（结构性必然）→ fresh age < 8h → severe_outage=False → stale_alertable=False → effective_liveness.alive=True → 不建告警 + 关闭既有 scanner_stale 告警（正确限定于 evolution-heartbeat 标签且 recorded 全清者）+ main() 返 0；无任何跨 tick 逃生门，抑制被 cron 与自愈 dispatch 双机制永续满足。防线的全部可观测效果 = heartbeat 日志一行 ALERT + 一次无法治病的 dispatch；唯一告警窗口是 self-heal 自身也失败（自举倒挂）。决策文档将其归入「态一：CI 内阻断」属**分类失实**（正确定性是态二「有实现·条件生效」，条件 = dispatch 被拒 或 measured age > 8h）。

与 Fable 的增量：① severe 穿透路径在「停触发+历史 streak」交叉场景可达（修复测试双场景锁定）；② (b) 关闭面的范围精确边界（标签 × marker × recorded 全清三重限定，coverage 维度不误伤）；③ (a) 因果的双机制表述（cron 与自愈 dispatch 共同压制 age）。

审计链处理回顾的再确认：heartbeat worker handoff 原文（本次亲手解析）如实披露了整条链并给出 suggestedFix（「suppression 条件加入 failure-streak 不可抑制维度……决策文档的态一分界可按此口径描述」）——**worker 的披露是准确且完整的**，问题出在审计链四方共享了「spec 裁定 main() 不动 ⇒ 非缺陷」的推理跳跃，把分类失实降格为措辞偏好（A5 minor/记录）。Fable 对这一共享盲区的定性（「范围裁定被等同于非缺陷」）经我独立复核成立。

---

## Y2 最终问题总表与 diff

### Y2.1 本复核员认可的最终问题总表（23 项 → 归并后 20 项）

**立即修桶（6 项）**

| ID | 内容 | severity | 第一手证据 |
|---|---|---|---|
| I1 | README.md:187 引用不存在的 docs/architecture.md §2.4；「四层防御体系」措辞撞车（D1b, info） | major | README.md:187 实读本次确证（原文「详见 docs/architecture.md §2.4」） |
| I2 | **决策文档 liveness「态一」归类失实 + README:191 bullet 无限定**（A5 分类面，Fable T3-1） | major | 本报告 Y1 八步推演；heartbeat handoff 第 5 条（全局编号）原文 |
| I3 | test_ci_structure_contract.py:477 docstring「13 job」实为 12（D7b） | info | r2 终审第一手（维持） |
| I4 | VAL-CI-003-actionlint-output.txt 0 字节无 exit 标记（D5） | minor | r2 终审第一手（维持） |
| I5 | 本地 main 落后 / 本地 feature 分支未删 / evidence/ untracked 无处置（D6） | minor | git status 实况（本轮 `[gone]` 仍在，Fable T3-5 同证） |
| I6 | 决策文档:135 元数据时滞（D7a） | info | r2 终审第一手（维持；read-first-CRUD 增补节回填） |

**follow-up 登记桶（6 项）**

| ID | 内容 | severity | 备注 |
|---|---|---|---|
| F1 | 收口件（README）零断言覆盖——CI 覆盖存在于 head 527a435（gh pr checks pytest/test-groups 实跑其上），缺的是内容级断言（D2，措辞按 Fable 修订） | major（流程面） | features.json `fulfills: []` |
| F2 | gate1 `check_docs_references` 在 tags 空集时跳过 @vX.Y.Z 扫描，consumer-onboarding.md 实含 engine tag 引用（A2，**单标签 minor**，采纳 Fable 2.4 的自洽性修正；登记状态并入全局待补块） | minor | gate1 handoff 第③条 |
| F3 | heartbeat 测试无 `--json`/conclusion 接缝锁：删字段则 streak 恒 False、防线静默失效而测试全绿（D3） | minor（高优先） | `:318-328` vs 测试文件 `--json` 零命中（Fable ③ 已验，与本复核 :322-323 实读一致） |
| F4 | conclusion 仅认 `"failure"` 字面，timed_out/cancelled/startup_failure 不计 streak（D4） | info（符合批准口径，完备性选项） | `:344`；实测直方图当前零 timed_out/cancelled，触发条件现实 rarity 支持 info |
| F5 | **A5 代码面**：suppression 条件加「failure-streak 不可抑制」维度 + main() 级测试锁（3 连 failure + dispatch 接受 → 仍告警；双场景见 Y1.2） | major（代码面，随分类面降为文档后代码部分独立成项） | `:903`/`:906`；worker suggestedFix 原文 |
| F6 | gate_common.py:184 排除集 `{"", "---", "Entry", "Path prefix", "Item"}` 缺 `"Template"`，而 `gate0-exemptions.md:22` 的 `## Gate 1` 表头首列即 `Template` → 表头被登记为有效 token；一旦 error 串含大写 `Template` 即误判 owned → latent 软通过面 | minor（latent，当前无害：fail-closed 文案用小写 template） | gate1 handoff 第④条原文 + `gate_common.py:184` + `gate0-exemptions.md:22` 三方第一手（本复核员独立验证） |

**记录桶（6 项）**

| ID | 内容 | severity |
|---|---|---|
| R1 | scrutiny synthesis 闭环理由与 guidance 实际内容不符（A3） | minor |
| R2 | mission.md:51 保留错误前提「CI 的 gate-tests job 持有 GH_TOKEN」——architecture.md:53 已更正并注明，mission.md 本体未更正（D7 只覆盖「13 job」未覆盖此前提） | info |
| R3 | mission 文档「13 job」残留（D7c 主体）：planning 已更正，transcript/handoff 不可变历史——**无问题** | — |
| R4 | mission architecture.md:31 残留 `.gitignore:32-38`（D7c 附注；同文档 ：148 已更正） | info |
| R5 | 审计质量：GLM 主审 handoff 清点 11 条列 7 条（A1）、「白名单 8 文件」下列 9 名（A4）——方法论节承载，不入修复桶 | — |
| R6 | evidence/ 与本地 KB decisions 无 sha256 manifest；「3 文档 byte-identical」自述无哈希佐证（Fable T3-4，维持 minor） | minor |

### Y2.2 与 final-synthesis §三 + Fable 修订清单的 diff

比对基准：final-synthesis §三 终裁表（11 项）∪ Fable T4 修订表（A5 升 major、D2 措辞、A2 单标签、条件清单 +1/+1）。

**一致项（零分歧，19/20）**：I1=D1、I3=D7b、I4=D5、I5=D6、I6=D7a、F1=D2（含措辞修订）、F2=A2（含单标签）、F3=D3、F4=D4、F5=A5 代码面、R1=A3、R3=D7c、R4=D7c 附注、R6=T3-4、A5 分类面=I2、完成度标签「带条件完成」、D1 不升 critical、方法论结论加「共享前提盲区」限定、排除项零触碰——**全部认可，无错级**。

**实质 diff（3 处，均为增补/合并，无一方需撤回）**：

1. **【漏项 → 补入】F6（gate_common.py:184 Template 表头 latent 软通过面，minor/follow-up）**。来源：gate1 handoff 第④条（本次亲手清点的 11 条之一，suggestion 级）。final-synthesis §三 无此项；Fable 报告未提。三方第一手验证属实（handoff 原文 + gate_common.py:184 实读 + gate0-exemptions.md:22 实读）。当前无害（error 文案小写），latent 风险（error 串含大写 Template 即误判 owned 放行），性质与 A2 同类（残留静默面），应入 follow-up 桶。
2. **【漏项 → 补入】R2（mission.md:51 错误前提未更正，info/记录）**。本次 grep 第一手：mission.md:51 原文「CI 的 gate-tests job 持有 GH_TOKEN 与 gh api 访问，不受影响」，与 architecture.md:53 的更正注记并存。r2 终审 D7 只裁定「13 job」项，未覆盖此条。mission 内部文档、无仓内影响 → 记录桶。
3. **【重复项合并】final-synthesis §三 的 A5 独立行（「suggestedFix 未采纳 / minor / 记录」）与 heartbeat handoff 披露为同一事实的两次登记**——A5 已被 I2（分类面，立即修）+ F5（代码面，follow-up）吸收，原 A5 行撤销，避免总表出现「同一问题三个 ID」。同时 final-synthesis §三 处置汇总中「记录：A3、A5」应更新为「记录：A3」。

**口径收窄（非 diff，归档时反映）**：final-synthesis §六「残余不确定项：全量 2401 测试未独立重跑」应按 Fable T3-3 收窄为「GitHub Actions 在最终 head 527a435 上的 pytest/test-groups 第三方 pass 已实质覆盖，残余仅环境差异」。final-synthesis 文本写作时点未含此限定，归档版应补。

**无漏项声明**：11 条 handoff discoveredIssues 全部有着落——①planning CI 前提证伪→R2+architecture.md:53 已更正部分；②CI 传输未端到端实测→final-synthesis 残余不确定项（PR 全绿事实闭环）；③→F2/A2；④→F6；heartbeat 1 条→I2/F5；core-delivery 2 条（harness 遮蔽，环境问题非 mission 缺陷）→不立条（工具链环境事项，两条同质合并）；zero-red ①→同 core-delivery ①② 性质（transmission 未实测）已并入残余不确定项口径、zero-red ②（harness 遮蔽）同前合并；decision-doc 2 条→I6（:135 时滞）+R4（.gitignore 行号，architecture.md:148 已更正故降记录）。清点无孤儿。

---

## Y3 修复建议预研（仅建议，不实施）

### Y3.1 A5 最小修复面

**分类面（立即修，零代码）**：

- 决策文档增补节（read-first-CRUD 只增不改）：liveness conclusion 由态一迁至**态二「有实现·条件生效」**，写明生效条件 =「dispatch 被拒 或 measured age > 8h」；3 连 failure + dispatch 接受场景的实际效果 = 日志 ALERT + 自愈 dispatch，不建告警、不返非零、无红
- README:191 bullet 补限定（如「判 stale；告警面受 INFRA-597 自愈抑制链约束——仅 dispatch 被拒或 age>8h 时产生告警 issue」）
- 与 I1（:187 重指向）、I3（docstring）同一 docs PR（见 Y3.2）

**代码面（F5，follow-up mission——main() 编排在原白名单外，必须走新 mission）**，两个方案：

- **方案 α（推荐，最小语义改动，~3 行 + 测试）**：
  1. `_check_workflow_liveness` 返回 dict 增加 `conclusion_streak` 键（`:343` 已算出 all_failed_streak，目前仅分支内使用）。**实现复杂点**：该函数有四个 return 出口（`:361-370` streak 提前返回、`:371-383` age 路径、`:384-387` 异常兜底、`:293-303` gh 失败/空 history 提前返回），每个出口都需正确携带（或重构为出口统一组装）；四个 early-return 场景下 streak 语义分别为 True/按 age/False/False
  2. `:903` 改为 `stale_alertable = not liveness["alive"] and (not dispatch_accepted or severe_outage or liveness.get("conclusion_streak", False))`
  3. `:906` 的 effective_liveness 同步加第三析取（否则告警建了、同 tick 又被 resolve_cleared_alerts 关掉——自相咬）
  4. 保留 INFRA-597 语义不变（dispatch 失败路径、severe 路径原样）
- **方案 β（语义最干净，改动面大）**：把 failure-streak 提升为独立 anomaly 类型（`scanner_failed_streak`）：新增 marker 常量 + `_build_alert_body`/`extract_recorded_anomalies`/`compute_current_anomalies`/resolve 规则四处改动。streak 与 age 本是正交失效类，独立维度可消除「streak 借道 stale_alertable」的耦合；但新增告警文案需同步 evaluate 现有告警去重（alert_issue_exists 按标签去重，两类异常共享 issue body，marker 解析需兼容混合 body）
- **必配测试（main() 级全 mock，5 条）**：
  1. 3 连 failure + dispatch accepted + age fresh → **rc==1 且 create_alert_issue 被调用**（当前行为返 0，此测试先红即 TDD 锚）
  2. 3 连 failure + dispatch rejected → rc==1（既有语义回归保护）
  3. streak + age>8h + dispatch accepted → rc==1（Y1.2 交叉场景，穿透路径回归保护）
  4. 2 failure + 1 success + dispatch accepted → rc==0（grace 窗口语义不回归）
  5. `--json` 实参断言锁（与 F3/D3 同一 PR 一并修，先例 `tests/test_trigger_ci_droid_fallback.py:1093`）

### Y3.2 「D1/D5/D6/D7 立即修单一 PR」可行性核验

**「单一 PR」只覆盖仓内文件改动，D5/D6/I6 根本不在 git 内**：

- ✅ 可入 PR：I1（README.md:187，仓内）+ I3（tests/test_ci_structure_contract.py:477 docstring，仓内）+ I2 的 README 半边（:191 限定语）。三处改动一个 docs PR，合计 ~5 行 diff，**可行且推荐**
- ❌ 不在 PR：I4/D5（`evidence/` 是 **untracked**——本轮 `git status ?? evidence/` + `git check-ignore` exit 1 实证，0 字节证据文件是本地工件，补跑 actionlint 是本地命令）——与 I5/D6 的「evidence/ 处置声明」绑定执行；I5/D6（main fast-forward、删本地分支、处置声明）纯本地 git 操作；I6/D7a（决策文档回填）与 I2 的决策文档半边（迁类增补节）都是 KB 增补，不入 git
- 结论：**立即修应拆三个执行通道**——①仓内 docs PR（I1+I3+I2-README 半边）；②本地 git/工件序列（I4+I5，操作前备份、顺序：ff main → 删分支 → actionlint 补捕获 → evidence/ 处置裁定三选一）；③KB 增补序列（I6 回填 + I2 决策文档迁类 + 4+2 项 follow-up 登记：F1–F6。注意：4 项 follow-up 的前次写入已被 memory_kb 域守卫拦截（preserve-and-escalate），本轮登记需按 kb_policy 走 escalate 通道或由用户裁定放行）
- 风险点：I1 重指向目标——docs/architecture.md 当前**不含**四防线内容（r2 终审第一手），要么新增小节、要么改自含描述；**不可指向本地 KB 决策文档**（gitignored，不入 git，r2 处置原判正确）

---

## 本轮结论

1. **A5 终裁：与 Fable 一致，升 major 成立。** 八步控制流独立复现；(a) 因果成立（cron 双窗 + 自愈 dispatch 双机制把 age 永续压在 8h 下，dispatch 失败子场景是唯一告警窗口——自举倒挂）；(b) 关闭面三重限定（evolution-heartbeat 标签 × body marker 可解析 × recorded 全清），coverage 维度不误伤，该子项无缺陷；(c) 无跨 tick 逃生门。对 Fable 的两点精确化：severe 穿透在「停触发+历史 streak」交叉场景可达（修复测试须双场景锁定）；「结构上不可达」限定于持续触发场景。
2. **最终问题总表 diff：无错级，3 处实质增补/合并**——补入 F6（gate_common.py:184 Template 表头 latent 面，minor/follow-up，三方第一手验证）与 R2（mission.md:51 错误前提，info/记录）；A5 拆为 I2（分类面/立即修）+ F5（代码面/follow-up）并撤销 final-synthesis 原 A5 行避免三重登记。其余 19 项与 final-synthesis+Fable 修订版一致。
3. **修复预研**：代码面推荐方案 α（`conclusion_streak` 透传 + `:903`/`:906` 双点加第三析取，~3 行 + 4 个 return 出口处理 + 5 条 main() 级测试）；「单一 PR」仅覆盖 I1+I3+I2-README（仓内 ~5 行），I4/I5 走本地序列、I6/I2-决策文档走 KB 增补通道（前次登记被守卫拦截，需 escalate），立即修应按三通道执行而非强行单 PR。
