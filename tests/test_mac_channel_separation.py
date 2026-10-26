"""Mac 通道分离（evolution/mac-channel-separation）：trigger-droid.sh 演化卡自动执行分支删除。

owner 第零条裁定（2026-10-04）：Mac 退出自进化。演化卡不再属于 Mac 自动处理域：
  识别为演化卡 → REJECTED 日志 + PostHog mac_evolution_rejected → 退出（零 droid exec 启动）。
  人工/对话触发入口保留（RESUME_SESSION_ID 人工恢复分支位于演化拒绝之前）。

识别 = title "[evolution]" 前缀（创建时稳定信号，解 GitHub→Linear 先建后贴 label 竞态）
       或归一化 labels（trim + 大小写折叠）。
识别只是防御纵深第二道；第一道在 Worker 侧（路径 D 零转发）。

测试分四层：
  T1 归一化解析器行为断言（lib/evolution-channel.sh 直接 source 调用）
  T2 变异矩阵（共享测试向量 tests/evolution_channel_vectors.json，hash 校验双仓同步）
  T3 trigger-droid.sh 可执行语义行为断言（沙箱 stub 全链路，演化卡零 droid exec）
  T4 结构性不变量（RESUME 分支次序、grep 核验、MANIFEST 注册、PostHog 事件名）

architecture.md §4 / validation-contract.md VAL-GATE-003。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.trigger_sandbox_helpers import (
    bash_syntax_ok,
    combined_log,
    darwin_skip_reason,
    non_comment_lines,
    wait_for,
    write_stub,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_DIR = REPO_ROOT / "webhook-scripts"
SCRIPT_PATH = WEBHOOK_DIR / "trigger-droid.sh"
CHANNEL_LIB_PATH = WEBHOOK_DIR / "lib" / "evolution-channel.sh"
VECTORS_PATH = Path(__file__).resolve().parent / "evolution_channel_vectors.json"

SCRIPT_RELATIVE = "webhook-scripts/trigger-droid.sh"
CHANNEL_LIB_RELATIVE = "webhook-scripts/lib/evolution-channel.sh"

PY = "/opt/homebrew/bin/python3"


# ============================================================================
# T0: 共享测试向量文件（双仓同步的 hash 校验）
# ============================================================================


class TestSharedVectors:
    """Mac 侧与 Worker 侧共享同一份测试向量（channels/test-vectors/evolution-channel-vectors.json）。"""

    WORKER_REPO = Path.home() / "cf" / "xun201811" / "webhook"
    WORKER_VECTOR_RELATIVE = Path("channels/test-vectors/evolution-channel-vectors.json")

    @pytest.fixture(scope="class")
    def vectors(self) -> dict:
        return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))

    def test_vectors_file_exists_and_valid(self, vectors: dict):
        assert vectors["vectors"], "向量文件不得为空"
        for vec in vectors["vectors"]:
            assert {"name", "title", "labels", "expected_evolution", "expected_reason"} <= set(
                vec
            ), f"向量 {vec.get('name')} 缺少必需字段"
            if vec["expected_evolution"]:
                assert vec["expected_reason"] in {"title_prefix", "normalized_labels"}
            else:
                assert vec["expected_reason"] is None

    def test_worker_side_vector_copy_hash_match(self, vectors: dict):
        """Worker 仓副本必须与本仓向量 sha256 一致（hash 校验双仓同步）。"""
        worker_copy = self.WORKER_REPO / self.WORKER_VECTOR_RELATIVE
        if not worker_copy.exists():
            pytest.skip(
                "Worker 仓向量副本尚未落盘（worker-gate-core 同 PR 部署）；"
                "本仓真源已就绪，hash 校验待双仓 PR 合并后生效"
            )
        repo_sha = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
        worker_sha = hashlib.sha256(worker_copy.read_bytes()).hexdigest()
        assert repo_sha == worker_sha, (
            "Mac 侧与 Worker 侧演化识别测试向量 sha256 不一致（双仓同步被破坏）"
        )


# ============================================================================
# T1: 归一化识别解析器（lib/evolution-channel.sh 行为断言）
# ============================================================================


def _run_channel_lib(
    script: str, env_overrides: dict | None = None, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """source lib/evolution-channel.sh 后执行调用片段，返回进程结果。

    POSTHOG_DRY_RUN=1 隔离外发；POSTHOG_API_KEY 清空（测试机无 key，双保险）。
    extra_env 用于安全传递含转义序列的变量（绕开 bash 双引号不解释 \t 等的坑）。
    """
    env = os.environ.copy()
    env["POSTHOG_DRY_RUN"] = "1"
    env.pop("POSTHOG_API_KEY", None)
    if env_overrides:
        env.update(env_overrides)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", f'source "{CHANNEL_LIB_PATH}"\n{script}'],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


class TestNormalizeLabel:
    """evolution_normalize_label：trim + 大小写折叠 + 修剪换行/制表符。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("evolution-found", "evolution-found"),
            ("  evolution-found  ", "evolution-found"),
            ("EVOLUTION-FOUND", "evolution-found"),
            ("Evolution-Found", "evolution-found"),
            (" evolution-found\n", "evolution-found"),
            ("\tevolution-found\t", "evolution-found"),
        ],
    )
    def test_normalization(self, raw, expected):
        result = _run_channel_lib(f'evolution_normalize_label "{raw}"')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    def test_empty_input_yields_empty(self):
        result = _run_channel_lib('evolution_normalize_label ""')
        assert result.returncode == 0
        assert result.stdout.strip() == ""


