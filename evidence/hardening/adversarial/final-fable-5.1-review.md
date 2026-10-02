# Fable 5.1 独立终审复核（对四模型两轮对抗审计链的第二意见）

- 复核员：Fable 5.1（claude-fable-5.1），独立于 R1/R2 四模型，全程只读
- 复核时间：2026-09-23
- 被审对象：`~/infraro-core/evidence/hardening/adversarial/final-synthesis.md`（修正版）及其 r1 ×4 / r2 ×3 子报告；原始被审物为 mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（PR #141，squash `bc810b7`）
- 证据基准：本地工作树 HEAD `527a435`（`git diff --stat HEAD origin/main` 为空，即与 `origin/main` = `bc810b7` 树逐字节一致）；gh 只读查询；mission 目录只读
- 未执行任何测试（按任务约束）；凡靠代码静态追踪得出的结论均标注「静态验证」
- 术语：下文「决策文档」指本地 KB 中 decisions 子目录下的 `architecture-evolution-goal-correction.md`（gitignored，不入 git）

---

## T1 重验记录

| # | 原论断 | 复核结果 | 我的证据 |
|---|---|---|---|
| ① | README.md:187 引用 `docs/architecture.md` §2.4，而该文件无 §2.4 | **已验证** | `README.md:187` 原文「详见 docs/architecture.md §2.4」；`docs/architecture.md` 全部标题 `## 1`(1.1/1.2) / `## 2 命名契约`（无子节）/ `## 3`–`## 8`；`grep -n '2\.4' docs/architecture.md` 零命中。在 `origin/main` 树上同样成立（`git show origin/main:README.md` 第 187 行相同；`origin/main:docs/architecture.md` 无 2.4） |
| ② | D2 时间线：README 提交晚于 user-testing synthesis；readme feature `fulfills: []` | **已验证** | `git show -s --format=%ci 527a435` = 2026-09-22 23:28:57 +0800；`validation/hardening/user-testing/synthesis.json` mtime 23:22:15 +0800（差 +6m42s）；`validation-state.json` mtime 23:22:15；user-testing handoff 23:22:38；readme handoff 23:30:28；PR 合并 23:34:31。`features.json` 中 `readme-defense-hardening-note.fulfills = []`，其余 5 个 spec feature 共 21 个 VAL-* 断言与 `validation-state.json` 21 条 passed 一一对应。readme feature 的 `preconditions[0]` 原文即「milestone hardening 已验证（21/21 断言 passed）」，description 原文含「README.md 为本 feature 唯一新增可改文件（白名单外旧约束不适用于本 feature 的 README.md）」——白名单豁免确系 orchestrator 预先授权 |
| ③ | `evolution_heartbeat.py:322-323` 请求 `--json status,conclusion,createdAt`，`:344` 按 `conclusion == "failure"` 判 streak；测试文件 `--json` 零命中 | **已验证** | `origin/main:src/infra_core/engine/evolution_heartbeat.py:322-323` 为 `"--json", "status,conclusion,createdAt"`；`:343-345` 为 `all_failed_streak = len(runs) >= CONSECUTIVE_FAILURE_STALENESS and all(run.get("conclusion") == "failure" ...)`；`:54` `CONSECUTIVE_FAILURE_STALENESS = 3`。`tests/test_evolution_heartbeat.py`：`grep -c -- '--json'` = 0；`grep -E 'timed_out|cancelled|startup_failure'` 零命中 |
| ④ | `ci.yml:548`（droid-review 轮询）< `:563`（零红聚合）+ `TestCiOkStepOrder` 锁 | **已验证** | `.github/workflows/ci.yml:548` `- name: Check droid-review status`（调 `scripts/check_droid_review.sh`），`:563` `- name: Zero-red aggregation`（`:577` 调 `scripts/check_zero_red.sh`）。`tests/test_ci_structure_contract.py:448-520`：`TestCiOkStepOrder.test_droid_review_polling_precedes_zero_red_scan` 以脚本名为锚点、要求锚点唯一、断言 `droid_review_at < zero_red_at`；另有 `test_moved_zero_red_step_keeps_execution_wiring` 锁 GH_TOKEN 与实参。锁的强度足够（锚脚本名非 step 名，名字漂移不致失效） |
| ⑤ | PR #141 真实全绿并 squash 合并至 main | **已验证** | `gh pr view 141`：state=MERGED，mergedAt=2026-09-22T15:34:31Z，baseRefName=main，mergeCommit=`bc810b71e339…`，headRefName=`feat/audit-defense-hardening`，5 个 commit（9d7b6ae/5ff9cd2/4a44a10/a4d2ae3/527a435）。`gh pr checks 141`：30 个 check 除「Full Regression (Nightly only)」skipping 外全部 pass，含 ci-ok / droid-review / quality-gate / substrate-gate-suite / pytest / test-groups（这些 check 对应 PR 最终 head `527a435`，即 README 提交在内）。`git branch -r --contains bc810b7` → `origin/main`；`git branch -r --contains 527a435` 为空（squash 后原 SHA 不在 main，符合预期） |
| ⑥ | final-synthesis.md 修正版不再含「4 项 follow-up 已登记」失实陈述 | **已验证** | `grep -nE '已登记|待补|拦截|登记' final-synthesis.md`：第 7/99/151/153 行均为「待补」「被守卫拦截」「当前未写入」口径；唯一「已登记」出现在第 130 行，指的是 spec **排除项**（B1/watchdog/F8/P3/B5）已在决策文档 backlog 表登记——该陈述属实（决策文档第 85 行 `## 排除范围与后续 backlog` 存在且列出上述项） |

