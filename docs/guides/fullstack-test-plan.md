# infraro 全流程测试计划（声明层 + 引擎层 + CI 自建 runner + 发版闭环）
<!-- markdownlint-disable-file MD013 -->

| 项 | 值 |
| --- | --- |
| 文档性质 | 测试计划。本计划只定义测试设计与判据，不执行测试 |
| 编写日期 | 2026-09-27 |
| 数据时点 | 实测数字按各处标注时点取值（2026-09-27 规划调查 + 2026-09-28 缺陷销项重采，溯源见 §11 引用表与行内标注），不虚构 |
| 被测对象 | 声明仓 hdot123/infraro（thin-caller 模板面）＋ 引擎仓 hdot123/infraro-core（reusable workflows 与引擎本体）＋ CI 自建 runner 池 ＋ 发版公告闭环 |
| 执行前提 | §3 前置修复清单三项全部完成；L4 发版腿前提已满足——v0.20.0 已正式发布（2026-09-28 16:10:17Z，PR #163 合并 16:00:59Z，gh api 复验），消费面四件套复验全中（见 §7.2）（各层对前置项的依赖见各层「范围与前置」） |
| 执行边界 | L3/L4/L5 为真跑形态设计（真实 PR、真实 runner、真实合并）；活体步骤的执行者与远端权限约束见 §9 R6 |

---

## 1. 范围与三层拓扑

### 1.1 被测系统总览

被测系统是一条贯穿「消费仓声明 → 引擎 reusable → runner 路由 → AI 审查 → check 聚合 → 分支保护 → 自动合并 → 发版公告」的完整交付链。本计划按物理分层设计五层验证体系：

| 层 | 对象 | 形态 | 成本量级 |
| --- | --- | --- | --- |
| L1 | 引擎仓（infraro-core）本地静态 | 零凭证、零网络、分钟级 | 本地资源，无远端副作用 |
| L2 | 声明仓（infraro）不变量 | 计划新增 pytest 套，全离线 | 本地资源 |
| L3 | 引擎仓活体 E2E | 真实 PR 全链（CI → droid-review → 聚合 → auto-merge） | 一条 PR 的 Actions 消耗 |
| L4 | 消费仓演练 | 新建专用测试仓 + 最小配置集 + 故障注入（旧备选 consumer-a/b 已于 2026-09-25 注销删除，唯一路径见 §7.1） | 新仓一次全链 + 拆除 |
| L5 | 发版公告闭环 | 哨兵修复 → 派发 → 下游 pin-bump PR 闭环 | 一次发版腿 + 逐仓 PR |

### 1.2 端到端链路地图

```text
消费仓 PR opened/synchronize
  ├─ droid-review.yml（thin caller，pull_request_target）
  │    ├─ job1 droid-auto-review: uses 镜像仓 droid-review-shards.yml@v0.20.0（声明仓模板自 v0.19.0 bump 后）
  │    └─ job2 droid-review（聚合 job，留在 caller 本地，check 名契约）
  ├─ ci.yml（本地 lint + gate-tests advisory）→ ci-ok
  ├─ auto-merge.yml（thin caller）→ 镜像仓 auto-merge-pipeline.yml@v0.20.0
  └─ branch-cleanup.yml → 镜像仓 actions/branch-cleanup@v0.20.0
        ↓
引擎 reusable（公开镜像 hdot123/infraro-core-mirror 为消费面；镜像唯一同步入口 = 镜像仓
sync-release.yml（dispatch / 上游事件转发 / 6h schedule 三触发，硬前置 UPSTREAM_READ_TOKEN +
MIRROR_PUSH_TOKEN 两 secret），2026-09-29 两 secret 已配置且同步链已实跑修复——消费 tag v0.20.0
已于 09-28 16:10:17Z 发布并同步至镜像（run 36488422715 success，机制与解锁过程 §8.5）；
私有真源 hdot123/infraro-core 自仓 23/23 workflow 用 self-hosted）
        ↓
job 路由：hosted 常规槽 vs 自建 runner 池（labels [self-hosted, pve-linux]）
        ↓
droid-review BYOM 链：runner 上 droid exec → 入口网关（droid-review-gateway
Worker 四合一门禁：IP 门禁 fail-closed / 时段标记 / 非流式改写 / x-ci-key 门禁——
⚠ 版本分裂标注：四合一门禁与 x-ci-key 消费通道自 main 侧 F2（PR #161，c6b8e0e）起有效，
仅随 v0.20.0 起进入镜像消费面；v0.19.0 消费面为三合一旧形态（无 x-ci-key 通道），
在 Worker REQUIRE_CI_KEY=true 下必 401 fail-closed，禁止 pin）
→ CF AI Gateway「go-github」/compat + dynamic/droid-review 动态路由
（peak→mimo-v2.6-flash / off-peak→deepseek-v4.1-flash）→ opencode Go plan 上游
        ↓
check 聚合：ci-ok（ci.yml 全 12 job，ci-ok needs 10 job 全绿 → 轮询 droid-review → 零红扫描；
ci.yml:469-487 实测 needs 十项：pytest / lint-bundle / type-bundle / advisory-bundle /
test-groups / guards / integration-tests / e2e-tests / health-check / gate-tests）
与 quality-gate（轮询 ci-ok + substrate-gate-suite + qa-ok，120min timeout——
PR #248 慢尾死锁后自 80min 扩容，MAX_ATTEMPTS=200×30s≈100min）
        ↓
分支保护 ruleset（required = quality-gate、substrate-gate-suite、droid-review；
strict up-to-date；squash-only + linear history）
        ↓
auto-merge squash 合并 → release-please push → tag v*
→ release-announce → 宿主 Mac poll-releases 轮询 → trigger-release（per-tag 锁）
→ 逐 engineConsumer 仓派发 release-gateway 会话 → pin-bump PR → 回到主链闭环
（production environment 实测仅 branch_policy、无 required reviewer，审批门表述见 §8.1/§9 R1）
```

要点（引用链路面语义，数字溯源见 §11）：

- 引擎 reusable 面实测 6 文件（auto-merge-pipeline / droid-review-shards / droid-review-watchdog-handlers / evolution-heartbeat / evolution-scan / setup-labels）；引擎仓本体 23 个 workflow（2026-09-30 口径，#168 publish-pypi 落仓后 22→23）。
- `job.workflow_sha` 语义：workflow_call 下恒有值，等于 caller `uses@tag` 解析的 commit；引擎 ref 解析链为 `inputs.engine_ref || job.workflow_sha || github.sha`。弃用 `github.action_ref` 与不存在的 `github.job_workflow_sha`。
- droid-review 三契约（NEVER break）：workflow 名 `Droid Auto Review`、artifact 前缀 `droid-review-debug-`、聚合 job key `droid-review`。
- BYOM 认证头族：`x-ci-key`、`cf-aig-authorization`、透传上游授权头、`x-opencode-session: droid-review-shard-<SHARD_ID>`（缺头 400）。`worker_ci_key` secret 声明面 `required: false` 是有意过渡语义（PR #161/#162，2026-09-27 合入 main）：门禁单一真源在 Worker 侧，缺失即 401 fail-closed，声明面不做二次判定。⚠ 版本分裂标注：上述 x-ci-key 消费通道与门禁语义在 main 侧已生效，但其镜像消费面仅随 v0.20.0 镜像同步后存在（同步入口与解锁过程见 §8.5；v0.19.0 消费面为三合一旧形态且已 401 fail-closed）。

### 1.3 三层实测数据速览

| 层 | 实测基线 | 数据源 |
| --- | --- | --- |
| 引擎层 | 2426 个测试（collect-only 实测）；五套 marker；CI/QA/substrate 三路聚合 + quality-gate 收口；本地零凭证全量可跑 | §11 引用表 engine-survey |
| 声明层 | 零自动化测试；gate1 模板扫描 glob 空洞（命中 0 文件） | §11 引用表 declaration-survey |
| CI/runner | ce01 宿主（PVE 单台 VM）PR #162 时点 9 runner 实例并存；runner-06~09 于 2026-09-28 退役后同日经用户指令重建（现活体 9 = 引擎仓 4 + runner-hub 4 + scheduler 1，2026-09-28 17:16Z 终局复探；muse-1 曾短暂入池、同日离场并按清册 §4 回写闭环，详见 §3.3 P-3）；引擎仓专属池当时 runner-06~09 全 online；PR #162 全链 ≈80min；Mac 侧 poll-releases 断链（哨兵缺失，locks/ 路径） | §11 引用表 runner-chain-survey + §2.3 时点口径 |

## 2. 现状基线

### 2.1 引擎层（infraro-core）

- 测试总数 **2426**（collect-only 实测，收集约 40s）。框架：pytest 9.1.1 + cov 7.1.0 + timeout 2.4.0（300s）+ xdist 3.8.0；111 个 test 文件；conftest autouse TickBudgetTracker 重置。

| marker | 测试数 | 覆盖域 |
| --- | --- | --- |
| security | 286 | boundary、governance、pr-ref-consistency、fix-has-test、doc-classification |
| schema | 681 | version_sync、naming_contract、adapter、migration、template |
| integration | 488 | CLI 入口、跨模块、drift_watch、suppression、tick_budget |
| e2e | 456 | scanner 全流程、heartbeat、webhook_anchor、CLI 冒烟 |
| business_policy | 346 | governance、naming contract、guard contracts、delivery policy |

