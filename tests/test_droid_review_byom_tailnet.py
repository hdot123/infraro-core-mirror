"""droid-review BYOM endpoint routing test（go-github composite action 契约，2026-09-21 起）。

Regression protection: BYOM 配置收敛到 composite action
`.github/actions/setup-byom-go-github`（用户裁定 2026-09-21：凭证不允许
经 job env，只走 action inputs）。两个载体（droid-review.yml 自用 +
droid-review-shards.yml reusable）都必须通过该 action 写 settings.json。

链路（2026-09-22 起动态路由形态，实测 E2E 200）：CI →
gh.lumivane.dpdns.org（IP 门禁 Worker：GitHub Actions 段 ∪ [REDACTED-HOST]
出口）→ CF AI Gateway「go-github」@ 3-服务器管理 /compat 统一入口 +
model=dynamic/droid-review（primary=deepseek-v4.1-flash @ opencode-go
provider，custom provider base_url 已含 opencode.ai/zen/go 前缀；
fallback=Workers AI llama）→ deepseek-v4.1-flash。动态路由经 /compat
入口，无需 custom provider 路径前缀段；model id 不含 [REDACTED-HOST] 后缀，
runner-topology gate3 检查不再触发。

History: 旧链 lumivane/custom-node01/Kong 已死（NVIDIA_KONG_PROXY_KEY 被
Kong 删除 → 401）；更早的 tailnet-only 端点 hosted runner 不可达；曾用
custom-opencode-go/zen/go/v1 路径前缀 + custom:...-([REDACTED-HOST]) 注册 id，
2026-09-22 切换动态路由后废弃。OpenCode Go 硬性要求 x-opencode-session
头（缺头 400 MissingSessionID）。
"""

import json

import pytest
import yaml

pytestmark = pytest.mark.integration

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# BYOM routing contract covers two carriers: self repo droid-review.yml + reusable workflow
# droid-review-shards.yml (since M4, memory-core and other thin callers' sharding pipeline uses latter)
WORKFLOW_PATHS = [
    REPO_ROOT / ".github/workflows/droid-review.yml",
    REPO_ROOT / ".github/workflows/droid-review-shards.yml",
]

# Backward compatibility alias (old reference point)
WORKFLOW_PATH = WORKFLOW_PATHS[0]

BYOM_ACTION_STEP_NAME = "Setup BYOM (go-github / DeepSeek V4.1 Flash)"
ACTION_PATH = REPO_ROOT / ".github/actions/setup-byom-go-github/action.yml"


def _get_byom_action_step(workflow_path: Path) -> dict:
    """Locate the step that invokes the BYOM composite action in review-shard job."""
    data = yaml.safe_load(workflow_path.read_text())
    shard_job = data["jobs"]["review-shard"]
    for step in shard_job.get("steps", []):
        if step.get("name") == BYOM_ACTION_STEP_NAME:
            return step
    pytest.fail(
        f"{workflow_path.name}: review-shard job must have a {BYOM_ACTION_STEP_NAME!r} step"
    )


def _load_action() -> dict:
    """Load the composite action definition."""
    return yaml.safe_load(ACTION_PATH.read_text())


def _extract_settings_block_from_action() -> str:
    """Extract the embedded settings JSON (raw-string inside the merge step's python)."""
    action = _load_action()
    write_step = next(
        s for s in action["runs"]["steps"] if s.get("name") == "Write BYOM settings file"
    )
    run_script = write_step["run"]
    marker = 'ENTRY = json.loads(r"""'
    start = run_script.index(marker) + len(marker)
    end = run_script.index('"""', start)
    return run_script[start:end]


def _load_settings() -> dict:
    """Parse embedded settings JSON (indentation is harmless to json.loads)."""
    return json.loads(_extract_settings_block_from_action())


def _action_run_text() -> str:
    action = _load_action()
    return "\n".join(s.get("run", "") for s in action["runs"]["steps"])


@pytest.fixture(params=WORKFLOW_PATHS, ids=[p.name for p in WORKFLOW_PATHS])
def wf_path(request) -> Path:
    return request.param


