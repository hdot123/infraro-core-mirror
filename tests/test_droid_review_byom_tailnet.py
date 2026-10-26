"""droid-review BYOM endpoint routing test（go-github composite action 契约，2026-09-21 起）。

Regression protection: BYOM 配置收敛到 composite action
`.github/actions/setup-byom-go-github`（用户裁定 2026-09-21：凭证不允许
经 job env，只走 action inputs）。两个载体（droid-review.yml 自用 +
droid-review-shards.yml reusable）都必须通过该 action 写 settings.json。

链路（2026-09-27 F3 起 dynamic 分时路由，经 Worker 入口）：CI →
https://gh.lumivane.dpdns.org/custom-opencode-go/v1（域名与路径不变）
→ droid-review-gateway Worker custom domain（CF account 0a14f655，
zone lumivane.dpdns.org 同账号）入口层（IP 门禁 + 峰谷标记 +
非流式 SSE 聚合 + CI_KEY 门禁）→ CF AI Gateway「go-github」/compat +
model=dynamic/droid-review（peak→mimo-v2.6-flash /
off-peak→deepseek-v4.1-flash）。仍走 ai-gateway（用户裁定 CI 只允许
使用 ai-gateway）。

Worker 层 CI_KEY 门禁（2026-09-27 F2）：REQUIRE_CI_KEY=true 后
x-ci-key 是过 Worker 的唯一通行证（缺失/不匹配 → 401
{error:"unauthorized"} 零细节，常量时间比较）。CI 侧凭证链与既有两枚
同构：secrets → action inputs（worker-ci-key）→ step env
（WORKER_CI_KEY）→ settings.json extraHeaders；静态内嵌块零凭证。

baseUrl 域名必须保持 gh.lumivane.dpdns.org（org policy allowedBaseUrls
白名单；2026-09-27 实证 gateway.ai.cloudflare.com 原生域被拒
「Model blocked by organization policy」，run 36283619676）；路径由
Worker 统一接管转发 /compat，CI 侧零路径变更。

2026-09-27 修复史：① /compat 统一入口对非流式响应有 ~9s 首字节超时
（droid-review 两次 run 各 14-15×504；探针：同大请求 /compat 非流式
504@11.9s、流式 200@45.6s、provider-direct 非流式 200@41s）→ 由
Worker 的「改写流式 + 聚合」根治（非流式大请求 200@22.7s 实证）；
② guard Worker（go-github.exa.edu.kg）同日退役删除——它自 9/22 起
就不在实链上，门禁职责现由 droid-review-gateway Worker 在实链入口域
承接。

History: 旧链 lumivane/custom-node01/Kong 已死（NVIDIA_KONG_PROXY_KEY 被
Kong 删除 → 401）；更早的 tailnet-only 端点 hosted runner 不可达；
2026-09-22 曾切 /compat + dynamic/droid-review 动态路由，2026-09-27
因非流式 9s 超时弃用。OpenCode Go 硬性要求 x-opencode-session 头
（缺头 400 MissingSessionID）。
"""

import json

import pytest
import yaml

