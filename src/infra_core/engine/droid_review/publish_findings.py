#!/usr/bin/env python3
"""
TD-DR-01 findings 发布器：下载、校验、去重、inline/summary 发布。

职责：
1. 读取各 shard 的 findings JSON（从 artifacts 下载后）
2. Schema 校验（非法 → fail-closed）
3. 去重（相同 file+line+message 跨 shard 去重）
4. inline comment 行号校验（对照 patch hunks）
5. 422 降级 summary comment
6. 批次 ≤50（GitHub API 限制）
7. 按 shard 分节统计严重度
8. 已裁定 findings 基线抑制（U2'，仅收设计项/误报；键=路径+锚点串+锚点
   ±radius 行窗口内容哈希——窗口内代码一变即失效；抑制项在 summary 显式
   列出 [adjudicated-suppressed]，门禁读过滤后严重度）
9. 终态/分节 summary 的 P0/P1 永不折叠（终裁 §3a：阻塞级 finding 折叠即
   披露缺陷，round-5 第 4 条被折叠实证）
10. 基线信任边界（round-2 P1）：基线条目只从服务端 BASE ref 经 gh api
   contents 加载（--baseline-ref），绝不读 PR 可影响的工作区 checkout——
   PR 树自带伪造基线文件不得自灭 findings；BASE 取不到=不抑制（fail-closed）
"""

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REQUIRED_FINDING_FIELDS = {"severity", "file", "line", "message"}
VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}
BLOCKING_SEVERITIES = frozenset({"P0", "P1"})

# ── 已裁定 findings 基线（U2'，用户 2026-10-22 批准 D2）──
# 基线只收经终审裁定为设计项/误报的 finding；真缺陷永不入。键 = 路径 +
# 锚点串 + 锚点 ±context_radius 行（strip 后）内容 SHA-256 + message 归一化
# 片段全含 + severity。双树键：findings 以 PR-head 行号报告而发布器工作区是
# BASE checkout，故 head 侧窗口（gh api raw contents）必须与 BASE 窗口同样
# 哈希命中——PR 在锚点窗口内改代码时条目失效，PR 自引新缺陷不误杀。护栏：
# 仅 main 维护、条目 ≤10、每条带 adjudication_ref、created_at/expires_at 必
# 填且 TTL ≤90 天、基线文件缺失=不抑制、基线非法=fail-closed（红跑而非静默）。
ADJUDICATED_SUPPRESSED_MARK = "[adjudicated-suppressed]"
DEFAULT_BASELINE_PATH = "scripts/droid_review/adjudicated_findings.json"
BASELINE_MAX_ENTRIES = 10
BASELINE_MAX_TTL_DAYS = 90
DEFAULT_CONTEXT_RADIUS = 3

# Marker for cross-run dedup: summary comments contain this marker so reruns
# can detect and skip/update instead of duplicating.
SUMMARY_MARKER = "<!-- droid-review-summary -->"

# Bot login for auto-resolve eligibility（本发布器 inline/summary 均以此身份发出）。
# GraphQL author.login 与 REST user.login 拼写不一致（同一 bot 注释前者返回
# 裸 github-actions，后者返回 github-actions[bot]，缺陷 #001b 实证），资格
# 过滤必须接受双拼写，否则过滤零命中静默 return。
GITHUB_ACTIONS_BOT_LOGIN = "github-actions[bot]"
BOT_LOGIN_SPELLINGS = frozenset({"github-actions", GITHUB_ACTIONS_BOT_LOGIN})

# ── Round-cap sentinel（轮次上限，切断 review→session→push 无限循环）──
# 每 PR 最多 3 轮 inline findings；第 4 轮起只发终态 summary 并打 needs-human
# label 交还人工（仿 droid-autofix-attempt 先例，隐藏 HTML 注释埋点）。
MAX_REVIEW_ROUNDS = 3
NEEDS_HUMAN_LABEL = "needs-human"
# Line-anchored: the sentinel is machine-embedded on its own line; prose
# quotes of the marker (text around it on the same line) must not count.
_ROUND_MARKER_RE = re.compile(r"^[ \t]*<!--\s*droid-review-round-(\d+)\s*-->[ \t]*$", re.M)


def round_marker(round_number: int) -> str:
    """Per-round sentinel embedded in the summary comment body."""
    return f"<!-- droid-review-round-{round_number} -->"


_REVIEW_THREADS_QUERY = """\
query($owner:String!,$name:String!,$pr:Int!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:50,after:$cursor){
        nodes{
          id isResolved isOutdated path
          comments(first:1){nodes{author{login __typename} body}}
        }
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}"""

_RESOLVE_THREAD_MUTATION = (
    "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}"
)

# 分页保险上限：50/page × 100 = 5000 thread，防异常 pageInfo 造成死循环
_MAX_REVIEW_THREAD_PAGES = 100


