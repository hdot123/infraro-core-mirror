# 四簇根治方案 · 对轮对抗（R1+R2）

- 对象：`cure-plan-v1.md`（定版 2026-10-25）
- 日期：2026-10-25（Asia/Shanghai）
- 方法：只读；对照 main@`7fa94f1`、ruleset `23079535`、现行 workflow；口径=说法+证据指针+可证伪条件，**不给处置建议**（处置已在定版方案内）
- 目的：打方案本身（不是再打病），看硬门是否够挡、施工序是否自相残杀

判定标签：
- **HOLD** — 攻击不成立或已被硬门挡住
- **PATCH** — 方案方向对，须在定版补一句/补验收否则会翻车
- **KILL** — 该簇动作在现行平台约束下不可行（本轮未出）
- **OPEN** — 需测 PR / API 实证后才能升格

---

## 总判定（先结论）

1. **四簇根治方向不被本轮推翻**；无 KILL。
2. **硬门 1/2/3 全是真硬门**——不是装饰。尤其硬门 2：现行 ruleset 三门均绑 `integration_id: 15368`（GitHub Actions），App 贴 check 若不改绑/不对齐，测 PR 必红，拆信封必回退。
3. 本轮产出 **PATCH ×6、OPEN ×3、HOLD ×4**。PATCH 须在「大哥亲口确认」前并入定版附录，否则施工会踩已知坑。

---

## 对轮 1：四簇动作

### R1-A1 · 病一「未齐退出」若落成 skipped，会被当成通过

**说法**：QG 双触发、另一条未齐时若 job conclusion=`skipped`，而 ruleset / GraphQL 旁路把 skipped 当通过（#358/#372 旧坑），则「未齐」=假绿，③ 可能被误开或合并守卫误判。

**证据**：定版硬门 1 已写「绝不能 success」+ 参谋长补注覆盖 skip；`auto_merge_triage.sh` 明确禁止 rollup 绿判（SKIPPED≈通过）；ruleset 对 required check 的 skipped 语义需契约钉死。

**可证伪**：给出契约测试 + 实测：quality-gate=`skipped` 时 ruleset `mergeStateStatus` 仍为 BLOCKED / 非 CLEAN。

**判定**：**PATCH**（硬门 1 已指向，但定版正文须显式写「未齐出口优先用 cancelled/neutral 或**不创建 success check**；若用 skipped，必须有「skipped ≠ 满足 required」的契约+实测」——不可只写「非 success」）。

### R1-A2 · 病一双触发竞态：两跑几乎同时看到双绿 → 双开 ③

**说法**：CI/QA 同时完成时两次 QG 均可 success，触发两次 Droid；依赖 concurrency cancel 收口，但组键若回落到 `head_branch=main`（今日 API 可见大量 Droid run 的 `head_branch: main`），会变成**跨 PR 互砍**。

**证据**：`droid-review.yml` concurrency 组键含 `pull_requests[0].number || … || head_branch`；2026-10-25 当日 Droid `workflow_run` 样本大量 `head_sha=7fa94f1` / `head_branch=main`。

**可证伪**：组键强制解析到 PR number（失败则 fail-closed 不入组），且同秒双 QG 只保留一条 ③ 且不误杀他 PR。

**判定**：**PATCH**（病一施工必须捆绑 concurrency 组键硬化；否则「敲门」放大 cancel 风暴）。

### R1-A3 · 病二与 #444 同向但正文冲突

**说法**：#444 后期 commit 自述「keep quality-gate and droid rollup on pve-linux」；定版病二要求汇总/信封类上 ubuntu。若 #444 先合，病二会被钉成反例。

**证据**：PR #444 commits（`09a5e15` 信息：keep QG and rollup on pve）；定版写「与 #444 同向」。

**可证伪**：#444 合并 diff 中 QG/rollup/`纯 API` job 的 `runs-on` 已是 `ubuntu-latest`，或定版明确「#444 若保留 pve 等候则作废其部分、以病二为准」。

**判定**：**PATCH**（定版改「与 #444 的轻活上 hosted 同向；若 #444 把等候留在 pve，以本方案病二覆盖」）。

### R1-B1 · 病三 App 贴 check 对不上 integration_id 15368

**说法**：ruleset required 三门全部带 `integration_id: 15368`（Actions）。GitHub App 创建的 Check Run 归属该 App 的 integration，**默认不满足**绑死 Actions 的 required context。硬门 2 是唯一刹车；若施工跳过测 PR 直接拆信封 → main 永久 BLOCK。

**证据**：`gh api …/rulesets/23079535` → `quality-gate` / `substrate-gate-suite` / `droid-review` 均 `integration_id: 15368`。

**可证伪**：测 PR 上 App 贴的 `droid-review` 出现在 `statusCheckRollup` 且 `mergeStateStatus` 能被该 check 单独推到非 BLOCK（在其他门绿时）。

