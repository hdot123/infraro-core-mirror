# R3 DS4 终局红队：攻击 Fable 5.1 修订终稿

- 红队：DS4.1-Flash（第三轮·收敛轮），全程只读（git/gh/grep/cat，未跑测试、未改文件）
- 攻击对象：`final-fable-5.1-review.md`（Fable 5.1 修订终裁）与承载它的 `final-synthesis.md`（经索引行挂接 Fable 后的终稿）
- 证据基准：本地工作树 HEAD `527a435`（= origin/main 树 `bc810b7`）；`src/infra_core/engine/evolution_heartbeat.py` 静态逐行追踪
- 纪律说明：本轮不复核 mission 本身，只攻击「Fable 修订结论」及其在终稿中的承载

---

## X1 A5 攻击结果

### X1.1 逐行控制流推演（3 连 failure 场景）

场景设定：evolution-scan 每次被触发都真实运行、每次都 `conclusion=failure`，createdAt 新鲜。

**Step 1 — 探活**（`check_scanner_liveness` `:248-249` → `_check_workflow_liveness(SCANNER_WORKFLOW, 2)` `:283`）
- `gh run list --limit 5 --json status,conclusion,createdAt`（`:322-323`）
- `all_failed_streak = len(runs) >= 3 and all(runs[:3].conclusion == "failure")`（`:343-345`）→ **True**
- 循环 newest-first，最新 run age ≤ 2h → `liveness_data_ok=True` 且 streak 命中 → `result["alive"]=False`（`:364`）、message「Scanner stale: last 3 runs concluded failure」（`:365-368`）、**return**（`:369`）
- 关键：`hours_since_last_run` = **最新 run 的年龄**（新鲜，远 < 8h）——Fable 第 1 步正确

**Step 2 — main 的 dispatch 分支**（`main()` `:837`；dispatch 段 `:850-870`）
- `liveness["alive"]=False` → 打印 ALERT（`:850-852`）→ `trigger_scanner_dispatch()`（`:854`，实调 `gh workflow run` `:404-424`）
- dispatch 被 GitHub 接受 → `dispatch_accepted=True`（`:854`）
- `stale_hours = 0.x`（`:856`）；`stale_hours > 8` 为 False → else 分支「Self-heal dispatch accepted; suppressing scanner_stale alert」（`:867-870`）——Fable 第 2 步正确

**Step 3 — 抑制条件**（`:899-903`）
- `measured_hours = 0.x`（`:899`）
- `severe_outage = (not alive) and (measured_hours is not None and measured_hours > 8)` = `True and False` = **False**（`:900-902`）
- `stale_alertable = not alive and (not dispatch_accepted or severe_outage)` = `True and (False or False)` = **False**（`:903`）——Fable 第 3 步正确

**Step 4 — 告警关闭**（`:906-908`）
- `effective_liveness = {"alive": False or (True and not False)} = {"alive": True}`（`:906`）
- `compute_current_anomalies(effective_liveness, coverage)`：`scanner_stale` 仅在 `not liveness["alive"]` 时加入（`:535-545`，即 `:541`）→ 当前异常集**不含** scanner_stale（`:907`）
- `resolve_cleared_alerts(...)`（`:908`，实现 `:613-720`）：对每个 open 告警，`recorded - current` 非空且 `recorded ∩ current` 为空即 `gh issue close`（`:655-720`）→ **scanner_stale-only 告警被当「已恢复」关闭**——Fable 第 4 步正确

**Step 5 — 退出码**（`:916-940`）
- `anomaly_count = sum([stale_alertable, bool(coverage["issues_without_pr"] > 0)])`（`:916`）→ 无 issues_without_pr 时 = 0
- `stale_alertable=False` 且 `issues_without_pr=0` → 跳过建告警（`:919-935`）→ `write_monitor_heartbeat(0)`（`:936-939`）→ `return 0`（`:939-940`）
- heartbeat step 无 `|| true`（`.github/workflows/evolution-heartbeat.yml:141`）→ exit 0 → **workflow 绿**——Fable 第 5 步正确

