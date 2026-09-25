# R1 红队报告：攻击「mission 已完成」声明（DS4.1-Flash，只读对抗审计）

审计对象：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（架构演进目标修正沉淀 + 审计防线加固）
权威基准：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`（三轮终稿）
被审交付：PR #141（`feat/audit-defense-hardening`，MERGED 2026-09-22T15:34:31Z，merge commit `bc810b71`）
审计时间：对现树只读复核（已 fetch，origin/main = bc810b71）

---

## 总判定

**部分完成（非整体虚报）**：四个代码防线项 + P0 决策文档真实落地、经测试与 CI 验证并合并；但 mission 的「收口件」README.md 新增节引入一处可复核的事实缺陷（指向不存在的 `docs/architecture.md §2.4`），且该 README.md 变更发生在 user-testing 验证之后、落在 VAL-CROSS-002 白名单之外、无任何断言覆盖——因此「21/21 断言通过」不足以认证最终交付物，完成声明带未披露的收口缺陷与验证覆盖缺口。

---

## 逐攻击面发现

### (a) README.md「防线现状」节 vs 代码现实

**[major] F-01 README.md 指向不存在的文档节 `docs/architecture.md §2.4`**
- 证据：`README.md:187`（新增节首句）「本仓库已完成四层防御体系加固，详见 [`docs/architecture.md`](docs/architecture.md) §2.4。」
- 反证：`docs/architecture.md` 的一级/二级标题仅有 `## 1`（含 1.1/1.2）、`## 2 命名契约`、`## 3`、`## 4`、`## 5`（5.1）、`## 6`、`## 7`、`## 8`——**不存在 §2.4**（grep `^### 2.4`/`2\.4` 在 `docs/architecture.md` 零命中；`§2.4` 只存在于 `docs/governance/source-of-truth.md` 等无关文档）。
- 加剧事实：`docs/architecture.md` 内 grep `fail-closed|conclusion|零红|Gate1|AST|加固` 只命中既有的 governance/命名契约段落，**根本没有本 mission 四项防线的描述**——即被指向的文档既无该节、也无该内容。
- 同 README.md 其余引用（`:9` §1.1、`:104` §7/§C9）均有效，仅 §2.4 悬空，属孤立缺陷。
- 定性：这是「把不存在的文档内容写成已存在」——与 P0 原罪（未来态写成已实现态）同一失效类别，只是载体从「能力」变成「交叉引用」。因四项 bullet 本身各自属实（见 F-06），故定级 **major** 而非 critical；但 README.md 是本次 P0 语境下最敏感的收口件，建议按 critical 优先级修复。

**[major] F-02 README.md（第 9 个文件）在验证之后落地、落在白名单外、零断言覆盖**
- 证据链：
  - `features.json` 中 `readme-defense-hardening-note` 的 `"fulfills": []`——该 feature 不对应任何验证断言。
  - `validation-contract.md` 的 `VAL-CROSS-002`（范围护栏）白名单**不含 README.md**（列 8 个受控文件 + 本地决策文档）。
  - user-testing `synthesis.json` cross 组自述「Exactly 8 whitelisted files changed」；证据 `evidence/hardening/cross/diff-stat.txt` 为 **8 files / 827 insertions**，而最终 PR #141 为 **9 files / 836 insertions**（`gh pr view 141 --json files`）。
  - 时序：user-testing handoff 15:22:38 → readme handoff/state 15:30:28；README.md commit `527a435` 在验证之后。
- 结论：21/21 认证的是「8 文件状态」；README.md 这一 audit-sensitive 收口件既无断言、也未纳入白名单复核，缺陷（F-01）因此漏网。属验证覆盖缺口，非代码缺陷。