（marker 间有重叠，重叠用例跨套重复执行；五套合计 286+681+488+456+346=2257，小于全量 2426——其余用例不带这五个 marker。）

- 聚合门结构：ci.yml 12 job → ci-ok（内嵌 droid-review 轮询，`check_droid_review.sh` MAX_ATTEMPTS=90 × 30s ≈ 45min 上限——h1-alwayscheck-earlyexit 自 120×30s 收紧后再放宽（45 < quality-gate 100min 聚合窗，预算拉齐），上游聚合天花板 135min 不可外等，随后零红扫描）；qa.yml → qa-ok（文件头记录 1203 passed 约 2min 时点值，按当前 2426 规模估算 8 worker 为 3-5 分钟量级）；quality-gate 以 120min timeout 轮询 ci-ok + substrate-gate-suite + qa-ok（PR #248 死锁后自 80min 扩容；零红扫描排除 quality-gate 环形引用）。ruleset required 名单为 quality-gate、substrate-gate-suite、droid-review；ci-ok/qa-ok 由 quality-gate 间接聚合。
- 结构契约测试：`tests/test_ci_structure_contract.py:468-513`（TestCiOkStepOrder）钉住 ci-ok 内 droid-review 轮询 step 必须先于零红聚合 step。
- 覆盖缺口集中在交付执行面：BYOM 真实链路零执行级测试（只能真 PR 验证）、webhook 3 个脚本与 2 个 workflow 无直接测试、substrate gates 0/2/3/4 无 pytest 化。

### 2.2 声明层（infraro）

- tests/ 目录仅 tests/.memory-anchor.md（memory 锚点），**零自动化测试**。
- 三门常驻（gate0/gate1/gate3），但 CI 侧 step 级 `continue-on-error: true`（红不阻断）；gate0 当前实测红（test-droid-review.md unowned 未登记，仓根新增文件未进 registry）。
- gate1 接口面比对**空洞通过**：`substrate/gates/gate1_interface.py:127`（声明仓侧 `check_template_calls`；引擎仓侧同款扁平扫描在 `_templates_in_dir`，`substrate/gates/gate1_interface.py:73`）用 `glob("*.yml")` 只扫根层，而声明仓模板在双栈重构后全部位于 `templates/python/`、`templates/typescript/`，根层命中 0 文件（实测 glob=0、rglob=20），接口面比对形同虚设。详见 §3.2。
- 七类可测不变量（锚点纪律 / 双栈对齐 / secrets 规范 / config schema / 命名契约 / ci.yml 结构 / docs 一致性）全部无自动化断言，是 L2 的建设面（§5）。

### 2.3 CI 与自建 runner

账号为 User 账号（无 org runner 端点），全 per-repo 注册。宿主为单台 VM（代号 ce01，PVE）。**拓扑为时点快照，L3/L4 执行前必须按 §3.3 P-3 用例重探**（清册真源 evidence/ce01-runner-layout.md，活体 gh api 为准）：PR #162 时点（2026-09-27）为 9 实例并存（infraro-core 4 + runner-hub 4 + scheduler 1）；2026-09-28 runner-06~09 经用户裁定退役注销（同日 11:50 CST 中间态为 6 实例：runner-hub 5 + scheduler 1 + 引擎仓 0，hub 池曾现 muse-1 且其 labels 缺 pve-linux）；同日 runner-06~09 经用户指令重建（边界例外二），17:16Z 终局复探为 **9 实例**（引擎仓 4 + runner-hub 4 + scheduler 1；muse-1 已不在任何池，出现→离场闭环回写见清册 §4）：

| 注册仓 | runner（PR #162 时点口径） | 2026-09-28 终局口径（17:16Z 复探） |
| --- | --- | --- |
| hdot123/infraro-core | runner-06 / 07 / 08 / 09（引擎仓专属池，当时全 online idle） | **已退役并于同日重建**（退役=用户 2026-09-28 裁定，清册 §3.1 登记；重建=同日用户指令边界例外二，清册 §3.3；现 4 台 online） |
| hdot123/runner-hub（私有信任域仓） | runner-01~04 | online 4 台（runner-01~04；muse-1 曾于 09-28 午前短暂入池，labels `[self-hosted, Linux, X64, muse-1]` 缺 pve-linux，同日离场——事件闭环见清册 §4 回写记录） |
| hdot123/scheduler | runner-05 | online（不变） |
| hdot123/infraro（声明仓） | 无 | 聚合 job 用 ubuntu-latest（不变） |
| hdot123/consumer-a / consumer-b | 各 1（pve-runner-consumer-a/b） | **已注销**：仓随 scheduler PR #29（commit 52a5d73，2026-09-25）删除，runner 随之 404（本行仅为注销登记，非活体） |

runner 环境契约（版本面单一事实源 runner-tools.toml，六工具，契约测试钉住）：uv 0.12.7 / ruff 0.16.3 / mypy 2.3.1 / actionlint 1.7.11 / shellcheck 0.10.0 / jq 1.7 全局直调；droid CLI 预装（≥0.200）——**该契约不是 runner-tools.toml 成员**，真源为 workflow 内联契约 + 测试钉（tests/test_droid_cli_host_first.py:40,68-69，VERSION_FLOOR_MINOR=200）；python3.12 per-run venv；共享缓存；labels 契约 `["self-hosted", "pve-linux"]`。运维边界：runner-01~04 禁触碰；已注销实例（consumer-a/b）不在活体清单（runner-06~09 曾于 2026-09-28 退役，同日已重建为引擎仓专属池活体，见 §2.3 表与清册 §3.3）；禁 systemctl（用 kill 进程树 + `setsid nohup ./run.sh`）；共享 _work 禁 sparse-checkout（防毒铁律）。

job 路由总表：引擎仓 23/23 workflow 用 self-hosted（CI 全 12 job、QA、droid-review 全部、auto-merge、governance、quality-gate、release 双件、scan/heartbeat、branch-cleanup 等，实测逐文件含 self-hosted 零例外）；hosted 仅镜像仓副本 job 与声明仓聚合 job。镜像仓 workflow 副本由其 sync-release.yml 随 release 快照机械同步（机制与解锁终态见 §8.5）。

### 2.4 PR #162 全链时间线（活体成功例）

成功例 run 36317738610（PR #162，2026-09-27 合并；时点为调查记录时区）：

| 里程碑 | 实测 | 备注 |
| --- | --- | --- |
| PR open | ≈12:03 | 起点 |
| setup job | 11s | self-hosted pickup 正常 |
| plan-shards | 1m27s | setup 完成到 plan-shards 开机存在 **8m26s 排队空窗**（疑 4-runner 池与 CI 并发争用） |
| review-shard（matrix） | 5m29s | BYOM 链正常；结束（12:25:33Z）到聚合 job 开工（12:35:55Z）另有 **10m22s 未标注间隙**（轮询零红期与排队混合，性质未分离，L3 用例设计时单列观测项） |
| droid-review 聚合 job | 21m05s | 整 run wall ≈53min |
| droid-review check 完成 | 12:57 | |
| ci-ok | 12:57 → 13:05 | 轮询 + 零红扫描 |
| quality-gate | 2m56s | |
| auto-merge squash | 13:23:50 | DISPATCH_TOKEN 凭证 |
| 全链（open → merge） | **≈80min** | check-runs 实测 27 SUCCESS + 2 SKIPPED（总 29，head sha f5e2e343，gh api 2026-09-28 复核） |

失败例 run 36312532565：review-shard failure → 聚合 fail-closed 跟红。引擎仓近 30 runs 分布为**时点快照而非稳定基线**，L3 执行前必须重测并归因：2026-09-28 重采（gh run list --limit 30，窗口 09-27T23:32:48Z ~ 09-28T01:44:51Z）为 success 13 / skipped 15 / failure 2 / cancelled 0；2026-09-28 凌晨审计时点为 success 11 / failure 6 / skipped 8 / cancelled 4，failure 6 集中于 09-27T15:15Z 与 15:17Z 两波事故簇（CI 与 Quality Gate 等 2-4 连红），执行前须先归因；09-27 调查时点为 success 17 / skipped 11 / failure 1 / cancelled 1。「无系统性常红」的结论以执行时点重采与事故簇归因为准。self-hosted pickup 健康度：12 job 由 runner-06~09 四台正常拾取（run 36317738711 jobs API 实测分布 06×2/07×3/08×4/09×3），单 job 1-4min。真正要盯的两个信号：plan-shards 后约 8.5 分钟排队空窗（容量信号）与 Mac 侧 poll-releases 断链（§3.1）。

## 3. 前置修复清单（测试执行前必须完成）

> 三项活体问题均由 2026-09-27 调查实证。每项配「修复后如何验证」的专项用例（P-1/P-2/P-3）；未完成前，对应层的验证结果不具证据价值。

### 3.1 前置修复① Mac 侧 poll-releases 哨兵断链