附加验证（非任务指定但为 T2/T3 所需）：

- 决策文档：sha256 = `dd100ace5a3273571bdf550941b27abb7942b0d267212f09e497a1111306d9a2`，15301 bytes，与 handoff 记录的「15301 B，sha256 dd100ace…」一致（**已验证**，P0 交付物自 handoff 后未被改动）。
- 「决策文档走本地 KB 不入 git」的用户裁定：`mission.md:19` 明文「用户裁定：决策文档走本地 KB 写入（read-first-CRUD），不进 git」；`.gitignore:54` 忽略整个 memory 工件目录；`git ls-files` 在该目录下计数为 0（**已验证**，偏差有留痕）。
- spec 对 P1 的批准口径：spec 终稿第 53 行「heartbeat liveness 补 conclusion 判定（连续 failure 视为 stale）」（**已验证**，D4 的「字面 failure」确为批准口径）。

---

## T2 裁定挑战

### 2.1 「带条件完成」 vs 「部分完成」 vs 「5/5 完成」

**我的意见：标签维持「带条件完成」，但条件清单不完整，需增一条。** 理由：

- 不是和稀泥。三个标签衡量的对象不同：主审的「5/5」只量 spec 5 个 feature 的落地（事实成立）；DS4 的「部分完成」把收口件缺陷算进交付主体（过重——README 收口件是 mission 自加的第 6 个 feature，不在 spec 整改表内）；终审「带条件完成」正确地把两者拆开。这个折中有清晰的判据边界，可复核。
- 但终审列出的「条件」（立即修 ×4 / follow-up ×5）漏掉了一项**实质性**条件，见 T3-1：P1 liveness conclusion 防线在当前 main 上**不能产生任何人可见告警**，而决策文档把它归入「态一：CI 内阻断」、README 把它写成「连续 3 次 failure 判 stale」。这恰是 P0 要根治的「把效果写过头」问题在新交付物上的复现。审计链知道这件事（heartbeat handoff discoveredIssues 第 1 条、r1-glm53-primary:55、r2-ds4-attack:29/60-61/177 均有记录），却把它降成 A5「minor / 记录（suggestedFix 未采纳）」。
- 修订版条件：在「立即修」桶追加第 5 项——决策文档以增补节（read-first-CRUD 只增不改）将 liveness conclusion 从态一迁至态二并写明生效条件（dispatch 被拒 或 age > 8h）；README `:191` bullet 补一句限定（如「判 stale；告警面受既有自愈抑制，见决策文档」）。这两处与 D1 同一小 PR/同一 KB 增补即可闭环。

### 2.2 D1 定级：major（不升 critical）

**同意 major。** critical 应留给「行为/治理面被绕过或失真」的缺陷；D1 是入口文档一处不可达指向，bullet 内容属实，无消费路径依赖该锚点，无自动化工具会解析 `§2.4`。DS4 的 critical 主张更像是对「P0 语境敏感度」的强调而非严重度判定。维持 major、立即修，合理。

### 2.3 D4「符合批准范围」是否低估运行时风险

**分两层回答。**

