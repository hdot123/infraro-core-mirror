#!/usr/bin/env python3
"""repair_shard_findings.py — run_shard.sh Attempt 4 的 JSON 修复器。

背景（2026-09-30 run 36623737488 实证，droid-review shard 0）：
模型偶发违反 shard-review-prompt.md 的「裸 JSON」约定——输出带开场白 +
```json 围栏，且 message 字符串内嵌未转义双引号（例：

    "message": "`tail = lines[-30:]` then `"轮询开始"` cycle detection ..."

）。严格 jq 解析（Attempt 1/2/3）必然失败，fail-closed 会把一次完整有效的
评审整个报废，droid-review check 红、ci-ok 红、quality-gate 红三连。

修复策略（定向、保守）：
1. 读 droid exec stdout envelope，取 .result 字符串（兼容裸 JSON / 围栏包裹
   / 任意前缀散文三种形态：从文本中定位第一个 '{' 到最后一个 '}'）。
2. 先直接 json.loads——本来就是合法 JSON 时零改动通过。
3. 失败则逐字符扫描：追踪是否处于 JSON 字符串内部；字符串内遇到裸 '"'
   且其后（跳过空白）不是结构性字符（, : } ]）→ 判定为内嵌引号，转义。
   结构性 token（键名、括号、逗号、冒号）与字符串外内容一概不动。
4. 修复结果必须 json.loads 成功、shard_id 匹配、findings 为数组，否则
   非零退出（调用方继续 fail-closed 链）。

用法：
    repair_shard_findings.py <stdout_json_file> <out_file> <shard_id>
"""

import json
import sys
from typing import Any, Optional

# JSON 字符串结束后合法跟随的结构性字符（含空白跳过后的）
_STRUCTURAL_AFTER_STRING = {",", ":", "}", "]"}


def _extract_candidate(text: str) -> Optional[str]:
    """从任意形态文本中定位 findings JSON 候选块（首 '{' 到尾 '}'）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _repair_unescaped_quotes(candidate: str) -> str:
    """转义 JSON 字符串值内部的裸双引号。

    扫描规则：处于字符串内部时遇到 '"'，向前看下一个非空白字符——
    是结构性字符（, : } ]）则视为字符串结束；否则视为内嵌引号，转义。
    这对 findings 形态（值内引号后必跟文字/反引号，而非 , : } ]）足够，
    且不会破坏本来就合法的转义序列（已转义的不会重复处理）。
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
            # 保真透传转义对（如 \" \\ \n）
            out.append(ch)
            if i + 1 < n:
                out.append(candidate[i + 1])
                i += 2
            else:
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

    # 第二搏：修复内嵌裸引号
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