def validate_findings(data: dict[str, Any]) -> bool:
    """
    校验 findings JSON schema。

    合法格式：
    {
      "shard_id": int,
      "findings": [
        {"severity": "P0"|"P1"|"P2"|"P3", "file": str, "line": int, "message": str}
      ]
    }

    非法 → False（调用方应 fail-closed）。
    """
    if not isinstance(data, dict):
        return False

    # shard_id 必须是 int
    if "shard_id" not in data or not isinstance(data["shard_id"], int):
        return False

    # findings 必须是 list
    if "findings" not in data or not isinstance(data["findings"], list):
        return False

    # 每条 finding 必须含必需字段且类型正确
    for finding in data["findings"]:
        if not isinstance(finding, dict):
            return False
        # 必需字段检查
        if not REQUIRED_FINDING_FIELDS.issubset(finding.keys()):
            return False
        # 类型检查
        if not isinstance(finding["severity"], str):
            return False
        if not isinstance(finding["file"], str):
            return False
        if not isinstance(finding["line"], int):
            return False
        if not isinstance(finding["message"], str):
            return False
        # 严重度枚举
        if finding["severity"] not in VALID_SEVERITIES:
            return False

    return True


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    跨 shard 去重：相同 (file, line, message) 只保留一条。
    """
    seen = set()
    unique = []
    for f in findings:
        key = (f["file"], f["line"], f["message"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def adjudicated_context_hash(
    lines: list[str], anchor_index0: int, radius: int = DEFAULT_CONTEXT_RADIUS
) -> str:
    """窗口内容哈希：锚点 ±radius 行（strip 后）的 SHA-256。

    strip 行内空白：shard 对 PR head 逐字报告、基线哈希对 BASE 树计算，两侧
    仅缩进漂移不应失效条目；语义内容（非空白）变化仍失配。
    """
    lo = max(0, anchor_index0 - radius)
    hi = min(len(lines), anchor_index0 + radius + 1)
    window = "\n".join(line.strip() for line in lines[lo:hi])
    return hashlib.sha256(window.encode("utf-8")).hexdigest()


def _fetch_head_file_lines(repository: str, head_sha: str, path: str) -> list[str] | None:
    """读取 PR head 树文件行（gh api contents，findings 的行号同源于该树）。

    发布器工作区是 BASE checkout（fetch-depth:1），head 树只能经 API 取。
    失败（网络/权限/路径不存在）返回 None——调用方 fail toward the gate
    （不抑制），绝不因取不到 head 内容而放宽门禁。
    """
    import base64
    from urllib.parse import quote

    cmd = [
        "gh",
        "api",
        f"repos/{repository}/contents/{quote(path)}?ref={head_sha}",
        "--header",
        "Accept: application/vnd.github+json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        payload = json.loads(result.stdout.decode())
        content = payload.get("content", "")
        if not isinstance(content, str):
            return None
        return base64.b64decode(content).decode("utf-8", errors="replace").splitlines()
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        print(
            f"Warning: head-tree fetch failed for {path}@{head_sha[:7]}: {e} "
            "— suppression disabled for this entry (fail toward the gate)",
            file=sys.stderr,
        )
        return None


def _fetch_ref_file_lines(repository: str, ref: str, path: str) -> list[str] | None:
    """按服务端 ref（BASE sha）读文件行——基线条目的唯一信任通道。

    发布工作区是 BASE checkout，但 PR 树可自带伪造基线，工作区读法不可信；
    只有服务器解析的 <ref> 上的内容才配作为裁定证据。失败（网络/权限/文件
    不存在）返回 None——调用方 fail toward the gate（不抑制），绝不回落读
    工作区（回落即信任边界漏洞本体）。
    """
    import base64
    from urllib.parse import quote

    cmd = [
        "gh",
        "api",
        f"repos/{repository}/contents/{quote(path)}?ref={ref}",
        "--header",
        "Accept: application/vnd.github+json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        payload = json.loads(result.stdout.decode())
        content = payload.get("content", "")
        if not isinstance(content, str):
            return None
        return base64.b64decode(content).decode("utf-8", errors="replace").splitlines()
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        print(
            f"Warning: ref-tree fetch failed for {path}@{ref[:7]}: {e} "
            "— no suppression this round (fail toward the gate)",
            file=sys.stderr,
        )
        return None


def load_adjudicated_baseline(
    path: Path | str, repository: str | None = None, ref: str | None = None
) -> list[dict[str, Any]]:
    """加载并护栏校验基线文件。

    信任边界（round-2 P1）：ref 传入时条目只从 `gh api contents?ref=<ref>`
    读取（与 _fetch_head_file_lines 同通道，ref=服务端解析的 BASE sha）——
    发布工作区是 BASE checkout，但 PR 树可自带伪造基线文件，工作区路径不
    可作为条目信任源。ref 侧取不到（网络/权限/文件不存在）→ 返回空表
    （不抑制），绝不回落读工作区（回落即漏洞本体）。ref 为空串（CI 请求
    了 ref 通道但 base 未解析）→ 同样空表：绝不退回 path。ref 非空但
    repository 为空串（argparse required=True 不拒空串）→ 同样空表：
    ref 通道无从服务，工作区更不可信（round-5 P1：旧守卫 `if repository:`
    被空串跳过即落到磁盘读）。ref 缺省 None（本地/测试路径）退化为直读
    path。

    护栏（终裁 U2' 条款，全部 fail-closed）：文件缺失 → 空表（不抑制）；
    JSON/schema/条目上限/TTL/裁定引用/severity 任一非法 → exit 1（红跑，
    绝不静默放宽）。返回激活态条目列表（已剔除过期）。
    """
    if ref is not None:
        if not ref:
            print(
                "Adjudicated baseline: no BASE ref available — no suppression "
                "(fail toward the gate; workspace path is not trusted)"
            )
            return []
        if not repository:
            # ref 通道被请求但 repository 为空串：gh api 无从寻址（argparse
            # required=True 不拒空串）。此组合下任何回落都只能落回工作区
            # 磁盘——正是本函数声称堵死的路径，故同样空表 fail-closed。
            print(
                f"Adjudicated baseline: empty repository with BASE ref {ref[:7]} "
                "— no suppression (fail toward the gate; workspace path is not trusted)"
            )
            return []
        lines = _fetch_ref_file_lines(repository, ref, DEFAULT_BASELINE_PATH)
        if lines is None:
            print(
                "Adjudicated baseline unavailable from BASE ref "
                f"{ref[:7]} — no suppression (fail toward the gate)"
            )
            return []
        return _validate_adjudicated_entries(
            _parse_baseline_json("\n".join(lines), f"BASE ref {ref[:7]}")
        )

    path = Path(path)
    if not path.exists():
        print(f"Adjudicated baseline not found at {path} — no suppression")
        return []
    return _validate_adjudicated_entries(
        _parse_baseline_json(path.read_text(encoding="utf-8"), str(path))
    )


def _parse_baseline_json(text: str, origin: str) -> tuple[list[Any], str]:
    """解析基线 JSON 为 (entries, origin)；解析失败 fail-closed exit 1。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in adjudicated baseline {origin}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        print(f'ERROR: adjudicated baseline {origin} must be {{"entries": [...]}}', file=sys.stderr)
        sys.exit(1)
    return data["entries"], origin