- 就 D4 本体（`timed_out/cancelled/startup_failure` 不计入 streak）：**不低估，Info 成立**。实证：`gh run list --workflow evolution-scan.yml --limit 100` 的 conclusion 直方图为 `failure: 6, success: 83`，最近 30 次全 success，**零** timed_out/cancelled。触发条件在实际数据中不出现，按「理论性问题触发条件不现实的降级为 Info」原则，Info 正确。且用户裁定与 spec 均为 failure 字面口径，实现无越权。
- 但父代理提问的前提「持续 timed_out 的 scanner 永不告警」抓错了对象：**在当前 main 上，即使是持续 `failure` 的 scanner 也不会告警**（见 T3-1）。真正的运行时风险不在 conclusion 枚举宽窄，而在 `main()` 的 INFRA-597 抑制把整个 conclusion 维度的告警吞掉。审计链把这个问题绑在 D4/A5 两个 minor/info 项下，是**低估**。

### 2.4 A2 的 minor/major 双标签

**不自洽，建议单标签。** 一行两个 severity（「minor（残留面）/major（未登记）」）把「缺陷本身」和「缺陷的登记状态」混在一个 severity 字段里。登记状态是流程属性，不应有独立 severity，尤其在 final-synthesis 头部已把「4 项 follow-up 登记待补」作为全局条件之后，「major（未登记）」就与全局条件重复计数。建议：A2 = minor（残留面），登记状态归入「待补登记块」统一跟踪。

### 2.5 「21/21 构成 by omission 误导」是否公允

**部分公允，措辞需收窄。**

- 支持终审的部分：终审已把裁定限定为「用于**最终交付态**时」构成误导，并明确承认白名单豁免是 orchestrator 预先授权（r2-glm53-adjudication D2 (a)）。features.json 的 readme feature 把「21/21」写在 `preconditions` 里，表明 orchestrator 自己也把 21/21 视为 README 之前的里程碑门，而非最终交付认证。因此若有人拿「21/21」为 9 文件终态背书，确属过度引用。
- 不公允的部分：原表述「README 收口件逃逸**验证**覆盖」夸大了。README 提交 `527a435` 是 PR 最终 head，`gh pr checks 141` 所列 pytest / test-groups / quality-gate / ci-ok 等 29 个 pass 的 check 都跑在这个 head 上——README 没有逃逸 **CI** 覆盖，逃逸的是**断言级内容核验**（没有任何 VAL-DOC 断言检查 README 的指向是否可达）。这也是 D1 能漏网的真实机制：CI 不检查 Markdown 内的 `§x.y` 文本锚点。建议把 D2 表述改为「收口件零断言覆盖（CI 覆盖存在，内容级核验缺失）」，severity 维持 major（流程面），因为经验事实是唯一实证缺陷正落在这里。

---

## T3 盲区发现（severity 标注）

### T3-1 【major｜效果层 + 分类层】liveness conclusion 防线在 main 上不产生可见告警；决策文档态一归类失当

**证据链（静态验证，均为第一手代码）**：

1. `evolution_heartbeat.py:343-345` streak 命中 → `:361-369` 置 `alive=False`，但 `hours_since_last_run` 仍为最新一次运行的年龄（fresh 场景下远小于 8h）。
2. `main()`（`:846-870`）：`alive=False` → 先 `trigger_scanner_dispatch()`（`workflow_dispatch` 一个存在但持续失败的 workflow，GitHub 会接受）→ `dispatch_accepted=True`，`stale_hours` 不大于 `SCANNER_SEVERE_STALENESS_HOURS = 8`（`:46`）→ 进入 else 分支「Self-heal dispatch accepted; suppressing scanner_stale alert」。
3. `:899-902`：`severe_outage = False`，`stale_alertable = not alive and (not dispatch_accepted or severe_outage)` = **False**。
4. `:905` `effective_liveness = {"alive": alive or (dispatch_accepted and not severe_outage)}` = **True** → `compute_current_anomalies` 不含 scanner_stale → `resolve_cleared_alerts` 会把**已存在的** scanner_stale 告警 issue 当作「已恢复」关闭。
5. `:925-940`：`anomaly_count` 不计 stale → `main()` 返回 **0**，heartbeat workflow 保持绿。
6. 每个 heartbeat tick 都会再 dispatch 一次失败的 scanner，制造新的 fresh failure run，使年龄永远重置在 8h 以下——**抑制条件被防线自身的自愈动作永续满足**，`severe_outage` 路径在此场景下结构上不可达。

