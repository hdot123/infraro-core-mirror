# infraro-core PR→main 流水线根治 · 最终方案（整体替换版）

> **Agent / 新会话唯一入口（完整路径）**  
> 仓内：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md`  
> 证据同文副本：`evidence/pipeline-adversarial-2026-10-25/FINAL-PLAN.md`  
> `AGENTS.md` 已挂入口。未合 `main` 前仅在分支 `cursor/pipeline-cure-adversarial-a471`（PR https://github.com/hdot123/infraro-core/pull/446 ）。

- 定版：2026-10-25（Asia/Shanghai）· **打法改版**（废「逐 PR 在旧流水线求过」）
- 基线：`main` @ `7fa94f104bb555fbbb8b27752fce64c942ee995c`（#420）
- 前序材料：`evidence/pipeline-adversarial-2026-10-25/`（对抗 / 病四补充），**施工以本文为准**
- 一句话：**病秧子流水线不配被人守着规矩修——整套在本地写绿，旧集存档可回滚，一次性替换进 main。**

---

## 0. 打法（强制）

| 旧打法（废） | 新打法（现行） |
|--------------|----------------|
| 拆成多个 PR，每个都走现网 ①②③④ 求绿 | **本地整集组装** → 契约+ruff+pytest 绿 → **替换清单**交大哥一眼 → **一次性进 main** |
| 在旧轮询/信封上打补丁 | **整体替换胶水层**；旧 workflow/脚本原样存档可回滚 |
| 第 0 步先关存量 PR 再改线 | 止血可并行，**不阻塞**替换；替换不依赖旧线放行 |

### 0.1 替换瞬间的硬约束

1. **三门 check 名原样保留产出**：`quality-gate` / `substrate-gate-suite` / `droid-review`  
   → ruleset `23079535`（`integration_id=15368`）**不必改绑**；替换瞬间禁止出现「required 缺席」空窗。
2. **旧集先存档**（路径例：`evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover/` 或约定 tag），替换清单逐文件标 **改 / 删 / 增**。
3. 大哥看完清单点头后才合入；不合则整集回滚到存档，不留半截。

### 0.2 边界（宪法，不动）

- 四段心智模型不动：①廉价门 → ②汇总 → ③审查 → 合并守卫；fail-closed；PR≠main；`do-not-merge` 保留；主路径不靠人手点 Run
- `GOVERNANCE.md` 第 1 条不动；改自愈层另走 1.2
- **废弃的是胶水与容量实现，不是分层宪法**

---

## 1. 三件拍板（已消化，交付明示；不认可可回退）

### 1.1 病三贴 check：B 先行

- **B**：新集里 `droid-review` 用 **Actions 最瘦形态**——删轮询等待 / 拆信封依赖；结论由干活腿在可进 rollup 的原生路径贴出（保留 context 名 `droid-review`）。
- 替换后 **第一个测 PR** 验：该 check 是否进 GraphQL `statusCheckRollup` 且能满足 ruleset。
- **不过 → 转 A**（专用/已有 App 贴 check + 必要时改 `integration_id`）。B 失败不得硬拆、不得空窗。

### 1.2 未齐出口：`exit 1` + `NOT_READY`（禁 skip）

- QG（及同类单次判定）在「另一条未齐」时：**进程失败退出 + 结构化 `NOT_READY`**（日志/output 可机读）。
- **禁止** `skipped` / 假 success（skipped≈required 通过 = 假绿）。
- 「另一条已红」→ 普通 `failure`（可与 NOT_READY 区分码/文案）。
- 「双绿」→ `success`，并触发下游 ③。
- 契约测试必须锁：NOT_READY 时 PR **不得**因 `quality-gate` 变 CLEAN。

> 说明：原生 job 没有「温柔未齐」态；用失败承载未齐，是唯一不假绿的原生解。合并守卫 / 哨兵须识别 NOT_READY ≠ 质量红（可重试、不升级骂街），但 **ruleset 面仍非绿**——这是故意的 fail-closed。

### 1.3 铸卡在飞去重：工单号反查 issue 取键

- **主键仍是** `(rule_id, _normalize_dedup_location(location))`。
- PR 侧：**不先改模板埋两行**；从 PR 的 `Fixes INFRA-N` / 分支 `INFRA-N` / `Closes #N` **反查**对应 evolution issue，再解析 issue body 两行键。
- 反查失败 → 不进抑制集 + 打点（不误杀）。
- **埋键**（PR body 复制两行）留作后续加固，不挡本集替换。
- **同文件 DUPLICATE 文件核附加闸**仍要做（精确键挡不住同行号多对洪水）——见 `evidence/pipeline-adversarial-2026-10-25/disease-4-design-supplement.md`；本集纳入。