def _classify(title: str, labels_json: str) -> tuple[str, str]:
    """调用 evolution_is_evolution_card，返回 (decision, reason)。decision ∈ evolution|normal|empty。

    title/labels 经环境变量传递（绕开 bash 引号层，保证 JSON 转义序列原样到达）。
    """
    script = (
        'evolution_is_evolution_card "$T" "$L"\n'
        'echo "rc=$? decision=$EVOLUTION_DECISION reason=$EVOLUTION_REASON"'
    )
    result = _run_channel_lib(
        script,
        extra_env={"T": title, "L": labels_json},
    )
    assert result.returncode == 0, result.stderr
    m = re.search(r"rc=(\d+) decision=(\S*) reason=(\S*)", result.stdout)
    assert m, f"无法解析输出: {result.stdout!r} stderr={result.stderr!r}"
    return m.group(2), m.group(3)


class TestClassifyBehavior:
    """evolution_is_evolution_card：标签归一化命中 / title 前缀命中 / 变体排除。"""

    @pytest.mark.parametrize(
        "title,labels,expect_evolution,expect_reason",
        [
            ("Fix gateway timeout", ["bug"], False, None),
            ("Fix gateway timeout", ["Evolution-Found"], True, "normalized_labels"),
            ("[evolution] CODE_HYGIENE_001", [], True, "title_prefix"),
            ("[EVOLUTION] CODE_HYGIENE_001", [], True, "title_prefix"),
            ("  [evolution] X", [], True, "title_prefix"),
            ("Feature: [evolution] demo", [], False, None),
            ("Fix gateway timeout", None, False, None),
        ],
    )
    def test_classification(self, title, labels, expect_evolution, expect_reason):
        labels_json = json.dumps(labels)
        decision, reason = _classify(title, labels_json)
        if expect_evolution:
            assert decision == "evolution", (
                f"{title!r} {labels} 应识别为演化卡（decision={decision}）"
            )
            assert reason == expect_reason
        else:
            assert decision == "normal", (
                f"{title!r} {labels} 不应识别为演化卡（decision={decision}）"
            )

    def test_labels_not_json_array_falls_back_to_title_scan(self):
        """labels 非数组（如字符串/null）：不 crash，退化为 title 前缀扫描。"""
        decision, _ = _classify("[evolution] X", '"evolution-found"')
        assert decision == "evolution"

    def test_null_labels_no_crash(self):
        decision, _ = _classify("plain card", "null")
        assert decision == "normal"

    def test_empty_title_and_labels_normal(self):
        decision, _ = _classify("", "[]")
        assert decision == "normal"

    def test_empty_title_with_evolution_label(self):
        """payload 未透传 title（Linear 事件形态漂移）时标签仍兜底识别。"""
        decision, reason = _classify("", '["evolution-found"]')
        assert decision == "evolution"
        assert reason == "normalized_labels"


