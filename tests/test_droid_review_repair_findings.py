"""repair_shard_findings 契约测试（2026-09-30 run 36623737488 实证）。

故障链：模型偶发违反「裸 JSON」约定（开场白 + ```json 围栏 + message 字段
内嵌未转义双引号）→ run_shard.sh Attempt 1/2/3 严格 jq 解析全失败 →
fail-closed 把一次完整有效的评审报废 → droid-review / ci-ok / quality-gate
三连红。

修复（Attempt 4，本模块）：定向转义 JSON 字符串值内部的裸引号后重试解析；
修好的 JSON 必须通过 shard_id / findings 硬校验，失败仍走 fail-closed。

本文件锁定以下语义：

* 合法裸 JSON（envelope .result 直接可解析）→ 严格路径零改动通过；
* 围栏包裹 + 内嵌裸引号 → 修复后解析成功，findings 数量与原文一致；
* shard_id 不匹配 / findings 非数组 / 无 JSON 块 → 非零退出（fail-closed）；
* 修复只动字符串内部裸引号——结构性 token（键名/括号/逗号）保持不变；
* 输出文件内容为合法 JSON 且通过 publish_findings.validate_findings。

全部用例零网络依赖，直接调用修复器 CLI（与 run_shard.sh 调用形态一致）。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine" / "droid_review"
REPAIR_HELPER = ENGINE_DIR / "repair_shard_findings.py"

pytestmark = pytest.mark.integration


def _run_repair(envelope: dict, shard_id: str, tmp_path: Path):
    """以 run_shard.sh 相同的 CLI 形态调用修复器，返回 (returncode, out_file)。"""
    stdin_file = tmp_path / "droid-exec-stdout.json"
    out_file = tmp_path / "findings-shard-0.json"
    stdin_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPAIR_HELPER), str(stdin_file), str(out_file), shard_id],
        capture_output=True,
        text=True,
    )
    return proc, out_file


def _envelope(result_text: str) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_text,
        "session_id": "test-session",
    }


GOOD_FINDINGS = {
    "shard_id": 0,
    "findings": [
        {
            "severity": "P1",
            "file": "scripts/livetruth_probes.py",
            "line": 977,
            "message": '`tail = lines[-30:]` then `"轮询开始"` cycle detection assumes a completed cycle marker.',
        },
        {
            "severity": "P2",
            "file": "scripts/livetruth_probes.py",
            "line": 42,
            "message": "普通消息无引号",
        },
    ],
}


class TestStrictPathFirst:
    def test_bare_valid_json_passes_without_repair(self, tmp_path):
        """envelope .result 本来就是合法 JSON → 严格路径通过。"""
        envelope = _envelope(json.dumps(GOOD_FINDINGS, ensure_ascii=False))
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "no repair needed" in proc.stdout
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(data["findings"]) == 2


class TestQuoteRepair:
    def test_fenced_output_with_unescaped_quotes_is_repaired(self, tmp_path):
        """run 36623737488 实证形态：开场白 + ```json 围栏 + 内嵌裸引号。"""
        # 手工构造带内嵌裸引号的 JSON 文本（严格解析必然失败）
        raw_block = (
            '{\n  "shard_id": 0,\n  "findings": [\n    {\n'
            '      "severity": "P2",\n      "file": "a.py",\n      "line": 977,\n'
            '      "message": "`tail = lines[-30:]` then `"轮询开始"` cycle detection"\n'
            "    }\n  ]\n}"
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)  # 前置确认：原文确实不合法

        envelope = _envelope(
            "I have the full file. Now I'll produce the review findings as JSON.\n\n"
            "```json\n" + raw_block + "\n```"
        )
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "quote repair succeeded" in proc.stdout

        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["shard_id"] == 0
        assert len(data["findings"]) == 1
        # 修复后的 message 保留了内嵌引号语义（转义形态）
        assert '"轮询开始"' in data["findings"][0]["message"]

    def test_real_run_36623737488_payload_shape(self, tmp_path):
        """重现实证 payload 的关键特征：多条 findings + 中文 + 引号嵌套。"""
        findings = []
        for i in range(11):
            findings.append(
                {
                    "severity": "P2",
                    "file": "scripts/livetruth_probes.py",
                    "line": 100 + i,
                    "message": 'probe `%s` then `"轮询开始"` marker %d' % ("t2_0%d" % i, i),
                }
            )
        # 构造裸引号文本：serialize 后人为破坏——把 已转义的 \" 还原成裸 "
        good = json.dumps({"shard_id": 0, "findings": findings}, ensure_ascii=False)
        broken = good.replace('\\"', '"')
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)

        envelope = _envelope("```json\n" + broken + "\n```")
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(data["findings"]) == 11

    def test_repaired_output_passes_publish_validation(self, tmp_path):
        """修好的 findings 必须通过 publish_findings.validate_findings。"""
        raw_block = (
            '{"shard_id": 0, "findings": [{"severity": "P1", "file": "a.py", '
            '"line": 5, "message": "内嵌 `"裸引号`" 示例"}]}'
        )
        envelope = _envelope("```json\n" + raw_block + "\n```")
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr

        sys.path.insert(0, str(ENGINE_DIR))
        try:
            from publish_findings import validate_findings
        finally:
            sys.path.pop(0)
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert validate_findings(data)


class TestFailClosed:
    def test_shard_id_mismatch_rejected(self, tmp_path):
        """shard_id 不匹配 → 非零退出（防串扰，同 recover 脚本语义）。"""
        good = json.dumps({"shard_id": 7, "findings": []}, ensure_ascii=False)
        envelope = _envelope(good)
        proc, _ = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 1

    def test_findings_not_array_rejected(self, tmp_path):
        """findings 非数组 → 非零退出。"""
        envelope = _envelope('{"shard_id": 0, "findings": "not-an-array"}')
        proc, _ = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 1

    def test_no_json_braces_rejected(self, tmp_path):
        """result 无 JSON 块 → 非零退出。"""
        envelope = _envelope("纯散文，没有任何 JSON。")
        proc, _ = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 1

    def test_missing_result_field_rejected(self, tmp_path):
        """envelope 缺 .result → 非零退出。"""
        proc, _ = _run_repair({"type": "result", "subtype": "success"}, "0", tmp_path)
        assert proc.returncode == 1

    def test_structural_tokens_not_touched_on_failure(self, tmp_path):
        """修复失败时不得产出半成品文件（fail-closed 由调用方保证）。"""
        # 缺闭括号的 JSON：修复器只能转义引号，不能补括号
        broken = '{"shard_id": 0, "findings": [{"severity": "P1"'
        envelope = _envelope("```json\n" + broken + "\n```")
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 1
        assert not out_file.exists() or not out_file.read_text(encoding="utf-8").strip()
