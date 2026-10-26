# infraro-core 流水线四簇根治方案（定版 · 2026-10-25）

状态：方案已过问题侧对抗评审（问题清单 13 条主体全部证实）。
拍板用语（待大哥亲口确认后生效）：
> 准：按四簇根治走；先验收「QG 三态出口」和「App check 进 ruleset」，再拆信封；第 0 步等你下令。

模型层不动：四段分层（廉价门 → 汇总 → 审查 → 合并守卫）、fail-closed、PR/main 不对称、主路径不靠人手点 Run。只治实现层的胶水与容量。

## 四簇动作

| 簇 | 病 | 根治动作 |
|----|----|----------|
| 病一 | 长轮询串联当胶水 | 干完的自己敲门：事件触发 + 单次判定。QG 改 CI/QA 双触发单次判（硬门 1）；③ 维持 `workflow_run(Quality Gate success)` 单触发（#420，别退回双触发） |
| 病二 | 等候 job 占自建池 | 纯 API 活（汇总、守卫、对账、哨兵）全上 `ubuntu-latest`；`pve-linux` 只留真干活（测试、substrate、Droid 分片）。与 #444 同向 |
| 病三 | 关联靠刮日志、信封搬运 | ③ 生产者用 App 身份以 Checks API 把 `droid-review` 直接贴到 PR head SHA（开工即贴 `in_progress`、先占名后填结论）；rollup 信封与 mirror 退休。关联 = SHA 本身 |
| 病四 | 跨 PR 无限流、同质 PR 堆积 | 生产端：scanner 铸卡在飞去重 + 全仓在飞 PR 上限。合并口：Auto Merge 真 FIFO（concurrency 先 1、队头失败/超时踢出）。③ 全仓共享并发上限先 2；超限入口排队（硬门 3） |

哨兵（活性兜底，可与病三并行）：每 15–30 分钟扫 open PR，①②已绿但 30 分钟内无 `droid-review` 实例 → 显式 `failure(not-started)` + 重派 + 通知。

## 三条硬门

1. **QG 三态出口**：未齐 → 非 success；已红 → failure；双绿 → success。同名多实例按 id 取最新；skip 出口不得被 ruleset/旁路当成通过（#358/#372）。
2. **App check 进 ruleset**：App 贴的 `droid-review` 必须进 `statusCheckRollup`，且 posting 身份与 ruleset `integration_id` 对齐。测 PR 不过 → 不拆信封。
3. **排队态可读**：入口 pending / 超并发等候必须可读；禁止 skip/缺席当门。pending 带最长等待，超时由哨兵转显式 failure。

## 施工顺序

0. 止血（等下令）：铸卡去重 + 存量同质 PR 处置（capture_run 同文件只留最早）。
1. 病三（先过硬门 2 测 PR）
2. 病一 + 病二（含硬门 1 契约）
3. 病四（含硬门 3）
4. 哨兵

## 不在范围

不迁仓、不依赖官方 merge queue；不动四段模型与 GOVERNANCE 第 1 条。
