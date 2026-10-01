"""droid-review 链路 Factory CLI 宿主候选择优契约测试（host-first → D1-c/e）。

演进（2026-09-30 H1 六件套③收尾，PR 冻结纪律「D1-c/e 选最新+下限 warning」）：
安装步从 first-on-PATH 锁死升级为**宿主候选择优**——扫描两条安装路径
（/usr/local/bin/droid + ~/.local/bin/droid），MAJOR.MINOR 语义比较选最新；
同版本并列取先（PATH 顺序，宿主 provisioning 为准）。双路径皆缺失才
fallback ``curl app.factory.ai/cli`` 下载并打 ::warning。

背景一（host-first 由来）：``curl app.factory.ai/cli`` 是 [REDACTED-HOST] 出口拓扑
下最后一条非镜像下载路径——PR #52 review-shard 被 runner shutdown 杀死在
下载中、PR #61 首轮 droid-review 同因红（rerun 自愈）。修复照抄 actionlint
host-first 模式（PR #53），[REDACTED-HOST] 五台 pve runner 预装 /usr/local/bin/droid
（Layer 1 离线化），正常全绿 run 零 curl 到 app.factory.ai。

背景二（D1-c 事故事实）：2026-09 型号服务端目录变更后，宿主
/usr/local/bin/droid=0.221（mtime 09-17）先于用户级 ~/.local/bin/droid=0.228
被 first-on-PATH 命中，服务端撤销 0.221 支持后三次路径 0 字节 stderr 静默
死亡（change-timeline-audit 链）。选最新候选即整案消失。

背景三（D1-e 下限语义）：下限 0.200 从「阻断+fallback」降级为「仅告警
不阻断」（漂移可观测，不制造下一个静默墙）；生效路径与版本必须打印
（CI 日志可审计宿主优先命中，与 run_shard.sh 分类时点打印呼应）。

契约覆盖两个载体（双载体模式）：
- 自仓 droid-review.yml（infra-core PR 的 droid-review 生产形态）
- reusable workflow droid-review-shards.yml（memory-core 等 thin
  caller 的分片流水线载体）
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent

# 宿主优先契约覆盖两个载体：自仓 droid-review.yml + reusable workflow
# droid-review-shards.yml（M4 起 memory-core 等 thin caller 的分片流水线走后者）
WORKFLOW_PATHS = [
    REPO_ROOT / ".github/workflows/droid-review.yml",
    REPO_ROOT / ".github/workflows/droid-review-shards.yml",
]

INSTALL_STEP_NAME = "Install Droid CLI"
# 版本下限（D1-e，warn-only 不阻断）：宿主实装 0.206.0（本机）/ 0.208.1
# （CI 现装，run 33249966263），下限取 0.200。
VERSION_FLOOR_MINOR = 200
# D1-c 候选清单（双安装路径；升级同步义务见 docs/governance/
# droid-cli-upgrade-runbook.md——升级时两份必须同版本）。
CANDIDATE_PATHS = ["/usr/local/bin/droid", "$HOME/.local/bin/droid"]


def _get_install_step(workflow_path: Path) -> str:
    """定位 review-shard job 的 Install Droid CLI step，返回其 run 块文本。"""
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    shard_job = data["jobs"]["review-shard"]
    for step in shard_job.get("steps", []):
        if step.get("name") == INSTALL_STEP_NAME:
            return str(step["run"])
    pytest.fail(f"{workflow_path.name}: review-shard job must have a {INSTALL_STEP_NAME!r} step")


@pytest.fixture(params=WORKFLOW_PATHS, ids=[p.name for p in WORKFLOW_PATHS])
def install_script(request: pytest.FixtureRequest) -> str:
    return _get_install_step(request.param)


def _run_install_step(
    script: str, env: dict[str, str], sandbox: Path, cand1: str | None = None
) -> subprocess.CompletedProcess:
    """把 workflow step 的 run 块当 bash 脚本在沙箱里实跑（行为级契约）。

    bash 走绝对路径（env.PATH 已被沙箱化，不能供查找用）；基础环境继承
    测试进程再以沙箱值覆盖 HOME/PATH。cand1：把硬编码的系统路径候选
    /usr/local/bin/droid 文本替换为沙箱路径——该路径在测试宿主可能真实
    存在，不替换则行为测试非封闭（真实 droid 会被选中）；候选字面量本身
    由 test_candidate_scan_present 静态锁定。
    """
    if cand1 is not None:
        script = script.replace("/usr/local/bin/droid", cand1)
    merged_env = {**os.environ, **env}
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=str(sandbox),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _make_fake_droid(path: Path, version: str) -> None:
    """造一个假 droid 二进制：--version 输出指定版本号。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'#!/bin/sh\necho "Factory Droid CLI {version}"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _sandbox_env(home: Path, tmp_path: Path) -> dict[str, str]:
    """行为测试沙箱环境：PATH 保留 /usr/bin:/bin（脚本内 grep/head 可用）。

    沙箱 bin 目录置于 PATH 最前（影子优先）：沙箱化的意义就是能压制宿主
    命令——置尾是潜在缺陷（放什么都无法遮蔽 /usr/bin 下的真身，fallback
    curl 泄漏即因此不可挡）。bin 内只放测试显式植入的影子命令（当前仅
    fallback 测试的离线 curl stub），grep/head 仍解析到 /usr/bin。

    GITHUB_PATH 重定向到临时文件（runner 语义：步内写入的目录在后续步骤
    前插进程 PATH）——脚本引用该变量（选择支配接线），缺失会在 set -u 下
    unbound，与真实 runner 环境不符。
    """
    return {
        "HOME": str(home),
        "PATH": f"{tmp_path}/bin:/usr/bin:/bin",
        "GITHUB_PATH": str(tmp_path / "github-path"),
    }


