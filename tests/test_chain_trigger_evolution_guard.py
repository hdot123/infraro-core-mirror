"""Mac 链式触发环演化守卫（evolution/m1fix-chain-trigger-guard）。

scrutiny 阻断②（违反 owner 第零条裁定，生产实证 2 次演化卡自动执行）：
trigger-droid.sh 链式触发环在正常卡完成后 linear_query_next_queued →
process_one_issue，绕过唯一守卫点（主流程 evolution_reject_or_pass）；
lib/linear-queue.sh 在 AGENT_UUID 空时按 evolution-found 标签**优选**演化卡
且未入 MANIFEST 管辖（sync 不部署 = 生产副本游离于版本控制之外）。

修复语义（守卫唯一入口原则）：
  ① process_one_issue 的全部调用方先过 evolution_reject_or_pass——
     webhook 面用 exit 形态（无参调用），链式 pick 点用 return 形态（false：
     演化卡跳过、子 shell 链式环继续）；
  ② linear-queue.sh 演化卡中立：删除 evolution-found 优选回退 + 查询恒排除
     演化标签 + 入 MANIFEST.sh 受管；
  ③ 结构测试：grep 枚举 trigger-droid.sh 内全部 droid-exec 可达路径，
     断言每条路径前方存在守卫且形态正确（无演化卡旁路）。

识别 = lib/evolution-channel.sh 共享识别（title "[evolution]" 前缀或归一化
labels）；链式面 labels 信号来自 linear_query_next_queued 输出第 5 字段
（该卡自身标签 JSON，旧 4 字段形态向后兼容 → 仅 title 信号）。

测试分层：
  A  linear-queue.sh 演化卡中立（文件入仓 + MANIFEST 注册 + 查询构造断言）
  B  链式 pick 点守卫（可执行语义：真库 source 调用，false=true 形态对照）
  C  全链路行为沙箱（链式环对演化卡零 droid exec、正常卡不受影响）
  D  结构性不变量（droid-exec 可达路径 grep 枚举 + 守卫唯一入口锁定）
"""

from __future__ import annotations

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
MANIFEST_PATH = WEBHOOK_DIR / "MANIFEST.sh"
QUEUE_LIB_PATH = WEBHOOK_DIR / "lib" / "linear-queue.sh"
CHANNEL_LIB_PATH = WEBHOOK_DIR / "lib" / "evolution-channel.sh"
LEDGER_LIB_PATH = WEBHOOK_DIR / "lib" / "dispatch-ledger.sh"
POSTHOG_LIB_PATH = WEBHOOK_DIR / "lib" / "posthog.sh"
OP_MCP_LIB_PATH = WEBHOOK_DIR / "lib" / "op-mcp.sh"


def _q(s: str) -> str:
    """单引号安全包裹（bash -c 片段内使用）。"""
    return "'" + s.replace("'", "'\\''") + "'"


# ============================================================================
# A: linear-queue.sh 演化卡中立
# ============================================================================