**Step 6 — 永续性**
- 每 tick 都 `workflow_dispatch` 一个「存在但必失败」的 scanner（`:404-424`）→ GitHub 接受 → 新增一条 fresh failure run → 年龄永远重置 < 8h → Step 3 的 `severe_outage` 结构上不可达 → 抑制永续。Fable 的「抑制条件被防线自身自愈动作永续满足」**成立**。

**X1.1 结论：Fable 的六步链条在代码层逐行成立，无断点。**

### X1.2 反证搜索（Fable 是否漏看分支）

逐条找反例，**均未推翻**：

1. **dispatch 失败路径**：`gh workflow run` 返非零（403/无权限）→ `dispatch_accepted=False` → `stale_alertable=True` → 建告警、return 1。这条**会破抑制**——但它正是 Fable 明写的生效条件「dispatch 被拒」。故不是反证，而是 Fable 归类为「态二·条件生效」的正确依据。
2. **限频/concurrency 路径**：若 scanner 有 `concurrency: cancel-in-progress`，重复 dispatch 会把上一条 run 置 `cancelled`；最新窗口含非 failure → `all_failed_streak=False` → age 规则判 `alive=True` → 既不 ALERT 也不 dispatch。**仍是零可见告警**，强化结论。
3. **dispatch 后 run 未终结（conclusion=None）**：最新 run `conclusion=None` → streak 不成立 → age 规则判 `alive=True` → 无 ALERT。同样零可见告警。
4. **`stale_alertable` 其他置真条件**：唯一置真路径 = `not alive and (not dispatch_accepted or severe_outage)`（`:903`）；`severe_outage` 需 `measured_hours>8`（`:900-902`）。3 连 failure 场景 age 恒新鲜 → 两条都不满足。**无 Fable 漏看的置真分支。**
5. **告警关闭是否仅限 label 子集**：`resolve_cleared_alerts` 只处理 `ALERT_LABEL="evolution-heartbeat"` 的 open 告警（`:519-540`）——Fable「关闭既有 scanner_stale 告警」限定在正确 label 面内，成立。精度补充：仅当该告警记录的异常**全部**清除才关；scanner_stale-only 告警必关，混合（含仍存在的 issues_without_pr）告警不关。
6. **fail-closed 关闭守卫**：`coverage_data_ok=False` 时跳过自愈关闭（`:632-635`）。此时告警不关、但也不会被新建（`stale_alertable` 仍 False）；不推翻主链。

**Fable 漏看的唯一实质补充（加强而非削弱结论）**：Step 5 还执行 `write_monitor_heartbeat(0)`（`:916`、`:939`），把 meta-monitor 标记写成 `status="ok", anomalies_detected=0`。即该场景下防线不只是「无可见告警」，而是**主动写出「一切正常」的健康标记**（`monitor_heartbeat.json` 仅测试消费，无 src 消费面，故不产生新告警）。

**行号精度**：Fable 引的 `:846-870` / `:905` / `:925-940` 与本文件实际（`main` 起 `:837`、`effective_liveness` `:906`、退出段 `:916-940`）有 ±1–9 行漂移，语义全对，不构成事实错误。

### X1.3 severity 裁定（链条成立后攻定级）

Fable 的定级依据是「分类失实」。实测证据：

- 决策文档 `architecture-evolution-goal-correction.md:56` 标题「态一：CI 内阻断（有实现，在 required 链内）」；`:65` 把 liveness conclusion 列为态一条目；`:69` 依据「四项全部在 CI 内以红/绿判定」。
- 实测：heartbeat 只由 `evolution-heartbeat.yml`（schedule `53 */2 * * *`，`:67-68`）触发，`python -m infra_core.engine.evolution_heartbeat`（`:141`）；它**不在 required 链**——`quality-gate.yml:69` `EXPECTED_CHECKS="ci-ok substrate-gate-suite"`，无 heartbeat；目标场景下 `main()` 返 0 → heartbeat 绿（`:940`）。