---

## 2. 新集必含模块（组装范围）

| 模块 | 要什么 | 不要什么 |
|------|--------|----------|
| **QG** | CI/QA completed 双触发、单次判定、NOT_READY/`failure`/`success` 三出口；PR 号+head SHA 稳定解析；concurrency 组键含 PR 号、**禁回落 main** | 200×30s 轮询；未齐 skip |
| **③ Droid** | 仍听 QG success；瘦 Actions 贴 `droid-review`；组键禁回落；cancel 仅同 PR 新 head | rollup 长等；刮日志认 PR；跨 PR 互砍 |
| **信封/mirror** | 替换清单里 **删**（B 测过 rollup 后再删；若 B 首测前需短暂并存，清单须写明窗口与单一写者） | 双写同名 check |
| **合并 FIFO** | label 介质；mergeable 仅队头；`max-parallel:1`；踢出+冷却；三要素写清 | mergeable 无序扫合；快路径自称队头直合 |
| **铸卡去重** | issue∪（经工单反查的）PR 键；文件核附加闸；存量帽 `max_inflight_prs` | 只查 issue；文件集主键 |
| **哨兵** | **并入 watchdog**（不另起炉灶）；缺席→显式红；不误伤合法排队；不误杀 in_progress；时限按实际 cron/heartbeat 口径 | 30min 空头支票；独立抢戏主路径 |
| **病二** | 先无等待；纯 API/短判定上 hosted；QG 按 job 定性后再搬；pve 只留真干活 | 等候继续占 pve |

---

## 3. 交付物（组装方回来必须交齐）

1. **本地绿证明**：契约测试 + `ruff` + 聚焦 `pytest` 输出（命令与摘要）
2. **替换清单**：逐文件 `改|删|增` + 一句话意图；标出三门 check 名的产出点
3. **旧集存档路径 / tag**
4. **回滚一步说明**（如何把存档打回）
5. **明示三项拍板落地处**（B / NOT_READY / 工单反查）及「若要回退 A·埋键·某默认」的开关
6. **替换后第一测 PR 计划**（验 B：rollup + ruleset）

大哥只做两眼：**清单** + **测 PR 结果**。不在旧线上陪跑十几轮。

---

## 4. 已知前提（勿再空转查）

- ruleset 三门 `integration_id=15368`（github-actions）
- 外置 App（factory-droid/cursor）有 `checks:write`，但 **未**贴 required `droid-review` → A 是 B 失败后的退路
- 修复 PR 今日无 Rule ID/Location 行，只有 `Fixes INFRA-*` → 与 §1.3 一致
- QG 现行含 checkout + ensure-jq，**非纯 API** → 搬家前先瘦成单次判定

---

## 5. 新会话 / 组装方启动提示词

```
按 docs/guides/pr-pipeline-cutover-FINAL-PLAN.md 组装新流水线（整体替换版）。

打法：本地整集写绿 → 旧集存档 → 交替换清单（改/删/增）→ 等大哥点头后一次性进 main。
禁止：拆成多个 PR 走现网旧流水线求过。

已拍板：
- 病三 B 先行（Actions 瘦形态）；测 PR 进不了 rollup 再转 A
- 未齐 = exit 1 + NOT_READY（禁 skip）
- 去重：Fixes/INFRA/Closes 反查 issue 取键；埋键后续；含同文件 DUPLICATE 文件核闸

边界：四段心智模型不动；GOVERNANCE §1 不动；三门 check 名必须继续产出。

交付：本地绿日志 + 替换清单 + 存档路径 + 回滚说明 + 第一测 PR 计划。
参考 evidence/pipeline-adversarial-2026-10-25/（cure-adversarial / disease-4-design-supplement / final-verdicts）。
```

---

## 6. 状态

| 项 | 状态 |
|----|------|
| 打法 | **整体替换**（本文） |
| A/B · NOT_READY · 去重反查 | **已拍板按上文消化** |
| 组装 | 已派出；回交付物 §3 |
| 合进 main | **等清单过目 + 点头** |
| 第 0 步关存量同质 PR | 可并行，不挡替换 |
