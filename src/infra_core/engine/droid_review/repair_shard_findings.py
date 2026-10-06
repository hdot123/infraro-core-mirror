#!/usr/bin/env python3
r"""repair_shard_findings.py — run_shard.sh Attempt 4 的 JSON 修复器。

背景一（2026-09-30 run 36623737488 实证，droid-review shard 0）：
模型偶发违反 shard-review-prompt.md 的「裸 JSON」约定——输出带开场白 +
```json 围栏，且 message 字符串内嵌未转义双引号（例：

    "message": "`tail = lines[-30:]` then `"轮询开始"` cycle detection ..."

）。严格 jq 解析（Attempt 1/2/3）必然失败，fail-closed 会把一次完整有效的
评审整个报废，droid-review check 红、ci-ok 红、quality-gate 红三连。

背景二（2026-10-01 run 36833875661 实证，PR #197 shard 0）：
被审 diff 全是含反斜杠序列的 sed/正则（\\1 反向引用、\\s、\\K 等），评审
message 原样引用这些序列 → JSON 字符串内出现非法 \\escape（JSON 只认
\\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX）。裸引号修复器对转义对保真透传，
\\1 原样保留 → 严格解析与裸引号修复双失败，会话 jsonl 兜底同源同内容
也失败 → 一次完整有效的 4-finding 评审整个报废。

修复策略（定向、保守）：
1. 读 droid exec stdout envelope，取 .result 字符串（兼容裸 JSON / 围栏包裹
   / 任意前缀散文三种形态：从文本中定位第一个 '{' 到最后一个 '}'）。
2. 先直接 json.loads——本来就是合法 JSON 时零改动通过。
3. 失败则逐字符扫描修复两类缺陷：
   a. 非法 \\escape：字符串内 '\' 后随字符不构成合法 JSON 转义（\\1 \\s
      \\K \\' 等正则/反引用序列）→ 把反斜杠本身转义为 '\\\\'，字面语义
      保真（解析后仍是原文 \\1）；合法转义对（含 \\uXXXX）保真透传。
   b. 裸双引号：字符串内遇 '"' 且其后（跳过空白）不是结构性字符
      （, : } ]）→ 判定为内嵌引号，转义。结构性 token 与字符串外内容
      一概不动。
4. 修复结果必须 json.loads 成功、shard_id 匹配、findings 为数组，否则
   非零退出（调用方继续 fail-closed 链）。

用法：
    repair_shard_findings.py <stdout_json_file> <out_file> <shard_id>
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

# JSON 字符串结束后合法跟随的结构性字符（含空白跳过后的）
_STRUCTURAL_AFTER_STRING = {",", ":", "}", "]"}

# JSON 合法简单转义字符（\uXXXX 单独处理）
_VALID_SIMPLE_ESCAPES = set('"\\/bfnrt')

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _extract_candidate(text: str) -> Optional[str]:
    """从任意形态文本中定位 findings JSON 候选块（首 '{' 到尾 '}'）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _repair_unescaped_quotes(candidate: str) -> str:
    r"""转义 JSON 字符串值内部的裸双引号 + 修复非法 \escape。

    扫描规则：处于字符串内部时遇到 '"'，向前看下一个非空白字符——
    是结构性字符（, : } ]）则视为字符串结束；否则视为内嵌引号，转义。
    这对 findings 形态（值内引号后必跟文字/反引号，而非 , : } ]）足够，
    且不会破坏本来就合法的转义序列（已转义的不会重复处理）。

    非法 \escape（背景二）：字符串内 '\' 后随字符不构成合法 JSON 转义
    ——把反斜杠转义为 '\\'（解析后还原为字面 '\'，后续语义不变）。
    '\u' 后随 4 个 hex 才算合法，否则同样按非法处理。
    """
    out = []
    i = 0
    n = len(candidate)
    in_string = False
    while i < n:
        ch = candidate[i]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
            continue
        # in_string
        if ch == "\\":
            nxt = candidate[i + 1] if i + 1 < n else ""
            if nxt in _VALID_SIMPLE_ESCAPES:
                # 合法转义对（\" \\ \/ \b \f \n \r \t）保真透传
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt == "u" and all(candidate[j] in _HEX_DIGITS for j in range(i + 2, min(i + 6, n))):
                # 合法 \uXXXX（4 位 hex 齐）保真透传
                out.append(candidate[i : i + 6])
                i += 6
                continue
            # 非法 \escape（\1 \s \K \' 等）：反斜杠转义为 \\，字面保真
            out.append("\\\\")
            i += 1
            continue
        if ch == '"':
            # 向前看：跳过空白后是否为结构性字符
            j = i + 1
            while j < n and candidate[j] in " \t\r\n":
                j += 1
            if j >= n or candidate[j] in _STRUCTURAL_AFTER_STRING:
                # 字符串结束
                in_string = False
                out.append(ch)
            else:
                # 内嵌裸引号 → 转义
                out.append('\\"')
            i += 1
            continue
        if in_string and ord(ch) < 0x20:
            # 裸控制字符（C0 全集：字面换行/制表/ANSI \x1b 等其他 C0）：
            # JSON 字符串内非法，转义为 \uXXXX 字面保真（解析后还原原字符）
            out.append("\\u%04x" % ord(ch))
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _validate(data: Any, shard_id: str) -> bool:
    """硬校验：必须是 dict、shard_id 匹配、findings 为数组。"""
    if not isinstance(data, dict):
        return False
    sid = data.get("shard_id")
    if str(sid) != str(shard_id):
        return False
    return isinstance(data.get("findings"), list)


def main() -> int | None:
    if len(sys.argv) != 4:
        print(
            "usage: repair_shard_findings.py <stdout_json_file> <out_file> <shard_id>",
            file=sys.stderr,
        )
        return 2

    stdout_file, out_file, shard_id = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        with open(stdout_file, encoding="utf-8") as f:
            envelope = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print("repair: cannot load stdout envelope: %s" % e, file=sys.stderr)
        return 1

    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(result, str) or not result.strip():
        print("repair: envelope has no usable .result string", file=sys.stderr)
        return 1

    candidate = _extract_candidate(result)
    if candidate is None:
        print("repair: no JSON object braces found in .result", file=sys.stderr)
        return 1

    # 第一搏：本来就是合法
    data = None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        pass
    if data is not None and _validate(data, shard_id):
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(
            "repair: strict parse succeeded (no repair needed), %d findings" % len(data["findings"])
        )
        return 0

    # 第二搏：修复非法转义 + 内嵌裸引号
    repaired = _repair_unescaped_quotes(candidate)
    try:
        data = json.loads(repaired)
    except json.JSONDecodeError as e:
        print("repair: still unparseable after quote repair: %s" % e, file=sys.stderr)
        return 1

    if not _validate(data, shard_id):
        print("repair: repaired JSON failed shard_id/findings validation", file=sys.stderr)
        return 1

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("repair: quote repair succeeded, %d findings" % len(data["findings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