# ============================================================================
# T2: 变异矩阵（共享测试向量全量回放）
# ============================================================================


class TestMutationMatrix:
    """共享测试向量全量回放：防 Opus 清单里的标签/标题形态漂移。"""

    @pytest.fixture(scope="class")
    def vectors(self) -> list[dict]:
        return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["vectors"]

    def test_mutation_matrix_replay(self, vectors):
        mismatched = []
        for vec in vectors:
            labels_json = json.dumps(vec["labels"])
            decision, reason = _classify(vec["title"], labels_json)
            expected_decision = "evolution" if vec["expected_evolution"] else "normal"
            if decision != expected_decision or reason != (vec["expected_reason"] or ""):
                mismatched.append(
                    f"{vec['name']}: title={vec['title']!r} labels={vec['labels']} "
                    f"期望={expected_decision}/{vec['expected_reason']} 实际={decision}/{reason or '-'}"
                )
        assert not mismatched, "变异矩阵回放失败:\n" + "\n".join(mismatched)


# ============================================================================
# T3: trigger-droid.sh 行为断言（沙箱 stub 全链路）
# ============================================================================


def _write_stub(path: Path, content: str) -> None:
    write_stub(path, content)


def _darwin_skip_reason() -> str | None:
    """行为沙箱跑真源 trigger-droid.sh 全链路：macOS 专属路径已在 MANIFEST
    ENV_DIFF_LINES 声明（/opt/homebrew/bin/{python3,flock}）。Linux CI 上跳过
    行为层（T1 归一化/T2 变异矩阵/T4 结构断言仍全平台执行）。"""
    return darwin_skip_reason()


def _requires_darwin(func):
    return pytest.mark.skipif(
        _darwin_skip_reason() is not None, reason=_darwin_skip_reason() or ""
    )(func)