**判定**：**HOLD**（硬门 2 已正确挡住；本攻击证实硬门必要，不推翻簇）。**OPEN**：测 PR 结果未出前不得拆信封。

### R1-B2 · 病三「开工贴 in_progress」取消后变永久 pending

**说法**：③ 开工贴 `in_progress` 后若被 concurrency cancel / 哨兵重派砍掉，收尾 job 不跑 → head 上 `droid-review` 永 `in_progress` = 新静默态（硬门 3 补注的「pending 到天荒地老」变种）。

**证据**：今日 Droid cancelled 样本（如 run 1104，占位约 53min 后取消）；rollup/mirror 现靠 job 生命周期结束；改 App 贴名后无自动关闭。

**可证伪**：任意 cancel 路径（concurrency / workflow cancel / 哨兵重派）后 5 分钟内同 SHA 的 `droid-review` 必达 completed（failure/cancelled），由收尾、`workflow_run` 清理或哨兵之一保证。

**判定**：**PATCH**（病三验收加：「cancel/重派后无残留 in_progress」；哨兵职责扩一列或 ③ 加 `if: always()` 收尾）。

### R1-B3 · 病三施工窗双写同名 check

**说法**：测 PR 阶段 App 已贴 `droid-review`，同时 rollup 信封仍在跑同名 job → 同 context 双生产者；latest-by-id 可能让「绿信封/红 App」或相反互相覆盖，验收失真。

**证据**：现行 required context 名即 `droid-review`；#420 Option A 信封 job 同名；定版顺序「先过硬门 2 再拆信封」暗示并存窗。

**可证伪**：并存窗内约定单一写者（App 用临时 context 名测 ruleset 绑定，或 rollup 对该 SHA no-op），契约测试锁死。

**判定**：**PATCH**（定版施工注：「硬门 2 测 PR 期间禁止双写同名；用影子 context 或关闭信封对测 PR 的写入」）。

### R1-C1 · 病四只卡 ③/合并口，① 仍洪水

**说法**：定版「③ 并发上限 2 + 合并 FIFO concurrency 1」若不在 **CI/QA 入口**做准入，18 个 PR 仍同时打满 pve 真干活池；病二腾出的官方池救不了测试分片。容量病只治了半截。

**证据**：对抗问题清单 D1/D3；现行无跨 PR 入口；定版病四入口描述偏「超限者在入口排队」但验收条写的是「③ 并发不超上限」。

**可证伪**：验收改为「同时开 10 个 PR 时，① 的 running+queued 真干活 job 数 ≤ 明示上限」，而不只量 ③。

**判定**：**PATCH**（病四「入口」必须含 ① 准入或全局 in-flight PR 上限在铸卡/开 PR 侧生效，否则病二后 pve 仍会被测试洪水打满）。

### R1-C2 · 病四 FIFO 队头 45min 踢出与 Droid 常态 10–35min 重叠

**说法**：队头超时 45min 含排队+审查时偏紧或偏松：池堵时健康 PR 被误踢；池空时坏 PR 多占窗口。参数缺「纯执行时间 vs 墙钟」定义。

**证据**：仓内自述 Droid 10–35min；QG/rollup 旧预算 100–120min；定版写「失败或超 45 分钟踢出」。

**可证伪**：超时时钟从「获得 ③ 工位起算」而非「入队起算」，并有踢出后显式 failure + 不堵后续的验收实例。

**判定**：**OPEN**（参数可调；须在病四开工前写清计时零点）。

### R1-C3 · 铸卡去重键「同规则+同文件集」被换皮绕过

**说法**：改一个无关文件或拆文件集即可绕过去重，同质逻辑 PR 仍堆积。

**证据**：今日 capture_run 堆叠已证实无去重；定版键未含语义/finding-id。

**可证伪**：去重键含稳定 rule-id（或 finding fingerprint），文件集为必要非充分；对抗用例「多加一个无关文件」仍抑制。

**判定**：**OPEN**（不推翻病四；第 0 步可先文件集止血，键强化单列 follow-up）。

---

## 对轮 2：硬门 · 顺序 · 哨兵 · 模型层

### R2-H1 · 硬门 1「按 id 取最新」与 success 误开 ③

**说法**：双触发下先跑的 QG 因未齐而 skip/fail，后跑 success；③ 只听 `workflow_run` conclusion==success —— 只要**成功那一次**发事件即可。按 id 取最新主要防的是**合并守卫读到旧 skip**，不是防误开 ③。硬门 1 两段都要：出口语义 + 消费者（ruleset/auto-merge/③）读法。

**证据**：`droid-review.yml` 触发条件看 QG run conclusion；auto-merge REST 三门看 head check-runs。

