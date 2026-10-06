"""H1 六件套 ①②（h1-classify-capture）契约测试。

三契约面（VAL-H1-001）：
1. 分类指纹映射表：classify_shard_failure.sh 七类指纹（silent_death /
   model_not_allowed / model_unresolvable / auth_401 / rate_limit_429 /
   exit_137 / unclassified 兜底）——行为级（真实 bash 执行）+ 优先级锁。
2. 脱敏正则：redact_secrets.sh 四类凭证样字（Bearer / sk- / x-ci-key /
   apiKey）→ [REDACTED]，幂等、行结构无损；引号 JSON 键形态（"apiKey":"x"）
   与键名全大写形态（API_KEY=）同灭（sonnet 三审 P1 回归锁）；
   capture_host_log.sh 上限行数 + 特征行过滤 + 尽力而为语义。
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

from tests.shard_sandbox_helpers import init_two_commit_repo

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

    def test_10_quoted_json_key_forms_redacted(self):
        """引号 JSON 键形态（键后闭引号）→ [REDACTED]。

        sonnet 三审 P1 实证：{"apiKey":"x"} / {"x-ci-key":"y"} 曾原样输出，
        活密钥可随 droid-host-log-redacted.txt artifact 与 job log 泄漏。
        issue #204 P3①：补结构保留断言——键前缀与闭合 } 必须存活到终行，
        整行抹掉的实现不能通过（exact 断言 + 幂等锁）。
        """
        cases = [
            ('{"apiKey":"plainsecret777"}\n', '{"apiKey":"[REDACTED]"}\n'),
            ('{"apiKey": "plainsecret777"}\n', '{"apiKey": "[REDACTED]"}\n'),
            ('{"x-ci-key":"plainsecret777"}\n', '{"x-ci-key":"[REDACTED]"}\n'),
            ('{"x-ci-key" : "plainsecret777"}\n', '{"x-ci-key" : "[REDACTED]"}\n'),
            ("'apiKey': 'plainsecret777'\n", "'apiKey': \"[REDACTED]\"\n"),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"{line!r} -> {out!r} (expected {expected!r})"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_11_api_key_uppercase_form_redacted(self):
        """API_KEY= 全大写环境变量形态 → [REDACTED]（键名任意大小写组合）。"""
        for line in (
            "API_KEY=plainsecret777\n",
            'API_KEY="plainsecret777"\n',
            "ApiKey=plainsecret777\n",
            "API_KEY: plainsecret777\n",
        ):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            assert "plainsecret777" not in out

    def test_12_existing_unquoted_forms_not_regressed(self):
        """既有 apiKey= / apiKey: 混合大小写形态不回归（三审回归锁反面）。"""
        for line in (
            "apiKey=plainsecret777\n",
            "apiKey: plainsecret777\n",
            "api_key=plainsecret777\n",
            "x-ci-key: plainsecret777\n",
        ):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            assert "plainsecret777" not in out

    def test_13_escaped_quote_value_fully_redacted(self):
        """JSON 转义引号值整值脱敏（值类须支持 \\" 转义，否则尾部残泄）。

        圆10 裁决：} 属值材料（出类会在 } 处截断残泄），闭合括号一并吞没，
        故输出不带尾随 }。
        """
        out = self._redact('{"apiKey":"esc\\"apedSecret12"}\n')
        assert "apedSecret12" not in out
        assert "[REDACTED]" in out
        assert out.startswith('{"apiKey":')

    def test_14_unterminated_quote_value_no_plaintext_tail(self):
        """引号未闭合值不整值泄漏；输出不带引号的 [REDACTED] 令牌。"""
        out = self._redact('apiKey="unterminated_secret\n')
        assert "unterminated_secret" not in out
        assert out == "apiKey=[REDACTED]\n"
        assert self._redact(out) == out, "脱敏输出必须幂等"

    def test_15_identifier_suffix_key_redacted(self):
        """API_KEYS= / apiKeyName= 键族值一并脱敏（过滤器宁多脱不漏）。"""
        for line in ("API_KEYS=[a,b,c_secret]\n", "apiKeyName=secretname44\n"):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            assert "secret" not in out

    def test_16_new_forms_keep_structure_and_idempotent(self):
        """新形态键结构保留 + 幂等锁（仅断言 [REDACTED] 存在挡不住整行丢弃）。"""
        cases = [
            ('{"apiKey":"plainsecret777"}\n', '{"apiKey":'),
            ("API_KEY=plainsecret777\n", "API_KEY="),
            ('{"x-ci-key":"plainsecret777"}\n', '{"x-ci-key":'),
        ]
        for line, key_prefix in cases:
            out = self._redact(line)
            assert "[REDACTED]" in out
            assert "plainsecret777" not in out
            assert out.startswith(key_prefix), f"键与结构必须保留: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_17_backslash_bearing_value_no_tail_leak(self):
        """裸值含反斜杠整值脱敏（POSIX 括号内 \\ 是字面量，入排除类会在 \\ 处截断）。"""
        for line in ("apiKey=tok\\EEE444\n", "x-ci-key: a\\b\n"):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            assert out.endswith("[REDACTED]\n"), f"尾部残泄: {line!r} -> {out!r}"

    def test_18_xci_family_identifier_suffix_keys_redacted(self):
        """x-ci-keys= / x-ci-key-id= 键族命中（与 api 族尾随标识符对称）。"""
        for line in ("x-ci-keys=tok_AAA111\n", "x-ci-key-id=tok_BBB222\n"):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r}"
            assert "tok_" not in out

    def test_19_querystring_ampersand_context_preserved(self):
        """api 族裸值在 & 处截断——查询串行内上下文（&mode=fast）不丢。"""
        out = self._redact("https://h/?apiKey=abc123&mode=fast\n")
        assert "abc123" not in out
        assert "apiKey=[REDACTED]&mode=fast" in out

    def test_20_xci_comma_merged_header_whole_value(self):
        """xci 族逗号不截断——合并头 x-ci-key: tok1, tok2 与紧连 aaa,bbb 整值灭。"""
        for line in ("x-ci-key: tok1, tok2\n", "x-ci-key: aaa,bbb\n"):
            out = self._redact(line)
            assert out == "x-ci-key: [REDACTED]\n", f"尾部残泄: {line!r} -> {out!r}"
            assert self._redact(out) == out

    def test_21_uppercase_authorization_scheme_redacted(self):
        """AUTHORIZATION: BEARER（全大写头名+scheme）同灭——HTTP 头大小写不敏感。"""
        for line in ("AUTHORIZATION: BEARER tokZ\n", "auth: BEARER lowertok9\n"):
            out = self._redact(line)
            assert out.endswith("[REDACTED]\n"), f"not redacted: {line!r} -> {out!r}"
            assert "tokZ" not in out and "lowertok9" not in out

    def test_22_bare_value_embedded_quotes_whole_swallow(self):
        """裸值内嵌引号（ab\"cd / ab'cd）整值吞没——值类含引号，出类会截断残泄。"""
        for line in ('apiKey=ab"cd\n', "apiKey=ab'cd\n", 'x-ci-key: ab"cd\n'):
            out = self._redact(line)
            assert out.endswith("[REDACTED]\n"), f"尾部残泄: {line!r} -> {out!r}"
            assert "cd" not in out.replace("[REDACTED]", "")

    def test_23_unterminated_single_quote_no_space_tail(self):
        """单引号未终结值空格尾巴整吞（未终结分支对称吞至行尾/停止符）。"""
        out = self._redact("apiKey='abc def ghi\n")
        assert out == "apiKey=[REDACTED]\n", f"空格尾巴残泄: {out!r}"
        assert self._redact(out) == out

    def test_24_comma_continuation_items_allow_inner_spaces(self):
        """逗号续项允许内部空格（api=abc, def ghi 与 xci tok1, tok2 tok3 整值灭）。"""
        for line in ("apiKey=abc, def ghi\n", "x-ci-key: tok1, tok2 tok3\n"):
            out = self._redact(line)
            assert out.endswith("[REDACTED]\n"), f"续项尾巴残泄: {line!r} -> {out!r}"
            assert "ghi" not in out.replace("[REDACTED]", "") and "tok3" not in out

    def test_25_nested_structured_value_whole_swallow(self):
        """结构化嵌套值（值为对象/数组）整值吞至 ; ) 或行尾——内层密钥不残泄。"""
        for line, frag in (
            ('{"apiKey": {"token": "innerSecret99"}}\n', "innerSecret99"),
            ('{"apiKey": [1,2,"arrSecret7"]}\n', "arrSecret7"),
            ('apiKey={"a":"b&c"}\n', "&"),
        ):
            out = self._redact(line)
            assert frag not in out, f"残泄: {line!r} -> {out!r}"
            assert "[REDACTED]" in out
            assert self._redact(out) == out, f"幂等: {line!r}"

    def test_26_quoted_scheme_token_context_preserved(self):
        """兜底规则冒号后整行吞至行尾（头行全是凭证，宁多脱不漏）。"""
        for line in (
            'Authorization: Basic "dXNxIGRlZg" trailing\n',
            "Authorization: Basic a)b\n",
            "Authorization: 123456\n",
            'Authorization: Digest username="admin" password="secret"\n',
        ):
            out = self._redact(line)
            assert out == "Authorization: [REDACTED]\n", f"{line!r} -> {out!r}"
            assert self._redact(out) == out

    def test_26b_digit_leading_authorization_whole_swallow(self):
        """纯数字开头裸 token（Authorization: 123456）同灭——scheme 可省。"""
        out = self._redact("Authorization: 9zyx8wv\n")
        assert out == "Authorization: [REDACTED]\n"
        assert self._redact(out) == out

    def test_26c_digest_multi_credential_line_whole_swallow(self):
        """Digest 多凭据段整段灭——scheme+首段后整吞至行尾，password= 不残泄。"""
        out = self._redact('Authorization: Digest username="admin" password="secret"\n')
        assert out == "Authorization: [REDACTED]\n", f"多凭据段残泄: {out!r}"
        assert self._redact(out) == out

    def test_26d_bearer_comma_continuation(self):
        """Bearer 逗号续项允许内部空格（Bearer abc, def 整值灭）。"""
        for line in ("Bearer abc, def\n", "Authorization: Bearer abc, def ghi\n"):
            out = self._redact(line)
            assert out.endswith("[REDACTED]\n"), f"续项尾巴残泄: {line!r} -> {out!r}"

    def test_26e_structured_array_value_whole_swallow(self):
        """结构化数组值（[ 起始）整值吞——与对象值对称，注释契约一致。"""
        out = self._redact('{"apiKey": [ "mysecret" ]}\n')
        assert "mysecret" not in out
        assert "[REDACTED]" in out

    def test_26f_quoted_authorization_key_whole_swallow(self):
        """JSON 序列化引号键 "authorization": 同灭（HTTP-2 小写形态）。"""
        out = self._redact('{"authorization":"Basic dGV4dA=="}\n')
        assert "dGV4dA==" not in out
        assert "[REDACTED]" in out
        assert self._redact(out) == out

    def test_26g_close_brace_is_value_material(self):
        """裸值内嵌 } 整值吞（出类会在 } 处截断残泄 cd）——api/xci/bearer。"""
        for line in ("apiKey=ab}cd\n", "Bearer ab}cd\n", "x-ci-key: ab}cd\n"):
            out = self._redact(line)
            assert out.endswith("[REDACTED]\n"), f"尾部残泄: {line!r} -> {out!r}"
            assert "cd" not in out.replace("[REDACTED]", "")

    def test_26h_authz_env_var_separator(self):
        """AUTHORIZATION= 环境变量分隔符形态整行灭（bot 圆14 P1）。"""
        for line in (
            "AUTHORIZATION=token ghp_abc123def456\n",
            "AUTHORIZATION=Basic dXNlcjpwYXNz\n",
        ):
            out = self._redact(line)
            assert out == "AUTHORIZATION=[REDACTED]\n", f"{line!r} -> {out!r}"
            assert self._redact(out) == out

    def test_26i_bearer_multiple_quoted_tokens(self):
        """Bearer 引号 token 空格续项整吞（"t1" "t2" 双 token 同灭）。"""
        out = self._redact('Bearer "t1" "t2"\n')
        assert out == "Bearer [REDACTED]\n", f"续引号 token 残泄: {out!r}"
        assert self._redact(out) == out

    def test_26j_structured_semicolon_is_value_material(self):
        """结构化值内分号整吞（"b;ctail" 截断即残泄——圆14 P3）。"""
        out = self._redact('apiKey={"a":"b;ctail"}\n')
        assert out == "apiKey=[REDACTED]\n", f"; 截断残泄: {out!r}"
        assert self._redact(out) == out

    def test_26k_authz_array_value_whole_swallow(self):
        """authorization 数组值（[ 开头无空格）整行灭（圆16 P1）。"""
        for line in ('{"authorization":["tokSECRET"]}\n', 'AUTHORIZATION=["tok"]\n'):
            out = self._redact(line)
            assert "tokSECRET" not in out and "tok" not in out.replace("[REDACTED]", "")
            assert "[REDACTED]" in out
            assert self._redact(out) == out

    def test_26l_xci_structured_paren_is_value_material(self):
        """xci 结构化值内右括号整吞（"b)c" 截断即残泄——圆16 P1）。"""
        out = self._redact('x-ci-key: {"a":"b)c"}\n')
        assert out == "x-ci-key: [REDACTED]\n", f") 截断残泄: {out!r}"
        assert self._redact(out) == out

    def test_26m_api_structured_semicolon_paren_both_material(self):
        """api 结构化值 ; 与 ) 都整吞（"b;c)d" 双截断点——圆16 P2）。"""
        out = self._redact('apiKey={"a":"b;c)d"}\n')
        assert out == "apiKey=[REDACTED]\n", f";) 截断残泄: {out!r}"
        assert self._redact(out) == out

    def test_26n_xci_paired_quote_multi_token(self):
        """xci 引号值空格续项整吞（"t1" "t2" 首遍即灭——圆16 P2）。

        成对引号输出带引号存活到终行（issue #204 P3② 契约：占位字节
        机制使 "[REDACTED]" 不再被后层裸值规则吞为无引号形态）。
        """
        out = self._redact('x-ci-key: "t1" "t2"\n')
        assert out == 'x-ci-key: "[REDACTED]"\n', f"续引号 token 残泄: {out!r}"
        assert self._redact(out) == out

    def test_27_tab_separators_matched(self):
        """tab 分隔形态同灭（分隔符用 [[:space:]]，字面空格会被 tab 绕过）。"""
        for line in ("Bearer\ttokTAB123\n", "AUTHORIZATION:\tBEARER tokZ\n"):
            out = self._redact(line)
            assert "[REDACTED]" in out, f"not redacted: {line!r} -> {out!r}"
            assert "tokTAB123" not in out and "tokZ" not in out

    def test_28_bearer_colon_equals_separator_forms_redacted(self):
        """Bearer scheme 后 : / = 分隔符形态同灭（issue #204 P1）。

        旧规则只认 scheme 后空白（[[:space:]]+），BEARER:tok / Bearer=tok
        实测原样输出（token 直通）。补键形态层与 api/xci 族同构：分隔符
        : 或 =，头名尾随标识符（BEARER_TOKEN=）并入键族。分隔符前空白
        形态（Bearer : tok / Bearer = tok）由规则次序保障——分隔符规则
        必须先于裸值规则，否则裸值规则先吞掉裸 :/=（#229 droid-review P1：
        token 残泄 Bearer [REDACTED] tok）；分隔符规则的 .* 吞掉含 token
        的整段尾部，终态单令牌（Bearer [REDACTED]，#229 round-5 P3 修正
        此前双令牌的过时表述）。
        """
        for line in (
            "BEARER:tokABCDEFGH\n",
            "Bearer: tokABCDEFGH\n",
            "Bearer:tokABCDEFGH\n",
            "Bearer=tokABCDEFGH\n",
            "bearer: tokABCDEFGH\n",
            "BEARER_TOKEN=tokABCDEFGH\n",
            "Bearer : tokABCDEFGH\n",
            "Bearer = tokABCDEFGH\n",
            "BEARER_TOKEN = tokABCDEFGH\n",
            'Bearer : "tokABCDEFGH"\n',
        ):
            out = self._redact(line)
            assert "tokABCDEFGH" not in out, f"token 直通: {line!r} -> {out!r}"
            assert (
                out.startswith("Bearer") or out.startswith("BEARER") or out.startswith("bearer")
            ), f"键结构必须保留: {line!r} -> {out!r}"
            assert out.endswith("[REDACTED]\n"), f"值必须整灭: {line!r} -> {out!r}"

    def test_28b_bearer_separator_forms_idempotent(self):
        """新分隔符形态脱敏输出二次过滤幂等（[REDACTED] 不被再吞变形）。"""
        for line in (
            "BEARER:tokABCDEFGH\n",
            "Bearer: tokABCDEFGH\n",
            "Bearer=tokABCDEFGH\n",
            'Bearer: "tokABCDEFGH"\n',
            "Bearer : tokABCDEFGH\n",
            "Bearer = tokABCDEFGH\n",
            'Bearer : "tokABCDEFGH"\n',
        ):
            once = self._redact(line)
            assert self._redact(once) == once, f"不幂等: {line!r} -> {once!r}"

    def test_28c_bearer_whitespace_forms_not_regressed(self):
        """既有空白分隔形态回归锁（issue #204 验收：L62-64 不回归）。"""
        for line in (
            "Bearer abc123XYZ_def-098\n",
            "Authorization: Bearer abc777\n",
            'Bearer "t1" "t2"\n',
            "Bearer abc, def\n",
            "Bearer abc}cd\n",
        ):
            out = self._redact(line)
            for frag in ("abc123XYZ", "abc777", "t1", "def", "cd"):
                assert frag not in out.replace("[REDACTED]", ""), f"残泄: {line!r} -> {out!r}"
            assert "[REDACTED]" in out, f"not redacted: {line!r} -> {out!r}"

    def test_29_paired_quote_output_survives_to_final_line(self):
        """成对引号值输出 "[REDACTED]" 存活到终行（issue #204 P3②）。

        旧实现成对引号规则输出 "[REDACTED]" 被同管后层未终结引号/裸值
        规则整值再吞为无引号形态——文档与实现矛盾。裁决：改实现，让
        成对引号输出令牌存活（JSON 闭合 } 一并保留），幂等测试锁定。
        """
        cases = [
            ('apiKey="qsecret9"\n', 'apiKey="[REDACTED]"\n'),
            ('x-ci-key: "wq8899"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey = "quoted9"\n', 'apiKey = "[REDACTED]"\n'),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"{line!r} -> {out!r} (expected {expected!r})"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_29b_paired_quote_json_closing_brace_preserved(self):
        """成对引号 JSON 值形态终行保留闭合 }（结构保留契约，test_10 对称）。

        仅单键 JSON 保障（值后紧跟 }）——多键 JSON（值后逗号续项）由占位符
        续项规则吞至行尾、} 一并吸收（#229 droid-review P3：文档契约限定
        单键形态，多键丢 } 属过度脱敏安全方向，行为锁定如下）。
        """
        cases = [
            ('{"apiKey":"plainsecret777"}\n', '{"apiKey":"[REDACTED]"}\n'),
            ('{"x-ci-key": "plainsecret777"}\n', '{"x-ci-key": "[REDACTED]"}\n'),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"{line!r} -> {out!r}"
            assert self._redact(out) == out

    def test_29c_multi_key_json_brace_swallowed_safe_direction(self):
        """多键 JSON 逗号续项吞 } 锁定（过度脱敏安全方向，不视为契约破坏）。"""
        out = self._redact('{"apiKey":"q1", "model":"gpt"}\n')
        assert "q1" not in out and "gpt" not in out, f"残泄: {out!r}"
        # 占位符续项规则吞至行尾——闭合 } 不保证存活（安全方向）
        assert out == '{"apiKey":"[REDACTED]"\n', f"行为漂移: {out!r}"
        assert self._redact(out) == out, f"必须幂等: {out!r}"

    def test_30_paired_quote_marker_not_leaked_as_placeholder(self):
        """内部占位字节不泄漏到终行（占位符必须全部还原为 "[REDACTED]"）。"""
        out = self._redact('apiKey="qsecret9" x-ci-key: "wq8899" apiKey=k2\n')
        assert "\x01" not in out
        assert out == 'apiKey="[REDACTED]" x-ci-key: "[REDACTED]" apiKey=[REDACTED]\n'

    def test_30b_paired_quote_comma_continuation_still_swallowed(self):
        """占位符后逗号续项仍整值灭（"t1", "t2" 与 "q1", extra——占位符续项规则）。"""
        cases = [
            ('x-ci-key: "t1", "t2"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey="q1", extra\n', 'apiKey="[REDACTED]"\n'),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"{line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_28d_bearer_quoted_key_separator_forms_redacted(self):
        """带引号 Bearer 键的分隔符形态同灭（#229 round-2 P1）。

        分隔符规则此前缺键引号（api/xci/Authorization 层都有 ["']?），
        {"Bearer":"tok"} / 'Bearer': 'tok' / {"BEARER_TOKEN":"tok"} 实测
        整行放行（token 直通）。补 ["']? 对齐三邻层。
        """
        for line in (
            '{"Bearer":"tok123ABC"}\n',
            "{'Bearer':'tok123ABC'}\n",
            '{"BEARER_TOKEN":"tok123ABC"}\n',
            "'bearer' : 'tok123ABC'\n",
            '"Bearer" = tok123ABC\n',
        ):
            out = self._redact(line)
            assert "tok123ABC" not in out, f"token 直通: {line!r} -> {out!r}"
            assert "[REDACTED]" in out, f"not redacted: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r} -> {out!r}"

    def test_30c_space_before_comma_continuation_redacted(self):
        """逗号前空白续项形态同灭（#229 round-2 P1）。

        续项规则此前要求逗号紧邻值/占位符，"t1" , "t2"（逗号前空白）形态
        实测续项 token 残泄。续项重复组补 [[:space:]]*, 前缀，覆盖裸值与
        占位符两类续项（Bearer/xci/api 三族）。
        """
        cases = [
            ('x-ci-key: "t1" , "t2"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey="q1" , extra\n', 'apiKey="[REDACTED]"\n'),
            ("x-ci-key: tok1 , tok2\n", "x-ci-key: [REDACTED]\n"),
            ("Bearer tok1 , tok2\n", "Bearer [REDACTED]\n"),
            ("apiKey=k1 , k2 , k3\n", "apiKey=[REDACTED]\n"),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"{line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r} -> {out!r}"

    def test_31_clean_line_with_brace_preserved(self):
        """无凭证行的 { } 结构原样透传（占位符机制不误伤净行）。"""
        src = 'cfg = {"mode": "fast", "retry": 3}\n'
        out = self._redact(src)
        assert out == src

    def test_32_raw_placeholder_byte_input_redacted(self):
        """原生 \\x01 输入不截断脱敏（#229 round-4 P1）。

        裸值值类对占位字节的排除使输入携带裸 \\x01 时在该字节处截断
        （apiKey=abc\\x01def 尾段残泄、\\x01 起始值整值跳过、两遍输出
        不一致破坏幂等契约）。修复：sed 前 tr -d 整字节删除——日志文本
        不含控制字符，值恢复整段连续。
        """
        for line in ("apiKey=abc\x01def\n", "x-ci-key: \x01abc\n"):
            out = self._redact(line)
            assert "\x01" not in out, f"占位字节泄漏: {line!r} -> {out!r}"
            assert "[REDACTED]" in out, f"not redacted: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r} -> {out!r}"
        # 具体形态：尾段 def 不得残泄（cat -v 下 \x01 显示为 ^A）
        out = self._redact("apiKey=abc\x01def\n")
        assert "def" not in out.replace("[REDACTED]", ""), f"截断残泄: {out!r}"
        # 值仅含占位字节（删除后为空值）——无密文可泄，占位字节不残留
        out = self._redact("apiKey=\x01\n")
        assert out == "apiKey=\n", f"空值形态漂移: {out!r}"

    def test_33_glued_tail_after_paired_quote_redacted(self):
        """成对引号闭合后粘连尾整吞（#229 round-4 P2）。

        占位符续项规则此前只认逗号——"t1""t2" 粘连引号 token、
        "t1"tail 粘连裸尾、"tok1" "tok2 截断行尾 token 无规则承接，
        t2/tail/tok2 残泄。修复：续项规则扩为三类承接（逗号组 / 粘连
        引号 token 组 / 粘连裸尾含停止符组）+ 空白未终结引号行尾。
        """
        cases = [
            ('x-ci-key: "t1""t2"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey="t1"tail\n', 'apiKey="[REDACTED]"\n'),
            ('x-ci-key: "tok1" "tok2\n', 'x-ci-key: "[REDACTED]"\n'),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"粘连尾残泄: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_34_glued_tail_boundary_brace_preserved(self):
        """粘连尾承接的边界：直接粘连 } 仍保留（test_29b 结构契约）。"""
        src = 'x-ci-key: "q1"' + "}\n"
        out = self._redact(src)
        assert out == 'x-ci-key: "[REDACTED]"' + "}\n", f"}} 误吞: {out!r}"
        assert self._redact(out) == out

    def test_35_dot_delimited_key_forms_redacted(self):
        """点分键形态同灭（#229 round-4 P3②：bearer.token= / x-ci-key.id=）。

        键类尾随标识符此前不含 .，bearer.token=SECRET 完全放行
        （properties/config 常见形态）。三族键类补 . 点分。
        """
        for line in (
            "bearer.token=SECRET999\n",
            "BEARER.TOKEN=tokABCDEFGH\n",
            "x-ci-key.id=tokAAA111\n",
            "api.key.name=tokBBB222\n",
        ):
            out = self._redact(line)
            for frag in ("SECRET999", "tokABCDEFGH", "tokAAA111", "tokBBB222"):
                assert frag not in out, f"点分键直通: {line!r} -> {out!r}"
            assert "[REDACTED]" in out, f"not redacted: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_36_bearer_colon_tail_whole_swallowed(self):
        """Bearer 冒号尾整吞（#229 round-4 P3①：Bearer abc: def 残泄 def）。

        Bearer 裸值类停止符仅 ; ) 行尾——空格/逗号/冒号/等号都是值材料
        （Bearer 头整行即凭据，与 Authorization 兜底行尾扫尾语义一致）。
        """
        for line in ("Bearer abc: def\n", 'Bearer "tok": secret\n', "Bearer abc, def\n"):
            out = self._redact(line)
            assert out == "Bearer [REDACTED]\n", f"尾段残泄: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_37_semicolon_tail_after_quoted_value_preserved(self):
        """成对引号闭合后 ; 尾保留（#229 round-5 P1：; 不是续吞分隔符）。

        占位符续项组此前把 ; 当续吞分隔符，apiKey="q1";q=1 的 ;q=1 整段
        被吞（base 保留 ;q=1）。契约：续项吞至 ; ) 或行尾——; 是截断符，
        续吞分隔符仅逗号（三族对称，test_19 的 & 上下文保留同语义）。
        """
        cases = [
            ('apiKey="q1";q=1\n', 'apiKey="[REDACTED]";q=1\n'),
            ('x-ci-key: "t1";q=1\n', 'x-ci-key: "[REDACTED]";q=1\n'),
            ("Bearer tok;q=1\n", "Bearer [REDACTED];q=1\n"),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"; 尾误吞: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"

    def test_38_ampersand_glued_tail_after_quoted_value_preserved(self):
        """api 族成对引号闭合后粘连 & 尾保留（#229 round-5 P1）。

        粘连尾首字符类此前漏 &，?apiKey="q1"&mode=fast 的 &mode=fast
        整段被吞（base 保留，test_19 裸值 & 截断同契约）。xci 族 & 保持
        值材料（header 值无查询串上下文，"t1"&hint=2 整值灭——base 行为）。
        """
        out = self._redact('?apiKey="q1"&mode=fast\n')
        assert out == '?apiKey="[REDACTED]"&mode=fast\n', f"& 尾误吞: {out!r}"
        assert self._redact(out) == out, "必须幂等"
        out = self._redact('apiKey="q1"&mode=fast&user=1\n')
        assert out == 'apiKey="[REDACTED]"&mode=fast&user=1\n', f"& 尾误吞: {out!r}"
        out = self._redact('x-ci-key: "t1"&hint=2\n')
        assert out == 'x-ci-key: "[REDACTED]"\n', f"xci & 应整值灭: {out!r}"
        assert self._redact(out) == out, "必须幂等"

    def test_39_comma_continuation_still_swallowed_after_p1_fix(self):
        """; 退出续吞分隔符后逗号续项不回归（test_30b/30c 行为锁）。"""
        cases = [
            ('x-ci-key: "t1", "t2"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey="q1", extra\n', 'apiKey="[REDACTED]"\n'),
            ('x-ci-key: "t1" , "t2"\n', 'x-ci-key: "[REDACTED]"\n'),
            ('apiKey="tok","next";q=1\n', 'apiKey="[REDACTED]";q=1\n'),
        ]
        for line, expected in cases:
            out = self._redact(line)
            assert out == expected, f"逗号续项回归: {line!r} -> {out!r}"
            assert self._redact(out) == out, f"必须幂等: {line!r}"


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

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_01_raw_stderr_removed(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert "shard-exec-error.log" not in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_02_session_jsonl_removed(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert ".factory/sessions" not in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_03_classification_files_added(self, workflow_path):
        paths = _upload_step(workflow_path)["with"]["path"]
        assert "error-classification.txt" in paths
        assert "droid-host-log-redacted.txt" in paths

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_04_prefix_contract_preserved(self, workflow_path):
        """watchdog quota-sweep 依赖 droid-review-debug- 前缀（NEVER break）。"""
        name = _upload_step(workflow_path)["with"]["name"]
        assert name.startswith("droid-review-debug-")

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_05_include_hidden_files_off(self, workflow_path):
        """隐藏目录面移除后 include-hidden-files 必须关闭（最小上传面）。"""
        assert _upload_step(workflow_path)["with"].get("include-hidden-files") is not True

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_06_collect_transcripts_step_removed(self, workflow_path):
        data = yaml.safe_load(workflow_path.read_text())
        review = data["jobs"]["review-shard"]
        names = [s.get("name", "") for s in review["steps"]]
        assert not any("Collect debug transcripts" in n for n in names)

    @pytest.mark.parametrize("workflow_path", [WORKFLOW_SHARDS])
    def test_07_helpers_synced_alongside_engine_script(self, workflow_path):
        """失败分类/脱敏采集助手必须随引擎副本同步（stale pin 下诊断面完整）。

        B-1 收敛：Run shard review 步单源于 shards（自仓壳零内联分片步，
        壳只经 uses 调用）——助手同步断言随实现单源到本载体。"""
        raw = workflow_path.read_text()
        for helper in (
            "classify_shard_failure.sh",
            "capture_host_log.sh",
            "redact_secrets.sh",
        ):
            assert helper in raw, f"{workflow_path.name} 缺少助手同步: {helper}"
        shell_jobs = yaml.safe_load(WORKFLOW_SELF.read_text())["jobs"]
        assert "review-shard" not in shell_jobs, (
            "自仓壳不得残留 review-shard 内联 job（实现单源 shards）"
        )


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

        base_sha, head_sha = init_two_commit_repo(head, git_cfg)

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