class TestLinearQueueEvolutionNeutral:
    """①演化优选选择器中和 ②文件入 MANIFEST 受管。"""

    def test_lib_exists_in_repo(self):
        """lib/linear-queue.sh 必须入仓（游离态 = sync 不部署 = 生产副本不受管）。"""
        assert QUEUE_LIB_PATH.is_file(), (
            "webhook-scripts/lib/linear-queue.sh 不在仓库中："
            "未入 MANIFEST 管辖的 lib 文件是链式触发演化卡旁路的载体，必须移入仓库受版本控制"
        )

    def test_manifest_registers_queue_lib(self):
        """lib/linear-queue.sh 必须注册进 MANIFEST.sh MANAGED_LIB_FILES（随 sync 部署）。"""
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert '"lib/linear-queue.sh"' in manifest, (
            "MANIFEST.sh MANAGED_LIB_FILES 必须包含 lib/linear-queue.sh"
        )

    def test_no_evolution_found_positive_selector(self):
        """演化优选选择器必须中和：不得再按 evolution-found 标签正向优选（containsIgnoreCase 回退）。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        assert "containsIgnoreCase" not in text, (
            "linear_query_next_queued 仍含 evolution-found 正向优选选择器"
            "（containsIgnoreCase 回退 = 演化卡铸卡源）"
        )

    def test_query_excludes_evolution_labels(self):
        """查询必须恒排除演化标签（与 EVOLUTION_CHANNEL_LABEL 同步的双保险）。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        assert "evolution-found" in text, "排除机制必须针对 evolution-found 标签"
        assert "EVOLUTION_EXCLUDE_LABEL" in text, "排除标签需可配置（与 channel 库同步面）"

    def test_uses_validated_graphql_forms_only(self):
        """查询只用实测合法的 GraphQL filter 形态（生产 API 400 实证）：
        - Linear 无 labels none 反向过滤（"Field \\"none\\" is not defined"）；
        - StringComparator 无 inIgnoreCase（"Field \\"inIgnoreCase\\" is not defined"）；
        - 合法形态 = labels some eqIgnoreCase（单标量）+ id nin（排除已拒绝卡），
          演化排除用 A/B 双查询差集。
        （仅查非注释行：文档注释里的形态名引用合法。）
        """
        code_lines = [
            line
            for line in QUEUE_LIB_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "none:" not in code, "labels none 反向过滤不可用（Linear 实证 400）"
        assert "inIgnoreCase" not in code, "inIgnoreCase 不可用（StringComparator 实证 400）"
        assert "some: { name: { eqIgnoreCase:" in code, (
            "演化命中集查询需用 labels some eqIgnoreCase（实测合法形态）"
        )
        assert "nin:" in code, "本轮已拒绝卡需经 id nin 排除（跳过列表契约）"

    def test_skip_uuids_wired(self):
        """查询函数需消费本轮已拒绝卡 skip 列表（SKIP_UUIDS → id nin）。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        assert "SKIP_UUIDS" in text, "查询需消费 SKIP_UUIDS 环境（调用方 skip 列表注入面）"

    def test_ab_set_difference_pattern(self):
        """演化排除=A 主查询 + B 演化命中集差集（B 失败降级放行 A，title 信号兜底）。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        assert "evo_ids" in text, "需计算演化命中集 id 集合（A/B 差集）"
        assert "WARN: evolution-set query failed" in text, (
            "B 查询失败必须降级（不阻断 A 候选批，链式面 title 守卫兜底）"
        )

    def test_no_delegate_fallback_branch(self):
        """AGENT_UUID 空时不得走标签查询分支：空 = 跳过 pick（宁少处理不优选演化卡）。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        # 旧实现按 AGENT_UUID 是否为空动态拼 delegate_filter / 标签回退——必须删除该分支
        assert "delegate_filter" not in text, (
            "delegate_filter 动态拼接分支仍在（空 AGENT_UUID → evolution-found 标签查询的载体）"
        )

    def test_output_includes_labels_field(self):
        """查询输出必须带第 5 字段（该卡标签 JSON）供链式守卫识别。"""
        text = QUEUE_LIB_PATH.read_text(encoding="utf-8")
        assert "labels { nodes { name } }" in text, "GraphQL 查询需取回 labels.nodes.name"
        assert "LABELS_JSON" in text, "输出契约需含 LABELS_JSON 字段说明"

    def test_shellcheck_clean(self):
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed")
        result = subprocess.run(
            ["shellcheck", str(QUEUE_LIB_PATH)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"shellcheck 未过:\n{result.stdout}"


# ============================================================================
# B: 链式 pick 点守卫（真库 source 调用）
# ============================================================================


def _run_guard(
    tmp_path: Path, args: list[str], title: str, labels_json
) -> subprocess.CompletedProcess:
    """source 真守卫库后按给定形态调用守卫，末尾放探针 echo 检测 exit/return 语义。

    env 走 bash 文件内赋值（LOG_FILE 指向 tmp_path/guard.log）；labels_json 为
    JSON 值（str 或 None→null）经单引号安全注入；log() 由片段内定义（守卫库
    与 posthog.sh 都依赖调用方提供 log——trigger-droid.sh 主流程 tee 双写 /
    子 shell append-only 两种先例，此处取 append-only）。
    """
    labels_literal = "null" if labels_json is None else _q(json.dumps(labels_json))
    script = "\n".join(
        [
            f"LOG_FILE={_q(str(tmp_path / 'guard.log'))}",
            'POSTHOG_EVENT_NAME="linear_webhook_failure"',
            'POSTHOG_DISTINCT_ID="linear-webhook"',
            "export POSTHOG_DRY_RUN=1",
            'log() { echo "[$(date \'+%Y-%m-%d %H:%M:%S\')] $*" >> "$LOG_FILE"; }',
            f"ISSUE_TITLE={_q(title)}",
            f"LINEAR_LABELS={labels_literal}",
            f"source {_q(str(CHANNEL_LIB_PATH))}",
            "evolution_reject_or_pass " + " ".join(args),
            'echo "GUARD_RC=$?"',
        ]
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )


class TestChainPickGuardExecutableSemantics:
    """chain 形态：演化卡 return 非 0（调用方 continue），非演化卡 return 0；
    无参形态（webhook 面）：保持 exit 0 幂等语义（探针不可达）。"""

    def test_chain_form_evolution_card_returns_nonzero(self, tmp_path):
        result = _run_guard(tmp_path, ["chain"], "[evolution] CHAIN_E2E_002", None)
        assert "GUARD_RC=" in result.stdout, (
            f"守卫必须以 return（非 exit）结束（子 shell 存活）；实际输出：{result.stdout!r} "
            f"stderr：{result.stderr[-500:]}"
        )
        assert "GUARD_RC=0" not in result.stdout, (
            f"链式形态演化卡必须返回非 0 供调用方 continue；实际：{result.stdout!r}"
        )
        combined = result.stdout + (tmp_path / "guard.log").read_text(encoding="utf-8")
        assert "REJECTED: evolution card reached Mac" in combined
        assert "mac_evolution_rejected" in combined

    def test_chain_form_normal_card_returns_zero(self, tmp_path):
        result = _run_guard(tmp_path, ["chain"], "Fix chain routing for INFRA team", None)
        assert "GUARD_RC=0" in result.stdout, (
            f"链式形态非演化卡必须 return 0 放行；实际：{result.stdout!r} "
            f"stderr：{result.stderr[-800:]}"
        )

    def test_chain_form_labels_signal_also_triggers(self, tmp_path):
        """title 无前缀但该卡带 evolution-found 标签（第 5 字段信号）：同样拒绝。"""
        result = _run_guard(
            tmp_path,
            ["chain"],
            "Refactor duplicated helper",
            ["evolution-found"],
        )
        assert "GUARD_RC=" in result.stdout and "GUARD_RC=0" not in result.stdout, (
            f"标签信号演化卡必须被链式守卫拒绝；实际：{result.stdout!r} stderr：{result.stderr[-500:]}"
        )
        combined = result.stdout + (tmp_path / "guard.log").read_text(encoding="utf-8")
        assert "normalized_labels" in combined

    def test_webhook_form_still_exit_semantics(self, tmp_path):
        """无参调用（webhook 面，既有写法）保持 exit 0 幂等语义（探针不可达）。"""
        result = _run_guard(tmp_path, [], "[evolution] WEBHOOK_FACE_001", None)
        assert "GUARD_RC=" not in result.stdout, (
            f"webhook 形态必须保持 exit 语义（探针不可达）；实际：{result.stdout!r}"
        )
        combined = result.stdout + (tmp_path / "guard.log").read_text(encoding="utf-8")
        assert "REJECTED: evolution card reached Mac" in combined

    def test_webhook_form_normal_card_passes_through(self, tmp_path):
        result = _run_guard(tmp_path, [], "Normal INFRA card", None)
        assert "GUARD_RC=0" in result.stdout, (
            f"webhook 形态非演化卡放行守卫，主流程继续；实际：{result.stdout!r}"
        )


# ============================================================================
# C: 全链路行为沙箱（链式环：演化卡零 droid exec、正常卡不受影响）
# ============================================================================


_requires_darwin = pytest.mark.skipif(
    darwin_skip_reason() is not None, reason=darwin_skip_reason() or ""
)


def _make_chain_sandbox(tmp_path: Path) -> dict:
    """构造链式触发环可执行的沙箱：真 trigger-droid.sh + 真 channel lib +
    可控队列 stub linear-queue（EVOLUTION_TEST_QUEUE_FILE 驱动）+ 记痕 stub droid。

    队列文件每行 = linear_query_next_queued 输出行（UUID|REF|TITLE|TEAM|LABELS_JSON），
    stub 每调用弹出队首行（模拟 pick 消费），空队列返回空（链式环退出）。
    """
    sandbox = tmp_path / "webhook"
    repo = tmp_path / "target-repo"
    logs = sandbox / "logs"
    logs.mkdir(parents=True)
    (sandbox / "locks").mkdir(parents=True)
    (sandbox / "status").mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    marker = tmp_path / "droid-launched.marker"
    (tmp_path / "bin").mkdir(parents=True, exist_ok=True)

    # stub gh：记录调用、恒成功（隔离真实 gh——写回兜底不触达生产仓）
    write_stub(
        tmp_path / "bin" / "gh",
        '#!/bin/bash\necho "gh-stub: $*" >> "GH_LOG"\nexit 0\n'.replace(
            "GH_LOG", str(tmp_path / "gh-calls.log")
        ),
    )

    # 仓库路由配置（route_repo：INFRA → 沙箱 repo）
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

    # lib stub：linear-queue 用队列文件驱动链式环（消费 SKIP_UUIDS=生产契约
    # 形态：被拒卡不再返回）；p0a-guard 放行
    sandbox_lib = sandbox / "scripts" / "lib"
    sandbox_lib.mkdir(parents=True)
    write_stub(
        sandbox_lib / "linear-queue.sh",
        "linear_queue_init() { return 1; }\n"
        "linear_move_to_in_progress() { return 1; }\n"
        "linear_query_next_queued() {\n"
        '  local qf="${EVOLUTION_TEST_QUEUE_FILE:-}"\n'
        '  [ -n "$qf" ] && [ -f "$qf" ] || return 1\n'
        "  local first skip\n"
        '  first=$(head -n 1 "$qf")\n'
        '  while [ -n "$first" ]; do\n'
        '    skip="${first%%|*}"\n'
        "    local excluded=0\n"
        '    for u in "$@"; do\n'
        '      [ "$u" = "$skip" ] && excluded=1 && break\n'
        "    done\n"
        '    if [ "$excluded" = "0" ]; then\n'
        '      tail -n +2 "$qf" > "${qf}.tmp" && mv "${qf}.tmp" "$qf"\n'
        "      printf '%s\\n' \"$first\"\n"
        "      return 0\n"
        "    fi\n"
        '    tail -n +2 "$qf" > "${qf}.tmp" && mv "${qf}.tmp" "$qf"\n'
        '    first=$(head -n 1 "$qf")\n'
        "  done\n"
        "  return 1\n"
        "}\n"
        "linear_get_issue_team_key() { return 1; }\n",
    )
    write_stub(sandbox_lib / "p0a-guard.sh", "# test stub: guard disabled\n")

    # 生产目录形态：真源脚本 + 真源 channel lib + 账本 lib + posthog/op-mcp（被测对象及其依赖）
    prod_scripts = sandbox / "scripts"
    prod_scripts.mkdir(exist_ok=True)
    shutil.copy2(SCRIPT_PATH, prod_scripts / "trigger-droid.sh")
    prod_lib = prod_scripts / "lib"
    prod_lib.mkdir(exist_ok=True)
    shutil.copy2(CHANNEL_LIB_PATH, prod_lib / "evolution-channel.sh")
    shutil.copy2(LEDGER_LIB_PATH, prod_lib / "dispatch-ledger.sh")
    shutil.copy2(POSTHOG_LIB_PATH, prod_lib / "posthog.sh")
    shutil.copy2(OP_MCP_LIB_PATH, prod_lib / "op-mcp.sh")
    write_stub(
        prod_lib / "op-mcp.sh",
        'OP_VAULT_SEVER="test-vault-id"\nop_get_field() { return 1; }\n',
    )

    # stub droid：记录调用即失败退出（若被启动，落痕 marker）
    write_stub(
        tmp_path / "bin" / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho "STUB-DROID-LAUNCHED" >&2\nexit 127\n',
    )

    return {
        "sandbox": sandbox,
        "repo": repo,
        "logs": logs,
        "marker": marker,
        "queue_file": tmp_path / "chain-queue.txt",
    }


def _combined_log(sandbox_info: dict, tmp_path: Path) -> str:
    gh_calls = tmp_path / "gh-calls.log"
    return combined_log(sandbox_info["logs"], extra_paths=[gh_calls])


def _chain_run_tick(
    tmp_path: Path, info: dict, queue_lines: list[str], ref: str
) -> subprocess.CompletedProcess:
    """链式环单 tick 驱动（TestChainTriggerLoopBehavior / TestChainTriggerLedgerDedup
    共用：31 行沙盒执行体抽单一定义，避免 100% AST 同源复刻触发
    CODE_HYGIENE_DUPLICATE_BLOCK；两类的差异仅在硬编码卡号）。
    跳过语义归两类上的 @_requires_darwin（行为沙箱 macOS-only，
    Linux 无 /opt/homebrew/bin/flock——装饰在方法/工具函数上不会生效）。"""
    info["queue_file"].write_text("\n".join(queue_lines) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "WEBHOOK_BASE": str(info["sandbox"]),
            "REPO_CONFIG": str(tmp_path / "repositories.yml"),
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "POSTHOG_DRY_RUN": "1",
            "LINEAR_API_KEY": "",
            "LINEAR_LABELS": "null",
            "EVOLUTION_TEST_QUEUE_FILE": str(info["queue_file"]),
        }
    )
    env.pop("RESUME_SESSION_ID", None)
    return subprocess.run(
        [
            "bash",
            str(info["sandbox"] / "scripts" / "trigger-droid.sh"),
            "create",
            "Issue",
            ref,
            "test-uuid-main",
            "INFRA",
            "",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(tmp_path),
    )


@_requires_darwin
class TestChainTriggerLoopBehavior:
    """链式触发环全链路：演化卡被 pick 点守卫跳过（零 droid exec），
    队列中后续正常卡照常处理（守卫不得杀掉链式环）。"""

    def _run(self, tmp_path: Path, info: dict, queue_lines: list[str]):
        return _chain_run_tick(tmp_path, info, queue_lines, "INFRA-9901")

    def test_evolution_chain_pick_skipped_normal_card_processed(self, tmp_path):
        info = _make_chain_sandbox(tmp_path)
        # 队列首 = 演化卡（title 信号），次 = 正常卡（旧 4 字段形态 → labels=null 兼容）
        result = self._run(
            tmp_path,
            info,
            [
                "test-uuid-evo|INFRA-9902|[evolution] CHAIN_E2E_001|INFRA|null",
                "test-uuid-norm|INFRA-9903|Fix chain routing|INFRA|null",
            ],
        )
        assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr[-2000:]}"

        # 链式环最终行在最后一张卡 process_one_issue 返回后才落盘（正常卡含
        # stub droid 18s + crash-retry 10s + 终态查询）——必须等环真正走完再断言，
        # 60s 默认在负载下不够（xdist 8 worker 并发实测）。
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        ), f"链式环未走完（守卫不得杀掉环）。日志尾部：\n{_combined_log(info, tmp_path)[-3000:]}"

        final_log = _combined_log(info, tmp_path)
        # 演化卡：被链式守卫识别 + 跳过，零 droid exec、零状态副作用
        assert "CHAIN-TRIGGER: Skipping evolution card INFRA-9902" in final_log
        assert "process_one_issue started: INFRA-9902" not in final_log, (
            "演化卡被链式触发环执行（pick 点守卫未接入）"
        )
        assert "mac_evolution_rejected" in final_log, (
            "链式跳过演化卡必须发 mac_evolution_rejected 事件"
        )
        evo_status = list((info["sandbox"] / "status").glob("INFRA-9902.json"))
        assert evo_status == [], f"演化卡不应创建状态文件: {evo_status}"
        # 正常卡：链式正常卡照常执行
        assert "process_one_issue started: INFRA-9903" in final_log, "链式守卫不得阻断正常卡处理"
        assert info["marker"].exists(), "正常卡 droid exec 未启动"

    def test_chain_labels_signal_card_skipped(self, tmp_path):
        """队列卡 title 无演化前缀但带 evolution-found 标签（第 5 字段信号）：同样跳过。"""
        info = _make_chain_sandbox(tmp_path)
        result = self._run(
            tmp_path,
            info,
            [
                'test-uuid-lbl|INFRA-9905|Refactor duplicated helper|INFRA|["evolution-found"]',
            ],
        )
        assert result.returncode == 0
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        )
        final_log = _combined_log(info, tmp_path)
        assert "CHAIN-TRIGGER: Skipping evolution card INFRA-9905" in final_log
        assert "process_one_issue started: INFRA-9905" not in final_log
        assert "mac_evolution_rejected" in final_log

    def test_chain_evolution_head_skipped_via_skip_list(self, tmp_path):
        """队首=演化卡：本轮 skip 列表（id nin 契约）让查询跳过已拒绝卡，
        后续正常卡不被饿死；REJECTED 告警恰 1 次（零无限拒绝环）。"""
        info = _make_chain_sandbox(tmp_path)
        result = self._run(
            tmp_path,
            info,
            [
                "test-uuid-evo|INFRA-9906|[evolution] LOOP_E2E_001|INFRA|null",
                "test-uuid-after|INFRA-9907|Fix after evolution head|INFRA|null",
            ],
        )
        assert result.returncode == 0
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        )
        final_log = _combined_log(info, tmp_path)
        assert "process_one_issue started: INFRA-9906" not in final_log
        assert "CHAIN-TRIGGER: Skipping evolution card INFRA-9906" in final_log
        # 正常卡不被演化队首饿死（droid-review P1-2 修复面）
        assert "process_one_issue started: INFRA-9907" in final_log, (
            "演化卡队首后的正常卡被饿死（skip 列表未生效）"
        )
        rejected_count = final_log.count("REJECTED: evolution card reached Mac")
        assert rejected_count == 1, (
            f"演化队首 REJECTED 应恰 1 次（skip 列表排除后不再重查），实际 {rejected_count}"
        )

    def test_chain_queue_exhausts_normally_without_evolution(self, tmp_path):
        """无演化卡的队列：链式环行为不变（回归保护）。"""
        info = _make_chain_sandbox(tmp_path)
        result = self._run(
            tmp_path,
            info,
            ["test-uuid-n1|INFRA-9904|Fix something|INFRA|null"],
        )
        assert result.returncode == 0
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        )
        final_log = _combined_log(info, tmp_path)
        assert "process_one_issue started: INFRA-9904" in final_log
        assert "mac_evolution_rejected" not in final_log


@_requires_darwin
@_requires_darwin
class TestChainTriggerLedgerDedup:
    """共用派发账本（VAL-LEDGER-002）：链式 pick 点先查账本——completed 派发的
    卡回到 queued 查询集（漂移信号）时跳过（零 droid exec），后续卡不饿死。"""

    def _seed_completed_dispatch(self, info: dict, uuid: str, ref: str) -> None:
        base = info["sandbox"]
        ledger_dir = base / "status" / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (ledger_dir / f"{uuid}.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "schema": "dispatch-ledger/1",
                    "linearUuid": uuid,
                    "linearRef": ref,
                    "kind": "linear-gateway",
                    "source": "trigger-droid",
                    "ghIssue": None,
                    "title": "",
                    "team": "INFRA",
                    "claimedAt": "2026-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (base / "status" / f"{ref}.json").write_text(
            json.dumps({"status": "completed", "exitCode": 0}), encoding="utf-8"
        )

    def _run(self, tmp_path: Path, info: dict, queue_lines: list[str]):
        return _chain_run_tick(tmp_path, info, queue_lines, "INFRA-9911")

    def test_chain_completed_dispatch_card_skipped(self, tmp_path):
        """队首=已成功派发的卡（漂移回队）：账本命中跳过，后续正常卡照常。"""
        info = _make_chain_sandbox(tmp_path)
        self._seed_completed_dispatch(info, "test-uuid-done", "INFRA-9912")
        result = self._run(
            tmp_path,
            info,
            [
                "test-uuid-done|INFRA-9912|Already done card|INFRA|null",
                "test-uuid-fresh|INFRA-9913|Fresh card after done|INFRA|null",
            ],
        )
        assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr[-2000:]}"
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        ), f"链式环未走完。日志尾部：\n{_combined_log(info, tmp_path)[-3000:]}"
        final_log = _combined_log(info, tmp_path)
        assert "dispatch ledger hit" in final_log, "链式 pick 点必须查账本"
        assert "process_one_issue started: INFRA-9912" not in final_log, (
            "completed 派发的卡被链式环重复执行（pick 点账本检查缺失）"
        )
        assert "process_one_issue started: INFRA-9913" in final_log, "后续正常卡被饿死"

    def test_chain_failed_dispatch_card_not_blocked(self, tmp_path):
        """失败卡的链式重试不受账本拦截（去重只命中 completed 终态）。"""
        info = _make_chain_sandbox(tmp_path)
        self._seed_completed_dispatch(info, "test-uuid-failed", "INFRA-9914")
        (info["sandbox"] / "status" / "INFRA-9914.json").write_text(
            json.dumps({"status": "failed", "exitCode": 1}), encoding="utf-8"
        )
        result = self._run(
            tmp_path,
            info,
            ["test-uuid-failed|INFRA-9914|Retry me|INFRA|null"],
        )
        assert result.returncode == 0
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        )
        final_log = _combined_log(info, tmp_path)
        assert "process_one_issue started: INFRA-9914" in final_log, (
            "失败卡重试被账本误拦（去重语义过宽）"
        )

    def test_chain_ledger_lib_missing_degrades(self, tmp_path):
        """账本 lib 缺失（sync 漂移窗口）：链式环 fail-open 照常处理队列。"""
        info = _make_chain_sandbox(tmp_path)
        (info["sandbox"] / "scripts" / "lib" / "dispatch-ledger.sh").unlink()
        result = self._run(
            tmp_path,
            info,
            ["test-uuid-n2|INFRA-9915|Normal no-lib card|INFRA|null"],
        )
        assert result.returncode == 0
        assert wait_for(
            lambda: "CHAIN-TRIGGER: All queued issues processed" in _combined_log(info, tmp_path),
            timeout=240,
        )
        final_log = _combined_log(info, tmp_path)
        assert "process_one_issue started: INFRA-9915" in final_log, (
            "lib 缺失不得瘫痪链式环（fail-open 契约）"
        )


# ============================================================================
# D: 结构性不变量（droid-exec 可达路径 grep 枚举 + 守卫唯一入口）
# ============================================================================


_GUARD_CALL_RE = re.compile(r"^(! )?evolution_reject_or_pass( (true|chain))?\s*(\|\| \{)?$")
_GUARD_CHAIN_RE = re.compile(r"^if ! evolution_reject_or_pass chain; then$")
_GUARD_TRUE_RE = re.compile(r"^evolution_reject_or_pass$")


class TestStructuralNoEvolutionBypass:
    """grep 枚举 trigger-droid.sh 全部 droid-exec 可达路径，断言无演化卡旁路
    （守卫唯一入口原则：process_one_issue 全部调用方先过守卫）。

    services.yaml mac-evolution-exec-audit 的日志面佐证：REJECTED 缺失 +
    "Launching droid exec" 的日志 = 演化卡到达 Mac 后被自动执行的旁路实证；
    本类在代码面锁定同类旁路不可达。
    """

    @classmethod
    @pytest.fixture(scope="class")
    def script_lines(cls) -> list[tuple[int, str]]:
        return non_comment_lines(SCRIPT_PATH)

    @classmethod
    @pytest.fixture(scope="class")
    def script_text(cls) -> str:
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_chain_loop_guarded_before_process_one_issue(self, script_text: str):
        """链式触发 pick 点必须在链式 process_one_issue 之前过守卫（chain 形态）。"""
        chain_guard_pos = script_text.find("evolution_reject_or_pass chain")
        assert chain_guard_pos != -1, (
            "链式触发环未接 evolution_reject_or_pass chain 守卫（演化卡旁路仍在）"
        )
        chain_process_pos = script_text.find('process_one_issue "$n_uuid"')
        assert chain_process_pos != -1, "找不到链式触发环的 process_one_issue 调用"
        assert chain_guard_pos < chain_process_pos, "链式守卫必须位于链式 process_one_issue 之前"

    def test_droid_exec_reachable_paths_all_guarded(self, script_lines):
        """grep 枚举全部 droid exec 启动行：每条路径前方必须有守卫调用。"""
        exec_lines = [
            (idx, line)
            for idx, line in script_lines
            if "DROID_BIN:-" in line and re.search(r"\bexec\b", line)
        ]
        assert exec_lines, "找不到 droid exec 启动行（枚举器失效）"
        guard_calls = [(idx, line) for idx, line in script_lines if _GUARD_CALL_RE.search(line)]
        assert guard_calls, "trigger-droid.sh 中无守卫调用"

        for exec_idx, _line in exec_lines:
            preceding = [idx for idx, _ in guard_calls if idx < exec_idx]
            assert preceding, f"droid exec 启动行 (line {exec_idx}) 前方无任何守卫调用——演化卡旁路"

    def test_exactly_two_guard_forms(self, script_lines):
        """守卫唯一入口：恰两处调用（webhook 面 / 链式面），无第三形态、无多入口漂移。"""
        calls = [
            line
            for _, line in script_lines
            if _GUARD_CALL_RE.search(line) or _GUARD_CHAIN_RE.search(line)
        ]
        assert len(calls) == 2, f"守卫调用数应为 2，实际 {len(calls)}: {calls}"
        assert any(_GUARD_TRUE_RE.search(c) for c in calls), "缺 webhook 面守卫"
        assert any(_GUARD_CHAIN_RE.search(c) for c in calls), "缺链式面守卫"

    def test_chain_skip_uses_continue_not_exit(self, script_text: str):
        """链式守卫命中后必须 continue（环存活），不得 exit（杀环）。"""
        m = re.search(
            r"if ! evolution_reject_or_pass chain; then(.*?)fi",
            script_text,
            re.S,
        )
        assert m, "找不到链式守卫块（if ! evolution_reject_or_pass chain; then ... fi）"
        seg = m.group(1)
        assert "continue" in seg, "链式演化卡跳过必须 continue（环存活）"
        assert not re.search(r"\bexit\b", seg), "链式跳过路径不得 exit"

    def test_chain_skip_records_rejected_uuid_for_query_skip(self, script_text: str):
        """守卫命中后必须把该卡 UUID 记入本轮 skip 列表（_chain_rejected_uuids），
        下一轮查询 id nin 排除——拒绝不消费 Linear 卡片，不排除则查询恒返回队首
        同一张演化卡，后续正常卡饿死且 repick-guard break 收场（droid-review
        P1-2 修复面）。"""
        m = re.search(
            r"if ! evolution_reject_or_pass chain; then(.*?)fi",
            script_text,
            re.S,
        )
        assert m, "找不到链式守卫块"
        seg = m.group(1)
        assert '_chain_rejected_uuids="$_chain_rejected_uuids $n_uuid"' in seg, (
            "链式跳过路径必须记入 _chain_rejected_uuids（查询 skip 列表）"
        )
        # 查询消费 skip 列表（word-split 传参）
        assert re.search(r"linear_query_next_queued \$_?chain_rejected_uuids", script_text), (
            "链式查询需传 skip 列表（word-split 多参数）"
        )

    def test_chain_pick_records_last_picked_uuid(self, script_text: str):
        """放行路径仍需记 _last_picked_uuid（repick 守卫依赖：move 失败时
        下一轮同 UUID → break，防 move-to-In-Progress 死循环）。"""
        m = re.search(
            r'CHAIN-TRIGGER: Picking up(.*?)process_one_issue "\$n_uuid"',
            script_text,
            re.S,
        )
        assert m, "找不到链式 pick 到 process_one_issue 的代码段"
        assert '_last_picked_uuid="$n_uuid"' in m.group(1), (
            "放行路径必须记录 _last_picked_uuid（repick 守卫依赖）"
        )

    def test_chain_labels_signal_wired(self, script_text: str):
        """链式守卫的 labels 信号必须来自查询第 5 字段（n_labels→LINEAR_LABELS）。"""
        assert re.search(r"read -r n_uuid n_ref n_title n_team n_labels", script_text), (
            "链式解析需取第 5 字段 n_labels（该卡标签 JSON）"
        )
        assert re.search(
            r'LINEAR_LABELS="\$\{n_labels:-null\}"',
            script_text,
        ), "链式守卫调用需把 n_labels 注入 LINEAR_LABELS"

    def test_webhook_face_guard_unchanged(self, script_text: str):
        """webhook 面守卫调用点保持在主流程（exit 形态，幂等 2xx 语义不回归）。"""
        assert re.search(r"^evolution_reject_or_pass$", script_text, re.M), (
            "webhook 面守卫（无参 exit 形态）丢失"
        )

    def test_guard_precedes_all_side_effect_machinery(self, script_lines):
        """webhook 守卫仍在锁/状态副作用之前（mac-channel-separation 结构断言的补强版）。"""
        guard_pos = next((idx for idx, line in script_lines if _GUARD_TRUE_RE.search(line)), None)
        assert guard_pos is not None, "webhook 面守卫丢失"
        first_status_pos = next(
            (idx for idx, line in script_lines if '_write_status "$p_ref"' in line), None
        )
        assert first_status_pos is not None and guard_pos < first_status_pos

    def test_bash_syntax_valid(self):
        ok, err = bash_syntax_ok(SCRIPT_PATH, QUEUE_LIB_PATH, CHANNEL_LIB_PATH)
        assert ok, err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