故「态一·在 required 链内·CI 内红/绿判定」对 liveness 项**事实不成立**，正确归类是态二「有实现·条件生效（dispatch 被拒 或 age > 8h）」。Fable 的「分类失实」**成立**。

**但 Fable 的 major 定级需精确化（收敛措辞，不推翻）**：这不是「把不存在的写成已存在」（实现真实存在、检测真实发生、76 用例），而是「把条件生效面写成 CI 阻断面」的**效果面分类失实**。major 的成立依据不是运行时风险（零消费者依赖该锚点、检测代码本身正确），而是两条：
(i) 它落在 P0 的**同类**失效（overstate 效果）上，且在「记录该 P0 修正」的同一份决策文档里复现——自洽性失败；
(ii) 它是唯一目标场景（scanner 持续失败）下**零可见效果**——防线的设计目的（发现「在跑但永不成功」的管道）被自身自愈彻底吞掉。

按此两条 major 站得住；但 Fable 的「落在 P0 管辖范围内」表述略外延（P0 8 处修正的对象是 north star 未来态，liveness 是新增条目的态分类），属**相邻同类**而非**直接命中**，须如实标注以免下游复用者误读。

**X1 终裁：A5 成立(major)。** 链条逐行成立、无反证分支、分类失实确证。Fable 修订方向正确，终稿须采纳该升级、不回滚；唯定性由「分类失实（P0 直接管辖）」精确为「态一归类失实 + 自愈抑制交互盲区（P0 同类·相邻范围）」。

---

## X2 处置清单攻击

### X2.1 计数不自洽（Fable 的 ×5/×6 无法从终稿正文复算）

三份报告的桶计数互不相等：

| 来源 | 立即修 | follow-up | 组成 |
|---|---|---|---|
| `r2-glm53-adjudication.md:145-160` | ×4 | ×5 | 立即修={D1,D7b,D5,D6}；follow-up={D2,D3,D4,D7a(决策文档:135),D7c(arch.md:31)} |
| `final-synthesis.md` §三 分桶汇总 | 5 子项 | ×4 | 立即修={D1,D5,D6,**D7a**,D7b}；follow-up={D2,A2,D3,D4}；记录={A3,A5} |
| `final-synthesis.md` 索引行（Fable 行） | ×5 | ×6 | 由 r2-glm53 的 ×4/×5 各 +1 得（+A5 / +failure-streak） |

**后果**：索引行「立即修 ×5」与正文「立即修 5 子项」数字巧合相同但**内容不同**（索引版含 A5 不含 D7a；正文版含 D7a 不含 A5）；「follow-up ×6」比正文 ×4 多 2，且其组成沿用 r2-glm53 版（含 D7a/D7c），与正文已把 D7a 移入立即修、把 D7c 判为「无问题」相冲突。**该行不是终稿正文的有效汇总。**

### X2.2 错桶

1. **A5 错桶（实质）**：正文 §三表 `A5 | minor | 记录`，索引行却写「升 major/立即修」——同一终稿对 A5 给出两个不同桶（见 X3）。
2. **D4 桶对象错位**：正文把 D4 放「follow-up（需用户裁定）」。Fable T2.3 判 D4 本体 = Info（枚举宽窄无需 follow-up），真正需要 follow-up 的是「suppression 加 failure-streak 穿透」。建议 D4 拆为「本体：记录/用户裁定」+「A5-followup：抑制穿透」。
3. **D7a（决策文档:135 元数据）桶漂移**：r2-glm53 放 follow-up，正文放「立即修顺带」，索引行 ×5 又把它算回 follow-up——三处不一（虽两种放法皆可）。
4. **D2 桶一致**（follow-up 流程改进），Fable 仅改措辞，无桶争议。

### X2.3 孤儿项（既非立即修、非 follow-up、非记录）

对照 r1/r2/fable 全部发现 ID，正文 §三表未处置、正文他处亦未给出桶的项：

