"""H1 六件套 ①②（h1-classify-capture）契约测试。

三契约面（VAL-H1-001）：
1. 分类指纹映射表：classify_shard_failure.sh 七类指纹（silent_death /
   model_not_allowed / model_unresolvable / auth_401 / rate_limit_429 /
   exit_137 / unclassified 兜底）——行为级（真实 bash 执行）+ 优先级锁。
2. 脱敏正则：redact_secrets.sh 四类凭证样字（Bearer / sk- / x-ci-key /
   apiKey）→ [REDACTED]，幂等、行结构无损；capture_host_log.sh 上限行数
   + 特征行过滤 + 尽力而为语义。
3. 上传列表变更：双 workflow（droid-review.yml + droid-review-shards.yml）
   移除未脱敏全量 stderr（shard-exec-error.log）与 session jsonl
   （.factory/sessions/**），新增 error-classification.txt +
   droid-host-log-redacted.txt；droid-review-debug- 前缀契约不变。
4. run_shard.sh 集成：失败出口产 error_class（E2E，fake droid）；droid
   二进制路径/版本打印（只告警不阻断）。
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine" / "droid_review"
CLASSIFY_HELPER = ENGINE_DIR / "classify_shard_failure.sh"
REDACT_HELPER = ENGINE_DIR / "redact_secrets.sh"
CAPTURE_HELPER = ENGINE_DIR / "capture_host_log.sh"
ENGINE_RUN_SHARD = ENGINE_DIR / "run_shard.sh"
CI_RUN_SHARD = REPO_ROOT / "scripts" / "droid_review" / "run_shard.sh"
CI_PUBLISH_FINDINGS = REPO_ROOT / "scripts" / "droid_review" / "publish_findings.py"

WORKFLOW_SELF = REPO_ROOT / ".github" / "workflows" / "droid-review.yml"
WORKFLOW_SHARDS = REPO_ROOT / ".github" / "workflows" / "droid-review-shards.yml"

# 合成测试样字（非真实凭证，拼接形态构造）——仅用于验证脱敏正则的样字匹配。
SK_SAMPLE = "sk-" + "proj-ABCDEFGH12345678"
SK_SAMPLE2 = "sk-" + "1234567890abcdef"


def run_bash(script: Path, args: list[str], env: dict | None = None, cwd: Path | None = None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(script), *args],
        env=full_env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ════════════════════════════════════════════════════════════════════════
# Part A: 分类指纹映射表（行为级）
# ════════════════════════════════════════════════════════════════════════


class TestClassifyFingerprintTable:
    """七类指纹映射表：stderr/stdout 内容 → error_class。"""

    def _classify(
        self,
        tmp_path: Path,
        *,
        droid_exit: str = "1",
        start_offset: int = 5,
        stderr_text: str = "",
        stdout_text: str = "",
    ) -> tuple[str, subprocess.CompletedProcess]:
        err = tmp_path / "shard-exec-error.log"
        out = tmp_path / "droid-exec-stdout.json"
        err.write_text(stderr_text, encoding="utf-8")
        out.write_text(stdout_text, encoding="utf-8")
        cls_file = tmp_path / "error-classification.txt"
        summary = tmp_path / "step-summary.md"
        import time

        start = int(time.time()) - start_offset
        r = run_bash(
            CLASSIFY_HELPER,
            ["0", droid_exit, str(start), "/usr/local/bin/droid", "0.201.0", str(err), str(out)],
            env={
                "ERROR_CLASS_FILE": str(cls_file),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            cwd=tmp_path,
        )
        assert r.returncode == 0, f"classifier must not fail itself: {r.stderr}"
        text = cls_file.read_text(encoding="utf-8")
        got = next(
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("error_class:")
        )
        return got, r

    @pytest.mark.parametrize(
        ("stderr_text", "expected"),
        [
            ("MetaError: Model not allowed for model dynamic/droid-review", "model_not_allowed"),
            ("Error: Invalid model id dynamic/droid-review", "model_unresolvable"),
            ("all fallback models failed, fallback exhausted", "model_unresolvable"),
            ("{error: unauthorized} (HTTP 401)", "auth_401"),
            ("BYOK Error: 429 usage allocated quota exceeded", "rate_limit_429"),
        ],
    )
    def test_01_fingerprint_mapping(self, tmp_path, stderr_text, expected):
        got, _ = self._classify(tmp_path, stderr_text=stderr_text)
        assert got == expected, f"stderr={stderr_text!r} → expected {expected}, got {got}"

    def test_02_exit_137_fingerprint(self, tmp_path):
        got, _ = self._classify(tmp_path, droid_exit="137", stderr_text="", stdout_text="")
        assert got == "exit_137"

    def test_03_silent_death_exit1_both_empty_under_10s(self, tmp_path):
        got, _ = self._classify(tmp_path, droid_exit="1", start_offset=1)
        assert got == "silent_death"

    def test_04_silent_death_requires_both_streams_empty(self, tmp_path):
        """stderr 有内容 → 不判 silent_death（降 unclassified 兜底）。"""
        got, _ = self._classify(
            tmp_path, droid_exit="1", start_offset=1, stderr_text="some stack trace\n"
        )
        assert got == "unclassified"

    def test_05_silent_death_requires_under_10s(self, tmp_path):
        """exit 1 + 双流空但耗时 ≥10s → 不判 silent_death。"""
        got, _ = self._classify(tmp_path, droid_exit="1", start_offset=60)
        assert got == "unclassified"

    def test_06_fingerprint_beats_silent_death(self, tmp_path):
        """优先级：词指纹命中优先于秒死特征（401 且 <10s → auth_401）。"""
        got, _ = self._classify(tmp_path, droid_exit="1", start_offset=1, stderr_text="HTTP 401\n")
        assert got == "auth_401"

    def test_07_exit137_beats_content_fingerprints(self, tmp_path):
        """优先级：137 是硬信号，内容指纹不覆盖（137 + 429 文本 → exit_137）。"""
        got, _ = self._classify(tmp_path, droid_exit="137", stderr_text="429 quota\n")
        assert got == "exit_137"

    def test_08_unclassified_fallback_never_drops(self, tmp_path):
        """指纹全未命中 → unclassified（非空降兜底，fail-closed 语义保留）。"""
        got, r = self._classify(tmp_path, droid_exit="1", start_offset=60, stderr_text="mystery\n")
        assert got == "unclassified"
        assert "fail" not in r.stderr.lower()

    def test_09_classifier_never_changes_exit_code(self, tmp_path):
        """分类器是观测面：任何输入 exit 0（run_shard 调用处另有 || true 双保险）。"""
        err = tmp_path / "e.log"
        err.write_text("x", encoding="utf-8")
        r = run_bash(
            CLASSIFY_HELPER,
            ["0", "1", "0", "/usr/local/bin/droid", "0.201.0", str(err)],
            env={
                "ERROR_CLASS_FILE": str(tmp_path / "c.txt"),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md"),
            },
            cwd=tmp_path,
        )
        assert r.returncode == 0

    def test_10_writes_step_summary_and_metadata(self, tmp_path):
        """step summary 落盘：error_class + droid_bin/version 元数据表。"""
        err = tmp_path / "e.log"
        err.write_text("MetaError: Model not allowed\n", encoding="utf-8")
        summary = tmp_path / "s.md"
        cls_file = tmp_path / "c.txt"
        run_bash(
            CLASSIFY_HELPER,
            ["3", "1", "0", "/usr/local/bin/droid", "0.201.0", str(err)],
            env={
                "ERROR_CLASS_FILE": str(cls_file),
                "GITHUB_STEP_SUMMARY": str(summary),
                "RUN_ID": "12345",
            },
            cwd=tmp_path,
        )
        cls_text = cls_file.read_text(encoding="utf-8")
        assert "error_class: model_not_allowed" in cls_text
        assert "droid_bin: /usr/local/bin/droid" in cls_text
        assert "droid_version: 0.201.0" in cls_text
        assert "shard_id: 3" in cls_text
        assert "run_id: 12345" in cls_text
        sum_text = summary.read_text(encoding="utf-8")
        assert "error_class" in sum_text and "model_not_allowed" in sum_text
        assert "droid_bin" in sum_text


# ════════════════════════════════════════════════════════════════════════
# Part B: 脱敏正则（redact_secrets.sh 行为级）
# ════════════════════════════════════════════════════════════════════════


class TestRedactionRegexes:
    """四类凭证样字 → [REDACTED]；幂等；行结构无损。"""

    def _redact(self, text: str) -> str:
        r = subprocess.run(
            ["bash", str(REDACT_HELPER)],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr
        return r.stdout

    def test_01_bearer_token_redacted(self):
        out = self._redact("Authorization: Bearer abc123XYZ_def-098\n")
        assert "abc123XYZ" not in out
        assert "[REDACTED]" in out

    def test_02_lowercase_bearer_redacted(self):
        out = self._redact("auth: bearer tokenvalue77\n")
        assert "tokenvalue77" not in out

    def test_03_sk_key_redacted(self):
        out = self._redact('{"key": "%s"}\n' % SK_SAMPLE)
        assert SK_SAMPLE not in out
        assert "[REDACTED]" in out

    def test_04_x_ci_key_header_redacted(self):
        out = self._redact("x-ci-key: wsecret77\n")
        assert "wsecret77" not in out
        assert "x-ci-key: [REDACTED]" in out

    def test_05_x_ci_key_quoted_redacted(self):
        out = self._redact('x-ci-key: "wq8899"\n')
        assert "wq8899" not in out

    def test_06_apikey_forms_redacted(self):
        for line in (
            "apiKey: k777\n",
            "api_key=abc999\n",
            'apiKey = "quoted9"\n',
        ):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            for secret in ("k777", "abc999", "quoted9"):
                assert secret not in out

    def test_07_clean_lines_preserved(self):
        src = "plain log line\nMetaError: Model not allowed\nanother line\n"
        out = self._redact(src)
        assert out == src, "无凭证行必须原样透传（行结构无损）"

    def test_08_idempotent(self):
        once = self._redact("Authorization: Bearer abc777 and x-ci-key: w888\n")
        twice = self._redact(once)
        assert once == twice, "二次脱敏必须幂等"

    def test_09_redaction_never_leaves_partial_secret(self):
        out = self._redact("Bearer aa11bb22cc33 and key %s end\n" % SK_SAMPLE2)
        for frag in ("aa11bb22", SK_SAMPLE2[:12]):
            assert frag not in out


class TestCaptureHostLog:
    """宿主日志脱敏采集：特征行过滤 + 脱敏 + 上限 + 尽力而为。"""

    def _capture(self, tmp_path: Path, log_text: str, max_lines: str = "50") -> str:
        log = tmp_path / "droid-log-single.log"
        log.write_text(log_text, encoding="utf-8")
        out = tmp_path / "droid-host-log-redacted.txt"
        r = run_bash(
            CAPTURE_HELPER,
            [str(out)],
            env={"DROID_LOG_FILE": str(log), "CAPTURE_MAX_LINES": max_lines},
            cwd=tmp_path,
        )
        assert r.returncode == 0, f"capture is best-effort, must exit 0: {r.stderr}"
        return out.read_text(encoding="utf-8")

    def test_01_captures_metaerror_and_redacts(self, tmp_path):
        log = (
            "boot version 0.201.0\n"
            "sending x-ci-key: sample77x to gateway\n"
            "MetaError: Model not allowed for model dynamic/x\n"
        )
        out = self._capture(tmp_path, log)
        assert "MetaError" in out
        assert "sample77x" not in out
        assert "[REDACTED]" in out

    def test_02_line_cap_enforced(self, tmp_path):
        log = "MetaError: one\n" + "\n".join(f"noise {i}" for i in range(300))
        out = self._capture(tmp_path, log, max_lines="10")
        assert len(out.splitlines()) <= 10

    def test_03_missing_log_is_best_effort(self, tmp_path):
        out = tmp_path / "cap.txt"
        r = run_bash(
            CAPTURE_HELPER,
            [str(out)],
            env={"DROID_LOG_FILE": str(tmp_path / "nonexistent.log")},
            cwd=tmp_path,
        )
        assert r.returncode == 0
        assert out.exists(), "尽力而为：日志缺失也要产出空文件供 artifact 上传"

    def test_04_no_match_produces_empty_file(self, tmp_path):
        out = self._capture(tmp_path, "nothing relevant here at all\n" * 10)
        assert out.strip() == ""


# ════════════════════════════════════════════════════════════════════════
# Part C: 上传列表变更（双 workflow）
# ════════════════════════════════════════════════════════════════════════


def _upload_step(workflow_path: Path) -> dict:
    data = yaml.safe_load(workflow_path.read_text())
    review = data["jobs"]["review-shard"]
    for step in review["steps"]:
        if str(step.get("uses", "")).startswith("actions/upload-artifact"):
            return step
    raise AssertionError(f"upload-artifact step not found in {workflow_path.name}")


class TestUploadListReduction:
    """圆3 暴露面收敛：artifact 只携带脱敏内容。"""

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_01_raw_stderr_removed(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert "shard-exec-error.log" not in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_02_session_jsonl_removed(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert ".factory/sessions" not in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_03_classification_files_added(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert "error-classification.txt" in paths
        assert "droid-host-log-redacted.txt" in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_04_prefix_contract_preserved(self, workflow_path):
        """watchdog quota-sweep 依赖 droid-review-debug- 前缀（NEVER break）。"""
        name = _upload_step(workflow_path)["with"]["name"]
        assert name.startswith("droid-review-debug-")

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_05_include_hidden_files_off(self, workflow_path):
        """隐藏目录面移除后 include-hidden-files 必须关闭（最小上传面）。"""
        assert _upload_step(workflow_path)["with"].get("include-hidden-files") is not True

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_06_collect_transcripts_step_removed(self, workflow_path):
        data = yaml.safe_load(workflow_path.read_text())
        review = data["jobs"]["review-shard"]
        names = [s.get("name", "") for s in review["steps"]]
        assert not any("Collect debug transcripts" in n for n in names)

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SELF, WORKFLOW_SHARDS])
    def test_07_helpers_synced_alongside_engine_script(self, workflow_path):
        """失败分类/脱敏采集助手必须随引擎副本同步（stale pin 下诊断面完整）。"""
        raw = workflow_path.read_text()
        for helper in (
            "classify_shard_failure.sh",
            "capture_host_log.sh",
            "redact_secrets.sh",
        ):
            assert helper in raw, f"{workflow_path.name} 缺少助手同步: {helper}"


# ════════════════════════════════════════════════════════════════════════
# Part D: run_shard.sh 集成（失败出口 + droid 路径/版本打印）
# ════════════════════════════════════════════════════════════════════════

FAKE_DROID_PY = r"""#!/usr/bin/env python3
import json, os, sys