**可证伪**：契约分列 (a) job 出口三态 (b) check-runs 读者 latest-by-id (c) workflow_run 听众只认 success 事件。

**判定**：**HOLD**（硬门成立；补注已部分覆盖；不削弱）。

### R2-H2 · 硬门 2 是方案唯一单点阻塞——且正确

**说法**：无第二路径。Actions 自贴（GITHUB_TOKEN）在 workflow_run 语境已证明不够进 PR head rollup（#420 才上信封）。硬门 2 失败则病三整簇停，病一/二仍可独立推进。

**证据**：#420 文件头自述；ruleset integration_id 绑死。

**可证伪**：硬门 2 失败时施工序允许病一+病二先行（定版现序是病三优先——见 R2-S1）。

**判定**：**HOLD**（硬门正确）。**PATCH** 见 R2-S1。

### R2-H3 · 硬门 3「可读 pending」若无 TTL，哨兵成主路径

**说法**：定版补注要求 pending 最长等待 + 超时转红。若实现只贴 pending、TTL 只写在哨兵，则哨兵从「兜底」升格为「主路径超时器」，与「哨兵不挡主序」略张力——可接受但须标明。

**判定**：**HOLD**（可接受；定版已写 TTL）。

### R2-S1 · 施工序「病三优先」在硬门 2 OPEN 时卡住全局

**说法**：硬门 2 未过则不能拆信封；定版把病三放在病一/二之前 → 官方池迁移与轮询删除被无谓串行推迟，止血后墙钟病（等候占池）继续出血。

**证据**：定版顺序 病三 → 病一+二 → 病四；病二不依赖 App check。

**可证伪**：允许「硬门 2 测 PR 并行；病一+病二可与硬门 2 验证并行合入；唯拆信封严格门禁硬门 2」。

**判定**：**PATCH**（建议定版改为：病一+病二可与病三验证并行；**拆信封**仍严格后置于硬门 2。第 0 步仍最先）。

### R2-S2 · 哨兵重派 × concurrency cancel = 自击

**说法**：哨兵见「无实例」就重派；若实例存在但 probe/关联失败（旧病三残留）或 in_progress 未入 rollup，误判缺席 → 重派 → cancel 真实例 → 再被判缺席。

**证据**：问题清单 B2 probe=none 三病因；C1 cancel 组键。

**可证伪**：哨兵「无实例」判定必须基于 head 上 check-run / 结构化 run 列表，禁止刮日志；重派前若存在 in_progress 同 PR 则只告警不 cancel。

**判定**：**PATCH**（哨兵验收加「不误杀 in_progress」）。

### R2-M1 · 模型层「不靠人手点 Run」与第 0 步「等大哥下令」

**说法**：第 0 步等人下令关 PR / 开去重，不违反宪法——那是治理授权，不是主路径日常点 Run。攻击不成立。

**判定**：**HOLD**。

---

## 终裁表（须并入定版附录后方可开工）

| ID | 判定 | 一句话 |
|----|------|--------|
| R1-A1 | PATCH | 未齐出口写清 skipped 与 required 的关系，勿只写「非 success」 |
| R1-A2 | PATCH | 病一捆绑 Droid concurrency 组键硬化（禁回落 main） |
| R1-A3 | PATCH | 病二覆盖 #444「等候留 pve」的冲突表述 |
| R1-B1 | HOLD+OPEN | 硬门 2 必要；测 PR 前不拆信封 |
| R1-B2 | PATCH | cancel 后禁止残留 in_progress |
| R1-B3 | PATCH | 硬门 2 测窗禁止双写同名 check |
| R1-C1 | PATCH | 病四入口须含 ①/铸卡侧，不只 ③ |
| R1-C2 | OPEN | 45min 计时零点待定 |
| R1-C3 | OPEN | 去重键防换皮 follow-up |
| R2-H1 | HOLD | 硬门 1 成立 |
| R2-H2 | HOLD | 硬门 2 成立 |
| R2-H3 | HOLD | 硬门 3+TTL 成立 |
| R2-S1 | PATCH | 病一+二可与硬门 2 验证并行；拆信封仍门禁 |
| R2-S2 | PATCH | 哨兵不误杀 in_progress |
| R2-M1 | HOLD | 第 0 步等人下令 ≠ 违宪 |

**方案整体**：**不推翻，带 PATCH 附录后可拍板。** 无 KILL。

---

## 可证伪总检（拍板后第一周）

若下列任一条在施工中被观测到且无对应 PATCH 落地，则本对抗结论升格为「定版执行失败」：

1. quality-gate skipped 使 PR 变 CLEAN（R1-A1）
2. App 贴 droid-review 进不了 statusCheckRollup 却已删 rollup（R1-B1）
3. 同时 10 PR 时 pve 真干活排队仍无上界（R1-C1）
4. 哨兵重派取消了健康 in_progress Droid（R2-S2）