**[minor] F-06 四项 bullet 与代码现实的逐条核对（结论：均属实）**
- 「接口门（Gate1）fail-closed：模板/tags 不可验证时显式红」——属实：`substrate/gates/gate1_interface.py:267-271`（模板空→错误）、`:299-306`（`tags_unavailable`）、`:327-332`（非零退出）；LOCAL-ONE 软通过分支已删（源码 grep 零命中，`test_no_local_one_soft_pass_remains` 锁）。
- 括注「CI 模板面经公开仓 tarball 兜底首次真实验证」——**部分证实（路径归属为推测）**：PR #141 的 `substrate-gate-suite` job（id 106813366167）日志显示 gate1 在 CI 内真实取到模板面（`- template calls checked: 7; engine tags visible: 6`）且通过；但日志未打印模板解析路径，无法直接证明走的是 tarball 而非 `gh api`。因 `ci.yml:615-616` 的 gate1 step **不注入 GH_TOKEN**（对比 gate2 `:619-621` 有），无凭证下 `gh_api`（`gate_common.py:121` 走 `gh api`）大概率失败→落 tarball，故括注方向可信，但「首次真实验证」的路径归属在证据上未闭环（见「无法定论项」）。
- 「heartbeat liveness conclusion 维度：连续 3 次 failure 判 stale」——属实（见 (c)）。
- 「零红聚合时序：ci-ok 内零红聚合在 droid-review 轮询之后执行（step-order 契约锁定）」——属实：`ci.yml:548`（droid-review）< `:563`（零红）；`tests/test_ci_structure_contract.py::TestCiOkStepOrder` 锁死。
- 「Core↛Delivery 反向 import：由 AST 契约测试强制」——属实：`tests/test_core_delivery_import_contract.py`（真 `ast.parse`/`ast.walk`），CI pytest step（`ci.yml` pytest 无 `-m` 反选，marker `business_policy` 照跑）内生效。

**[info] F-10 README.md 标题句「本仓库已完成四层防御体系加固」口径偏宽**
- 三态分界显示仍有多项处于态二/态三：F8 live 断言 CI 内 SKIP、Gate1 存量 6 模板豁免、Evolution Governance 非阻断、watchdog dispatch-only、`.evolution` 走 actions/cache（终稿 §3）。README.md 用「已完成…加固」总起，若被读成「四层架构的防线全部到位」即为 overclaim；按 bullet 读则仅指本 mission 四项。建议限定为「四项审计防线加固」。

### (b) mission 自报证据链可信度

抽查 `evidence/hardening/` 8 份证据（>4）结论：多数与结论匹配，但存在 1 份空证据 + 2 份「快照过期/弱证据」。

**[minor] F-03 VAL-CI-003 的 actionlint 证据文件为空（0 字节）**
- 证据：`evidence/hardening/ci/VAL-CI-003-actionlint-output.txt` 大小 **0 字节**（`ls -la`/`wc -c` 实测）；`evidence/` 全目录 grep `actionlint` 仅命中无关的决策文档摘录。
- 反证：user-testing `synthesis.json` ci 组自述「47/47 CI structure tests + **actionlint exit 0** + 32/32 twin script tests」——`actionlint exit 0` 这半条断言**无捕获证据**。
- 缓解：本机装有 `/opt/homebrew/bin/actionlint`，且改动后的 `ci.yml` 确实在 PR #141 内被 GitHub 成功解析执行（CI 全绿），故底层结论几乎必然为真；缺陷在证据完整性而非结论真伪。定级 **minor**。

**[minor] F-07 diff-stat 证据为「验证前快照」**
- 证据：`evidence/hardening/cross/diff-stat.txt` / `VAL-CROSS-002-diff-stat.txt` / `heartbeat-import/git-diff-stat.txt` 三份内容一致，均为 **8 files / 827 insertions**，与最终 PR 的 9 files 不符（README.md 尚未加入）。即证据固化在 README.md 落地之前。

**[info] F-11 弱证据文件**
- `evidence/hardening/gate1/grep-local-one.txt` 内容仅 `Exit code: 1`（grep 无命中）；`evidence/hardening/cross/VAL-CROSS-002-excluded-check.txt` 仅 `0`/`0`——缺少被检命令与上下文，可读性/可复核性弱（真正有牙齿的是 `test_no_local_one_soft_pass_remains`）。

**[info] F-12 可复现性抽验（通过）**
- 重跑四个受影响测试文件：`tests/test_gate1_fail_closed.py test_core_delivery_import_contract.py test_ci_structure_contract.py test_evolution_heartbeat.py` → **160 passed in 0.56s**，与 `gate1/pytest-output.txt`（10 passed）、`heartbeat-import/full-pytest-output.txt`（103 passed）、`ci/VAL-CI-001-002-structure-test-output.txt`（47 passed）、`ci/VAL-CI-003-twin-test-output.txt`（32 passed）的结论一致。全量 2401 未重跑（见「无法定论项」）。

### (c) 四代码项是否只做表面