| 孤儿 | 来源 | 性质 | 建议桶 |
|---|---|---|---|
| **F-13** `gate_common.py:184` 排除集缺 `"Template"`（登记表 `## Gate 1` 首列即 `Template`，一旦 error 串含大写 `Template` 即误判 owned → 放行） | r1-ds4 (c) | latent 软通过面 | **follow-up**（真实残留风险；r1 自称「synthesis 已作为 follow-up 记录」，但本终稿 §三无此条） |
| **F-14** `mission.md` 保留错误前提（称 gate-tests 持 GH_TOKEN，实为 gate1 step 无 GH_TOKEN） | r1-ds4 (d) | 规划文档事实错误 | 记录（D7 只覆盖「13 job」，未覆盖此前提） |
| **F-11** 弱证据文件（`grep-local-one.txt` 仅 `Exit code: 1`；`VAL-CROSS-002-excluded-check.txt` 仅 `0`/`0`，缺被检命令） | r1-ds4 (b) | 证据可复核性 | 记录 |
| **F-17** `features.json` 把 validator 类条目计 `completed`、readme `fulfills:[]` | r1-ds4 (d) | 语义混淆（info） | 记录 |
| **A1** GLM 主审「handoff 无一被忽略」不成立（11 条列 7 条，漏 4） | r2-ds4:173 | 审计质量（声明完整性 major） | 记录（正文仅 §四 叙事，未入发现表） |
| **A4** GLM「白名单 8 文件」下列 9 名（标签与枚举矛盾） | r2-ds4:176 | 审计质量（minor） | 记录 |
| **T3-3** 「全量 2401 未独立重跑」漏引 `gh pr checks 141` 的 pytest/test-groups 第三方证据 | Fable T3-3 | 报告口径 | 记录 |
| **T3-4** `evidence/` 与 KB decisions 无 sha256 清单（单机、0600、无外部锚） | Fable T3-4 | 证据信任根 | **follow-up**（Fable 明确建议追加 manifest，正文未登记） |
| **T3-5** D6 桶尚未执行 | Fable T3-5 | 状态 | 记录（D6 执行后自动消解） |
| Qwen×4 Info（`pinned_v_refs` 去重 / gzip 头检测 / 测试用 AST 替字符串匹配 等） | r1-qwen | 可选优化 | 记录或显式排除 |

**孤儿总评**：**F-13 与 T3-4 是两个真孤儿**（有实质动作建议、终稿零登记）；F-14/A1/A4 是「只活在 §四 叙事或 r1/r2 原文、未进终裁表」的半孤儿；其余为可显式判「记录/排除」的 Info。

### X2.4 缺失项（应进清单但未进）

1. **Fable「立即修 ×5」第 5 项内容缺失**：正文无「决策文档增补节把 liveness 由态一迁态二 + README:191 加限定」这一条；索引行只说「A5 升 major/立即修」，正文 A5 仍 = 记录。→ 终稿缺一条立即修动作定义。
2. **Fable「follow-up ×6」新增项内容缺失**：正文无「suppression 条件加 failure-streak 不可抑制 + `main()` 级测试锁（3 连 failure + dispatch 接受 → 仍建告警）」这一条。→ 终稿缺一条 follow-up 定义。
3. **README:191 限定**未被任何桶承载（属 A5 立即修的动作面）。

---

## X3 终稿自洽性

通读 `final-synthesis.md`，索引行（Fable 行）与正文（多轮修正后的主体）**五处直接矛盾**，全部因「只追加索引行、未回改正文」：