- **现状**：宿主 Mac 上 poll-releases.log 每 5 分钟报「哨兵缺失（locks/.poll-bootstrap-done 不存在），禁止派发。请先运行 poll-releases.sh --init」（2026-09-28 10:16 实测仍在报；哨兵在 `~/.factory/webhook/` 根与 `locks/` 两处均不存在，日志明示真路径在 locks/ 下）；trigger-release.log 最后成功派发 v0.18.8（2026-09-18）；v0.19.0（2026-09-25 发布）无锁文件、未派发。release-gateway 下游链自 v0.18.8 后未跑通。
- **修复**：Mac 本机执行 `poll-releases.sh --init` 生成 `.poll-bootstrap-done` 哨兵（落点 `~/.factory/webhook/locks/`，以脚本与日志口径为准），解除派发禁令。
- **专项验证用例 P-1**：
  1. 哨兵文件存在（Mac 本机 `ls ~/.factory/webhook/locks/.poll-bootstrap-done`，或按 poll-releases.sh 部署路径核验）；
  2. 下一个轮询周期（launchd `com.factory.poll-releases.plist`，300s）内 poll-releases.log 无 ERROR 行，且出现对下一发版 tag 的发现记录（哨兵修复时点后首个发布 tag；v0.20.0 已于公告链断链期发出 tag，恢复后可能需按 §3.1 处理历史 tag 的补发现或锁目录核对）；
  3. locks 目录出现 `release-announce-<tag>.json` 锁，各 engineConsumer 仓收到 release-gateway 派发会话（派发目标真源 = 本机 repositories.yml engineConsumer:true 条目的 githubRepo 字段，当前映射：memory-core→hdot123-org/memory、mencbo→hdot123-org/mencbo、infraro→hdot123/infraro；执行前核对，防散文命名与实际仓名不一致）；
  4. 完整闭环与幂等负例见 §8。
- **层级依赖**：L5（§8）以此为本层前置；其他层不受影响。

### 3.2 前置修复② 声明仓 gate1 模板扫描 glob 空洞

- **现状**：声明仓 `substrate/gates/gate1_interface.py:127` 的 `check_template_calls` 用 `templates_dir.glob("*.yml")` 只扫根层（引擎仓侧同款扁平 glob 在 `_templates_in_dir`，`substrate/gates/gate1_interface.py:73`）；双栈重构后模板全在 `templates/python/`、`templates/typescript/`，实测 `templates/*.yml` glob 命中 0、rglob 命中 20。接口面比对（uses 目标存在、带 workflow_call、with 键合法、@ref 现存 tag、fail-closed）在空洞上 PASS，**无证据价值**。
- **修复**：一行修复 `glob("*.yml")` → `rglob("*.yml")`，并把支撑件（ci.yml、actionlint.yaml 等两栈配套件）纳入扫描面；引擎仓与声明仓两侧实现同步修。
- **专项验证用例 P-2**：
  1. 修复前基线取证（声明仓根执行）：

     ```bash
     python3 -c "from pathlib import Path; t=Path('templates'); print(len(list(t.glob('*.yml'))), len(list(t.rglob('*.yml'))))"
     ```

     期望输出 `0 20`，证明空洞存在；
  2. 修复后本地跑 gate1 exit 0，且输出可见的 template call 覆盖数从 7（现仅自仓调用可见）上升到覆盖两栈模板面（具体计数以实跑输出为准，登记进证据）；
  3. fail-closed 回归：故意把一个模板的 uses 指向不存在的 workflow 文件 → gate1 必须红；还原后复绿；
  4. §5 的 L2 不变量套件在修复后才有接口面证据，故 gate1 修复是 L2 的前置项。
- **层级依赖**：L2（§5）前置。

### 3.3 前置修复③ 自建 runner 清册过时

- **现状（2026-09-28 已修复并多次校准，终局 9 实例闭环）**：`evidence/ce01-runner-layout.md` 原为 2026-09-24 快照（7 实例，缺 runner-06~09、含已注销 consumer-a/b 死行），2026-09-28 已完成多轮拉平：晨间补录 06~09 并注销 consumer 死行（9 实例口径校准）；同日 runner-06~09 经用户裁定退役注销（清册 §3.1），二次校准为 5 实例口径；午前 muse-1 短暂入池 runner-hub（labels `[self-hosted, Linux, X64, muse-1]` 缺 pve-linux）触发 6 实例中间态；同日 runner-06~09 经用户指令重建（边界例外二，清册 §3.3），17:16Z 终局复探 **9 实例**（引擎仓 4 + runner-hub 4 + scheduler 1，muse-1 已不在任何池）。muse-1 事件已按清册 §4 回写闭环（出现登记→离场确认，未在池不占 §1 表行，表 == 活体 9，无「待 owner 裁定」悬挂态）；L3/L4 执行前以活体 gh api 与清册回写后状态为准。该清册性质为**快照性登记文档（registry of record）**：注册真源 = 各仓 runners API + 宿主 VM 本机，容量基线 = 活体 gh；清册与真源之间唯一合法关系是回写对账——注册/注销必回写（checklist 已固化于清册 §4）。runner-01~04（runner-hub）与 runner-05（scheduler 仓）仍以活体 gh api 为准。
- **专项验证用例 P-3**（按扩展后清册，任一时点可复跑）：
  1. 三 runner 仓逐仓对清册 diff：`gh api repos/hdot123/{infraro-core,runner-hub,scheduler}/actions/runners` 输出与清册 §2 活体核实段逐条一致（2026-09-28 17:16Z 终局复探口径 4 / 4 / 1（合计 9）；时点不同口径不同，以重探时点为准）；
  2. 死行零残留：consumer-a/b（2026-09-25 注销，scheduler commit 52a5d73）不出现在清册 §1 活体实例清单；runner-06~09 曾于 2026-09-28 退役、同日重建（清册 §3.1/§3.3 闭环），现为 §1 活体表行；
  3. 总数断言：三仓 total_count 合计 = 清册头部总量口径（2026-09-28 17:16Z 终局为 9——断言锚定「清册口径 == 同时刻活体实测」而非写死数值）；
  4. 清册 labels 与契约 `["self-hosted", "pve-linux"]` 一致。
- **层级依赖**：L4（§7）runner 注册指引以拉平后的清册为基线；如引擎仓需恢复自建执行面，按清册 §4 注册 checklist 重新注册并回写。

### 3.4 已知问题登记（不修复，仅登记，作为测试素材）

| 编号 | 已知问题 | 影响 | 关联测试 |
| --- | --- | --- | --- |
| K-1 | 声明仓 auto-merge caller 监听名单缺 `Quality Gate`（引擎仓自仓名单 2026-09-19 已补） | 绿 PR 合并延迟，依赖 schedule 兜底（名义 `*/30`，实测约每天 4.8 次、间隔 2-7 小时） | L3-1 时延观察项 |
| K-2 | 声明仓 docs/delivery-standard.md:78 secrets 面陈旧（仍写旧代理 key 形态） | 文档面与模板实际引用集漂移 | L2-C3 负例素材 |
| K-3 | 双栈 ci.yml 头注释仍写「引用引擎 v0.18.5」（实际锚 v0.19.0） | 注释级陈旧，无检测器 | L2-G3 负例素材 |
| K-4 | 模板目录可能存在嵌套 .git 工件（本地未跟踪工件，时变：曾观测 templates/python/.git 与 templates/typescript/.git 并存，2026-09-28 复测两侧均不存在；若未来在本地 checkout 过程中重新出现，仅会影响 python 栈一侧的扫描遍历——typescript 栈为纯拷贝面） | 干扰目录扫描自动化 | L2 扫描实现注意项（条件性：存在时过滤） |
| K-5 | tools/health-check.sh 与 scripts/bootstrap-substrate.sh 为 stub | 空转工具面 | 不纳入任何测试 |
| K-6 | 声明仓 gate0 实测红（test-droid-review.md unowned 未登记） | 声明仓门禁欠账 | L2 前置说明 |

## 4. L1 引擎层本地静态验证

### 4.1 范围与前置

- 对象：引擎仓（infraro-core）本地全量静态面。零凭证、零网络（pytest 套件全部 stub/monkeypatch）、分钟级。
- 前置：无（不依赖 §3）。Python 3.12 严格锁定（requires-python ==3.12.*），使用仓内 `.venv`（实测 3.12.13）。
- 执行位置：以下命令先 `cd` 到引擎仓根再执行。工具版本契约见 runner-tools.toml（ruff 0.16.3 / actionlint 1.7.11 / shellcheck 0.10.0）。

### 4.2 pytest 五 marker 套

分套命令（`--no-cov` 加速本地分套，`-n 4` 分套并行；五套命令可直接复制）：

```bash
pytest -q -m security -n 4 --no-cov -rA
pytest -q -m schema -n 4 --no-cov -rA
pytest -q -m integration -n 4 --no-cov -rA
pytest -q -m e2e -n 4 --no-cov -rA
pytest -q -m business_policy -n 4 --no-cov -rA
```

各套规模见 §2.1 表。注意 marker 间有重叠：五套合计执行数（2257）小于全量一次（2426），重叠用例会跨套重复执行，其余用例不带这五个 marker，均属预期。

### 4.3 全量 CI 同款命令

```bash
pytest -q -n 8 --dist loadgroup
```

CI 侧在此基础上另加 `-rA --cov=src/infra_core --cov-fail-under=45`（覆盖率门 45%，爬坡至 80% 计划中）。本地若要完全复刻 CI 判定：

```bash
pytest -q -n 8 --dist loadgroup -rA --cov=src/infra_core --cov-fail-under=45
```

### 4.4 lint 四件

```bash
ruff check . && ruff format --check .
actionlint -color
find . -name '*.sh' -not -path './.venv/*' -not -path './.git/*' -print0 | xargs -0 shellcheck -x
markdownlint docs/guides/fullstack-test-plan.md
```

