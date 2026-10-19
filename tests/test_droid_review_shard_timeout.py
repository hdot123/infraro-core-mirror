"""droid-review shard 超时预算回归测试（2026-08-25 PR #1027 三连超时）。

回归保护：review-shard job 的 timeout-minutes 必须由变量驱动。
B-1 收敛（2026-10-05）后分片实现单源于 droid-review-shards.yml（自仓壳
droid-review.yml 改为触发壳 + 本地聚合，零 review-shard job）——本套测试
随实现迁移读 reusable 载体；聚合（Layer 2）timeout 仍在自仓壳 droid-review.yml
本地聚合 job。背景：PR #1027 实证 BYOM 链路（Qwen 3.7 Plus）单轮延迟
1-8 分钟、完整审查需 13-15 轮模型交互，硬编码 timeout-minutes: 30 导致
三次确定性超时（session transcript 证实模型被杀时仍在活跃产出，非卡死），
且超时取消不匹配 watchdog 的 503/429 自愈特征，rerun 无法恢复。

变量驱动与其他预算层一致（Layer 2 DROID_REVIEW_TIMEOUT_MINUTES、
Layer 3 SHARD_MAX_FILES/SHARD_MAX_LINES、Layer 4 SHARD_MAX_PARALLEL），
可在线调参无需改 workflow。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parent.parent
# B-1 收敛：shard timeout 实现单源于 reusable（自仓壳经 workflow_call inputs
# 显式转发 vars.SHARD_TIMEOUT_MINUTES，默认值兜底双处一致）
SHARDS_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/droid-review-shards.yml"
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/droid-review.yml"
SHELL_CALL_PATH = REPO_ROOT / ".github/workflows/droid-review.yml"
PROMPT_PATH = REPO_ROOT / ".github/review/shard-review-prompt.md"


def _load_shards() -> dict:
    return yaml.safe_load(SHARDS_WORKFLOW_PATH.read_text())


def _load_shell() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


class TestShardTimeoutVariableDriven:
    """review-shard 超时必须由 SHARD_TIMEOUT_MINUTES 变量驱动。"""

    def test_shard_timeout_uses_variable(self):
        """timeout-minutes 引用 shard_timeout_minutes input（禁止回到硬编码）；
        自仓壳必须以 vars.SHARD_TIMEOUT_MINUTES 转发（变量驱动入口不迁移）。"""
        timeout = _load_shards()["jobs"]["review-shard"]["timeout-minutes"]
        assert "shard_timeout_minutes" in timeout
        shell_raw = SHELL_CALL_PATH.read_text()
        assert "vars.SHARD_TIMEOUT_MINUTES" in shell_raw

    def test_shard_timeout_default_fallback(self):
        """变量缺失时回退默认值 45（> 30，覆盖 PR #1027 实测的审查时长）。"""
        inputs = _load_shards()[True]["workflow_call"]["inputs"]
        assert inputs["shard_timeout_minutes"]["default"] == "45"
        shell_inputs = _load_shell()["jobs"]["shards"]["with"]
        assert shell_inputs["shard_timeout_minutes"] == "${{ vars.SHARD_TIMEOUT_MINUTES || '45' }}"

    def test_shard_timeout_default_below_aggregation_layer(self):
        """Layer 1 默认值必须低于 Layer 2 聚合路径预算（45 < 90），
        保证聚合 job 仍有余量收集 shard 结果。"""
        agg_timeout = _load_shell()["jobs"]["droid-review"]["timeout-minutes"]
        agg_default = int(agg_timeout.split("'")[1])
        shard_default = int(
            _load_shards()[True]["workflow_call"]["inputs"]["shard_timeout_minutes"]["default"]
        )
        assert shard_default < agg_default

    def test_workflow_contracts_preserved(self):
        """三大契约不被破坏：workflow 名 / 聚合 job 名 / artifact 前缀。"""
        data = _load_shell()
        assert data["name"] == "Droid Auto Review"
        assert "droid-review" in data["jobs"]
        # artifact 前缀上传在 reusable 的 review-shard（B-1 收敛后唯一上传点）
        shard_steps_text = yaml.safe_dump(_load_shards()["jobs"]["review-shard"]["steps"])
        assert "droid-review-debug-" in shard_steps_text

    def test_prompt_no_hardcoded_minutes(self):
        """prompt 模板不再硬编码 30 分钟数字（超时值由 workflow 层统一管理）。"""
        content = PROMPT_PATH.read_text()
        assert "30-minute" not in content
        assert "maximum of 30" not in content

    def test_prompt_budget_from_soft_timeout(self):
        """F3（v2.1 随修 D）：run_shard.sh 的预算文案由 SHARD_SOFT_TIMEOUT_SEC
        渲染——软超时调参后 prompt 不再残留硬编码 30min。"""
        script = (REPO_ROOT / "src/infra_core/engine/droid_review/run_shard.sh").read_text()
        assert "maximum of 30 minutes" not in script
        budget_line = next(line for line in script.splitlines() if "BUDGET_MINUTES=$" in line)
        assert "SHARD_SOFT_TIMEOUT_SEC" in budget_line
        assert "at most ${BUDGET_MINUTES} minutes" in script

    def test_shard_soft_timeout_below_job_timeout(self):
        """F3 数字链：软超时默认 25min < shard job timeout 45min（越界自然终结，
        不打穿 45min 硬杀）。"""
        script = (REPO_ROOT / "src/infra_core/engine/droid_review/run_shard.sh").read_text()
        assert 'SHARD_SOFT_TIMEOUT_SEC="${SHARD_SOFT_TIMEOUT_SEC:-2400}"' in script
        job_default = int(
            _load_shards()[True]["workflow_call"]["inputs"]["shard_timeout_minutes"]["default"]
        )
        assert 1500 // 60 < job_default