**结论**：在「scanner 每次都跑但每次都失败」这一 P1 的目标场景中，新防线的全部可观测效果 = heartbeat 运行日志里一行 `[heartbeat] ALERT: Scanner stale: last 3 runs concluded failure` + 一次冗余 dispatch，并可能**关闭既有告警**。这与决策文档「态一：CI 内阻断（有实现，在 required 链内）…归入态一的依据：四项全部在 CI 内以红/绿判定」直接矛盾——heartbeat 不是 required check，也不会红。正确归类是**态二「有实现·条件生效」**（条件：dispatch 被拒或 age > 8h）。README `:191`「连续 3 次 failure 判 stale」字面为真但效果层过头。

**审计链的处理**：heartbeat worker 在 handoff 里如实披露并给出 suggestedFix（「决策文档态一分界可按此口径描述」）；R1 主审判「spec 裁定 main() 不动范围内的既有行为，非缺陷」；R2 DS4 判「建议未落地，nice-to-have，不构成缺陷」；终审归为 A5 minor 记录。**四模型一致接受了「范围内=非缺陷」的推理跳跃**：`main()` 不动是范围裁定，但把一个效果被抑制的防线写进「态一阻断」是**分类失实**，落在 P0「不得把不存在的写成已存在」的管辖范围内，与范围裁定无关。这是本次复核发现的唯一与终审结论存在实质分歧的项。

**处置建议**：立即修（零代码）——决策文档增补节迁类 + README bullet 限定；follow-up（需新 mission，改 `main()`）——在 suppression 条件加入「failure-streak 不可抑制」维度（worker 的 suggestedFix 原文），并补一条 `main()` 级测试锁定「3 连 failure + dispatch 接受 → 仍建告警」。

### T3-2 【info】file:line 锚点在 origin/main 上无偏移

`git diff --stat HEAD origin/main` 为空，本地 `527a435` 工作树与 `bc810b7` 逐字节一致；在 `origin/main` 树上直接核验了 README.md:187、docs/architecture.md 无 2.4、evolution_heartbeat.py:322-323/343-345、ci.yml:548/563/577，全部吻合。squash 只改变提交历史不改变树，审计报告的 file:line 可直接用于 main。此盲区不成立。

### T3-3 【minor】「全量 2401 无人独立重跑」的表述漏掉了一条现成的独立证据

审计链自认无人重跑全量套件，依赖 mission 自证。但 `gh pr checks 141` 显示 `pytest`（1m17s）与 `test-groups`（49s）两个 job 在最终 head `527a435` 上 pass——这是由 GitHub Actions 独立于 mission 产生的第三方证据，覆盖了含 README 提交的最终交付态。审计链应引用它来缩小「未独立验证」的口径，而不是笼统写「PR 全绿」。本复核同样未重跑（任务约束），但认定此缺口已被 CI 证据实质覆盖，降为 minor（剩余风险仅为 CI 环境与本地环境的差异）。

### T3-4 【minor】证据链信任根：单机、未跟踪、无哈希清单

- `evidence/` 未跟踪且未被 `.gitignore` 覆盖（`git status` = `?? evidence/`）；8 份审计报告中 5 份权限 0600，无任何 sha256 清单，报告互相引用但无外部锚。
- 决策文档（P0 交付物）被 gitignore，单副本、0600、无版本历史；其完整性目前只能靠 handoff 中截断的 `dd100ace…` + 15301 B 佐证（本次已核对一致）。
- 「既有 3 份决策文档写前写后 sha256 byte-identical」：**无法验证**——mission 全部 handoff / library / transcript 中未记录这 3 个文件的任何 64-hex 哈希值，只有一句自述。弱佐证：三文件 mtime 分别为 09-18 16:53 / 09-21 01:12 / 09-22 11:48，全部早于 mission 启动（09-22 21:00），支持「未在 mission 期间改动」，但 mtime 可保留可伪造。
- 缓解：本复核所依赖的**关键事实**（README 悬空、时间线、代码行、PR 状态）全部可从 git/gh 独立重导出，审计链的结论不依赖 evidence/ 目录本身的可信度；受信任根影响的仅是「决策文档内容未被事后改写」与「3 文档零改动」两项。定 minor，建议追加一份 sha256 清单（evidence/ 报告 + 本地 KB decisions 目录全部 .md）作为后续复核锚点。