def _validate_adjudicated_entries(
    entries_with_origin: tuple[list[Any], str],
) -> list[dict[str, Any]]:
    entries, origin = entries_with_origin
    if len(entries) > BASELINE_MAX_ENTRIES:
        print(
            f"ERROR: adjudicated baseline has {len(entries)} entries "
            f"(cap {BASELINE_MAX_ENTRIES}) — baseline is a scalpel, not a blanket",
            file=sys.stderr,
        )
        sys.exit(1)
    today = date.today()
    active: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"ERROR: baseline entry must be an object: {entry!r}", file=sys.stderr)
            sys.exit(1)
        for field in (
            "id",
            "path",
            "anchor",
            "context_sha256",
            "severity",
            "adjudication_ref",
            "created_at",
            "expires_at",
            "finding_message_pattern",
        ):
            if not isinstance(entry.get(field), str) or not entry[field]:
                print(
                    f"ERROR: baseline entry {entry.get('id')!r} missing/invalid field {field!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
        digest = entry["context_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            print(
                f"ERROR: baseline entry {entry['id']!r} context_sha256 must be a 64-hex digest",
                file=sys.stderr,
            )
            sys.exit(1)
        # severity 锚与 findings 词表同源（source-of-truth：VALID_SEVERITIES）：
        # 词表外严重度永远匹配不到任何 finding，只可能是笔误或伪造条目。
        if entry["severity"] not in VALID_SEVERITIES:
            print(
                f"ERROR: baseline entry {entry['id']!r} severity {entry['severity']!r} "
                f"outside findings vocabulary {sorted(VALID_SEVERITIES)}",
                file=sys.stderr,
            )
            sys.exit(1)
        # message 锚归一化后必须留下至少一个非空片段：零片段的 pattern 对任意
        # finding message 子串包含命中（空锚匹配一切），等于无键抑制。
        if not _message_fragments(entry["finding_message_pattern"]):
            print(
                f"ERROR: baseline entry {entry['id']!r} finding_message_pattern has no "
                "non-empty fragment after normalization — would match every message",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            created = date.fromisoformat(entry["created_at"])
            expires = date.fromisoformat(entry["expires_at"])
        except ValueError as e:
            print(f"ERROR: baseline entry {entry['id']!r} bad date: {e}", file=sys.stderr)
            sys.exit(1)
        # TTL 锚定真实日历：created_at 晚于今天（或晚于 expires_at）= 永活条
        # 目/伪时序，裁定必须发生在过去才能被缓存。
        if created > today or created > expires:
            print(
                f"ERROR: baseline entry {entry['id']!r} created_at {entry['created_at']} "
                "is in the future or after expires_at — adjudication must precede expiry",
                file=sys.stderr,
            )
            sys.exit(1)
        if (expires - created).days > BASELINE_MAX_TTL_DAYS:
            print(
                f"ERROR: baseline entry {entry['id']!r} TTL > {BASELINE_MAX_TTL_DAYS} days "
                "— expired adjudications must be re-earned, not cached",
                file=sys.stderr,
            )
            sys.exit(1)
        if expires >= today:
            active.append(entry)
        else:
            print(f"Baseline entry {entry['id']!r} expired ({entry['expires_at']}) — inactive")
    return active


def _message_fragments(pattern: str) -> list[str]:
    """pattern 归一化后的非空 `/` 片段（匹配与护栏共用同一份切分）。"""
    return [f for f in _normalized_message(pattern).split("/") if f]


def _normalized_message(message: str) -> str:
    """message 归一化：casefold + 空白折叠 + 连字符转空格——锚比较容忍审查
    措辞漂移（大小写/空白/连字与否）。"""
    return " ".join(message.casefold().replace("-", " ").split())


def _message_matches(finding_message: str, pattern: str) -> bool:
    """message 锚：pattern 的 `/` 分隔片段全部归一化包含于 finding message。

    片段先归一化再拆分（`/` 分隔符语义优先于片段内空白），随后每个片段做
    casefold+空白折叠后的子串包含——容忍 shard 措辞漂移（大小写/空白/前后
    缀/连字符），又锁住同位置新缺陷：新缺陷 message 不含裁定片段，位置命中
    也不抑制。零非空片段在加载护栏处已 fail-closed（空锚匹配一切）。
    """
    normalized = _normalized_message(finding_message)
    return all(fragment in normalized for fragment in _message_fragments(pattern))


def apply_adjudicated_baseline(
    findings: list[dict[str, Any]],
    baseline_path: Path | str | None,
    repository: str | None = None,
    head_sha: str | None = None,
    base_ref: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按基线把 findings 分流为 (active, suppressed)。

    命中要求全键匹配：路径相等、severity ∈ 条目、message 归一化片段全含、
    finding 行锚定到锚点行（±radius 容差吸收行号漂移）、BASE 与 PR-head 双树
    窗口哈希都等于条目哈希。base_ref 传入时条目只从该服务端 ref 加载（信任
    边界：PR 树可自带伪造基线文件，工作区路径不作为条目源）；缺省（本地/
    测试路径）退化为直读 baseline_path 且无 head 侧窗口时单树语义。head 侧
    取不到文件 = 该条目本轮不抑制（宁红勿漏）。
    """
    if not findings or baseline_path is None:
        return findings, []
    active_entries = load_adjudicated_baseline(baseline_path, repository=repository, ref=base_ref)
    if not active_entries:
        return findings, []
    by_path: dict[str, list[tuple[dict[str, Any], list[str] | None, list[str] | None, int]]] = (
        defaultdict(list)
    )
    for entry in active_entries:
        lines = _read_file_lines(entry["path"])
        head_lines = (
            _fetch_head_file_lines(repository, head_sha, entry["path"])
            if repository and head_sha
            else None
        )
        radius = entry.get("context_radius", DEFAULT_CONTEXT_RADIUS)
        by_path[entry["path"]].append((entry, lines, head_lines, radius))

    def match(
        prepared: tuple[dict[str, Any], list[str] | None, list[str] | None, int],
        finding: dict[str, Any],
    ) -> bool:
        entry, lines, head_lines, radius = prepared
        # 仅同严重度可抑制：裁定针对的是该严重度的那个发现；位置重报但升级
        # （P1→P0）或降级（P1→P2）都算新缺陷，保持 blocking。
        if finding.get("severity") != entry["severity"]:
            return False
        # message 锚：同窗口同严重度但措辞不含裁定片段 = 新缺陷，不抑制。
        if not _message_matches(finding.get("message", ""), entry["finding_message_pattern"]):
            return False
        if lines is None:
            return False
        # 行号 ±radius 容差：shard 对 PR head 逐字报告，PR 侧行号相对 BASE
        # 偏移（qa.yml:323 vs main :325 实证）；双树窗口哈希仍锁内容。
        candidates = [finding["line"] - 1 + d for d in range(-radius, radius + 1)]
        for idx in candidates:
            if idx < 0 or not (0 <= idx < len(lines)):
                continue
            if adjudicated_context_hash(lines, idx, radius) != entry["context_sha256"]:
                continue
            if head_sha and repository:
                # findings 行号同源 head 树：锚点在 head 副本上解析，双树窗口
                # 必须一致；head 内容变了（PR 改了锚点窗口）即不抑制。
                if head_lines is None:
                    return False
                head_anchors = [
                    i
                    for i, line in enumerate(head_lines)
                    if entry["anchor"] in line.strip() and abs(i - (finding["line"] - 1)) <= radius
                ]
                if not head_anchors:
                    return False
                if any(
                    adjudicated_context_hash(head_lines, h_idx, radius) != entry["context_sha256"]
                    for h_idx in head_anchors
                ):
                    return False
            return True
        return False

    active, suppressed = [], []
    for finding in findings:
        prepared_list = by_path.get(finding.get("file", ""), [])
        hit = next((p for p in prepared_list if match(p, finding)), None)
        if hit is None:
            active.append(finding)
        else:
            marked = dict(finding)
            marked["adjudicated_suppressed"] = True
            marked["suppression_ref"] = hit[0]["id"]
            marked["adjudication_ref"] = hit[0]["adjudication_ref"]
            suppressed.append(marked)
    return active, suppressed


def _read_file_lines(path: str | Path) -> list[str] | None:
    """读取 BASE 工作树文件行（发布器运行在 BASE checkout）。

    BASE 侧哈希只覆盖一半的键；PR-head 侧窗口由 _fetch_head_file_lines 经
    gh api 补齐（findings 行号同源 head 树，head checkout 不在工作区）。
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"Warning: baseline anchor file {path} unreadable: {e}", file=sys.stderr)
        return None


def _suppressed_section_lines(suppressed: list[dict[str, Any]]) -> list[str]:
    """抑制项披露小节（共享体例：显式列出 + 标记，绝不静默丢弃）。"""
    if not suppressed:
        return []
    lines = [
        "",
        "### ⚖️ Adjudicated-suppressed findings (not blocking)",
        "",
        f"{len(suppressed)} finding(s) matched the main-maintained adjudicated "
        "baseline (design decisions / false positives only). Listed for audit; "
        "code changes at the anchored location invalidate the entry.",
        "",
    ]
    for f in suppressed:
        ref = f.get("adjudication_ref", "")
        suffix = f" — adjudication: {ref}" if ref else ""
        lines.append(
            f"- {ADJUDICATED_SUPPRESSED_MARK} **{f['severity']}** "
            f"`{f['file']}:{f['line']}`: {f['message']}{suffix}"
        )
    lines.append("")
    return lines


def group_by_shard(findings: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """按 shard_id 分组（用于 summary comment 分节）。"""
    # 这里假设 findings 已带 shard_id 字段（从 validate_findings 解包后注入）
    groups = defaultdict(list)
    for f in findings:
        groups[f.get("shard_id", -1)].append(f)
    return dict(groups)


def count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    """统计严重度分布。"""
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for f in findings:
        sev = f["severity"]
        if sev in counts:
            counts[sev] += 1
    return counts


def count_blocking_findings(findings: list[dict[str, Any]]) -> int:
    """P0/P1 为合并阻断严重度（droid-review check 必须 fail-closed）。"""
    return sum(1 for f in findings if f.get("severity") in BLOCKING_SEVERITIES)


def load_findings_files(pattern: str) -> list[dict[str, Any]]:
    """
    从匹配 pattern 的文件加载 findings（glob 模式）。
    返回合并后的 findings 列表（带 shard_id 注入）。
    """
    all_findings = []
    for path in Path().glob(pattern):
        try:
            data = json.loads(path.read_text())
            if not validate_findings(data):
                print(f"ERROR: invalid findings schema in {path}", file=sys.stderr)
                sys.exit(1)
            # 注入 shard_id 到每条 finding（便于后续分组）
            for f in data["findings"]:
                f["shard_id"] = data["shard_id"]
            all_findings.extend(data["findings"])
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
            sys.exit(1)
    return all_findings


def _parse_paginated_json(output: str) -> list[dict[str, Any]]:
    """Parse gh api --paginate output, which may be one or more JSON arrays.

    gh --paginate concatenates page outputs. For list endpoints each page
    is a JSON array; the combined output is either a single array or
    multiple arrays on separate lines. This helper handles both forms.
    """
    items: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            items.extend(parsed)
        elif isinstance(parsed, dict):
            items.append(parsed)
    return items


def find_existing_summary_comment(
    pr_number: int,
    repository: str,
    round_number: int | None = None,
) -> int | None:
    """
    Retrieve existing PR comments and find one containing the SUMMARY_MARKER.

    Uses --paginate to fetch all pages (GitHub defaults to 30 per page).
    When round_number is given, only a summary carrying the same round's
    marker counts as a duplicate — a previous round's summary is history,
    not a rerun of this one. Returns the comment ID if found, None otherwise.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--paginate",
            ],
            capture_output=True,
            check=True,
        )
        comments = _parse_paginated_json(result.stdout.decode())
        for comment in comments:
            body = comment.get("body", "")
            if SUMMARY_MARKER not in body:
                continue
            if round_number is not None:
                same_round = _ROUND_MARKER_RE.search(body)
                if not same_round or int(same_round.group(1)) != round_number:
                    continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                return comment_id
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        # Fail-open for dedup: if we can't retrieve, just post normally
        print(f"Warning: could not retrieve existing comments for dedup: {e}", file=sys.stderr)
    return None


def scan_round_markers(pr_number: int, repository: str) -> tuple[list[int], int]:
    """
    Scan issue + review comments for ``<!-- droid-review-round-N -->`` markers.

    Returns (sorted distinct rounds, max round); max is 0 when no marker found.
    Reviews comment bodies too so inline posts can never inflate the count.
    Fail-open: any API failure → ([], 0), i.e. treated as first round.
    """
    rounds: set[int] = set()
    for endpoint in (
        f"repos/{repository}/issues/{pr_number}/comments",
        f"repos/{repository}/pulls/{pr_number}/comments",
    ):
        try:
            result = subprocess.run(
                ["gh", "api", endpoint, "--paginate"],
                capture_output=True,
                check=True,
            )
            comments = _parse_paginated_json(result.stdout.decode())
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
            print(f"Warning: could not scan round markers from {endpoint}: {e}", file=sys.stderr)
            continue
        for comment in comments:
            match = _ROUND_MARKER_RE.search(comment.get("body", ""))
            if match:
                rounds.add(int(match.group(1)))
    ordered = sorted(rounds)
    return ordered, (ordered[-1] if ordered else 0)


def count_round_markers(bodies: list[str]) -> int:
    """Count comment bodies that carry a round sentinel marker (test-facing)."""
    return sum(1 for body in bodies if _ROUND_MARKER_RE.search(body))


def next_review_round(rounds: list[int]) -> int:
    """Derive this run's round number: max prior marker + 1 (first run → 1)."""
    return (max(rounds) + 1) if rounds else 1


def find_existing_inline_comments(pr_number: int, repository: str) -> set[tuple[str, int]]:
    """
    Retrieve existing PR review comments and return set of (path, line) tuples.

    Uses --paginate to fetch all pages (GitHub defaults to 30 per page).
    This enables cross-run dedup: reruns skip posting if same location already commented.
    """
    existing = set()
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/pulls/{pr_number}/comments",
                "--paginate",
            ],
            capture_output=True,
            check=True,
        )
        comments = _parse_paginated_json(result.stdout.decode())
        for comment in comments:
            path = comment.get("path")
            line = comment.get("line")
            if path and line:
                existing.add((path, int(line)))
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as e:
        # Fail-open for dedup: if we can't retrieve, just post normally
        print(
            f"Warning: could not retrieve existing inline comments for dedup: {e}",
            file=sys.stderr,
        )
    return existing


def post_inline_comment(
    finding: dict[str, Any],
    pr_number: int,
    repository: str,
    commit_id: str,
) -> bool:
    """
    Post inline review comment on PR (with cross-run dedup).

    Checks for existing comments at the same (file, line) before posting.
    Returns True if successful or skipped (dedup), False if API call failed.
    """
    # Cross-run dedup: skip if same (file, line) already has a comment
    existing = find_existing_inline_comments(pr_number, repository)
    key = (finding["file"], finding["line"])
    if key in existing:
        print(f"  ⊘ Skipped (already exists): {finding['file']}:{finding['line']}")
        return True

    body = f"**[{finding['severity']}]** {finding['message']}"

    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": finding["file"],
        "line": finding["line"],
        "side": "RIGHT",
    }

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/pulls/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps(payload).encode(),
            check=True,
            capture_output=True,
        )
        print(f"  ✓ Inline comment posted: {finding['file']}:{finding['line']}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        if "422" in stderr:
            print(f"  ⚠ 422 error (line may be invalid): {finding['file']}:{finding['line']}")
            return False
        print(f"  ✗ Failed to post inline comment: {stderr}", file=sys.stderr)
        return False


def post_summary_comment(
    findings: list[dict[str, Any]],
    pr_number: int,
    repository: str,
    by_shard: dict[int, list[dict[str, Any]]],
    round_number: int = 1,
    suppressed: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Post summary comment on PR with findings grouped by shard (with dedup).

    The body carries the round sentinel so later runs can count review rounds.
    Dedup is round-aware: a summary already carrying this run's marker is a
    rerun of the same review round → skip; an older round's summary is history
    and a fresh summary is posted.
    Returns True if successful or skipped (dedup).
    """
    existing_comment_id = find_existing_summary_comment(
        pr_number, repository, round_number=round_number
    )
    if existing_comment_id is not None:
        print(f"  ⊘ Skipped summary (same round {round_number} exists, id={existing_comment_id})")
        return True

    total_counts = count_by_severity(findings)

    body_lines = [
        SUMMARY_MARKER,
        round_marker(round_number),
        "## 🔍 Droid Auto Review — Findings Summary",
        "",
        f"**Total findings**: {len(findings)}",
        "",
        "**By severity**:",
    ]

    for sev in ["P0", "P1", "P2", "P3"]:
        count = total_counts.get(sev, 0)
        if count > 0:
            body_lines.append(f"- **{sev}**: {count}")

    body_lines.append("")
    body_lines.append("---")
    body_lines.append("")
    body_lines.extend(_suppressed_section_lines(suppressed or []))

    # Group by shard
    for shard_id in sorted(by_shard.keys()):
        shard_findings = by_shard[shard_id]
        shard_counts = count_by_severity(shard_findings)

        body_lines.append(f"### Shard {shard_id} ({len(shard_findings)} findings)")
        body_lines.append("")

        for sev in ["P0", "P1", "P2", "P3"]:
            count = shard_counts.get(sev, 0)
            if count > 0:
                body_lines.append(f"- **{sev}**: {count}")

        body_lines.append("")
        body_lines.append("<details>")
        body_lines.append(f"<summary>Details ({len(shard_findings)} items)</summary>")
        body_lines.append("")

        # §3a（终裁）：P0/P1 永不折叠——截断只允许吃非阻塞严重度。折叠行的
        # 计数 = 非阻塞余量；被截的阻塞级逐条展开披露（阻塞全量可见）。
        listed = shard_findings[:20]
        rest = shard_findings[20:]
        rest_non_blocking = [f for f in rest if f["severity"] not in BLOCKING_SEVERITIES]
        rest_blocking = [f for f in rest if f["severity"] in BLOCKING_SEVERITIES]

        for f in listed:
            body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")

        for f in rest_blocking:
            body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")
        if rest_non_blocking:
            body_lines.append(f"- ... and {len(rest_non_blocking)} more")

        body_lines.append("")
        body_lines.append("</details>")
        body_lines.append("")

    body = "\n".join(body_lines)

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"body": body}).encode(),
            check=True,
            capture_output=True,
        )
        print("✓ Summary comment posted")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"✗ Failed to post summary comment: {stderr}", file=sys.stderr)
        return False


def post_terminal_summary(
    findings: list[dict[str, Any]],
    pr_number: int,
    repository: str,
    round_number: int,
    suppressed: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Post the round-cap terminal summary (no inline comments accompany it).

    States that the per-PR review round cap (MAX_REVIEW_ROUNDS) is exhausted
    and hands the remaining findings back to a human. Still carries the round
    sentinel so the round count stays monotonic across capped PRs.
    """
    total_counts = count_by_severity(findings)
    blocking = [f for f in findings if f.get("severity") in BLOCKING_SEVERITIES]

    body_lines = [
        SUMMARY_MARKER,
        round_marker(round_number),
        "## 🔒 Droid Auto Review — Round Cap Reached",
        "",
        f"This PR has reached the automatic review round limit "
        f"({MAX_REVIEW_ROUNDS} rounds). Automated review is stopped and the "
        f"remaining findings are handed back to a human reviewer "
        f"(`{NEEDS_HUMAN_LABEL}` label).",
        "",
        f"**Total findings (this round)**: {len(findings)}",
        f"**Blocking (P0/P1)**: {len(blocking)}",
        "",
        "**By severity**:",
    ]
    for sev in ["P0", "P1", "P2", "P3"]:
        count = total_counts.get(sev, 0)
        if count > 0:
            body_lines.append(f"- **{sev}**: {count}")

    body_lines.append("")
    body_lines.append("<details>")
    body_lines.append(f"<summary>Details ({len(findings)} items)</summary>")
    body_lines.append("")
    # §3a（终裁）：P0/P1 永不折叠——round-5 第 4 条 P1 被折叠进 "... and 42
    # more" 实证。截断只允许吃非阻塞严重度：折叠行计数 = 非阻塞余量，被截的
    # 阻塞级逐条展开披露。
    listed = findings[:50]
    rest = findings[50:]
    rest_non_blocking = [f for f in rest if f["severity"] not in BLOCKING_SEVERITIES]
    for f in listed:
        body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")
    for f in rest:
        if f["severity"] in BLOCKING_SEVERITIES:
            body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")
    if rest_non_blocking:
        body_lines.append(f"- ... and {len(rest_non_blocking)} more")
    body_lines.append("")
    body_lines.append("</details>")
    body_lines.extend(_suppressed_section_lines(suppressed or []))

    body = "\n".join(body_lines)

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"body": body}).encode(),
            check=True,
            capture_output=True,
        )
        print(f"✓ Terminal summary posted (round cap {MAX_REVIEW_ROUNDS} reached)")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"✗ Failed to post terminal summary: {stderr}", file=sys.stderr)
        return False