def _make_sandbox(tmp_path: Path, evolution_title: str, issue_ref: str = "INFRA-9901") -> dict:
    """构造可全链路执行 trigger-droid.sh 的沙箱。

    - WEBHOOK_BASE: 独立 logs/locks/status 目录
    - REPO_CONFIG: 指向真实格式 repositories.yml，INFRA 团队 → 沙箱 repo 目录
    - repo: 真实 git 仓（memory/system 缺失 → 跳过 re-sign）
    - stub droid: 记录调用即失败退出（若被启动，exit 127 并落痕 audit 文件）
    - lib/linear-queue.sh / lib/p0a-guard.sh: stub（绕开 Linear GraphQL 依赖）
    返回 {sandbox, repo, log, marker} 路径字典。
    """
    sandbox = tmp_path / "webhook"
    repo = tmp_path / "target-repo"
    logs = sandbox / "logs"
    logs.mkdir(parents=True)
    (sandbox / "locks").mkdir(parents=True)
    (sandbox / "status").mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/hdot123/infraro-core.git",
        ],
        check=True,
        capture_output=True,
    )

    marker = tmp_path / "droid-launched.marker"
    (tmp_path / "bin").mkdir()
    _write_stub(
        tmp_path / "bin" / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho "STUB-DROID-LAUNCHED" >&2\nexit 127\n',
    )
    # stub gh：记录调用、恒成功（隔离真实 gh —— 写回失败兜底绝不触达生产仓）
    _write_stub(
        tmp_path / "bin" / "gh",
        '#!/bin/bash\necho "gh-stub: $*" >> "GH_CALLS_LOG"\nexit 0\n'.replace(
            "GH_CALLS_LOG", str(tmp_path / "gh-calls.log")
        ),
    )

    # 仓库路由配置（route_repo 解析 yaml：INFRA → 沙箱 repo，projectNames 优先匹配）
    config = tmp_path / "repositories.yml"
    config.write_text(
        textwrap.dedent(f"""
        teams:
          infra:
            teamKey: INFRA
            repos:
              - repoPath: "{repo}"
                default: true
                paths:
                  - when:
                      projectNames: ["Evolution"]
        """).strip()
        + "\n",
        encoding="utf-8",
    )

    # lib stub：linear-queue 恒失败（无网络）；p0a-guard 放行
    sandbox_lib = sandbox / "scripts" / "lib"
    sandbox_lib.mkdir(parents=True)
    _write_stub(
        sandbox_lib / "linear-queue.sh",
        "linear_queue_init() { return 1; }\n"
        "linear_move_to_in_progress() { return 1; }\n"
        "linear_query_next_queued() { return 1; }\n"
        "linear_get_issue_team_key() { return 1; }\n",
    )
    _write_stub(sandbox_lib / "p0a-guard.sh", "# test stub: guard disabled\n")

    # 生产目录形态：真源脚本 + 真源 channel lib + posthog/op-mcp（被测对象及其依赖）
    prod_scripts = sandbox / "scripts"
    prod_scripts.mkdir(exist_ok=True)
    shutil.copy2(SCRIPT_PATH, prod_scripts / "trigger-droid.sh")
    prod_lib = prod_scripts / "lib"
    prod_lib.mkdir(exist_ok=True)
    shutil.copy2(CHANNEL_LIB_PATH, prod_lib / "evolution-channel.sh")
    shutil.copy2(WEBHOOK_DIR / "lib" / "posthog.sh", prod_lib / "posthog.sh")
    shutil.copy2(WEBHOOK_DIR / "lib" / "op-mcp.sh", prod_lib / "op-mcp.sh")
    # stub 掉 op-mcp 的真实取值（无网络/无 1Password 依赖；LINEAR_API_KEY 为空即短路）。
    # OP_VAULT_SEVER 必须保留：trigger-droid.sh 主流程在 set -u 下引用它。
    _write_stub(
        prod_lib / "op-mcp.sh",
        'OP_VAULT_SEVER="test-vault-id"\nop_get_field() { return 1; }\n',
    )

    return {
        "sandbox": sandbox,
        "repo": repo,
        "logs": logs,
        "marker": marker,
        "issue_ref": issue_ref,
        "evolution_title": evolution_title,
    }


