# droid CLI 双路径升级 Runbook

维护者手册：pve runner 宿主与本地工作机上的 Factory CLI（`droid`）升级操作
规范。先例事故：2026-09 型号服务端目录变更后，宿主 `/usr/local/bin/droid`
（0.221.0，mtime 落后）被 host-first 探测按 PATH 顺序锁用，三次路径 0 字节
stderr 静默死亡（见 `memory/log/2026-09-28.md` 换代链路与 runner 韧性审计）。

## 两条安装路径

宿主与本地机上可能同时存在两份 `droid` 二进制：

- `/usr/local/bin/droid` — 宿主 provisioning 手动安装
  （Layer 1 离线化，CI host-first 探测优先命中）。
- `~/.local/bin/droid` — CLI 安装器默认落点，或 CI fallback 下载所得。

CI host-first 契约（`.github/workflows/droid-review.yml` 与
`droid-review-shards.yml` 的 Install Droid CLI 步）：`command -v droid` 按
PATH 顺序取第一份，版本 ≥0.200 直接使用。PATH 中 `/usr/local/bin` 先于
`~/.local/bin`，因此宿主那份先命中——**两份不同步时，CI 用的永远是较旧的
那份**。

## 升级操作（owner 手动）

1. 查看两份现役版本与落点：

   ```bash
   ls -l /usr/local/bin/droid ~/.local/bin/droid 2>/dev/null
   /usr/local/bin/droid --version 2>/dev/null
   ~/.local/bin/droid --version 2>/dev/null
   ```

2. 同步升级两份到同一版本（先用户级，再拷贝到系统路径；不依赖安装器的
   自定义目录参数——其语义未经核实，拷贝形态确定）：

   ```bash
   curl -fsSL https://app.factory.ai/cli | sh
   sudo install -m 0755 ~/.local/bin/droid /usr/local/bin/droid
   ```

3. 复核一致：

   ```bash
   /usr/local/bin/droid --version
   ~/.local/bin/droid --version
   ```

   两输出必须同版本号。不一致 → 回到第 2 步，禁止带病合流。

4. 同步登记：版本面单一事实源是 CI 内联下限（0.200）加
   `tests/test_droid_cli_host_first.py` 的 `VERSION_FLOOR_MINOR`，**不写进**
   `runner-tools.toml`（T1-11 探针钉死「toml 无 droid 键」）。升级无需改仓内
   文件；仅当下限本身需要抬升时，按 D1-b 清单（双 workflow 内联 + 测试钉 +
   探针字面量）同源改写。

## 禁止用户级 auto-update

`~/.local/bin/droid` 一旦跟随 CLI 自动更新，两份路径版本必然漂移，且 CI 命中的
宿主副本（`/usr/local/bin`）永远落后——host-first 要消灭的非确定性会原样回归
（PR #52/#61 下载中断的教训；D1-d 裁决：跟随自更新=重新引入网络与非确定源）。

当前环境已设 `DISABLE_AUTO_UPDATE=1`。维护约定：

- 保持该环境变量在 runner 宿主与本地 shell 配置中常驻（shell profile /
  launchd 环境按宿主形态落位），安装器与 CLI 均读它。
- 升级一律走本 runbook 的手动路径，时间点自选、两份同拍。
- 新增 runner 宿主 provisioning 时，把 `DISABLE_AUTO_UPDATE=1` 纳入初始化
  清单。

## 相关契约

- CI 安装步宿主优先 + 版本门限：`tests/test_droid_cli_host_first.py`
- 契约位置钉（toml 非成员）：`scripts/livetruth_probes.py` T1-11
- 生效路径与版本打印（run_shard.sh 执行时）：H1 分类采集套件
  （`src/infra_core/engine/droid_review/run_shard.sh` 版本段 +
  `tests/test_run_shard_error_classification.py`）