pytestmark = pytest.mark.integration

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# B-1 收敛：BYOM 调用面单源于 droid-review-shards.yml（自仓壳经 uses 调用，
# 零内联 review-shard job）；壳侧契约 = 调用关系存在（见 TestByomActionContract）。
WORKFLOW_PATHS = [
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
        assert "opencode-go-key" not in with_block, (
            "CF 路由模式（2026-10-18）：CI 不携带 opencode key——Worker 一律剥离"
            "客户端 Authorization，上游认证由 AI Gateway provider 存储 credential 承担"
        )
        assert "go-github-run-token" in with_block, "action 调用必须以 inputs 传入网关 run token"
        assert "worker-ci-key" in with_block, (
            "action 调用必须以 inputs 传入 Worker CI_KEY（2026-09-27 F2："
            "REQUIRE_CI_KEY=true 后 x-ci-key 是过 droid-review-gateway Worker 的唯一通行证）"
        )

    def test_no_secret_env_on_job(self, wf_path):
        """review-shard job must NOT carry credentials in job-level env."""
        data = yaml.safe_load(wf_path.read_text())
        job_env = data["jobs"]["review-shard"].get("env", {}) or {}
        secret_keys = [k for k in job_env if "KEY" in k.upper() or "TOKEN" in k.upper()]
        assert not secret_keys, (
            f"job-level env 不允许携带凭证（发现: {secret_keys}）；凭证只经 action inputs"
        )
        # B-1 收敛：壳侧 shards 调用 job 的 secrets 显式传递面不落 job env
        shell = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/droid-review.yml").read_text(encoding="utf-8")
        )
        assert not (shell["jobs"]["shards"].get("env") or {}), (
            "壳 shards 调用 job 零 env（凭证只经 secrets: 透传）"
        )

    def test_baseurl_points_to_provider_direct_gateway(self):
        """baseUrl must use the provider-direct gateway endpoint (no /compat).

        2026-09-27 F3 交接：域名 gh.lumivane.dpdns.org 从 AI Gateway
        custom domain 解绑后绑给 droid-review-gateway Worker（同 zone
        账号 0a14f655），Worker 统一接管任意路径并转发网关 /compat；
        路径保持 provider-direct 形态不变（CI 侧零路径变更）。域名不可
        改为 gateway.ai.cloudflare.com 原生域（org policy allowedBaseUrls
        白名单，『Model blocked by organization policy』run 36283619676
        实证）。用户裁定 CI 只允许使用 ai-gateway，provider-direct 即
        网关官方接入 URL，非绕过。模型走 dynamic/droid-review 动态路由
        （Worker 注入 cf-aig-metadata.time_slot；peak→mimo-v2.6-flash /
        off-peak→deepseek-v4.1-flash）。
        """
        settings = _load_settings()
        model = settings["customModels"][0]
        assert model["baseUrl"] == ("https://gh.lumivane.dpdns.org/custom-opencode-go/v1"), (
            "BYOM baseUrl must use the provider-direct AI Gateway endpoint on the "
            "gh.lumivane.dpdns.org custom domain (the /compat entry has a ~9s "
            "non-stream TTFB timeout that kills droid-review, 29x504 across two "
            "runs on 2026-09-26)"
        )
        assert "gateway.ai.cloudflare.com" not in model["baseUrl"], (
            "must not use the gateway.ai.cloudflare.com native domain: droid CLI "
            "org policy (allowedBaseUrls) blocks it with 'Model blocked by "
            "organization policy' (2026-09-27 run 36283619676)"
        )
        assert model["model"] == "dynamic/droid-review", (
            "BYOM model must be the dynamic route name (F3 2026-09-27): the "
            "Worker injects cf-aig-metadata.time_slot and the gateway maps "
            "peak->mimo-v2.6-flash / off-peak->deepseek-v4.1-flash"
        )
        assert "/compat" not in model["baseUrl"], (
            "CI ingress path must not be the /compat unified entry: the Worker "
            "owns the domain path and forwards to /compat internally; the raw "
            "/compat entry had the ~9s non-stream TTFB wall (F3 2026-09-27)"
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
        assert "OPENCODE_GO_KEY" not in step_env, (
            "CF 路由模式（2026-10-18）：opencode key 不再进入 CI 链路（Worker 剥离客户端 Authorization）"
        )
        assert "opencode-go-key" not in action.get("inputs", {}), (
            "action 不得再声明 opencode-go-key 输入"
        )
        assert step_env.get("GO_GITHUB_RUN_TOKEN") == "${{ inputs.go-github-run-token }}"
        assert step_env.get("WORKER_CI_KEY") == "${{ inputs.worker-ci-key }}"
        run_text = write_step["run"]
        assert "os.environ['OPENCODE_GO_KEY']" not in run_text, (
            "CF 路由模式：apiKey 为占位常量（cf-route），不从 env 注入真实凭证"
        )
        assert "byom['apiKey'] = 'cf-route'" in run_text, (
            "apiKey 必须为 CF 路由模式占位常量（provider 必填非空约束），无认证语义"
        )
        assert "os.environ['GO_GITHUB_RUN_TOKEN']" in run_text, (
            "cf-aig-authorization must be injected from action step env GO_GITHUB_RUN_TOKEN"
        )

    def test_worker_ci_key_header_from_step_env(self):
        """Worker 层 CI_KEY 门禁头（2026-09-27 F2）。

        语义：REQUIRE_CI_KEY=true 后 x-ci-key 是过 droid-review-gateway
        Worker 的唯一通行证（缺失/不匹配 → 401 {error:"unauthorized"}，
        零细节泄露；常量时间比较在 Worker 侧）。凭证纪律与既有两枚一致：
        值只经 action inputs → step env → settings.json，静态内嵌块零凭证。
        空值省略该头而非发空串——pull_request_target 读 base 的 with: 块，
        过渡窗口内 base 尚无 worker-ci-key 键；缺失由 Worker 侧 fail-closed
        兜底（门禁单一真源 = Worker，CI 侧不做二次判定）。
        """
        action = _load_action()
        assert "worker-ci-key" in action["inputs"], (
            "action 必须声明 worker-ci-key 输入（凭证只经 action inputs）"
        )
        assert action["inputs"]["worker-ci-key"].get("required") is False, (
            "worker-ci-key 必须 required: false——过渡窗口 base workflow 尚无该 "
            "with 键，硬 required 会让本 PR 自身的 droid-review（base workflow "
            "+ head action）在输入校验阶段直接失败"
        )
        write_step = next(
            s for s in action["runs"]["steps"] if s.get("name") == "Write BYOM settings file"
        )
        run_text = write_step["run"]
        assert "extra_headers['x-ci-key']" in run_text, (
            "extraHeaders 必须含 x-ci-key（Worker 层门禁头）"
        )
        assert "os.environ.get('WORKER_CI_KEY'" in run_text, (
            "x-ci-key 值必须取自 action step env WORKER_CI_KEY（不得硬编码值）"
        )
        assert "os.environ.get('WORKER_CI_KEY', '')" in run_text, (
            "缺失时必须走默认空值分支而非 KeyError——过渡窗口 base workflow 尚无 "
            "该 with 键，抛错会让 head action 在写盘前 abort"
        )
        assert "if worker_ci_key:" in run_text, (
            "空值必须走「省略该头」分支（不得发空串头）：门禁单一真源在 Worker，"
            "CI 侧不做二次判定，空值语义 = 不发头由 Worker 401 fail-closed 兜底"
        )
        assert "x-ci-key" not in json.dumps(_load_settings()), (
            "内嵌 settings 块是静态的，不得出现门禁头/凭证（凭证只在 python 注入）"
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