def add_needs_human_label(pr_number: int, repository: str) -> bool:
    """
    Attach the needs-human label to the PR (issues: write permission suffices).

    Fail-open: a label failure must never break the publishing flow.
    """
    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/labels",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"labels": [NEEDS_HUMAN_LABEL]}).encode(),
            check=True,
            capture_output=True,
        )
        print(f"✓ Label attached: {NEEDS_HUMAN_LABEL}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"Warning: could not attach {NEEDS_HUMAN_LABEL} label: {stderr}", file=sys.stderr)
        return False


def resolve_stale_bot_threads(
    pr_number: int,
    repository: str,
    current_findings: list[dict[str, Any]],
) -> None:
    """
    Resolve dissolvable bot review threads after publishing (fail-open).

    只清「已消解证据」的 thread，门禁不弱化：
    - outdated + github-actions bot + unresolved → resolveReviewThread
    - active bot thread 锚定位置不在本轮 findings（P2/P3 语义的已消解）→ resolve
    - 本轮 P0/P1 findings 对应 path 的 active bot thread 保持 unresolved（真门禁）
    - 人类作者 thread 永不触碰

    GraphQL 查询/变更失败仅 Warning，不抛出、不影响发布结果。
    """
    owner, _, name = repository.partition("/")
    if not owner or not name:
        print(
            f"Warning: invalid repository slug {repository!r}, skip thread resolve", file=sys.stderr
        )
        return

    # 本轮 P0/P1 锚定位置集合（安全护栏：这些位置绝不自动 resolve）
    blocking_paths = {
        f["file"] for f in current_findings if f.get("severity") in BLOCKING_SEVERITIES
    }

    stale_thread_ids: list[str] = []
    cursor: str | None = None
    pages = 0
    try:
        while pages < _MAX_REVIEW_THREAD_PAGES:
            args = [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
                "-f",
                f"query={_REVIEW_THREADS_QUERY}",
            ]
            if cursor is not None:
                args += ["-F", f"cursor={cursor}"]
            result = subprocess.run(args, capture_output=True, check=True)
            payload = json.loads(result.stdout.decode())
            data = payload.get("data", {})
            threads = data.get("repository", {}).get("pullRequest", {}).get("reviewThreads", {})
            nodes = threads.get("nodes", [])
            page_info = threads.get("pageInfo", {})
            for node in nodes:
                thread_id = node.get("id")
                if not thread_id or node.get("isResolved"):
                    continue
                comments = node.get("comments", {}).get("nodes", [])
                first_comment = comments[0] if comments else {}
                author = first_comment.get("author") or {}
                author_login = author.get("login") or ""
                # __typename 是拼写之外的第二道资格线：缺省/非 Bot 一律不自动
                # resolve（资格误判的代价是错误清门禁，宁漏勿错）
                if author.get("__typename") != "Bot":
                    continue
                if author_login not in BOT_LOGIN_SPELLINGS:
                    continue
                if node.get("isOutdated"):
                    stale_thread_ids.append(thread_id)
                    continue
                path = node.get("path") or ""
                # active thread 锚定本轮 P0/P1 位置 → 门禁，保持 unresolved
                if path in blocking_paths:
                    continue
                # 其余 active bot thread（P2/P3 语义已消解：本轮无该位置 finding）→ resolve
                stale_thread_ids.append(thread_id)
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
                pages += 1
            else:
                break
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        print(f"Warning: could not query review threads for resolve: {e}", file=sys.stderr)
        return

    if not stale_thread_ids:
        return

    resolved = 0
    for thread_id in stale_thread_ids:
        try:
            subprocess.run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-F",
                    f"id={thread_id}",
                    "-f",
                    f"query={_RESOLVE_THREAD_MUTATION}",
                ],
                capture_output=True,
                check=True,
            )
            resolved += 1
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""
            print(f"Warning: failed to resolve thread {thread_id}: {stderr}", file=sys.stderr)

    print(f"✓ Auto-resolved {resolved}/{len(stale_thread_ids)} stale bot threads")