（markdownlint 0.49.1，homebrew 安装；引擎仓无 lint 配置文件，走默认规则。actionlint 以 repo root 定位 `.github/actionlint.yaml`，自建 runner label 声明在其中。）

### 4.5 substrate 三门本地单跑

```bash
python substrate/gates/gate0_coverage.py
python substrate/gates/gate1_interface.py
python substrate/gates/gate3_exposure.py
```

- 三门秒级（就绪核验实测：gate0 0.08s / gate1 6.7s / gate3 5.1s，合计约 12s）；gate1 自动解析 sibling 声明仓（`../infraro`，无需设 DECL_REPO_DIR）。
- 若环境无 `python` 别名，用 `python3` 等价替换（仓库命令登记面为 python3 形态，见 services 约定）。
- gate2 需 GH_TOKEN（跨仓漂移），gate4 归引擎仓热启动计时，均不在本地默认三门内；gate1 在前置修复②完成前 PASS 不具证据价值（空洞通过）。

### 4.6 CLI 冒烟与 shell 守卫

```bash
bash scripts/cli_smoke_test.sh
bash test_shell_guards.sh
```

CLI 冒烟覆盖 `infra-cli` 七入口（scan / audit / version-sweep / venv create 及延迟导入面）；shell 守卫脚本为仓根 test_shell_guards.sh。

### 4.7 L1 通过判据

| 项 | 判据 |
| --- | --- |
| 五 marker 套 | 全部 exit 0，摘要行 passed 数与 §2.1 量级一致 |
| 全量 CI 同款 | exit 0；加 cov 形态时覆盖率 ≥ 45% |
| lint 四件 | 零输出零红 |
| 三门 | 各自 exit 0；gate1 覆盖数满足 §3.2 P-2 |
| CLI 冒烟 + shell 守卫 | 各自 exit 0 |

证据记录按 §10.2 四段式，命令输出原样粘贴。

## 5. L2 声明层不变量验证（计划新增 pytest 套）

### 5.1 前置依赖与执行形态