with open(os.environ["FAKE_DROID_CONFIG"]) as f:
    cfg = json.load(f)

args = sys.argv[1:]
if "--version" in args:
    print(cfg.get("version", "droid 0.201.0"))
    sys.exit(0)

mode = cfg["mode"]
if mode == "meta_not_allowed":
    sys.stderr.write("MetaError: Model not allowed for model dynamic/droid-review\n")
    sys.exit(1)
if mode == "silent":
    sys.exit(1)
if mode == "auth_401":
    sys.stderr.write("{error: unauthorized} (HTTP 401)\n")
    sys.exit(1)
if mode == "success":
    print(json.dumps({"shard_id": 0, "findings": []}))
    sys.exit(0)
sys.exit(1)
"""


class TestRunShardFailureClassification:
    """run_shard.sh 失败出口 E2E：fake droid → error_class 落盘。"""

    @pytest.fixture
    def shard_sandbox(self, tmp_path: Path) -> Path:
        """最小 workspace：prompt + publish_findings 副本 + head-src git diff。"""
        ws = tmp_path / "ws"
        (ws / ".github" / "review").mkdir(parents=True)
        (ws / ".github" / "review" / "shard-review-prompt.md").write_text(
            "Review the shard. Return only valid JSON.\n", encoding="utf-8"
        )
        scripts_dir = ws / "scripts" / "droid_review"
        scripts_dir.mkdir(parents=True)
        shutil.copy(CI_PUBLISH_FINDINGS, scripts_dir / "publish_findings.py")

        head = ws / "head-src"
        head.mkdir()
        git_cfg = tmp_path / "git-config"
        git_cfg.write_text("", encoding="utf-8")

        def git(*args: str) -> str:
            env = os.environ.copy()
            env["GIT_CONFIG_GLOBAL"] = str(git_cfg)
            env["GIT_CONFIG_SYSTEM"] = str(git_cfg)
            r = subprocess.run(
                ["git", "-C", str(head), *args], env=env, check=True, capture_output=True, text=True
            )
            return r.stdout.strip()

        git("init", "-q")
        (head / "a.py").write_text("def f(x):\n    return x / 0\n", encoding="utf-8")
        git("add", "a.py")
        git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base")
        base_sha = git("rev-parse", "HEAD")
        (head / "a.py").write_text("def f(x):\n    return x // 0\n", encoding="utf-8")
        git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-am", "head")
        head_sha = git("rev-parse", "HEAD")

        # fake droid on PATH
        bin_dir = ws / "fakebin"
        bin_dir.mkdir(exist_ok=True)
        droid = bin_dir / "droid"
        droid.write_text(FAKE_DROID_PY, encoding="utf-8")
        droid.chmod(droid.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        cfg = ws / "fake-droid-config.json"
        cfg.write_text(
            json.dumps({"mode": "meta_not_allowed", "version": "droid 0.201.0"}), encoding="utf-8"
        )

        (ws / "hostlog.txt").write_text(
            "MetaError: routing table stale\nAuthorization: Bearer secretparts99\n",
            encoding="utf-8",
        )

        (ws / "env-file").write_text(
            "\n".join(
                [
                    f"GITHUB_WORKSPACE={ws}",
                    "SHARD_ID=0",
                    'SHARD_FILES=["a.py"]',
                    f"BASE_REF={base_sha}",
                    f"HEAD_REF={head_sha}",
                    f"MERGE_BASE={base_sha}",
                    "RUN_ID=777000",
                    f"FAKE_DROID_CONFIG={cfg}",
                    f"DROID_LOG_FILE={ws / 'hostlog.txt'}",
                    "FACTORY_SESSIONS_DIR=" + str(ws / "no-sessions"),
                ]
            ),
            encoding="utf-8",
        )
        return ws

    def _run(self, sandbox: Path, mode: str) -> subprocess.CompletedProcess:
        cfg = sandbox / "fake-droid-config.json"
        cfg.write_text(json.dumps({"mode": mode, "version": "droid 0.201.0"}), encoding="utf-8")
        env = os.environ.copy()
        for line in (sandbox / "env-file").read_text().splitlines():
            k, _, v = line.partition("=")
            env[k] = v
        env["PATH"] = f"{sandbox / 'fakebin'}:{env['PATH']}"
        env["GIT_CONFIG_GLOBAL"] = str(sandbox.parent / "git-config")
        env["GIT_CONFIG_SYSTEM"] = str(sandbox.parent / "git-config")
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["GITHUB_STEP_SUMMARY"] = str(sandbox / "step-summary.md")
        return subprocess.run(
            ["bash", str(CI_RUN_SHARD)],
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_01_model_not_allowed_e2e(self, shard_sandbox):
        """MetaError Model not allowed → exit 非 0 + error_class=model_not_allowed
        + 脱敏 stderr 展示 + 分类入 step summary。"""
        r = self._run(shard_sandbox, "meta_not_allowed")
        assert r.returncode != 0, "fail-closed 语义保留"
        combined = r.stdout + r.stderr
        assert "error_class=model_not_allowed" in combined
        cls_file = shard_sandbox / "error-classification.txt"
        assert cls_file.exists(), "失败出口必须落盘 error-classification.txt"
        assert "error_class: model_not_allowed" in cls_file.read_text()
        assert "droid_bin:" in cls_file.read_text()
        summary = (shard_sandbox / "step-summary.md").read_text()
        assert "model_not_allowed" in summary

    def test_02_silent_death_e2e(self, shard_sandbox):
        """exit 1 + 双流空 + <10s → silent_death。"""
        r = self._run(shard_sandbox, "silent")
        assert r.returncode != 0
        assert "error_class=silent_death" in (r.stdout + r.stderr)
        cls_file = shard_sandbox / "error-classification.txt"
        assert "error_class: silent_death" in cls_file.read_text()

    def test_03_auth_401_e2e(self, shard_sandbox):
        r = self._run(shard_sandbox, "auth_401")
        assert r.returncode != 0
        assert "error_class=auth_401" in (r.stdout + r.stderr)

    def test_04_droid_binary_path_and_version_printed(self, shard_sandbox):
        """droid 二进制路径与版本必须打印（Opus C4 相邻项，只告警不阻断）。"""
        r = self._run(shard_sandbox, "meta_not_allowed")
        combined = r.stdout + r.stderr
        assert "droid binary:" in combined
        assert "0.201.0" in combined

    def test_05_stderr_displayed_redacted_in_job_log(self, shard_sandbox):
        """失败出口展示 stderr 时必须先脱敏（Bearer 样字不得原文进 job log）。"""
        (shard_sandbox / "hostlog.txt").write_text(
            "MetaError: Model not allowed for model dynamic/x\n"
            "Authorization: Bearer joblogsecret55\n",
            encoding="utf-8",
        )
        r = self._run(shard_sandbox, "meta_not_allowed")
        combined = r.stdout + r.stderr
        assert "joblogsecret55" not in combined, "stderr 原文凭证不得进 job log"
        assert "[REDACTED]" in combined or "Model not allowed" in combined

    def test_06_success_path_untouched(self, shard_sandbox):
        """成功路径零回归：findings 正常产出，不产 error-classification。"""
        r = self._run(shard_sandbox, "success")
        assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        out = json.loads((shard_sandbox / "findings-shard-0.json").read_text())
        assert out == {"shard_id": 0, "findings": []}
        assert not (shard_sandbox / "error-classification.txt").exists()


class TestRunShardScriptContract:
    """文本级契约：run_shard.sh 结构锁（防回退）。"""

    def test_01_helpers_referenced(self):
        content = ENGINE_RUN_SHARD.read_text()
        for helper in (
            "classify_shard_failure.sh",
            "capture_host_log.sh",
            "redact_secrets.sh",
        ):
            assert helper in content, f"run_shard.sh must reference {helper}"

    def test_02_classification_calls_guaranteed_nonblocking(self):
        """分类调用必须带 || true（观测面不改写主退出码，run_shard 自身 set -e 安全）。"""
        content = ENGINE_RUN_SHARD.read_text()
        count = content.count('CLASSIFY_HELPER"') + content.count("CLASSIFY_HELPER ")
        assert count >= 4, "四个失败出口都必须调用分类器"
        for line in content.splitlines():
            if 'bash "$CLASSIFY_HELPER"' in line:
                assert line.rstrip().endswith("|| true"), f"分类调用必须 || true: {line}"

    def test_03_engine_and_ci_wrapper_consistent(self):
        """CI wrapper 是引擎副本的薄委托：引擎文件存在且 wrapper exec 引擎路径。"""
        assert ENGINE_RUN_SHARD.exists()
        wrapper = CI_RUN_SHARD.read_text()
        assert 'exec bash "$ENGINE_SCRIPT"' in wrapper