def main() -> None:
    """CLI entry: load findings, validate, dedup, publish to GitHub."""
    import argparse

    parser = argparse.ArgumentParser(description="Publish shard review findings")
    parser.add_argument("--pattern", required=True, help="Glob pattern for findings files")
    parser.add_argument("--pr-number", type=int, required=True, help="PR number")
    parser.add_argument("--repository", required=True, help="Repository (owner/name)")
    parser.add_argument("--commit-id", help="Commit ID for inline comments (optional)")
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE_PATH,
        help=f"Adjudicated-findings baseline file (default: {DEFAULT_BASELINE_PATH})",
    )
    parser.add_argument(
        "--baseline-ref",
        default=None,
        help=(
            "Server-side git ref (BASE sha) to load the baseline from via "
            "gh api contents; empty = CI ref channel with BASE unresolved "
            "(no suppression, fail-closed); omitted = read --baseline from "
            "disk (local/testing)"
        ),
    )

    args = parser.parse_args()

    print(f"Loading findings from: {args.pattern}")
    findings = load_findings_files(args.pattern)

    if not findings:
        print("No findings to publish")
        return

    print(f"Loaded {len(findings)} findings")

    # Deduplicate
    unique = deduplicate_findings(findings)
    print(f"After dedup: {len(unique)} findings")

    # Adjudicated baseline suppression（U2'）：设计项/误报降级披露，门禁读过滤后
    # 严重度。suppress_findings 恒为全量披露（不静默），active 才进门禁统计。
    # repository/head_sha 驱动双树键（head 侧窗口经 gh api 取，缺失=单树语义）。
    # baseline_ref 驱动条目信任边界（round-3 P1：三态原样透传，禁止 or None
    # 压缩——None=本地/测试磁盘模式；''=CI ref 通道但 BASE 未解析 → 空 table
    # fail-closed；sha=从服务端 ref 加载。压缩 ''→None 会把 CI 空值静默改道
    # 工作区磁盘路径，绕回 round-2 修掉的伪造基线洞）。
    active, suppress_findings = apply_adjudicated_baseline(
        unique,
        args.baseline,
        repository=args.repository,
        head_sha=args.commit_id,
        base_ref=args.baseline_ref,
    )
    if suppress_findings:
        print(
            f"Adjudicated baseline: {len(suppress_findings)} suppressed "
            f"({len(active)} remain active)"
        )
    else:
        print("Adjudicated baseline: no hits")
    unique = active

    # Snapshot the full deduped set for safety-critical downstream consumers.
    # The inline-degrade below rebinds ``unique`` to the failed subset
    # (display-only: summary + stats). resolve_stale_bot_threads derives its
    # P0/P1 blocking_paths from the list it receives — feeding it the failed
    # subset would let a single inline 422 auto-resolve every P0/P1-anchored
    # active bot thread, including the just-posted ones.
    guard_findings = list(unique)

    # Group by shard（round-capped 分支的统计输出也依赖它）
    by_shard = group_by_shard(unique)

    # Round-cap sentinel: count prior rounds from PR comments (fail-open)
    prior_rounds, _max_prior = scan_round_markers(args.pr_number, args.repository)
    round_number = next_review_round(prior_rounds)
    print(f"Review round: {round_number} (cap {MAX_REVIEW_ROUNDS})")
    round_capped = round_number > MAX_REVIEW_ROUNDS

    if round_capped:
        print(
            f"\nRound cap reached (round {round_number} > {MAX_REVIEW_ROUNDS}), "
            "skipping inline comments and posting terminal summary..."
        )
        post_terminal_summary(
            unique, args.pr_number, args.repository, round_number, suppressed=suppress_findings
        )
        print("\nAttaching needs-human label...")
        add_needs_human_label(args.pr_number, args.repository)
    else:
        # Post inline comments (batch of 50 max per GitHub API limit)
        if args.commit_id:
            print("\nPosting inline comments...")
            inline_success = 0
            inline_failed = []

            for f in unique[:50]:
                if post_inline_comment(f, args.pr_number, args.repository, args.commit_id):
                    inline_success += 1
                else:
                    inline_failed.append(f)

            print(f"Inline comments: {inline_success} posted, {len(inline_failed)} failed")

            # Degrade failed inline comments to summary
            if inline_failed:
                print("\nDegrading failed inline comments to summary...")
                unique = inline_failed
                by_shard = group_by_shard(unique)
        else:
            print("No commit-id provided, skipping inline comments")

        # Post summary comment
        print("\nPosting summary comment...")
        post_summary_comment(
            unique,
            args.pr_number,
            args.repository,
            by_shard,
            round_number,
            suppressed=suppress_findings,
        )

    # Auto-resolve stale bot threads（fail-open；P0/P1 active 保持 unresolved）
    # 收全量快照而非降级后的 unique：门禁路径集合必须来自本轮全部 findings
    print("\nResolving stale bot threads...")
    resolve_stale_bot_threads(args.pr_number, args.repository, guard_findings)

    # Output statistics（展示用 unique 可为 inline 降级子集；门禁统计必须来自 guard_findings）
    total_counts = count_by_severity(unique)
    blocking_count = count_blocking_findings(guard_findings)
    guard_counts = count_by_severity(guard_findings)
    output = {
        "total_findings": len(unique),
        "review_round": round_number,
        "round_capped": round_capped,
        "by_severity": total_counts,
        "adjudicated_suppressed": {
            "count": len(suppress_findings),
            "refs": sorted({f.get("suppression_ref", "") for f in suppress_findings}),
        },
        "blocking_findings": {
            "count": blocking_count,
            "by_severity": {sev: guard_counts[sev] for sev in sorted(BLOCKING_SEVERITIES)},
        },
        "by_shard": {
            shard_id: {
                "count": len(fs),
                "by_severity": count_by_severity(fs),
            }
            for shard_id, fs in sorted(by_shard.items())
        },
    }
    print("\n" + json.dumps(output, indent=2))

    if blocking_count:
        print(
            f"ERROR: {blocking_count} blocking finding(s) (P0/P1) — droid-review check must fail",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