- **前置修复②（gate1 glob 修复）是本层前置**：修复前 gate1 的接口面比对空洞通过，L2 的锚点/引用类断言缺乏门禁交叉证据，PASS 无证据价值（见 §3.2）。
- 执行形态：在声明仓新增 `tests/`（pytest，纯静态、全离线、零凭证、零网络），运行命令 `pytest tests/ -q`。可先挂声明仓 ci.yml 的 gate-tests advisory（step 级 continue-on-error 现状下红不阻断），转 required 由后续决策。
- 断言实现只读两类文件：声明仓模板/文档面 + 引擎仓源（sibling 解析，config schema 断言需 import 引擎 `REQUIRED_CONFIG_KEYS`，位于引擎仓 src/infra_core/engine/evolution_utils.py:115）。
- 扫描实现注意 K-4：模板目录若存在嵌套 .git 工件（本地未跟踪、时变，2026-09-28 复测不存在），遍历时按文件名白名单（*.yml/*.md/*.json）过滤并跳过 .git 目录，不做 git 系统调用。

### 5.2 七类不变量断言设计

| 类 | 断言设计 |
| --- | --- |
| ① 锚点纪律 | A1 全部引擎引用锚单一 tag：正则收集 templates/ 全部 uses 行与 config engine_ref，断言全部等于 `v0.19.0`（单一常量；锚点常量随 L5 pin-bump 同步更新为 v0.20.0，断言锚定「单一常量」而非写死值）；A2 禁浮动锚：uses 行禁 `@main`/`@master`/裸 SHA；A3 锚点集合大小 == 1；provenance header 排除：copy-form 文件（governance.yml / branch-cleanup.yml）头部历史版本记录不算有效锚点 |
| ② 双栈对齐 | B1 七件套两栈逐件 diff==0（已知唯一豁免：evolution-scan.yml 的 engine_ref 行，python 栈不传依赖 callee 缺省 job.workflow_sha（templates/python/evolution-scan.yml:34-36），typescript 栈显式传 `engine_ref: v0.19.0`（templates/typescript/evolution-scan.yml:43）——功能等价但形态不对齐，先在测试中显式登记该豁免，修复形态后收窄）；B2 配套件（.evolution/config.yml、suppress.json、actionlint.yaml、shard-review-prompt.md）两栈逐字一致 |
| ③ secrets 规范 | C1 BYOM snake 单形态（factory_api_key / go_github_run_token，droid-review.yml 两栈同款；CF 路由模式 2026-10-18 起 opencode_go_key 退出注入面）；C2 废弃形态零引用（旧代理 key 名与旧域名面在模板面零命中）；C3 文档 secrets 面 ↔ 模板实际引用集一致（当前必红：K-2 声明仓 docs/delivery-standard.md:78 陈旧面，先修文档或登记 expected-fail）；C4 值零入仓交 gate3 兜底（L1 已含） |
| ④ config schema | D1 模板 `.evolution/config.yml` 九键全部 ∈ 引擎 REQUIRED_CONFIG_KEYS（只读 import 引擎常量断言，rule_packs 为可选键）；D2 白名单外未知键零容忍 |
| ⑤ 命名契约 | E1 workflow name 与 docs/naming-contracts.md 清单一致（CI / Evolution Governance / Droid Auto Review 等字节级）；E2 job key ci-ok 存在于 ci.yml；E3 文件名字节级契约（evolution-scan.yml / evolution-heartbeat.yml / droid-review-watchdog.yml 两栈精确存在）；E4 监听名一致性：auto-merge workflow_run 监听名单引用的名字必须在模板面存在或有登记豁免（当前必红：QA workflow 与 qa-ok job 在模板面不存在、仅作监听字符串，K 系素材，补齐或删监听前按 expected-fail 登记） |
| ⑥ ci.yml 结构 | F1 delivery-standard.md 4.2 七条结构断言（job 集合、ci-ok needs 面、droid-review 轮询 step 存在、三门 step 存在等）；F2 与引擎仓 TestCiOkStepOrder 同构：ci-ok 内 droid-review 轮询 step 必须先于零红聚合 step |
| ⑦ docs 一致性 | G1 README/architecture/onboarding 引用的模板文件名集合 == templates/ 实际集合；G2 onboarding 命令中的相对路径可解析；G3 ci.yml 头注释引擎版本与实际锚点一致（当前必红：K-3 注释仍写 v0.18.5） |

### 5.3 负例素材表

| 素材 | 现状 | 对应断言 | 预期 |
| --- | --- | --- | --- |
| 双栈 ci.yml:3 头注释「引用引擎 v0.18.5」 | 注释级漂移，实际锚 v0.19.0 | G3 | 修复前红（expected-fail 登记） |
| evolution-scan.yml engine_ref 分歧 | python 不传 / typescript 显式传 | B1 豁免登记 | 豁免后绿；形态统一后收窄豁免 |
| 声明仓 delivery-standard.md:78 secrets 陈旧面 | 文档写旧代理 key 形态，模板面零命中 | C3 | 修复前红 |
| QA/qa-ok 无属主 | 仅作 auto-merge workflow_run 监听字符串 | E4 | 登记豁免或补齐实体 |
| gate1 glob 空洞 | 接口面零比对 | 前置修复② P-2 | 修复前 gate1 PASS 无证据价值 |

### 5.4 L2 通过判据

- 七类断言套件在声明仓 `pytest tests/ -q` 全绿；expected-fail 项逐条显式登记（素材、原因、修复出口），不允许静默跳过（禁 skip 掩盖）。
- gate1（前置修复②后）与 L2 锚点断言交叉一致：两面对模板 uses 集合的可见性一致。
- 修复出口落实后收窄豁免：K-2/K-3/E4 对应素材修复后，把对应 expected-fail 转正为硬断言。

## 6. L3 引擎仓活体 E2E

### 6.1 范围与前置

- 对象：引擎仓（infraro-core）真实 PR 全链：CI 12 job → Droid Auto Review（BYOM 网关链）→ quality-gate 聚合 → auto-merge squash 合并 → webhook 通知链。
- 前置：无硬前置（可在前置修复完成前执行）；建议在 runner 池空闲窗口执行以降低 8.5 分钟排队空窗方差（§6.3）。
- 纪律：一律同仓分支 PR（fork PR 会被 setup job fail-closed 拒绝，见 L3-2）；不 force push（ruleset 禁止）；先例：PR #156 探针、PR #162 全链成功例。

### 6.2 用例矩阵

| 编号 | 用例 | 类型 | 目的 | 关键步骤 | 通过判据 |
| --- | --- | --- | --- | --- | --- |
| L3-1 | 常规 PR 正例全链 | 正例 | 验证全链收敛到 squash 合并 | 同仓 feature 分支开含代码+docs 的 PR（避免 docs-only 以穿过 plan-shards 真分片）→ 全链自然跑 | 全 check SUCCESS（成功例形态：27 SUCCESS + 2 SKIPPED（总 29，check-runs API 实测，2026-09-28 复核），以当次实况为准）；quality-gate 绿；PR 以 squash 合并；聚合 check 名为 `droid-review`；artifact 前缀 `droid-review-debug-` 存在 |
| L3-2 | fork PR fail-closed 负例 | 负例 | 验证 fork 防线 | 用 fork 仓携带同 patch 开 PR | setup job 以 fork guard 语义 exit 1（head.repo.full_name 与 repository 不同即拒）；下游全 skip；无任何密钥暴露面 |
| L3-3 | unattributed changes 额外审批 | 负例/流程 | 验证 ruleset 额外审批门 | 非 owner 账号开含受保护路径（.github/workflows 等）变更的 PR | merge 被 require_extra_approval_for_unattributed_changes 阻塞，要求额外批准；owner 批准后放行恢复 |
| L3-4 | watchdog 自愈恢复面 | 恢复 | 验证事件自愈与手动 sweep 路径（D-1 后触发器全开：workflow_run + schedule + workflow_dispatch） | 对卡死/失败的 droid-review run 观察一次真实上游失败触发的 self-heal（或 `gh workflow run droid-review-watchdog.yml` 手动 sweep）；注意 G6 dry-run 首跑期（前 2 次运行 observe）不产生真实 rerun/cancel | run 日志可见对应 job 真实执行（非全 skip）；G3/G5 护栏在日志可读；artifact 前缀契约保持；聚合最终收敛 |
| L3-5 | review-shard 失败 → 聚合 fail-closed 跟红 | 负例 | 验证聚合跟红语义 | 参照失败例 run 36312532565 形态：制造 shard 失败（如 BYOM 认证面异常） | shard 红 → 聚合红（fail-closed 跟红，绝不绿）；quality-gate 拒绝；PR 不合并；debug artifact 可取 |
| L3-6 | Dependabot 特例 | 正例 | 验证特例矩阵 | 触发一条 dependabot 分支 PR | droid-review 跳过、ci-ok 放行；check 集合符合特例矩阵 |
| L3-7 | evolution scanner Linear 写入链 | 正例/运行时 | 验证 Linear issue 写入闭环（scanner 建 issue / dedup / auto_close_resolved；Opus 5.5 终审事项 7 纳入） | 在引擎仓真实 self-scan 窗口（schedule cron `17,47 * * * *` 双窗，或 `gh workflow run evolution-scan.yml`）观察一个含新 finding 的 tick：scanner 创建 GitHub Issue（footer 含「此 Issue 由 evolution scanner 自动创建…对应 PR 合并后自动关闭」语义，evolution_scanner.py 建 issue 路径）→ 同 tick 内重复 finding 被 dedup_intra_tick 去重（不产生重复 Issue）→ 已修复 finding 的存量 Issue 由 auto_close_resolved 关闭（信任链 `_verify_fix_merged_via_linear`：linear-linkback 两级提取（evolution_utils.py：内联注释 INFRA-xxx 优先，href 兜底），LINEAR_API_KEY（secrets.linear_api_key）就绪时校验 Linear issue 终态后才关）。注意 auto_close_resolved 的时序不变量（update_history 先于 close 判定，evolution_utils.py L301）——同一 tick 内不可回滚历史 | Issue 面三个运行时证据齐备：新 finding 建了恰好一个 Issue（无 dedup 漏网重复）；同 tick 重放零新增；关闭的 Issue 带 linear-linkback 与 PR/merge 关联证据。负向判据：LINEAR_API_KEY 未配置时 auto_close_resolved 不验证 Linear 终态、保持保守不误关（scanner 日志明示降级语义），不作为失败 |

### 6.3 时延基线与宽时限建议

时延基线取自 PR #162 实测（§2.4），断言时限按下表放宽（self-hosted 池排队方差大，8m26s 空窗实证）：

| 观测点 | 实测基线 | 断言宽时限建议 |
| --- | --- | --- |
| 全链 open → merge | ≈80min | ≤ 4 小时（覆盖排队高峰与审批延迟） |
| 单 job pickup | 1-4min | ≤ 15min（空窗期容差） |
| plan-shards 排队空窗 | 8m26s（≈8.5min） | 观测项不断言；空窗中位数 >10min 触发扩容评审 |
| review-shard | 5m29s | ≤ 45min（reusable 默认 timeout） |
| 聚合 job | 21m05s | ≤ 90min（caller 聚合 job timeout 量级） |
| ci-ok 轮询窗口 | 12:57 → 13:05（约 8min） | ≤ 45min（check_droid_review.sh MAX_ATTEMPTS=90 × 30s 上限，h1-alwayscheck 收紧后；实测时点为收紧前 120×30s 窗口） |
| quality-gate | 2m56s（≈3min） | ≤ 120min（其自身 timeout；PR #248 慢尾死锁后自 80min 扩容） |

补充：fork PR 不适用本表（fail-closed 秒级）；pick-up 时延建议按周聚合为容量基线，空窗持续恶化时先查 runner 池占用再查 workflow 并发。

**9 实例口径自洽复查（2026-09-28 F4 销项）**：本表基线取自 PR #162 实测，基线的机理面是引擎仓专属池 runner-06~09 四台对 CI 12 job + droid-review + QA 的并发争用——9/6 实例总量口径只影响宿主负载叙述，不改变该机理与各观测点数值。consumer-a/b 注册于他仓不参与引擎 job 拾取，其注销不改变空窗机理；runner-06~09 曾于 2026-09-28 退役、同日重建（§2.3/清册 §3.3），执行面已恢复，L3 复测前仍按 §9 R3 口径核对活体承接面。本表无需改动：8m26s 基线作为历史实测维持，宽时限判据不受 9 实例→6 实例（2026-09-28 11:50 CST 口径）总量变化影响。

### 6.4 L3 通过判据

- L3-1 与 L3-6 必须绿（正例收敛）；L3-2/L3-3/L3-4/L3-5 观测到设计行为即通过（负例类以「按预期失败/阻塞/跟红」为通过形态）；L3-7 按 §6.2 行内三证据判据（建 issue / dedup / auto_close 各一），LINEAR_API_KEY 缺失的降级运行不作为失败（登记为凭据面观测项）。
- 时延数据按 §6.3 宽时限判据记录；超出宽时限即记录偏差并归因（排队 / BYOM / 聚合轮询）。
- 每例证据：run URL、check 集合 JSON（`gh run view <id> --json jobs`）、时间戳，按 §10.2 四段式回填。

## 7. L4 消费仓演练（新建专用测试仓）

### 7.1 双路径取舍

| 路径 | 适用 | 成本 | 说明 |
| --- | --- | --- | --- |
| 路径 A：引擎仓测试 PR（零新增基建） | 只需验证引擎仓主链 | 一条 PR 的消耗 | 直接在引擎仓开测试 PR 触发全链；有先例（PR #156、PR #162）；注意 unattributed changes 可能要求额外批准 |
| 路径 B：新建专用测试仓（本节主设计） | 需验证消费仓接入面（thin-caller / secrets / 授权门 / ruleset） | 新仓一次全链 + 拆除 | 最小可跑集 + runner 注册 + 故障注入 + 拆除，见下 |

路径 B 的必要性：只挂 evolution-scan + droid-review 不够（无 ci-ok 产出永不合并）；最小集必须能产出 ruleset required check 并走完 auto-merge。

### 7.2 路径 B 建仓步骤（最小可跑集）

1. 新建私有测试仓（owner 手工或 gh repo create；命名建议带 test 前缀便于拆除识别）。
2. 拷贝模板（引用声明仓模板时用相对形态）：

   ```bash
   cp templates/python/.github/workflows/ci.yml            <测试仓>/.github/workflows/ci.yml
   cp templates/python/droid-review.yml                    <测试仓>/.github/workflows/droid-review.yml
   cp templates/python/auto-merge.yml                      <测试仓>/.github/workflows/auto-merge.yml
   cp templates/python/.github/actionlint.yaml             <测试仓>/.github/actionlint.yaml
   ```

   模板 uses 当前锚 `v0.19.0`（镜像仓消费面）。**测试 caller 必须 pin `v0.20.0`**（首个含 F2 x-ci-key 门禁的消费面 tag；v0.19.0 消费面语义已断——物理上无 x-ci-key 通道，pin 必 401，禁止）。镜像侧 v0.20.0 消费 tag 已就绪（2026-09-29 实测 mirror tags 含 v0.20.0，同步 run 36488422715 success，机制见 §8.5），以 `uses@v0.20.0` 执行。caller 不传 x-ci-key 同样 401（§9 R2）。若 §8.2 pin-bump 已完成，拷贝源即已锚 v0.20.0；否则执行本节前先完成 bump 或手工改锚。
3. 配置四件 secrets（值一律不落仓库不落日志）：

   ```bash
   gh secret set DISPATCH_TOKEN      -R <owner>/<测试仓>
   gh secret set FACTORY_API_KEY     -R <owner>/<测试仓>
   gh secret set GO_GITHUB_RUN_TOKEN -R <owner>/<测试仓>
   gh secret set WORKER_CI_KEY       -R <owner>/<测试仓>
   ```

   WORKER_CI_KEY 对 caller pin `v0.20.0` 起有效（v0.20.0 消费面含 `worker_ci_key` 透传声明与 x-ci-key 注入；v0.19.0 面上配置该 secret 为 no-op 且不配也 401）。镜像侧 v0.20.0 消费面已就绪（§8.5 解锁闭环），run 生效以 pin 切到 v0.20.0 为准。

4. 配置 repo variable（引擎授权门判定源，缺失则引擎管线入口 fail-loud）：

   ```bash
   gh variable set ENGINE_CONSUMERS --body authorized -R <owner>/<测试仓>
   ```

5. 引擎消费授权门登记：hdot123/scheduler 仓根 authorized.yaml 是 ENGINE_CONSUMERS variable 的唯一真源（文件头自述），引擎管线自动铺设授权名单，当前名单仅 infraro，为测试仓加一行登记。**机制注意**：该文件是引擎消费授权门，不是 release-gateway 派发选仓真源——派发选仓真源为本机 `~/.factory/config/repositories.yml` 的 `engineConsumer: true` 字段（当前三块，githubRepo 字段分别为 hdot123-org/memory、hdot123-org/mencbo、hdot123/infraro）；测试仓如需被派发 pin-bump，须在 repositories.yml 另行登记 engineConsumer，此为独立步骤。
6. 配置分支保护 ruleset：required = ci-ok + droid-review；squash-only + linear history；strict up-to-date（可选，开了需处理 update-branch 腿）。
7. 打一条最小 PR（改动一个无关紧要文件）走全链，验证收敛合并。

### 7.3 最小配置集清单（四件 secrets + variable + scheduler + ruleset）

| 件 | 载体 | 用途 | 缺失后果 |
| --- | --- | --- | --- |
| DISPATCH_TOKEN | secret | PAT：auto-merge 合并凭证（防 GITHUB_TOKEN 递归抑制）、Issue/label 写入 | auto-merge 无法合并 |
| FACTORY_API_KEY | secret | droid-review BYOM 调用 | review-shard 失败 |
| GO_GITHUB_RUN_TOKEN | secret | BYOM 网关运行凭证（CF 路由模式 2026-10-18：上游 opencode 凭证由 CF AI Gateway 端存储承担，无需 OPENCODE_GO_KEY） | review-shard 失败 |
| WORKER_CI_KEY | secret | x-ci-key 门禁值（caller pin v0.20.0 起必配生效；镜像侧 v0.20.0 消费面已就绪 §8.5，v0.19.0 面上配置无效且不配也 401） | 网关 401 |
| ENGINE_CONSUMERS=authorized | repo variable | 引擎授权门判定源 | 引擎管线入口 fail-loud |
| scheduler authorized.yaml 加行 | scheduler 仓 | 引擎消费授权门（ENGINE_CONSUMERS 真源，自动铺设授权名单） | 引擎授权门不含测试仓，消费面被拒 |
| repositories.yml engineConsumer 登记 | 本机 ~/.factory/config/repositories.yml | release-gateway 派发选仓真源 | release-gateway 不向该仓派发 |
| ruleset（ci-ok + droid-review，squash-only） | 仓设置 | required check 收口 | 合并无门禁语义 |

### 7.4 自建 runner 注册指引

- 依据：声明仓 docs/runner-registration-runbook.md（per-repo 注册 runbook）。**路由事实**：reusable workflow 的 job 在 caller 仓的 runner 上跑；新仓不注册 runner，则 `[self-hosted, pve-linux]` job 永久排队（声明仓已实证此坑后改 hosted 聚合 job）。
- 注册命令（宿主 ce01 上执行，owner-only SSH 边界；registration token 从仓 Settings → Actions → Runners 获取，短时效不落文档）：

  ```bash
  ./config.sh --url https://github.com/<OWNER>/<测试仓> --token <RUNNER_REGISTRATION_TOKEN>
  ```

- labels 必须为 `self-hosted` + `pve-linux`（契约值）。
- 宿主运维边界（NEVER break）：runner-01~04 禁触碰；已注销实例（consumer-a/b 于 2026-09-25 注销）不在活体清单；runner-06~09 曾于 2026-09-28 退役，同日重建（见 §2.3/清册 §3.3），现为引擎仓专属池活体，禁止以其为新建备选——L4 测试 runner 走 §7.2 步骤 1 纯新建路径；如引擎仓恢复注册走清册 §4 checklist；禁 systemctl，停启用 kill 进程树 + `setsid nohup ./run.sh`；共享 _work 禁 sparse-checkout（防毒铁律，2026-08-28 污染事故）。
- 注册后核验：仓 Settings → Actions → Runners 显示 Online；新 run 成功拾取并记录 runner 名（对照拉平后的清册，§3.3 P-3）。

### 7.5 hosted 与 self-hosted 取舍

| 形态 | 成本 | 适用 |
| --- | --- | --- |
| public 测试仓 + 镜像面（全 hosted） | 免费（公开仓 hosted 免费），零 runner 维护 | 只验链路语义、代码可公开的实验 |
| private 测试仓 + hosted | 烧配额（私有仓 hosted 分钟计费） | 不推荐用于全链演练 |
| private 测试仓 + self-hosted | 零分钟计费，需注册专属 runner | 本计划默认路径（§7.4） |

分钟面治理：额度 API 当前 token 无 user scope（404），不能在线查询；以 actions-budget-guard 每周一 01:00 UTC 审计兜底（阈值：单仓周 >2000min / 失败率 >30% / 账号周 >20000min）。

### 7.6 故障注入 S1-S7（复用 readiness-exercise 演练 2 清单）

> 执行者注意：worker 会话远端写操作存在实测拦截（push 被 hook 拦、dispatch 404、秘密检测拦预存字面，见 readiness-exercise.md 阻塞证据），S1-S7 由 owner 执行或经特许通道（§9 R6）。

- **S1 建立演练分支**：gh api 在测试仓建 refs/heads/feat/rollback-test（以 main sha 为基）。
- **S2 植入故障**：把某 workflow 的 runs-on 改为不存在的 label（如 `[self-hosted, nonexistent-label]`）提交。
- **S3 观测故障**：触发一次 run，确认 job 进入 startup_failure（gh api 查 run 与 jobs conclusion）。
- **S4 执行模板级回滚**：revert 该改动（revert PR 或 contents API 改回），确认 runs-on 复原。
- **S5 验证恢复**：重新触发 run，conclusion=success，记录执行 runner 名。
- **S6 回填证据**：把 S3/S5 的 run URL、S4 的 revert PR 号、分支名按 §10.2 四段式写回证据文档。
- **S7 清理**：删除演练分支。

判定：S1-S6 完成且证据可核验，故障注入项才算通过。

### 7.7 拆除清单（测试完毕逐项执行并回勾）

| 序 | 拆除项 | 命令/操作 |
| --- | --- | --- |
| 1 | scheduler authorized.yaml 移行 | 从 hdot123/scheduler authorized.yaml 删除测试仓行 |
| 2 | repositories.yml engineConsumer 注销 | 从本机 ~/.factory/config/repositories.yml 删除测试仓的 engineConsumer 登记（对应 §7.2 步骤 5 的独立步骤） |
| 3 | 模板 cron 面拆除 | 删除或注释测试仓内带 schedule 的 workflow（实测模板面：scan `13,43 * * * *`、heartbeat `47 */2 * * *`、auto-merge `*/10`、watchdog `*/30`、branch-cleanup `0 * * * *`——均为名义 cron，实际投递节奏受平台抑制，见 K-1 注记），或直接删 workflow 文件 |
| 4 | runner 注销 | 宿主上 `./config.sh remove --token <TOKEN>`（或仓 Settings 页 Remove）+ 停进程树 |
| 5 | secrets 清理 | 逐件 `gh secret delete <NAME> -R <owner>/<测试仓>`（五件） |
| 6 | variable 清理 | `gh variable delete ENGINE_CONSUMERS -R <owner>/<测试仓>` |
| 7 | ruleset 降级/删除 | 仓 Settings → Rules 删除测试 ruleset |
| 8 | 测试仓归档 | `gh repo archive <owner>/<测试仓> --yes` |

核验：拆除后 `gh api repos/<owner>/<测试仓>/actions/runners` 为空、secrets 计数 0、无活动 cron run。

### 7.8 L4 通过判据

- 最小集全链一次绿灯并 squash 合并（check 集合与 §6.3 时延判据同口径）。
- S1-S7 证据齐全且可核验。
- 拆除清单 8 项全部回勾并附核验输出。

## 8. L5 发版公告闭环

### 8.1 断链现状

- 链路：代码 PR 合并 → release-please.yml（schedule 2x/日 + dispatch；environment: production，2026-09-28 实测仅 branch_policy、无 required reviewer，发布直通）→ tag v* → release-announce.yml（release published；self-hosted + production environment）→ 宿主 Mac launchd `com.factory.poll-releases.plist`（300s 轮询）→ poll-releases.sh → 新 tag 无锁则 trigger-release.sh（per-tag 锁 `~/.factory/webhook/locks/release-announce-{tag}.json`）→ 逐 engineConsumer 仓派发 release-gateway 会话 → pin-bump PR → CI → auto-merge 合并闭环。
- **断链现状（2026-09-29 重探更新）**：§3.1 哨兵已 `--init` 修复（09-28，锁+哨兵落位，后续轮询周期零 ERROR）；v0.20.0 已于 09-28 16:10:17Z 发布，但哨兵 --init 按设计为其预建 placeholder 锁（不派发历史 tag），其下游消费派发留待 L5 执行时按 §8.2 步骤 2 语义处理；release-announce 链的 RELEASE_BROADCAST_URL 未配置（announce job 直通但跳过公告转发）。镜像腿已解锁（2026-09-29：两 secret 配置 + sync-release.yml 双 Authorization 缺陷修复 + dispatch success run 36488422715，镜像消费 tag v0.20.0 就绪，详见 §8.5）。L5 执行前确认 §3.1 修复态；镜像腿已闭环无需再处理。
- **前置依赖：§3.1 前置修复①（哨兵初始化）已达成并验证；镜像腿（§8.5）已解锁达成，消费面 tag 可用。**

### 8.2 修复验证步骤（闭环正例）

1. Mac 本机执行 `poll-releases.sh --init` 生成哨兵（前置修复①）。
2. 观察一个轮询周期（≤300s）：poll-releases.log 无 ERROR，出现对下一发版 tag 的发现记录（哨兵 --init 已于 09-28 执行并为既有 tag 预建 placeholder 锁——v0.19.0/v0.20.0 按设计不补派发，锁形态 `bootstrap:true, consumers:[]`；L5 执行时若需公告 v0.20.0，删除 `release-announce-v0.20.0.json` 后复轮询触发真实发现；以执行时点实际 tag 为准）。
3. 确认派发：locks 目录出现 `release-announce-<tag>.json` 锁（以恢复时点实际发布 tag 为准；v0.19.0 为历史未派发 tag，不作为本轮判据）；trigger-release.log 出现派发记录；下游三仓按 §3.1 P-1 用例 3 的 repositories.yml githubRepo 映射核对（hdot123-org/memory、hdot123-org/mencbo、hdot123/infraro）收到 release-gateway 会话。
4. 下游闭环：各仓 release-gateway 会话按 bump 配方全仓搜索 pin 面（pyproject git+ 依赖、workflow uses@tag、测试断言双写、uv.lock relock、文档散文）→ bump 到新 tag → 本地自测 → 开 PR。**bump 新 tag 时同步更新**：模板 `worker_ci_key` secrets 透传声明与仓级 secret（新 tag 已含 F2 x-ci-key 门禁，消费面需透传该件才能过网关；执行前先刷新 PR #162 遗留的「消费者 rollout 未文档化」状态，以各仓 bump PR 的 secrets 段落为证）。
5. 各 pin-bump PR 走 CI → auto-merge squash 合并，仓内 pin 面与 v0.20.0 齐步走。
6. 判据：三仓 PR 全合并；`rg` 抽查各仓 pin 面无旧 tag 残留（以仓内 grep 输出为证）。

### 8.3 per-tag 锁幂等性负例

- 用例：锁已存在时重复触发零副作用。两个入口各验一次：
  1. 等待下一个 300s 轮询周期自然重复发现同一 tag；
  2. 手动对 trigger-release.sh 重放同一 tag。
- 判据：两者均被 per-tag 锁拦截跳过——locks 目录不新增文件、既有锁文件 mtime 不变、下游仓无第二个 release-gateway 会话、无重复 pin-bump PR。
- 语义提示（对齐 docs/onboarding/consumer-onboarding.md §6）：同 tag 重复公告不产生重复接单；HTTP 200 只证明公告送达路由层，不证明规则命中；推送面离线窗口公告会丢，轮询面在下个周期自动补齐（≤1 个轮询间隔）。断言以「锁目录 + 下游 PR 去重」为准，不以 HTTP 状态码为准。

### 8.4 L5 通过判据

- 闭环正例（§8.2）1-6 全过，pin 齐步走核验输出留档。
- 幂等负例（§8.3）两入口均零副作用。
- production environment 门（release-please 与 release-announce 两侧）按 2026-09-28 实测现状仅 branch_policy、无 required reviewer，链路直通；若 owner 后续补配 required reviewer，审批等待列为外部依赖并记录时点，不计入自动化判据（§9 R1）。

### 8.5 镜像同步腿（消费面 tag 供给机制，2026-09-29 按实测机制收口）

架构事实：引擎发版 = GitHub Release（engine repo，v0.20.0 已于 2026-09-28 16:10:17Z published）；镜像仓为公开免认证分发面（reusable workflow 消费 tag 的唯一来源）；无 PyPI。**镜像同步机制已查明（2026-09-28/29 全来源普查）：唯一同步入口 = 镜像仓 `sync-release.yml`**——三触发形态（workflow_dispatch 带 tag 参数 / repository_dispatch upstream-release 事件转发 / schedule `23 */6 * * *` 轮询兜底）；release notes、git 快照、asset 全部机械复制自上游，零手写内容；因 GitHub 平台规则（默认 GITHUB_TOKEN 永远不能写 `.github/workflows/` 下文件），快照提交必须用带 workflows 权限的凭据推送。

**解锁终态（2026-09-29 闭环，全一手实证）**：镜像仓两 secret（UPSTREAM_READ_TOKEN：上游 contents:read；MIRROR_PUSH_TOKEN：contents:read/write + workflows:read/write，均 fine-grained PAT、90 天有效期至 2026-12-28）已配置（`gh api …/actions/secrets` 实证 count=2，只验名不验值）。解锁过程暴露并修复两处凭据面缺陷：① sync-release.yml checkout 步 `persist-credentials: true` 与 push 步 extraheader 并存 → git 双 `Authorization` 头 → push 400「Duplicate header」（run 36470490535）——修复 commit 7d00f528（`persist-credentials: false`，保留 push 步 MIRROR_PUSH_TOKEN extraheader 设计）；② MIRROR_PUSH_TOKEN PAT 初建缺 Contents 权限（仅 Actions/Workflows R/W + Metadata）→ 修复后 push 403「Permission denied to hdot123」（run 36485710519）——经 PAT Edit 补 Contents Read/Write（token 值不变，secret 无需重写）。终态 dispatch（run 36488422715，2026-09-28T21:47:52Z）**success 全步绿**：镜像 tags 现含 v0.20.0（连带 v0.19.0/v0.18.9）。上游转私期间（09-25~09-29）schedule 腿 404 + soft_fail skip 静默 no-op 的历史在案。**「镜像 ref 随发版自动建立」假设仍作废**——同步只经 sync-release.yml 触发（发版后需 dispatch/repository_dispatch/6h schedule 之一到达），凭据就绪为前提。上游 release-announce 链的 RELEASE_BROADCAST_URL 仍未配置（转发腿不存在）；宿主 Mac 侧无镜像类脚本（普查在案）。

解锁与执行路径（历史记录保留；2026-09-29 已由 agent 在批准面内完成①②③）：

1. owner 在镜像仓 Settings → Secrets 配置 UPSTREAM_READ_TOKEN（对 hdot123/infraro-core 的 contents:read）与 MIRROR_PUSH_TOKEN（镜像仓 contents:write + workflows:write 的 fine-grained PAT 或 App installation token）。
2. 补同步：`gh workflow run sync-release.yml -R hdot123/infraro-core-mirror -f tag=v0.20.0`。
3. 验证四件套：镜像 tags 含 v0.20.0；`.github/workflows/droid-review-shards.yml?ref=v0.20.0` 含 `worker_ci_key` 且 BYOM baseUrl 为新形态（上游 v0.20.0 树已核：shards.yml worker_ci_key 命中、baseUrl `…/custom-opencode-go/v1`）；`livetruth_probes.py --t2` 的 T2-09 翻 PASS；gate3 版本四源对齐复跑绿。
4. 判据：四件套全中即消费面 v0.20.0 可用，§7.2 L4 pin 步骤与 §8.2 pin-bump 均以其为前提。**2026-09-29 复验：四件套全中**（tags 含 v0.20.0；shards.yml@v0.20.0 worker_ci_key 2 处命中 + BYOM action baseUrl `…/custom-opencode-go/v1`；T2-09 PASS、`--t2` 12/12；gate3 四源 0.20.0 复跑绿），挂账清零。

## 9. 风险与资源矩阵

| 编号 | 风险/约束 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | production environment 门（release-please + release-announce） | 2026-09-28 发版链实测：environment production 仅 branch_policy、无 required reviewer，发布直通（v0.20.0 全链零 waiting 在案；09-25 v0.19.0 announce run 的历史 waiting 为旧配置遗留，现已不在）。只挡发版腿不挡 PR 合并；若 owner 补配 required reviewer，则 L5 全链验证需 owner 两次批准 | 按当前现状（直通）写判据；门面如再变更，审批等待列为外部依赖并记录时点，不作为自动化判据 |
| R2 | x-ci-key 过渡窗口 | lagging pin caller 不传 x-ci-key → 网关 401 | L4 caller pin 含 F2 的首个消费面 tag **v0.20.0**（v0.19.0 无该通道：消费面语义已断，pin v0.19.0 必 401）；镜像消费 tag v0.20.0 已就绪（2026-09-29 镜像腿解锁，§8.5）；声明面 required:false 是有意过渡语义，门禁真源在 Worker |
| R3 | self-hosted 容量争用 | 引擎仓专属 4-runner 池（runner-06~09，PR #162 时点）vs CI 12 job 并发；8m26s 空窗实证；2026-09-28 06~09 退役后同日重建（§2.3/清册 §3.3），执行面已恢复；L3 执行前仍按 §2.3 时点快照纪律重探活体；链路时长方差大 | 断言给宽时限（§6.3）；避开整点 cron 高峰；空窗中位数 >10min 触发扩容评审 |
| R4 | Actions 分钟面 | private hosted 烧配额；额度 API 无 user scope（404） | self-hosted / 公开仓 mirror hosted 免费路径优先；budget-guard 周审计阈值兜底（§7.5） |
| R5 | 敏感字面量红线 | gate3 五类正则：家目录绝对路径、宿主名连字符形态（统一写 ce01）、1Password 引用协议字面量、裸 IP、个人邮箱、数字后缀主机名模式 | 写作、PR 描述、commit message、证据回填全程规避；证据入库前跑红线扫描 |
| R6 | worker 会话远端权限受限 | git push 被拦、dispatch 404、秘密检测拦预存字面（readiness-exercise.md 阻塞证据实证） | 活体步骤（L3 PR 开关、L4 S1-S7、L5 派发观察）由 owner 执行或先建特许通道（豁免专用测试仓） |
| R7 | 共享 _work 污染 | 2026-08-28 事故前科 | runner 使用铁律：禁 sparse-checkout（§7.4） |
| R8 | 通知链 strict 语义 | 引擎仓 WEBHOOK_NOTIFY_STRICT=true，CI 完成通知非送达即红。两跳语义：strict 实际只对第一跳故障（DNS/网关/Worker 鉴权）与双重故障生效——第二跳（Mac 离线）由 Worker 出箱入箱回 200 吸收，不触发红、不冻结合并（见引擎仓 docs/architecture.md §C9.5 两跳送达语义） | L3 观察项：测试窗口保持 webhook 链健康，通知失败红需与业务红区分归因 |
| R9 | 分支保护 strict/squash-only/linear history | 分支过期触发 update-branch 腿；禁 force push | 测试 PR 及时 rebase；全程禁 force push |
| R10 | release-please relock race（2026-09-28 实证） | relock push 后约 24s 窗口内 pull_request_target 触发锁旧 head：droid-review 对 pre-relock head 执行、其 check 永不出现于 PR 当前 head → ci-ok「Check droid-review status」轮询空转后 fail-closed 红（h1-alwayscheck 后：check_droid_review.sh stale-head 早退探测对非 PR-head 的 check 立即判因退出，不再烧满预算）；同型失败例 09-27T23:30Z（PR #163，55ab767→9e972824）与 09-27T15:56Z（feat PR） | 对 PR 正确 head 手动 dispatch `pr_number=<N>`（droid-review workflow_dispatch，setup 经 API 解析 HEAD_SHA 校验；PR #163 已实证 run 36372121057，queued 待拾取）；伴随 ci-ok/quality-gate 以 `gh run rerun --failed` 重试 |

资源量级：L1 本地（三门合计约 12s + pytest 分钟级）；L2 本地零凭证；L3 一条 PR（self-hosted 零计费）；L4 新仓一次全链 + 注册一台 runner + 拆除；L5 一次发版腿 + 三仓 pin-bump PR。16GB/8 核本地机器无并发压力。

## 10. 验收标准与证据记录模板

### 10.1 五层验收标准总表

| 层 | 通过判据 | 证据形态 |
| --- | --- | --- |
| 前置修复 | P-1/P-2/P-3 三用例全过（§3） | 修复 diff + 命令输出 |
| L1 | 五套 + 全量 + lint 四件 + 三门 + CLI 冒烟 + shell 守卫全绿（§4.7） | 终端输出原样（pytest 摘要行、各 exit code） |
| L2 | 七类断言全绿；负例素材逐条登记为 expected-fail，修复后转正（§5.4） | pytest 输出 + 豁免登记表 |
| L3 | 用例矩阵七例按 §6.4 判据（正例绿、负例按设计行为、L3-7 Linear 写入链三证据）+ 时延宽时限（§6.3） | run URL + check 集合 JSON + Issue 证据（L3-7）+ 时间戳 |
| L4 | 最小集全链合并绿 + S1-S7 + 拆除清单 8 项回勾（§7.8） | run URL + 拆除核验输出 |
| L5 | 闭环正例 1-6 + 幂等负例两入口零副作用（§8.4） | log 摘录 + PR 号 + 锁目录清单 |

### 10.2 四段式证据记录模板

每用例一份，落盘建议放引擎仓 evidence/testing/（该顶层目录已在 gate0 豁免登记）：

```markdown
## [用例编号] 用例名（执行日期）