def _make_offline_curl_stub(bin_dir: Path) -> None:
    """植入必然失败的离线 curl stub（环境泄漏修，PR #175 CI 红）。

    事故机理：HOME/PATH 沙箱早已隔离候选路径，但 fallback 分支的真
    ``curl | sh`` 会被真实执行——自托管 runner 网络可达 app.factory.ai
    （实测 200/3.5s），真下载安装超出测试 60s subprocess 预算
    （subprocess.TimeoutExpired）；本地恰好 curl 快速失败所以绿，行为级
    契约退化为「碰巧」。stub 立即失败退出（exit 22）并留调用日志：
    既证明 fallback 真的尝试了下载，又让「curl 失败穿透 set -e」的
    断言在任何网络环境下确定性成立（离线可重复）。
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "curl"
    stub.write_text(
        '#!/bin/sh\necho "offline-stub $*" >> curl-stub.log\nexit 22\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)


class TestDroidCliHostFirst:
    """Factory CLI 安装宿主候选择优契约（D1-c 选最新 + D1-e warn-only）。"""

    def test_candidate_scan_present(self, install_script: str) -> None:
        """安装步必须扫描双安装路径候选（而非单点 first-on-PATH 探测）。"""
        assert "/usr/local/bin/droid" in install_script, "缺少系统路径候选"
        assert "$HOME/.local/bin/droid" in install_script, "缺少用户级路径候选"

    def test_picks_newest_candidate_major(self, tmp_path) -> None:
        """行为级：双候选时选 MAJOR 更高者（first-on-PATH 会锁死旧版）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        _make_fake_droid(home / ".local/bin/droid", "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(sys_droid)
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert str(sys_droid) not in proc.stdout
        assert f"{home}/.local/bin/droid (version 0.228.0)" in proc.stdout

    def test_picks_newest_candidate_minor(self, tmp_path) -> None:
        """行为级：同 MAJOR 时选 MINOR 更高者（MINOR 比较分支独立覆盖）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        _make_fake_droid(home / ".local/bin/droid", "0.226.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(sys_droid)
        )
        assert proc.returncode == 0
        assert ".local/bin/droid (version 0.226.0)" in proc.stdout

    def test_picks_newest_on_shards_carrier(self, tmp_path) -> None:
        """行为级：双载体之一 shards.yml 的安装步同样选最新（跨载体行为一致）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        _make_fake_droid(home / ".local/bin/droid", "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[1])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(sys_droid)
        )
        assert proc.returncode == 0
        assert ".local/bin/droid (version 0.228.0)" in proc.stdout

    def test_tie_prefers_first_candidate(self, tmp_path) -> None:
        """行为级：双候选同版本 → 取 PATH 顺序第一（宿主 provisioning 为准）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.208.1")
        _make_fake_droid(home / ".local/bin/droid", "0.208.1")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(sys_droid)
        )
        assert proc.returncode == 0
        assert f"{sys_droid} (version 0.208.1)" in proc.stdout

    def test_single_missing_candidate_still_selects(self, tmp_path) -> None:
        """行为级：只装一份时仍正确选择（缺的那份跳过，不报错不下载）。"""
        home = tmp_path / "home"
        _make_fake_droid(home / ".local/bin/droid", "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script,
            _sandbox_env(home, tmp_path),
            tmp_path,
            cand1=str(tmp_path / "nonexistent/droid"),
        )
        assert proc.returncode == 0
        assert f"{home}/.local/bin/droid (version 0.228.0)" in proc.stdout
        assert "::warning::droid not found on host" not in proc.stdout

    def test_unparsable_version_treated_as_zero(self, tmp_path) -> None:
        """行为级：--version 输出不可解析的候选按 0 处理（可解析者胜出）。"""
        home = tmp_path / "home"
        gibberish = tmp_path / "usr-local-bin/droid"
        gibberish.parent.mkdir(parents=True, exist_ok=True)
        gibberish.write_text("#!/bin/sh\necho 'totally broken'\n", encoding="utf-8")
        gibberish.chmod(0o755)
        _make_fake_droid(home / ".local/bin/droid", "0.206.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(gibberish)
        )
        assert proc.returncode == 0
        assert f"{home}/.local/bin/droid (version 0.206.0)" in proc.stdout

    def test_both_missing_falls_back_to_download(self, tmp_path) -> None:
        """行为级：双路径皆缺失 → fallback 下载路径 + ::warning（GitHub-hosted 兼容）。

        环境泄漏修（PR #175 CI 红）：沙箱 PATH 影子优先 + 离线 curl stub，
        fallback 分支的下载意图被 stub 捕获并立即失败——不再真实执行
        ``curl | sh``（自托管 runner 网络可达时真下载超 60s 预算）。
        """
        home = tmp_path / "home"
        script = _get_install_step(WORKFLOW_PATHS[0])
        _make_offline_curl_stub(tmp_path / "bin")
        proc = _run_install_step(
            script,
            _sandbox_env(home, tmp_path),
            tmp_path,
            cand1=str(tmp_path / "nonexistent/droid"),
        )
        assert "::warning::droid not found on host" in proc.stdout
        assert proc.returncode != 0, "下载失败必须红（curl 失败穿透 set -e）"
        # stub 调用日志证明 fallback 真的尝试了下载（而非 curl 碰巧失败），
        # 且下载目标是预期的 CLI 安装端点。
        stub_log = (tmp_path / "curl-stub.log").read_text(encoding="utf-8")
        assert "app.factory.ai/cli" in stub_log, "fallback 未尝试下载（stub 未被调用）"

    def test_download_only_after_host_probe(self, install_script: str) -> None:
        """curl 下载只允许存在于候选扫描之后（禁止恢复每 run 无条件首下载）。"""
        assert "app.factory.ai/cli" in install_script, (
            "fallback 下载路径必须保留（GitHub-hosted 兼容）"
        )
        probe_at = install_script.index("/usr/local/bin/droid")
        download_at = install_script.index("app.factory.ai/cli")
        assert probe_at < download_at, "下载路径出现在候选扫描之前（退化为无条件下载）"

    def test_fallback_missing_warns(self, install_script: str) -> None:
        """宿主双路径皆缺失时 fallback 下载必须打 ::warning（异常态要显式暴露）。"""
        assert "::warning::droid not found on host" in install_script, (
            "宿主缺失 fallback 分支缺少 ::warning"
        )

    def test_curl_retry_resilience_preserved(self, install_script: str) -> None:
        """fallback curl 保留 --retry 韧性（出口抖动只应单次红，rerun 可自愈）。"""
        assert "--retry" in install_script, "fallback curl 缺少 --retry"
        assert "--retry-all-errors" in install_script, "fallback curl 缺少 --retry-all-errors"

    def test_fallback_wires_local_bin_path(self, install_script: str) -> None:
        """fallback 分支保留 ~/.local/bin → GITHUB_PATH 接线（后续步骤解析 droid）。"""
        assert '"$HOME/.local/bin" >> "$GITHUB_PATH"' in install_script, (
            "fallback 分支缺少 GITHUB_PATH 接线（后续步骤将解析不到 droid）"
        )


class TestDroidCliVersionFloorWarnOnly:
    """D1-e：下限 0.200 降级 warn-only + 生效版本可审计。"""

    def test_floor_warning_is_warn_only(self, install_script: str) -> None:
        """低于下限只打 ::warning，不 exit 不 fallback 下载（不制造静默墙）。"""
        assert "::warning::host droid" in install_script, "缺少下限告警"
        warn_at = install_script.index("::warning::host droid")
        assert "exit" not in install_script[warn_at : warn_at + 400], (
            "下限告警不得携带 exit（warn-only 契约）"
        )

    def test_floor_literal_200_present(self, install_script: str) -> None:
        """下限字面量 200 在双 workflow 内联（T1-11 探针按字面量锚定）。"""
        assert "-ge 200" in install_script, "缺少宿主版本 ≥0.200 门限判断"

    def test_effective_path_and_version_logged(self, install_script: str) -> None:
        """生效路径与版本必须打印（CI 日志可审计；与 run_shard.sh 打印呼应）。"""
        assert "Effective droid CLI" in install_script, "缺少生效路径+版本打印"

    def test_effective_version_logged_after_fallback(self, install_script: str) -> None:
        """fallback 路径的生效版本同样经过统一打印段（BEST_PATH 复用）。"""
        fallback_at = install_script.index("app.factory.ai/cli")
        log_at = install_script.index("Effective droid CLI")
        assert fallback_at < log_at, "生效版本打印必须覆盖 fallback 分支之后"

    def test_floor_stale_version_warns_behaviorally(self, tmp_path) -> None:
        """行为级：唯一候选 0.19x → 打下限告警但 exit 0（warn-only 不阻断）。"""
        home = tmp_path / "home"
        _make_fake_droid(home / ".local/bin/droid", "0.191.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script,
            _sandbox_env(home, tmp_path),
            tmp_path,
            cand1=str(tmp_path / "nonexistent/droid"),
        )
        assert proc.returncode == 0, f"warn-only 契约被破坏：stderr={proc.stderr}"
        assert "::warning::host droid 0.191.0" in proc.stdout
        assert "< 0.200" in proc.stdout

    def test_floor_satisfied_version_no_warning(self, tmp_path) -> None:
        """行为级：候选 ≥0.200 → 无下限告警，正常打印生效版本。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.208.1")
        script = _get_install_step(WORKFLOW_PATHS[0])
        proc = _run_install_step(
            script, _sandbox_env(home, tmp_path), tmp_path, cand1=str(sys_droid)
        )
        assert proc.returncode == 0
        assert "::warning::host droid" not in proc.stdout
        assert "version 0.208.1" in proc.stdout


class TestSelectionDominantExecution:
    """D1-c 收口（scrutiny r1 blocking）：选择支配执行——裸 droid 解析命中被选二进制。

    背景：BEST_PATH 候选择优只决定 Install 步内的打印与下限告警，同 job
    后续步骤（run_shard.sh 的 ``command -v droid`` / ``droid exec``）按进程
    PATH 解析。选中的候选目录若不写入 GITHUB_PATH，双路径漂移时 CI 仍执行
    PATH 顺序旧份——0.221 锁死、0.228 被绕过的静默死亡场景从选择面复活到
    执行面。契约：当前 PATH 的裸 droid 未解析到被选二进制时，选中目录写入
    GITHUB_PATH（已解析到被选二进制则零写入，幂等），使后续裸 droid 解析
    命中被选二进制。

    防降级守卫（scrutiny r2）：候选清单只有两条硬编码路径，PATH 解析到第三
    路径的**更新** droid 时前插会降级执行面——守卫拦截写入并告警。
    """

    @staticmethod
    def _env_with_github_path(home: Path, tmp_path: Path) -> tuple[dict[str, str], Path]:
        """沙箱 env + GITHUB_PATH 临时文件路径（供断言写入内容）。"""
        env = _sandbox_env(home, tmp_path)
        gh_path_file = Path(env["GITHUB_PATH"])
        return env, gh_path_file

    @staticmethod
    def _gh_path_lines(gh_path_file: Path) -> list[str]:
        """GITHUB_PATH 文件的非空行列表（runner 未创建文件时视为无条目）。

        runner 语义的单一编码点：写入面断言与 PATH 重组都消费本 helper。
        """
        if not gh_path_file.exists():
            return []
        return [
            line for line in gh_path_file.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    @staticmethod
    def _resolve_droid(gh_path_file: Path, base_path: str, cwd: Path) -> str:
        """按 GitHub runner 语义重组 PATH，返回裸 ``command -v droid`` 的解析结果。

        GITHUB_PATH 各行前插到原 PATH 之前；空组件会被 join 产生（文件缺失
        /空行场景），必须过滤——空 PATH 组件在 POSIX 语义下解析为当前工作
        目录，cwd 里若有杂散 droid 可执行文件会污染解析结果。
        """
        entries = [
            line for line in TestSelectionDominantExecution._gh_path_lines(gh_path_file) if line
        ]
        joined = ":".join(entries + [base_path]) if entries else base_path
        resolved = subprocess.run(
            ["/bin/bash", "-c", "command -v droid"],
            cwd=str(cwd),
            env={**os.environ, "PATH": joined},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert resolved.returncode == 0, f"stderr={resolved.stderr}"
        return resolved.stdout.strip()

    def test_selection_wiring_present(self, install_script: str) -> None:
        """静态：选择支配接线在位且顺序正确（扫描→fallback 置位→解析对比→接线→打印）。"""
        assert "FALLBACK_DOWNLOADED=1" in install_script, (
            "缺少 fallback 去重置位（fallback 落点已显式写入 GITHUB_PATH）"
        )
        assert 'RESOLVED_DROID="$(command -v droid 2>/dev/null || true)"' in install_script, (
            "缺少裸 droid 解析捕获（选择支配判据 + 防降级守卫共用）"
        )
        assert '"$RESOLVED_DROID" != "$BEST_PATH"' in install_script, (
            "缺少裸 droid 解析 vs 被选二进制的比较（选择支配判据）"
        )
        assert (
            '>> "$GITHUB_PATH"' in install_script[install_script.index("FALLBACK_DOWNLOADED=1") :]
        ), "选择支配分支缺少 GITHUB_PATH 接线（后续步骤将解析到 PATH 顺序旧份）"
        probe_at = install_script.index("/usr/local/bin/droid")
        fallback_at = install_script.index("FALLBACK_DOWNLOADED=1")
        dom_at = install_script.index('RESOLVED_DROID="$(command -v droid 2>/dev/null || true)"')
        log_at = install_script.index("Effective droid CLI")
        assert probe_at < fallback_at < dom_at < log_at, (
            "选择支配接线顺序错误（须在候选扫描/fallback 后、生效打印前）"
        )

    def test_selection_writes_github_path_when_drifted(self, tmp_path) -> None:
        """行为级：双路径漂移（PATH 侧旧、用户级新）→ 选中目录写入 GITHUB_PATH。

        保真度（scrutiny r2）：旧份目录必须真实置于 PATH——否则裸 droid 解析
        为空，走的是「不在 PATH」分支而非漂移分支，断言退化。本测试锁定
        漂移分支：``command -v droid`` 非空且 ≠ BEST_PATH 时仍前插。
        """
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        # 旧份目录置于 PATH 最前：漂移场景的忠实模拟（解析到 0.221 旧份）
        env["PATH"] = f"{sys_droid.parent}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert f"{user_droid} (version 0.228.0)" in proc.stdout
        assert self._gh_path_lines(gh_path_file) == [str(home / ".local/bin")]

    def test_selection_dominates_on_shards_carrier(self, tmp_path) -> None:
        """行为级：shards 载体同样选择支配（跨载体行为一致，漂移场景）。

        保真度（scrutiny r2）：同 drift 测试——旧份目录真实入 PATH。
        """
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[1])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{sys_droid.parent}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert self._gh_path_lines(gh_path_file) == [str(home / ".local/bin")]

    def test_tie_writes_no_github_path(self, tmp_path) -> None:
        """行为级：同版本并列取先且该份已居 PATH 首位 → 零 GITHUB_PATH 写入（幂等）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.208.1")
        _make_fake_droid(home / ".local/bin/droid", "0.208.1")
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{tmp_path}/usr-local-bin:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert self._gh_path_lines(gh_path_file) == []

    def test_only_candidate_on_path_head_no_write(self, tmp_path) -> None:
        """行为级：唯一候选已居 PATH 首位 → 不写（PATH 首位已命中被选路径）。"""
        home = tmp_path / "home"
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{home}/.local/bin:{env['PATH']}"
        proc = _run_install_step(
            script,
            env,
            tmp_path,
            cand1=str(tmp_path / "nonexistent/droid"),
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert f"{user_droid} (version 0.228.0)" in proc.stdout
        assert self._gh_path_lines(gh_path_file) == []

    def test_bare_droid_resolves_to_selected_binary(self, tmp_path) -> None:
        """行为级（契约核心）：runner 语义重组 PATH 后，裸 `command -v droid` 命中被选二进制。

        保真度（scrutiny r2）：旧份目录真实置于 PATH——解析面证明的是「前插
        的选中目录**压过 PATH 更早的旧份**」，而非仅「选中目录被追加」。
        断言的是执行面（解析结果），而非仅 GITHUB_PATH 写入顺序。
        """
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{sys_droid.parent}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        base_path = env["PATH"]
        assert self._resolve_droid(gh_path_file, base_path, tmp_path) == str(user_droid), (
            "裸 droid 解析未命中被选二进制（选择支配执行断裂）"
        )

    def test_no_downgrade_when_path_resolves_newer(self, tmp_path) -> None:
        """行为级（scrutiny r2 P2）：PATH 第三路径解析到更新 droid → 不前插（防降级守卫）。

        候选清单外的新版（如 /opt 预装 0.240）已可解析时，无条件前插被选
        旧版（0.228）会把执行面降级——守卫必须跳过写入并告警。
        """
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        opt_droid = tmp_path / "opt-bin/droid"
        _make_fake_droid(opt_droid, "0.240.0")
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        # 第三路径新版置于 PATH 最前：裸 droid 解析到 0.240（候选清单外）
        env["PATH"] = f"{opt_droid.parent}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert self._gh_path_lines(gh_path_file) == [], "守卫失效：解析份更新时仍前插（降级执行面）"
        assert "newer than selected" in proc.stdout, "守卫告警缺失（漂移不可见）"

    def test_no_downgrade_guard_on_shards_carrier(self, tmp_path) -> None:
        """行为级：shards 载体同样具备防降级守卫（跨载体行为一致）。"""
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        opt_droid = tmp_path / "opt-bin/droid"
        _make_fake_droid(opt_droid, "0.240.0")
        script = _get_install_step(WORKFLOW_PATHS[1])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{opt_droid.parent}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert self._gh_path_lines(gh_path_file) == []
        assert "newer than selected" in proc.stdout

    def test_unparsable_resolved_version_still_prepends(self, tmp_path) -> None:
        """行为级：解析份版本不可解析（按 0）→ 被选二进制仍前插（守卫不误拦）。

        坏份（--version 输出 gibberish）语义版本为 0，不比任何被选份新——
        前插照常，坏份被遮蔽（与候选择优的同款容错语义一致）。
        """
        home = tmp_path / "home"
        sys_droid = tmp_path / "usr-local-bin/droid"
        _make_fake_droid(sys_droid, "0.221.0")
        user_droid = home / ".local/bin/droid"
        _make_fake_droid(user_droid, "0.228.0")
        broken_dir = tmp_path / "broken-bin"
        broken = broken_dir / "droid"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("#!/bin/sh\necho 'totally broken'\n", encoding="utf-8")
        broken.chmod(0o755)
        script = _get_install_step(WORKFLOW_PATHS[0])
        env, gh_path_file = self._env_with_github_path(home, tmp_path)
        env["PATH"] = f"{broken_dir}:{env['PATH']}"
        proc = _run_install_step(script, env, tmp_path, cand1=str(sys_droid))
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert self._gh_path_lines(gh_path_file) == [str(home / ".local/bin")]