**[minor] F-04 heartbeat conclusion 判定：真实数据路径已接上，但缺「数据接缝」回归锁**
- 真实路径已接上（非仅 mock）：`src/infra_core/engine/evolution_heartbeat.py:320-323` 实调 `gh run list --limit 5 --json status,conclusion,createdAt`，`:343-344` 据此算 `all_failed_streak`——`conclusion` 字段确被请求，生产路径可用。
- 但接缝无锁：`tests/test_evolution_heartbeat.py` 全部新增用例 `patch("evolution_heartbeat.subprocess.run")`（`_gh_result`/`_recent_run` 直造 dict），**没有任何断言校验 `gh` 实参含 `conclusion` 字段**（grep `--json`/`status,conclusion` 在测试文件零命中）。若将来有人从 `--json` 列表删掉 `conclusion`，`run.get("conclusion")` 恒 None → `all_failed_streak` 恒 False，防线**静默失效**，而全部测试仍绿。这正是本 mission 反复要防的「防线无声空转」失效类，属新增防线自身的接缝盲点。

**[minor] F-05 conclusion 仅认字面 `"failure"`，不覆盖 timed_out/startup_failure/cancelled**
- 证据：`evolution_heartbeat.py:344` `run.get("conclusion") == "failure"`。一个持续 `timed_out`/`startup_failure`/`cancelled` 的 cron（如 503 卡死、被并发取消）将维持 `alive=True`。
- 说明：README.md/决策文档均按「连续 3 次 failure」字面表述，与实现一致，**不构成 README.md 失实**；但作为防线完备性是残留缺口（终稿 item 8 的「关机判 stale」只被部分覆盖）。

**[info] F-13 gate1 隐藏软通过面复核（未发现新增软通过；存量豁免与登记表缺陷为设计/既知）**
- `main()` 出口穷举：`:329 return 1`（fail_closed）、`:332 return 1`（new breaks）、`:334 return 0`（唯一 PASS）；无其它 `return 0`/`exit 0`。
- 存量豁免（`:311-317` registered stock 过滤）为终稿承认的设计（态二）；非新增。
- 既知地雷：`gate_common.py:184` 排除集 `{"", "---", "Entry", "Path prefix", "Item"}` **不含 `"Template"`**，而 `gate0-exemptions.md` 的 `## Gate 1` 表头首列正是 `Template` → 被登记为有效 token。当前无 error 串含字面 `Template`（fail-closed 文案用小写 `template`），故未造成实际软通过；但一旦某 error 含 `Template`（大写）即被误判 owned→放行。此点 synthesis 已作为 follow-up 记录，本 mission 未修（超出范围）。

**[minor] F-08 ci.yml 换序后 needs/always() 语义复核（结论：未破）**
- `git diff 0196dcd..527a435 -- .github/workflows/ci.yml` 显示**仅**在 ci-ok job 内搬移 `Check droid-review status` step（由零红之后移到之前），`needs:`（`ci.yml:471-485`，10 项）、`if: always()`（`:491`）、notify-ci-complete（`:639-644`）零变化。既有契约测试 `test_ci_ok_needs_all_blocking_jobs`/`test_ci_ok_always_runs` 通过。

**[minor] F-09 AST 锁复核（结论：真 AST，非字符串匹配）**
- `tests/test_core_delivery_import_contract.py:98` `ast.walk(ast.parse(...))`，覆盖 `ast.Import`/`ast.ImportFrom` + 相对形式 + 函数体惰性 import；扫描面 `engine/` 下 7 个 Core 文件，且 `test_core_module_inventory_is_complete` 断言 `engine/` 无未登记 `evolution_*.py`（实测 engine/ 顶层 9 个 py = 7 Core + 2 Delivery，覆盖完整）。注释/字符串不误报（`test_docstring_mention_is_not_an_import`）。有牙齿（21 组合成违例用例）。
- 残留边界（可接受）：不覆盖字符串动态 import（`importlib.import_module("droid_review")`）；不在 `engine/` 内但属 Core 的模块（如 `src/infra_core/packs/`）不在扫描面——但终稿红线定义（`docs/architecture.md:20`）本就限定 `evolution_*` 核心模块，故合规。

### (d) overclaim 检查（mission.md / features.json / AGENTS.md / README.md / 决策文档）

