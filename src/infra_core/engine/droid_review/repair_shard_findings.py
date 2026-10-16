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
3. 失败则逐字符扫描修复三类缺陷：
   a. 非法 \\escape：字符串内 '\' 后随字符不构成合法 JSON 转义（\\1 \\s
      \\K \\' 等正则/反引用序列）→ 把反斜杠本身转义为 '\\\\'，字面语义
      保真（解析后仍是原文 \\1）；合法转义对（含 \\uXXXX）保真透传。
   b. 裸双引号（key/value 位置判闭，圆17 P1）：值串内嵌 '"key":' /
      '"a", "b"' 片段在旧「引号+结构性字符即串结束」规则下会被误判为
      串结束，扫描失步、修复整体失败，fail-closed 丢弃一场有效评审。
      修复器依次尝试三级判闭策略，json.loads 成功即采纳：
      S1（legacy）闭引号后跟 , : } ] 即闭合（保既有 97 基线行为）；
      S2 串后跟 ':' 视为 key 闭合、',' 视为值闭合、'}' ']' 闭合要求
      其后随结构性字符（值内 '{"x"} was' 形态不再失步）；
      S3 最保守——值串仅 '}]'（含后随校验）闭合，',' 不闭合（值内
      '"a", "b"' 片段形态的最后捞救）。
   c. 裸控制字符（C0 全集，ord<0x20）：转义为 \\uXXXX 字面保真。
4. 修复结果必须 json.loads 成功、shard_id 匹配、findings 为数组，否则
   非零退出（调用方继续 fail-closed 链）。

用法：
    repair_shard_findings.py <stdout_json_file> <out_file> <shard_id>
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

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


def _scan(candidate: str, strategy: int) -> str:
    r"""单遍扫描修复：非法 \escape + 裸控制字符 + 裸双引号（按策略判闭）。

    判闭策略（圆17 P1，json.loads 为最终仲裁）：
    * strategy=1（legacy）：字符串内遇 '"'，跳过空白后下一字符是
      ',' ':' '}' ']' 或串尾 → 闭合；其余视为内嵌裸引号，转义
      （与既有基线行为一致）。
    * strategy=2：串后 ':' 或 ',' → 闭合；'}' ']' → 仅当其后（再跳过
      空白）是结构性字符（',}]:' 或串尾）才闭合——值串内嵌 '"key":' /
      '{"x"} was' 片段不再被误判为串结束导致扫描失步。
    * strategy=3：同 2 但 ',' 不闭合（值内 '"a", "b"' 片段形态的
      最后捞救）。

    非法 \escape：字符串内 '\' 后随字符不构成合法 JSON 转义——把反斜杠
    转义为 '\\'（解析后还原为字面 '\'，字面保真）。'\u' 后随 4 位 hex
    才算合法，否则同样按非法处理。

    裸控制字符（C0 全集，ord<0x20）：转义为 \uXXXX 字面保真。
    """
    out: list[str] = []
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
            # 向前看（跳过空白）：按策略判定闭合或内嵌
            j = i + 1
            while j < n and candidate[j] in " \t\r\n":
                j += 1
            after = candidate[j] if j < n else ""
            if strategy == 1:
                closes = after == "" or after in ",:}]"
            elif after == ":" or (after == "," and strategy == 2):
                closes = True
            elif after in "}]":
                k = j + 1
                while k < n and candidate[k] in " \t\r\n":
                    k += 1
                follow = candidate[k] if k < n else ""
                closes = follow == "" or follow in ",}]:"
            else:
                closes = False
            if closes:
                in_string = False
                out.append(ch)
            else:
                # 内嵌裸引号 → 转义
                out.append('\\"')
            i += 1
            continue
        if ord(ch) < 0x20:
            # 裸控制字符（C0 全集：字面换行/制表/ANSI \x1b 等其他 C0）：
            # JSON 字符串内非法，转义为 \uXXXX 字面保真（解析后还原原字符）
            out.append("\\u%04x" % ord(ch))
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_unescaped_quotes(candidate: str) -> str | None:
    """三级判闭策略依次修复，json.loads 成功即采纳；全败返回 None。

    S1 保持与既有基线完全一致的行为（绝大多数裸引号形态 S1 即命中）；
    S2/S3 逐级收紧闭合判定，捞救值串内嵌 '"key":' / '"a", "b"' 片段的
    失步形态（圆17 P1）。返回 None 时调用方 fail-closed。
    """
    for strategy in (1, 2, 3):
        repaired = _scan(candidate, strategy)
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            continue
    return None


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

    # 第二搏：修复非法转义 + 内嵌裸引号（三级判闭策略，json.loads 仲裁）
    repaired = _repair_unescaped_quotes(candidate)
    if repaired is None:
        print("repair: still unparseable after quote repair (all strategies)", file=sys.stderr)
        return 1

    data = json.loads(repaired)
    if not _validate(data, shard_id):
        print("repair: repaired JSON failed shard_id/findings validation", file=sys.stderr)
        return 1

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("repair: quote repair succeeded, %d findings" % len(data["findings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