class TestByomActionContract:
    """Both carriers must write BYOM settings via the composite action (no job env secrets)."""

    def test_byom_action_invoked(self, wf_path):
        """review-shard must call setup-byom-go-github with secret inputs."""
        step = _get_byom_action_step(wf_path)
        uses = step.get("uses", "")
        assert "setup-byom-go-github" in uses, (
            "BYOM 配置必须经 composite action setup-byom-go-github（用户裁定：不允许 job env 携带凭证）"
        )
        with_block = step.get("with", {})
        assert "opencode-go-key" in with_block and "go-github-run-token" in with_block, (
            "action 调用必须以 inputs 传入两份凭证"
        )

    def test_no_secret_env_on_job(self, wf_path):
        """review-shard job must NOT carry credentials in job-level env."""
        data = yaml.safe_load(wf_path.read_text())
        job_env = data["jobs"]["review-shard"].get("env", {}) or {}
        secret_keys = [k for k in job_env if "KEY" in k.upper() or "TOKEN" in k.upper()]
        assert not secret_keys, (
            f"job-level env 不允许携带凭证（发现: {secret_keys}）；凭证只经 action inputs"
        )

    def test_baseurl_points_to_guard_domain(self):
        """baseUrl must use the /compat unified entry (CF AI Gateway dynamic routing).

        CF AI Gateway 动态路由 dynamic/droid-review：请求走 /compat 统一
        入口 + model=dynamic/droid-review，网关按路由名分发（primary=
        deepseek-v4.1-flash @ opencode-go provider，其 base_url 已含
        opencode.ai/zen/go 前缀；fallback=Workers AI llama）。droid 的
        generic-chat-completion-api 自动追加 /chat/completions。无需
        custom provider 路径前缀段（2026-09-22 切换，旧前缀形态废弃）。
        """
        settings = _load_settings()
        model = settings["customModels"][0]
        assert model["baseUrl"] == "https://gh.lumivane.dpdns.org/compat", (
            "BYOM baseUrl must use the go-github guard domain /compat unified "
            "entry (CF AI Gateway dynamic routing), not the raw gateway domain "
            "or custom provider path prefix"
        )
        assert model["model"] == "dynamic/droid-review", (
            "BYOM model must be dynamic/droid-review (CF AI Gateway dynamic "
            "route: primary=deepseek-v4.1-flash@opencode-go, fallback=Workers AI)"
        )

    def test_model_id_is_server_registered_full_id(self):
        """model id 必须是 Factory 服务端注册名（2026-09-22 动态路由形态）。

        droid CLI 对 custom model id 做服务端注册校验：未注册 id 直接
        Invalid model（CI Exec failed，2026-09-21 曾实证无后缀 id 被拒，
        后在 Factory 端注册解决）。动态路由切换（2026-09-22）后使用
        custom:DeepSeek-V4.1-Flash-Go（无 [REDACTED-HOST] 后缀，runner-topology
        gate3 检查不再触发）。action.yml 的 inputs.byom-model default
        与 ENTRY id 必须同步使用注册名。
        """
        settings = _load_settings()
        model = settings["customModels"][0]
        assert model["id"] == "custom:DeepSeek-V4.1-Flash-Go", (
            "BYOM id 必须用服务端注册名（未注册 id droid exec 报 Invalid model）"
        )
        assert "[REDACTED-HOST]" not in model["id"], (
            "动态路由形态 id 不得含 [REDACTED-HOST] 后缀（触发 runner-topology gate3）"
        )
        action = _load_action()
        assert action["inputs"]["byom-model"]["default"] == "custom:DeepSeek-V4.1-Flash-Go", (
            "inputs.byom-model default 必须与 ENTRY id 一致（引擎 -m 读该值）"
        )

    def test_settings_block_is_valid_json(self):
        """Embedded settings block must be valid JSON."""
        settings = _load_settings()
        assert isinstance(settings, dict)
        assert len(settings["customModels"]) == 1

    def test_api_key_is_empty_placeholder_in_embedded_block(self):
        """API key should be empty in the embedded block, injected via python from step env."""
        settings = _load_settings()
        model = settings["customModels"][0]
        assert model["apiKey"] == "", (
            "API key in embedded block should be empty placeholder, real key injected via python script"
        )
        assert "${" not in json.dumps(settings), (
            "embedded block is static; shell variable placeholders must not appear in it"
        )

    def test_merge_write_never_clobbers_existing_models(self):
        """合并写铁律（2026-09-21 事故）：禁止整文件覆盖本地 settings.json。

        事故：调试链路在本机复刻 action 写入，`cat >` 把用户 8 个已有
        customModels 全部清空。合并语义：BYOM 条目恒占 customModels[0]，
        同 id 旧条目原位替换，其余模型原样保留。
        """
        run_text = _action_run_text()
        assert "cat > ~/.factory/settings.json" not in run_text, (
            "禁止 cat > 整文件覆盖（本机执行会清空用户已有模型）"
        )
        assert "> ~/.factory/settings.json" not in run_text, (
            "禁止 shell 重定向整文件覆盖 settings.json"
        )
        assert "[byom] + models" in run_text, "必须为合并写：BYOM 条目置顶，其余模型保留"
        assert "FileNotFoundError" in run_text, (
            "settings 文件不存在时必须按空配置初始化（hosted runner 首跑路径）"
        )

    def test_credentials_via_step_env_only(self):
        """Credentials must flow inputs -> step env -> python, never job env."""
        action = _load_action()
        write_step = next(
            s for s in action["runs"]["steps"] if s.get("name") == "Write BYOM settings file"
        )
        step_env = write_step.get("env", {})
        assert step_env.get("OPENCODE_GO_KEY") == "${{ inputs.opencode-go-key }}"
        assert step_env.get("GO_GITHUB_RUN_TOKEN") == "${{ inputs.go-github-run-token }}"
        run_text = write_step["run"]
        assert "os.environ['OPENCODE_GO_KEY']" in run_text, (
            "apiKey must be injected from action step env OPENCODE_GO_KEY"
        )
        assert "os.environ['GO_GITHUB_RUN_TOKEN']" in run_text, (
            "cf-aig-authorization must be injected from action step env GO_GITHUB_RUN_TOKEN"
        )

    def test_opencode_session_header_required(self):
        """OpenCode Go hard requirement: x-opencode-session must be configured."""
        run_text = _action_run_text()
        assert "x-opencode-session" in run_text, (
            "OpenCode Go hard requirement: x-opencode-session header must be configured"
        )

    def test_byom_model_exported_for_engine(self):
        """Action must export BYOM_MODEL to GITHUB_ENV (engine script reads it)."""
        run_text = _action_run_text()
        assert "BYOM_MODEL=" in run_text and "GITHUB_ENV" in run_text, (
            "action 必须把 BYOM_MODEL 写入 GITHUB_ENV 供引擎脚本读取"
        )
