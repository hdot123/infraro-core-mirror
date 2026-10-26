# final-verdicts · 2026-10-25

## A. 问题侧（流水线病）

来源：用户整理《infraro-core PR 流水线问题清单（对抗用）》+ 本 agent 2026-10-25 实读 main@`7fa94f1` / ruleset `23079535` / PR #444 运行时。

| ID | 判定 | 备注 |
|----|------|------|
| A1 轮询跨 workflow 唯一胶水 | **证实** | 文件 SHA 对齐；QG/rollup 200×30s |
| A2 等候预算 100–120min 量级 | **证实** | timeout 120；墙钟 6h 与 cancel 重试相容 |
| A3 等候与干活共 pve 池 | **证实** | QG/rollup `runs-on: [pve-linux]` |
| A4 超时不 fail-fast | **证实** | 第二段轮询无「上游永不到」提前退出 |
| B1 刮日志关联 | **证实** | 精确字符串 grep + DISMISSED |
| B2 probe=none 三病因 | **逻辑证实 / 实例未分型** | #444 维持未验分型 |
| B3 窗口 per_page=30 | **证实** | |
| B4 workflow_run PR 解析脆弱 | **同向证实 / 全量抽样未做** | 大量 run `head_branch=main` |
| C1 QG 重跑 cancel Droid | **机制证实 / 同 head 对账未验** | 有 cancelled 样本 |
| C2 信封搬运 required check | **证实** | ruleset 绑 `droid-review`；rollup+mirror |
| D1 入口无限流 | **证实** | processing 已废；无跨 PR 帽 |
| D2 同质铸卡 | **证实** | ≥9 PR 同改 `test_publish_findings_baseline.py` |
| D3 堵的截面 | **部分证实** | main 自 #420 后午间无新合入；#444 长时间未绿 |

**13 条主体：证实 11；逻辑/机制证实且标注未验 2（B2 分型、C1 同 head 对账）。无推翻。**

## B. 方案侧（四簇根治定版）

来源：`cure-plan-v1.md` + `cure-adversarial-r1-r2.md`（对轮 R1+R2）。

| 结论 | 内容 |
|------|------|
| 整体 | **不推翻**；无 KILL |
| 硬门 1/2/3 | **全部成立且必要** |
| PATCH 须并入定版附录 | R1-A1 A2 A3；R1-B2 B3；R1-C1；R2-S1 S2（共 8） |
| OPEN 不挡拍板 | R1-B1 测 PR；R1-C2 超时零点；R1-C3 去重键强化 |
| 拍板用语 | 待大哥亲口确认后生效（见 cure-plan-v1） |

## C. 心智模型

四段分层 + fail-closed + PR/main 不对称 + 主路径不靠人手点 Run：**不动、不判错。** 病在胶水与容量。
