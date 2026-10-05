r"""repair_shard_findings 契约测试（2026-09-30 run 36623737488 实证）。

故障链：模型偶发违反「裸 JSON」约定（开场白 + ```json 围栏 + message 字段
内嵌未转义双引号）→ run_shard.sh Attempt 1/2/3 严格 jq 解析全失败 →
fail-closed 把一次完整有效的评审报废 → droid-review / ci-ok / quality-gate
三连红。

修复（Attempt 4，本模块）：定向转义 JSON 字符串值内部的裸引号后重试解析；
修好的 JSON 必须通过 shard_id / findings 硬校验，失败仍走 fail-closed。

2026-10-01 run 36833875661（PR #197）扩展：被审 diff 含大量 sed/正则反斜杠
序列，评审 message 原样引用构成非法 \escape（\1 \s \K）——修复器扩展为
非法转义 + 裸引号双修（TestInvalidEscapeRepair）。

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


class TestInvalidEscapeRepair:
    """run 36833875661 实证（PR #197 shard 0）：非法 \\escape 修复。

    故障链：被审 diff 全是含反斜杠序列的 sed/正则（\\1 反向引用、\\s、\\K），
    评审 message 原样引用 → JSON 字符串内非法 \\escape → 严格解析与裸引号
    修复双失败（旧修复器对转义对保真透传）→ 会话 jsonl 兜底同源同内容也败
    → fail-closed 三连红，一次完整有效的 4-finding 评审整个报废。

    修复语义锁定：
    * 非法转义（\\1 \\s \\K 等）→ 反斜杠转义为 \\\\，解析后还原字面 '\\'；
    * 合法转义（\\" \\\\ \\n \\t）与 \\uXXXX 保真透传，不重复处理；
    * 裸引号与非法转义混合形态同修；
    * 修好的 findings 通过 shard_id / findings 硬校验。
    """

    def test_real_run_36833875661_invalid_backreference_repaired(self, tmp_path):
        """实证 payload 核心特征：message 引用 sed 反向引用 \\1。"""
        raw_block = (
            '{"shard_id": 0, "findings": [{"severity": "P3", "file": "redact_secrets.sh", '
            '"line": 55, "message": "The \\1 prefix-preserving intent of 55-56 never survives"}]}'
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)  # 前置确认：\1 是非法 JSON escape

        envelope = _envelope(
            "Verification complete: 30/30 tests pass.\n\n```json\n" + raw_block + "\n```"
        )
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "quote repair succeeded" in proc.stdout

        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(data["findings"]) == 1
        # 字面保真：解析后 message 仍是字面 \1（单反斜杠 + '1'）
        assert "\\1" in data["findings"][0]["message"]
        assert data["findings"][0]["message"].count("\\") == 1

    def test_regex_escape_sequences_repaired(self, tmp_path):
        """正则常见序列 \\s \\K \\d+ 同修（sed/awk/py regex 引用形态）。"""
        raw_block = (
            '{"shard_id": 0, "findings": [{"severity": "P1", "file": "a.py", '
            '"line": 9, "message": "rule `s/\\s+/ /g` and `grep -oP \'\\K.*\'` leak"}]}'
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)

        envelope = _envelope(raw_block)
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out_file.read_text(encoding="utf-8"))
        msg = data["findings"][0]["message"]
        assert "\\s" in msg and "\\K" in msg  # 字面序列保真

    def test_valid_escapes_passed_through_untouched(self, tmp_path):
        """合法转义（\\" \\\\ \\n \\t \\uXXXX）保真透传，不被二次处理。"""
        good = json.dumps(
            {
                "shard_id": 0,
                "findings": [
                    {
                        "severity": "P2",
                        "file": "a.py",
                        "line": 3,
                        "message": '合法转义 "引号" \\ 反斜杠\n\t换行制表 \u4e2d\u6587',
                    }
                ],
            },
            ensure_ascii=False,
        )
        # 合法 JSON 严格路径直通（零改动）——合法转义永不被修复器触碰
        envelope = _envelope(good)
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0
        assert "no repair needed" in proc.stdout
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["findings"][0]["message"].startswith("合法转义")

    def test_mixed_invalid_escape_and_bare_quote_repaired(self, tmp_path):
        r"""混合形态：非法 \escape + 内嵌裸引号 + 围栏包裹一步同修。"""
        # 手工构造双缺陷文本：值内既有内嵌裸引号（"a" "c"）又有非法 \escape（\K）
        invalid_escape = "\\" + "K"
        raw_block = (
            '{\n  "shard_id": 0,\n  "findings": [\n    {\n'
            '      "severity": "P1",\n      "file": "s.sh",\n      "line": 64,\n'
            '      "message": "value "b' + invalid_escape + '" quoted then tail"\n'
            "    }\n  ]\n}"
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)

        envelope = _envelope("前缀散文\n```json\n" + raw_block + "\n```")
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(data["findings"]) == 1
        msg = data["findings"][0]["message"]
        # 双缺陷同修：非法转义字面保真 + 裸引号保留语义
        assert invalid_escape in msg
        assert "quoted then tail" in msg

    def test_unicode_escape_passthrough_in_repair_scan(self, tmp_path):
        r"""\uXXXX 在修复扫描路径中保真透传（非严格路径短路）。

        圆10 指出旧用例 ensure_ascii=False 从未真正触达 \uXXXX 分支——
        此处手工构造含 \uXXXX 与非法转义（\1）同串的坏 JSON，强制走扫描：
        \u4e2d 必须原样保留（不得被二次转义为 \\u4e2d），\1 修复为 \\1。
        """
        raw_block = (
            '{\n  "shard_id": 0,\n  "findings": [\n    {\n'
            '      "severity": "P3",\n      "file": "s.sh",\n      "line": 7,\n'
            '      "message": "标记 \\u4e2d\\u6587 与反引用 \\1 混排"\n'
            "    }\n  ]\n}"
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)

        envelope = _envelope(raw_block)
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out_file.read_text(encoding="utf-8"))
        msg = data["findings"][0]["message"]
        assert "标记 中" in msg, f"\\uXXXX 未解码为字面中文: {msg!r}"
        assert "\\1" in msg, f"非法转义未字面保真: {msg!r}"
        assert "\\u4e2d" not in msg, "\\uXXXX 被二次转义"

    def test_raw_control_characters_repaired(self, tmp_path):
        r"""裸控制字符（字面换行/制表）转义修复——否则整场有效评审报废。

        圆14 P3 实证：message 内嵌字面 \n/\t 时扫描器原样透传，严格解析
        Invalid control character 失败 → fail-closed 丢弃评审。修复语义：
        控制字符转义为 \uXXXX（解析后还原原字符，字面保真）。
        """
        raw_block = (
            '{\n  "shard_id": 0,\n  "findings": [\n    {\n'
            '      "severity": "P3",\n      "file": "a.sh",\n      "line": 5,\n'
            '      "message": "line1\nline2\ttabbed"\n'
            "    }\n  ]\n}"
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)

        envelope = _envelope(raw_block)
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out_file.read_text(encoding="utf-8"))
        msg = data["findings"][0]["message"]
        assert "line1\nline2\ttabbed" in msg, f"控制字符未字面还原: {msg!r}"

    def test_full_c0_control_characters_repaired(self, tmp_path):
        r"""C0 全集控制符（\x1b ANSI 等）转义修复——圆16 P1。

        旧实现只覆盖 \x00\x08\x0c\n\r\t 六个，\x1b/\x0b/\x07 等其余 C0
        原样透传 → 仍 Invalid control character → fail-closed 报废评审。
        """
        raw_block = (
            '{"shard_id": 0, "findings": [{"severity": "P3", '
            '"file": "a.sh", "line": 5, '
            '"message": "esc\x1b[31m and \x0b vert and \x07 bell"}]}'
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw_block)

        envelope = _envelope(raw_block)
        proc, out_file = _run_repair(envelope, "0", tmp_path)
        assert proc.returncode == 0, proc.stderr
        msg = json.loads(out_file.read_text(encoding="utf-8"))["findings"][0]["message"]
        assert "esc\x1b[31m" in msg and "\x0b" in msg and "\x07" in msg, (
            f"C0 控制符未字面还原: {msg!r}"
        )


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