def _run_trigger(
    sandbox_info: dict,
    tmp_path: Path,
    *,
    labels: str | None = "auto",
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """labels="auto"（默认）注入演化标签数组；None = 不设 LINEAR_LABELS（hooks.json 当前形态）。"""
    if labels == "auto":
        labels_value: str | None = json.dumps(["evolution-found"])
    else:
        labels_value = labels  # None → 不设 LINEAR_LABELS（hooks.json 当前形态）
    env = os.environ.copy()
    env.update(
        {
            "WEBHOOK_BASE": str(sandbox_info["sandbox"]),
            "REPO_CONFIG": str(tmp_path / "repositories.yml"),
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "POSTHOG_DRY_RUN": "1",
            "LINEAR_API_KEY": "",
            "LINEAR_LABELS": labels_value,
        }
    )
    env.pop("RESUME_SESSION_ID", None)
    if labels_value is not None:
        env["LINEAR_LABELS"] = labels_value
    else:
        env.pop("LINEAR_LABELS", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            str(sandbox_info["sandbox"] / "scripts" / "trigger-droid.sh"),
            "create",
            "Issue",
            sandbox_info["issue_ref"],
            "test-uuid-0001",
            "INFRA",
            sandbox_info["evolution_title"],
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(tmp_path),
    )


def _combined_log(sandbox_info: dict, tmp_path: Path | None = None) -> str:
    """汇总沙箱日志：trigger-*.log（tee 双写主日志）+ 后台子 shell 追加流 + posthog dry-run。"""
    # posthog dry-run 走 stdout → 后台子 shell 的 stdout 追加进主 log 文件（已覆盖）
    # 但主流程（守卫在后台化之前）的 dry-run 回显进了 subprocess stdout；
    # 由调用方按需并入 result.stdout。此处再并入 gh stub 日志（有写回即暴露）。
    extra = []
    if tmp_path is not None:
        extra = [tmp_path / "gh-calls.log"]
    return combined_log(sandbox_info["logs"], extra_paths=extra)


def _wait_for(condition, timeout: float = 60.0, interval: float = 1.0) -> bool:
    """轮询等待条件成立（trigger-droid.sh 立即返回，droid 在后台子 shell 执行）。"""
    return wait_for(condition, timeout=timeout, interval=interval)


@_requires_darwin
class TestTriggerDroidEvolutionRejection:
    """演化卡到达 trigger-droid.sh：REJECTED 日志 + PostHog mac_evolution_rejected + 零 droid exec。"""

    @pytest.fixture
    def evolution_run(self, tmp_path):
        info = _make_sandbox(tmp_path, "[evolution] CODE_HYGIENE_001")
        result = _run_trigger(info, tmp_path)
        return info, result, tmp_path

    def test_exits_without_launching_droid(self, evolution_run):
        info, result, tmp_path = evolution_run
        assert result.returncode == 0, (
            f"演化拒绝必须幂等 exit 0（webhook 端 2xx）: rc={result.returncode}\n{result.stderr[-2000:]}"
        )
        assert not info["marker"].exists(), "演化卡触发了 droid exec 启动（自动执行分支未删除）"

    def test_rejected_log_line_present(self, evolution_run):
        info, result, tmp_path = evolution_run
        log_text = _combined_log(info, tmp_path)
        assert "REJECTED: evolution card reached Mac" in log_text, (
            f"缺少 REJECTED 日志行。stdout={result.stdout[-1500:]}"
        )
        assert "dropped, not queued" in log_text, "REJECTED 行必须含 'dropped, not queued' 措辞"
        assert "排队等待 R1 通道" in log_text, "必须记录「排队等待 R1 通道」日志行"

    def test_posthog_event_mac_evolution_rejected(self, evolution_run):
        info, result, tmp_path = evolution_run
        log_text = _combined_log(info, tmp_path) + result.stdout
        assert "mac_evolution_rejected" in log_text, "缺少 PostHog mac_evolution_rejected 事件"

    def test_no_lock_file_created(self, evolution_run):
        """拒绝必须在锁与状态副作用之前：零锁文件、零状态文件（账本对齐前置）。"""
        info, _, _ = evolution_run
        locks = list((info["sandbox"] / "locks").glob("l2d-*"))
        statuses = list((info["sandbox"] / "status").glob("*.json"))
        assert locks == [], f"演化拒绝不应创建锁文件: {locks}"
        assert statuses == [], f"演化拒绝不应创建状态文件: {statuses}"

    def test_backfilled_title_still_guarded(self, tmp_path):
        """title 经反向解析/回填到达（非 hooks 参数直传）：守卫同样识别。

        hooks.json 对 Comment 类事件不传 title — UUID 反向解析回填 ISSUE_TITLE 后，
        守卫必须按回填后的 title 识别（回归：守卫只看原始参数的漏洞）。
        注入点=主流程起点，模拟 resolve_issue_ref 成功后的变量状态。
        """
        info = _make_sandbox(tmp_path, "[evolution] UUID_RESOLVE_001", issue_ref="INFRA-9906")

        prod_script = info["sandbox"] / "scripts" / "trigger-droid.sh"
        text = prod_script.read_text(encoding="utf-8")
        injection = (
            "# TEST-INJECTION: 模拟 UUID 反向解析成功回填（真实解析块在 resolve_issue_ref 定义处）\n"
            '[ -n "${TEST_RESOLVED_TITLE:-}" ] && ISSUE_TITLE="${TEST_RESOLVED_TITLE}"\n'
        )
        text = text.replace(
            'log "=== trigger-droid.sh started ==="',
            'log "=== trigger-droid.sh started ==="\n' + injection,
            1,
        )
        prod_script.write_text(text, encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "WEBHOOK_BASE": str(info["sandbox"]),
                "REPO_CONFIG": str(tmp_path / "repositories.yml"),
                "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
                "POSTHOG_DRY_RUN": "1",
                "LINEAR_API_KEY": "",
                "LINEAR_LABELS": "null",
                "TEST_RESOLVED_TITLE": "[evolution] UUID_RESOLVE_001",
            }
        )
        result = subprocess.run(
            [
                "bash",
                str(prod_script),
                "create",
                "Issue",
                info["issue_ref"],  # REF 已解析（跳过网络反查），title 由回填到达
                "test-uuid-0002",
                "INFRA",
                "",  # title 参数为空 → 由回填提供
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert not info["marker"].exists(), "回填 title 的演化卡仍触发了 droid exec"
        assert "REJECTED: evolution card reached Mac" in _combined_log(info, tmp_path)


@_requires_darwin
class TestTriggerDroidNormalCardUnaffected:
    """非演化卡（无演化标记）走原路径不受影响（VAL-CROSS-002 对应 Mac 侧保障）。"""

    @pytest.fixture
    def normal_run(self, tmp_path):
        info = _make_sandbox(tmp_path, "Fix gateway timeout handling", issue_ref="INFRA-9902")
        # labels=None：模拟 hooks.json 当前形态（不透传 labels，无标签信号）
        result = _run_trigger(info, tmp_path, labels=None)
        return info, result, tmp_path

    def test_droid_stub_launched_for_normal_card(self, normal_run):
        """沙箱 stub droid 被启动（落痕 marker）= 原路径未被演化守卫拦截。

        trigger-droid.sh 先 fork 后台子 shell 再立即返回：需轮询等 stub droid 落痕。
        """
        info, result, tmp_path = normal_run
        assert result.returncode == 0, f"rc={result.returncode} stderr={result.stderr[-1500:]}"
        reached = _wait_for(lambda: info["marker"].exists(), timeout=90)
        assert reached, (
            "非演化卡未走到 droid exec（守卫误伤原路径）。日志尾部：\n"
            + _combined_log(info, tmp_path)[-2000:]
        )

    def test_no_rejected_log_for_normal_card(self, normal_run):
        info, _, tmp_path = normal_run
        log_text = _combined_log(info, tmp_path)
        assert "REJECTED: evolution card reached Mac" not in log_text
        assert "mac_evolution_rejected" not in log_text

    def test_flag_true_still_rejects_evolution(self, tmp_path):
        """flag=true 场景：Mac 侧通道分离是常量事实，不随 R1_ROUTE_LINEAR 翻转。"""
        info = _make_sandbox(tmp_path, "[evolution] FLAG_TRUE_001", issue_ref="INFRA-9903")
        result = _run_trigger(info, tmp_path)
        assert result.returncode == 0
        assert not info["marker"].exists()
        assert "REJECTED: evolution card reached Mac" in _combined_log(info, tmp_path)

    def test_env_labels_wins_over_missing_title(self, tmp_path):
        """title 缺失 + LINEAR_LABELS 携带演化标签：环境变量兜底识别。"""
        info = _make_sandbox(tmp_path, "", issue_ref="INFRA-9904")
        result = _run_trigger(info, tmp_path)  # labels="auto" → 注入 ["evolution-found"]
        assert result.returncode == 0
        assert not info["marker"].exists(), "标签信号兜底识别失败（演化卡走原路径）"
        assert "REJECTED: evolution card reached Mac" in _combined_log(info, tmp_path)


@_requires_darwin
class TestTriggerDroidResumePrecedence:
    """RESUME_SESSION_ID 人工恢复分支位于演化拒绝之前（演化卡可人工恢复，永不自动执行）。"""

    def test_resume_branch_takes_precedence(self, tmp_path):
        info = _make_sandbox(tmp_path, "[evolution] RESUME_001", issue_ref="INFRA-9905")
        prod_script = info["sandbox"] / "scripts" / "trigger-droid.sh"
        text = prod_script.read_text(encoding="utf-8")

        # 结构断言：演化拒绝（守卫调用点）必须先于 resume 分支
        guard_pos = text.find("evolution_reject_or_pass")
        resume_pos = text.find('"$ACTION" = "resume" ] && [ -n "${RESUME_SESSION_ID:-}" ]')
        assert guard_pos != -1, "找不到演化守卫调用点"
        assert resume_pos != -1, "找不到 resume 分支"
        assert guard_pos < resume_pos, "RESUME 人工恢复分支必须位于演化拒绝之后（守卫在前）"

        # 行为断言：resume + RESUME_SESSION_ID + 演化 title → 不触发演化拒绝日志，
        # 走 process_one_issue 内的 resume 分支（stub droid 落痕）
        env = os.environ.copy()
        env.update(
            {
                "WEBHOOK_BASE": str(info["sandbox"]),
                "REPO_CONFIG": str(tmp_path / "repositories.yml"),
                "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
                "POSTHOG_DRY_RUN": "1",
                "LINEAR_API_KEY": "",
                "LINEAR_LABELS": "null",
                "RESUME_SESSION_ID": "test-session-abc123",
            }
        )
        result = subprocess.run(
            [
                "bash",
                str(prod_script),
                "resume",
                "Issue",
                info["issue_ref"],
                "test-uuid-0005",
                "INFRA",
                info["evolution_title"],
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0, f"rc={result.returncode} stderr={result.stderr[-1500:]}"
        reached = _wait_for(lambda: info["marker"].exists(), timeout=90)
        assert reached, (
            "resume 流程未到达 droid exec（人工恢复被阻断）。日志尾部：\n"
            + _combined_log(info, tmp_path)[-2000:]
        )
        log_text = _combined_log(info, tmp_path)
        assert "REJECTED: evolution card reached Mac" not in log_text, (
            "带显式 RESUME_SESSION_ID 的演化卡被演化拒绝拦截（人工恢复入口被破坏）"
        )


# ============================================================================
# T4: 结构性不变量（grep 核验 + 部署注册）
# ============================================================================


def _non_comment_lines(path: Path) -> list[tuple[int, str]]:
    return non_comment_lines(path)


class TestStructuralInvariants:
    """VAL-GATE-003 grep 核验：演化分支不存在 droid exec 调用；守卫在执行分支之前。"""

    @pytest.fixture(scope="class")
    def script_text(self) -> str:
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_guard_early_in_main_flow(self, script_text: str):
        """守卫调用点必须位于锁/flock/状态副作用与 droid exec 之前。"""
        guard_pos = script_text.find("evolution_reject_or_pass")
        assert guard_pos != -1, "trigger-droid.sh 未接入演化守卫"
        first_lock_pos = script_text.find('LOCK_FILE="${LOCK_DIR}')
        first_exec_pos = script_text.find('exec 200>>"$LOCK_FILE"')
        first_status_pos = script_text.find('_write_status "$p_ref"')
        assert first_exec_pos != -1 and first_status_pos != -1 and first_lock_pos != -1
        assert guard_pos < first_exec_pos, "守卫必须位于 flock 锁获取之前"
        assert guard_pos < first_status_pos, "守卫必须位于状态文件写入之前"

    def test_evolution_branch_has_no_direct_droid_exec(self, script_text: str):
        """grep 核验：演化识别到退出之间不存在 droid exec 调用（守卫函数体内）。"""
        lib_text = CHANNEL_LIB_PATH.read_text(encoding="utf-8")
        guard_fn = re.search(r"evolution_reject_or_pass\(\).*?\n^}", lib_text, re.S | re.M)
        assert guard_fn, "lib/evolution-channel.sh 中找不到 evolution_reject_or_pass 函数体"
        body = guard_fn.group(0)
        assert "droid exec" not in body and "droid exec" not in "".join(
            ln for ln in body.splitlines() if not ln.strip().startswith("#")
        ), "演化拒绝分支内不得调用 droid exec"

    def test_rejected_wording_present(self, script_text: str):
        lib_text = CHANNEL_LIB_PATH.read_text(encoding="utf-8")
        combined = script_text + lib_text
        assert "REJECTED: evolution card reached Mac" in combined
        assert "dropped, not queued" in combined

    def test_mac_evolution_rejected_event_sent(self, script_text: str):
        """PostHog 独立事件名 mac_evolution_rejected（可配告警：到达 Mac=不变量破坏）。"""
        lib_text = CHANNEL_LIB_PATH.read_text(encoding="utf-8")
        assert "mac_evolution_rejected" in lib_text, "演化拒绝必须发送 mac_evolution_rejected 事件"
        guard_fn = re.search(r"evolution_reject_or_pass\(\).*?\n^}", lib_text, re.S | re.M)
        assert guard_fn and "mac_evolution_rejected" in guard_fn.group(0)

    def test_resume_branch_precedes_guard_semantics(self, script_text: str):
        """resume 判定（process_one_issue 内分支）在守卫之后的调用链中仍保持人工优先语义：
        守卫对 resume+显式 session id 放行（行为测试见 TestTriggerDroidResumePrecedence）。
        """
        assert "RESUME_SESSION_ID:-" in script_text

    def test_guard_is_delete_not_filter(self, script_text: str):
        """通道分离语义：守卫失败必须 fail-closed（解析器崩溃 → 按演化卡拒绝），非黑名单放行。"""
        lib_text = CHANNEL_LIB_PATH.read_text(encoding="utf-8")
        guard_fn = re.search(r"evolution_reject_or_pass\(\).*?\n^}", lib_text, re.S | re.M)
        assert guard_fn, "找不到守卫函数体"
        body = guard_fn.group(0)
        # fail-closed 模式：默认拒绝（_rejected=1 初始值），仅显式 normal 判定放行
        assert "_rejected=1" in body, "守卫必须默认拒绝（fail-closed 初始值）"
        assert '"normal"' in body, "仅显式 normal 判定可放行"
        # 无识别器结果（decision 空）不得放行：放行仅能经由 elif normal 分支
        pass_branch = re.search(r"elif.*normal.*\n\s*_rejected=0", body)
        assert pass_branch, "放行必须绑定显式 normal 判定"

    def test_manifest_registers_channel_lib(self):
        """lib/evolution-channel.sh 必须注册进 MANIFEST.sh（随 sync 部署生产副本）。"""
        manifest = (WEBHOOK_DIR / "MANIFEST.sh").read_text(encoding="utf-8")
        assert '"lib/evolution-channel.sh"' in manifest, (
            "MANIFEST.sh MANAGED_LIB_FILES 必须包含 lib/evolution-channel.sh"
        )

    def test_bash_syntax_valid_script_and_lib(self):
        ok, err = bash_syntax_ok(SCRIPT_PATH, CHANNEL_LIB_PATH)
        assert ok, err

    def test_shellcheck_clean_channel_lib(self):
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed")
        result = subprocess.run(
            ["shellcheck", str(CHANNEL_LIB_PATH)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"shellcheck 未过:\n{result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