### T3-5 【info】D6「立即修」桶尚未开始执行

复核时 `git status --short --branch` 仍为 `## feat/audit-defense-hardening...origin/feat/audit-defense-hardening [gone]`，本地仍停在已删除上游的 feature 分支。与终审描述一致，仅记录状态未变。

---

## T4 终局裁决

**这条审计链在事实层可采信收口；「带条件完成」维持，但条件清单需增补一项「liveness conclusion 防线效果层/态一归类修正」，并将 A5 从 minor/记录 升为 major/立即修——四模型两轮共同的盲区不是漏看事实，而是把一处 P0 同类的分类失实误当成范围内的措辞偏好。**

采信度评级：

- 事实层（file:line、时间线、PR/CI 状态、fulfills 映射、白名单授权）：**高**——6 项抽验全部已验证，零矛盾。
- 定级层：**中高**——D1 major / D3 minor / D4 info / D5–D7 合理；D2 措辞夸大（「逃逸验证覆盖」应为「零断言覆盖」）；A2 双标签不自洽；A5 显著低估。
- 方法论结论「单模型单审必有漏且各漏不同项；四模型两轮后事实层零分歧」：**成立但需加一句限定**——四模型在事实层零分歧，同时在**推理层**共享了同一跳跃（范围裁定 ⇒ 非缺陷），说明多模型交叉对「事实遗漏」有效、对「共同前提」无效；这类盲区需要换提问框架而非加模型。

我推翻/修订的终裁项：

| 项 | 原终裁 | 我的版本 |
|---|---|---|
| A5 | minor / 记录（suggestedFix 未采纳） | **major（分类失实）/ 立即修**：决策文档增补节将 liveness conclusion 由态一迁至态二并写明生效条件；README :191 bullet 加限定；follow-up 登记「suppression 条件加 failure-streak 不可抑制 + main() 级测试锁」 |
| D2 | major「README 收口件逃逸验证覆盖」 | major 维持，措辞改为「收口件零**断言**覆盖（CI 覆盖存在于 head 527a435，内容级核验缺失）」 |
| A2 | minor（残留面）/ major（未登记） | 单标签 minor；登记状态并入全局「待补登记块」 |
| 「带条件完成」的条件清单 | 立即修 ×4 / follow-up ×5 | 立即修 ×5（+A5 分类修正）/ follow-up ×6（+failure-streak 穿透抑制） |
| 方法论结论 | 「四模型两轮后事实层零分歧」 | 增加限定：「事实层零分歧，推理层存在共享前提盲区（范围裁定被等同于非缺陷）」 |

未推翻：mission 终判标签、D1 major、D3 minor、D4 info（本体）、D5/D6/D7、模型互评、排除项零触碰、P0–P2 五项落地事实。

---

## 对审计链的改进建议

1. **把「范围内」和「非缺陷」拆成两个判断**。对每条 worker discoveredIssue，先问「事实是否被文档/分类如实反映」，再问「修复是否在范围内」；范围外只能推迟修复，不能豁免失实。
2. **决策文档的三态分界应有可执行判据**，例如「态一 = 该面失效时某个 required check 必红」；按此判据 liveness 显然不属态一。审计时对每个态一条目跑一遍判据，而不只核 file:line。
3. **收口类文档 feature 至少配一条内容级断言**（如「README 中所有 `§x.y` / 相对路径指向在目标文件中可解析」），并把 Markdown 内部锚点检查加入 CI（当前 CI 不检查文本锚点，这是 D1 漏网的机制层原因）。
4. **证据目录出哈希清单**：evidence/ 全部报告 + 本地 KB decisions 目录全部 .md 的 sha256 写入一份 manifest，随报告一起归档；以后凡自述「byte-identical」必须附前后哈希值。
5. **引用现成的第三方证据**：审计报告在声明「全量未独立重跑」时应同时引用 `gh pr checks <PR>` 中 head 提交上的 pytest/test-groups 结果，明确剩余缺口只是环境差异。
6. **多模型审计增加「共享前提审查」环节**：R2 结束后由一名审查员专门列出所有模型**一致接受**的前提（如「spec 裁定 main() 不动 → 非缺陷」），逐条挑战，这比再加一个模型更能击中本次这类盲区。