| # | 位置 A（索引行·§〇 终局行） | 位置 B（正文） | 矛盾 |
|---|---|---|---|
| 1 | 「A5 升 major/立即修」 | §三表 A5 行「minor / 记录」；分桶汇总「记录：A3、A5」 | severity + 桶双矛盾 |
| 2 | 「条件清单增补为立即修 ×5 / follow-up ×6」 | 头部 line 7「4 项 follow-up」；分桶汇总 follow-up 4 项、立即修 4 桶 | 计数矛盾（×6 亦不可从正文复算，见 X2.1） |
| 3 | 「D2 措辞改『零断言覆盖』」 | §三表 D2 行仍「README 收口件逃逸验证覆盖」 | 措辞未回改 |
| 4 | 「A2 单标签 minor」 | §三表 A2 行仍「minor（残留面）/major（未登记）」 | 双标签未回改 |
| 5 | 「方法论结论加限定『推理层存在共享前提盲区』」 | §四 方法论结论仍「四模型两轮收敛后事实层零分歧，仅标签级差异」（无限定） | 结论未回改 |

补充自洽性瑕疵（非索引行导致，但终稿内）：

6. **§三表 D4 行自相矛盾**：「符合批准范围/无问题；完备性 info」与处置桶「follow-up（需用户裁定）」并列——「无问题」的项不该进 follow-up；且与 Fable「真正 follow-up 是抑制穿透」冲突（见 X2.2-2）。
7. **头部 line 7「4 项 follow-up」vs 索引行「follow-up ×6」**：同一文档对同一清单给出两个数字。
8. **§五 P1 行**把 liveness conclusion 记为「完成」，未反映「效果被抑制」——若采纳 A5，§五该行需加「态二/效果面」限定。

**X3 终裁**：终稿**不自洽**。索引行承载了 Fable 修订，正文停留在 R2 终裁，形成「一稿两裁」。修复二选一：(a) 回改正文 §三/§四/头部/§五 使之与 Fable 修订一致；(b) 显式标注「索引行 = Fable 第二意见、正文 = R2 终裁，采纳以 X 为准」。当前二者混排，下游复用者可能取到错误版本（最危险：读到 §三 A5=记录 而漏掉 A5 升级）。

---

## 本轮结论

**最终问题清单（我认可的版本）** —— 在 Fable 修订基础上做以下增删改：

**增（4 项）**
1. 【follow-up】**F-13** `gate_common.py:184` 排除集缺 `"Template"`（latent 软通过面）——真孤儿，须入桶。
2. 【follow-up】**T3-4** `evidence/` + KB decisions 出 sha256 manifest（Fable 建议，正文未登记）——真孤儿，须入桶。
3. 【立即修·A5 动作面】写全 A5 的两处动作：决策文档增补节迁类（态一→态二，写明「dispatch 被拒 或 age > 8h」生效条件）+ README:191 加限定。
4. 【follow-up·A5 动作面】suppression 加 failure-streak 不可抑制维度 + `main()` 级测试锁（3 连 failure + dispatch 接受 → 仍建告警）。

**删/并（2 项）**
5. A2 双标签合并为单标签 minor（采纳 Fable）；登记状态并入全局「待补登记块」。
6. D4 拆分：本体 = 记录/用户裁定（Info）；把 follow-up 让给「抑制穿透」（避免桶对象错位）。

**改（3 项）**
7. A5 定级：采纳 major/立即修，但定性收敛为「态一归类失实 + 自愈抑制交互盲区（P0 同类·相邻范围）」，不写「P0 直接管辖」。
8. 计数以正文为准重算：立即修 = {D1, D5, D6, D7a, D7b, A5} = 6 子项；follow-up = {D2, A2, D3, F-13, T3-4, 抑制穿透} = 6；记录 = {A3, F-11, F-14, F-17, A1, A4, T3-3, T3-5}。索引行「×5/×6」按此重写。
9. §三表 D4 行、§四 方法论结论、头部 line 7 回改至与采纳裁决一致；§五 P1 行加「态二/效果面」限定。

**维持（不推翻）**：mission 终判「带条件完成」、D1 major、D2 major（措辞改「零断言覆盖」）、D3 minor、D5/D6/D7、排除项零触碰、P0–P2 落地事实、Fable 的方法论限定。

**X1 终裁一句话**：Fable 的 A5 链条逐行成立、无 Fable 漏看的分支，A5 成立(major)，终稿须采纳该升级不回滚，唯定性由「P0 直接管辖」收敛为「P0 同类·相邻范围」。