### 前置条件
- 环境 / 分支 / 版本 pin（如 caller pin v0.20.0）
- 前置修复状态（引用 §3 对应项）

### 步骤
1. 命令或操作（含完整参数，可复现）
2. ……

### 结果
- 命令输出摘录（原样粘贴，含退出码，不加工）
- run / PR / issue 可核验标识（URL 或编号）

### 结论
- 通过 / 不通过，并对照 §10.1 判据行说明
- 偏差与归因（如有）
```

### 10.3 治理红线（记录可核验性）

readiness-exercise.md 的历史修正声明是本仓的治理样本：早期版本因伪造演练记录（虚构 run ID、虚构输出、代签 owner 批准）被独立复核证伪并整段删除。执行本计划时必须遵守：

- 证据三要素：可核验标识（run/PR/issue 编号）＋ 原样输出 ＋ 时点，缺一不成立。
- 不虚构、不代签：owner 批准栏由 owner 本人填写。
- 负例类用例以「观测到设计行为」为通过，不以「没出事」为通过。
- 所有引用的实测数字与 §11 引用表可对照；对不上的数字按证伪处理。

## 11. 附录——调查数据引用表

本计划全部实测数字与事实断言的溯源（规划调查时点 2026-09-27；标注「2026-09-28 重采/复核」的行为 v2 缺陷销项时点重采）：

| 引用 | 来源 | 支撑章节 |
| --- | --- | --- |
| runner-chain-survey | mission library 调查子代理 3（链路地图、runner 拓扑、PR #162 时间线、BYOM 链、发版断链、风险点） | §1 §2.3 §2.4 §3.1 §3.3 §6 §7 §8 §9 |
| engine-survey | mission library 调查子代理 1（2426 测试盘点、五 marker 数量、命令清单、覆盖缺口） | §2.1 §4 |
| declaration-survey | mission library 调查子代理 2（零测试基线、gate1 glob 空洞、七类不变量、负例素材） | §2.2 §3.2 §3.4 §5 |
| doc-readiness | mission library 就绪核验（守卫基线、三门实测耗时、gate3 红线、机器资源） | §4.5 §9 R5 与资源量级 |
| docs/readiness-exercise.md | 引擎仓（四段式模板形态、S1-S7 清单、环境阻塞实证、治理教训） | §7.6 §9 R6 §10.2 §10.3 |
| docs/onboarding/consumer-onboarding.md | 引擎仓（secrets 形态、公告七项语义、幂等锁语义） | §5.2 C1 §8.3 |
| docs/runner-registration-runbook.md | 声明仓（per-repo runner 注册流程与 labels） | §7.4 |
| evidence/ce01-runner-layout.md | 引擎仓（runner 拓扑登记正本：2026-09-28 多轮校准（9→5→重建回 9）+ muse-1 出现→离场闭环回写（清册 §4）+ 注销登记 + 回写 checklist，见 §3.3 P-3） | §3.3 |
| PR #163 / v0.20.0 发版腿证据 | gh api 一手探针（release-please PR #163 已 squash 合并 c1da488 09-28 16:00:59Z；release v0.20.0 published 09-28 16:10:17Z，含 F2；production 门实测零 waiting；镜像腿已于 2026-09-29 解锁闭环见 §8.5；详见 mission library/f2-release-leg-evidence.md、f2e-unblock-release-evidence.md 与 f2f-mirror-unlock-evidence.md） | §7.2 §8.2 §8.5 §9 R2/R10 |
| hdot123/infraro-core-mirror/.github/workflows/sync-release.yml | 镜像仓（镜像同步唯一入口：三触发形态、两 secret 凭据边界、机械复制语义；2026-09-29 解锁闭环——secrets count=2、双 Authorization 缺陷修复 commit 7d00f528、MIRROR_PUSH_TOKEN 补 Contents 权限、dispatch run 36488422715 success、tags 含 v0.20.0） | §8.5 |
| .github/workflows/evolution-scan.yml:6-21,64-72,183-192 | 引擎仓（自扫双窗 cron 17,47、reusable 输入 linear_api_key、scanner 运行与 LINEAR_API_KEY 注入、Issue footer 自动关闭语义） | §6.2 L3-7 |
| src/infra_core/engine/evolution_utils.py（linear-linkback 两级提取 / dedup_intra_tick / auto_close_resolved 时序不变量 L301） | 引擎仓（Linear 写入链信任链实现） | §6.2 L3-7 |
| tests/test_ci_structure_contract.py:468-513 | 引擎仓（TestCiOkStepOrder 契约） | §2.1 §5.2 F2 |
| src/infra_core/engine/evolution_utils.py:115 | 引擎仓（REQUIRED_CONFIG_KEYS） | §5.2 D1 |
| substrate/gates/gate1_interface.py:127（声明仓 check_template_calls；引擎仓同款扁平 glob 于 :73） | 引擎仓/声明仓（glob 只扫根层实现位） | §3.2 |
| scripts/cli_smoke_test.sh、test_shell_guards.sh、runner-tools.toml | 引擎仓（冒烟、shell 守卫、工具版本契约） | §4.4 §4.6 |

附注：mission library 四份报告为任务期调查工件；数字冲突时以文中标注的各处时点为准，长期真源以仓库内 evidence/ 与代码为准。文档自身作为交付物的合规基线：markdownlint 通过、doc classification 守卫 exit 0、gate0 exit 0、gate3 无新增暴露、敏感字面量零命中（在 feature 分支检出状态下以 rg 自查）。