- **排除项未被伪装成完成**：决策文档 `architecture-evolution-goal-correction.md:85-97` 明确列出 B1/watchdog/P3/B5/F8/消费仓接回为「留待后续」；`:71-77` 态二如实标注。未见排除项被写成已完成。**（通过）**
- **[minor] F-14 mission.md 保留错误前提**：`mission.md`（Architecture 段）称「CI 的 gate-tests job 持有 GH_TOKEN 与 gh api 访问，不受影响」——但 gate1 step（`ci.yml:615-616`）**无 GH_TOKEN**，这正是 feature 1 需要 tarball 兜底的原因（synthesis 的 rejectedObservations 亦自证「gate1 step has no GH_TOKEN」）。规划文档未同步更正，与 shipped 事实相左。
- **[minor] F-15 决策文档收尾元数据过期**：`architecture-evolution-goal-correction.md:135` 记「PR #141 … HEAD `a4d2ae3`」与「8 个白名单文件」；实际 PR head 为 `527a435`、9 个文件（README.md 后加）。
- **[minor] F-16 新测试文档串行陈旧数字**：`tests/test_ci_structure_contract.py` 新增 `TestCiOkStepOrder` docstring 写「needs 边、**13 job 集合**…」，而 mission.md 已更正为 12（scrutiny 实证）；数字口径不一致（仅注释，无功能影响）。
- **[info] F-17**：`features.json` 把 validator 类条目（scrutiny/user-testing）也标 `completed` 并计入「8 features 全 completed」，其中 readme feature `fulfills: []`——「8 features 全 completed」为真但语义上混入了无断言 feature。

### (e) PR 合并后纪律

- **远端 feature 分支已删（通过）**：`git ls-remote --heads origin` 仅余 `feature/byom-infra-only`、`feature/go-github-deepseek-review`、`main`——`feat/audit-defense-hardening` 已删除；本地 `git status` 该分支上游显示 `[gone]`。
- **[minor] F-18 本地 main 未与 origin/main 同步**：本地 `main` = `0196dcd`，`origin/main` = `bc810b71`（fetch 后），落后 1 个 merge commit；本地尚有大量陈旧分支。归因：mission `state.json` `updatedAt=15:30:28`，早于 PR #141 合并（15:34:31）——mission 在合并前即结束，故未执行「合并后三件事」。属时序可解释的收尾遗留，非虚报，但当前工作树未处于 AGENTS.md 要求的同步态。
- **[minor] F-19 `evidence/` 为未跟踪遗留、mission 未声明处置**：`git check-ignore evidence/` 退出 1（**未被 .gitignore 覆盖**），`git status` 显示 `?? evidence/`。mission/features.json/decision 文档均未声明该目录的处置（提交/忽略/清理），构成工作树残留。

---

## 无法定论项

1. **Gate1 在 PR #141 的 CI 中究竟走 tarball 还是 gh api（推测）**：CI 日志确证模板面被真实取到（7 个调用）并通过，但未打印解析路径；「tarball 兜底」为基于「gate1 step 无 GH_TOKEN → gh_api 失败」的推断，无直接日志。若要闭环需重跑 gate1 并打印解析分支。
2. **全量 2401 tests 未独立重跑**：为控时长仅重跑 4 个受影响文件（160 passed）；全量结论依赖 mission 证据（`VAL-PR-002`：2401 passed/5 skipped，两轮复跑）。未发现矛盾，但未独立证实。
3. **README.md §2.4 是否为「待补文档」的占位意图**：从 mission 交付面（PR 未触碰 architecture.md）判断为悬空引用，但无法排除作者另有后续补节意图；无论意图如何，当前事实是悬空。

---

## 红队结论

1. **不是整体虚报**：四代码项（Gate1 fail-closed、liveness conclusion、AST import 锁、零红移序）与 P0 决策文档**真实存在、测试有牙齿、PR #141 全绿合并**；终稿三态分界在决策文档中被如实承载，排除项未被伪装完成。核心交付可信。
2. **完成声明不干净，两处须认领**：(i) README.md 收口件引入**可复核的事实缺陷**——指向不存在的 `docs/architecture.md §2.4`，且被指向文档根本不含四项防线内容（F-01）；(ii) 该 README.md 变更**发生在 21/21 验证之后、落在白名单之外、零断言覆盖**，故「21/21 断言通过」不足以认证最终交付物（F-02）。
3. **同一失效类别的复发（受限）**：P0 原罪是「未来态写成已实现态」；README.md 四项 bullet 本身属实，故未直接重犯；但以「详见 §2.4」指认不存在的细节文档，属「把不存在的依据写成已存在」——同类根因、较轻载体。
4. **建议（非本任务执行）**：① 修 README.md §2.4（或补 architecture.md 对应节，或改指向决策文档）；② 为 README.md 变更补一次断言/白名单覆盖（把收口件纳入 VAL-CROSS-002 或新增 VAL-DOC）；③ 给 liveness 加 `--json` 字段接缝锁（F-04）；④ 补 VAL-CI-003 的 actionlint 证据（F-03）；⑤ 更正 mission.md/决策文档的过期元数据（F-14/15/16）。
