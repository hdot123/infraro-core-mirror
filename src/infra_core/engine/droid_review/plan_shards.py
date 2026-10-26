#!/usr/bin/env python3
"""
TD-DR-01 分片规划器：贪心装箱 + 目录亲和 + 溢出处理。

输入：文件列表（从 gh pr diff --name-only 获取）
输出：JSON，含 shards 列表（每片含 shard_id + files + lines）
不变量：
  - 并集 = 输入全集（永不丢文件）
  - 两两不相交（文件不重复）
  - 同目录文件尽量聚集（目录亲和）
  - 单片不超 max_files（除非溢出放大）
  - 单片不超 max_lines 行（除非溢出放大，或单文件自身超预算 → 独占一片）
  - 分片数尽量不超 max_count（溢出时可超，附告警）

溢出处理：当文件量大到无法塞入 max_count 个容量上限内的分片时，
自动放大分片数（突破 max_count 上限），并在输出中设 overflow_warning=true。

行数预算（SHARD_MAX_LINES）：2026-10-19 run 39253458566 实证只按文件数
装箱会让 2 个 600-1100 行大文件同包超时（ci.yml 778 + droid-autofix.yml
453 = 1231 行被 soft_timeout 杀），而 tests 小文件包 6-36min 跑完——
内容量失衡才是根因。max_lines=0/None 关闭行数维度（向后兼容 2 参调用）。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _group_by_directory(files: list[str]) -> dict[str, list[str]]:
    """按父目录分组，保持目录亲和。"""
    groups = defaultdict(list)
    for f in files:
        parent = str(Path(f).parent)
        groups[parent].append(f)
    return dict(groups)


def _count_lines(path: str) -> int:
    """统计文件行数；文件不存在/不可读时按 0 计（规划阶段防御性处理）。"""
    try:
        with Path(path).open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def plan_shards(
    files: list[str],
    max_files: int = 25,
    max_count: int = 6,
    max_lines: int = 0,
    deleted_files: dict[str, int] | None = None,
) -> dict[str, Any]:
    """
    贪心装箱 + 目录亲和 + 行数预算。

    算法：
    1. 按目录分组
    2. 对每个目录组，若 <= max_files 则整组作为一个候选
    3. 若 > max_files，按 max_files 切块
    4. 贪心合并候选到分片（不超 max_files；max_lines>0 时再受行数预算约束，
       单文件自身超预算者独占一片，不硬拆文件）
    5. 若分片数 > max_count，设 overflow_warning

    max_lines<=0 时关闭行数维度，行为与纯文件数模式一致（向后兼容）。

    deleted_files：删除文件 → BASE 侧（pre-PR HEAD）行数。删除文件在 PR
    head 树不存在，_count_lines 恒 0；0 行计费会让 deletion-heavy 变更完全
    绕过行数预算（round-3 ③：5000 行删除可与其他文件自由同包，复现内容
    失衡超时）。调用方从 BASE 检出侧统计行数后经本参传入，删除文件与普通
    文件同预算参与装箱。仅行数预算启用时生效。
    """
    if max_files <= 0:
        raise ValueError(f"max_files must be positive, got {max_files}")
    if max_count <= 0:
        raise ValueError(f"max_count must be positive, got {max_count}")
    if max_lines < 0:
        raise ValueError(f"max_lines must be non-negative, got {max_lines}")

    line_budget_active = max_lines > 0
    # 行数只在预算启用时统计，避免兼容模式引入文件系统读取。
    # 删除文件优先取 BASE 侧计费值，缺失该通道时退回 head 树读取（=0）。
    # 并集合并在空检查之前（round-9 sh9 ②）：纯删除 PR（files=[] +
    # deleted_files 非空）若先走 `if not files:` 早退，删除文件全部静默
    # 丢弃（0 分片，违反「永不丢文件」并集不变量）。
    deleted_map = deleted_files or {}
    if line_budget_active and deleted_map:
        # 删除文件纳入规划候选（并集不变量：不丢文件）；已显式传入的不再读盘。
        files = list(files) + [f for f in deleted_map if f not in set(files)]

    if not files:
        return {
            "shards": [],
            "total_files": 0,
            "overflow_warning": False,
        }

    line_counts = (
        {f: deleted_map.get(f, _count_lines(f)) for f in files} if line_budget_active else {}
    )

    def _fits(shard: list[str], candidate: list[str]) -> bool:
        if len(shard) + len(candidate) > max_files:
            return False
        if line_budget_active:
            return sum(line_counts[f] for f in shard + candidate) <= max_lines
        return True

    # Step 1: group by directory
    dir_groups = _group_by_directory(files)

    # Step 2: split large groups into chunks of max_files
    chunks = []
    for _dir_path, dir_files in dir_groups.items():
        for i in range(0, len(dir_files), max_files):
            chunks.append(dir_files[i : i + max_files])

    # Step 2b: 行数预算再切块——单文件超预算者独占成片；其余按行数切
    if line_budget_active:
        budgeted_chunks = []
        for chunk in chunks:
            current: list[str] = []
            current_lines = 0
            for f in chunk:
                fl = line_counts[f]
                if fl > max_lines:
                    # 大文件独占：不硬拆文件
                    if current:
                        budgeted_chunks.append(current)
                        current = []
                        current_lines = 0
                    budgeted_chunks.append([f])
                elif current and current_lines + fl > max_lines:
                    budgeted_chunks.append(current)
                    current = [f]
                    current_lines = fl
                else:
                    current.append(f)
                    current_lines += fl
            if current:
                budgeted_chunks.append(current)
        chunks = budgeted_chunks

    # Step 3: greedy bin-packing into shards
    shards_files: list[list[str]] = []
    for chunk in chunks:
        placed = False
        for shard in shards_files:
            if _fits(shard, chunk):
                shard.extend(chunk)
                placed = True
                break
        if not placed:
            shards_files.append(list(chunk))

    # Step 3b: 行数预算硬保证（round-1 P1-4 源侧兜底）。Step 3 的跨 chunk
    # 合并可能重新超预算（_fits 检查的是合并后总和，但目录亲和多 chunk
    # 并入同一 shard 时仍可能超），预算启用时逐 shard 复核——超预算 shard
    # 按预算重新切块，单文件超预算者独占成片。缺失文件（行数 0）不参与
    # 开包判定。这是行数维度的最后防线：workflow 侧接线（真实行数）+
    # 规划器内部复核双保险，确保 lines ≤ max_lines 或「单文件自身超预算」。
    if line_budget_active:
        final_shards: list[list[str]] = []
        for shard in shards_files:
            shard_total = sum(line_counts[f] for f in shard)
            if shard_total <= max_lines:
                final_shards.append(shard)
                continue
            # 超预算 shard 重切：按 Step 2b 同款贪心（大文件独占）
            current2: list[str] = []
            current_lines = 0
            for f in shard:
                fl = line_counts[f]
                if fl > max_lines:
                    if current2:
                        final_shards.append(current2)
                        current2 = []
                        current_lines = 0
                    final_shards.append([f])
                elif current2 and current_lines + fl > max_lines:
                    final_shards.append(current2)
                    current2 = [f]
                    current_lines = fl
                else:
                    current2.append(f)
                    current_lines += fl
            if current2:
                final_shards.append(current2)
        shards_files = final_shards

    # Step 4: check overflow
    overflow_warning = len(shards_files) > max_count

    # Step 5: build output（lines 字段仅在预算启用时输出——兼容模式输出面不变）
    shards = []
    for i, shard_files_list in enumerate(shards_files):
        entry: dict[str, Any] = {
            "shard_id": i,
            "files": sorted(shard_files_list),
        }
        if line_budget_active:
            entry["lines"] = sum(line_counts[f] for f in shard_files_list)
        shards.append(entry)

    return {
        "shards": shards,
        "total_files": len(files),
        "overflow_warning": overflow_warning,
    }


def main() -> None:
    """CLI entry: read files from stdin (one per line), output JSON.

    Usage: plan_shards.py [MAX_FILES] [MAX_COUNT] [MAX_LINES] [DELETED_LINES_FILE]
    MAX_LINES 省略或传 0 时保持纯文件数模式（向后兼容）。
    DELETED_LINES_FILE（round-3 ③）：TSV（filename<TAB>base_lines），删除文件
    按 BASE 侧行数计费的通道；文件不存在或为空 = 无删除计费。
    """
    max_files = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    max_count = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    max_lines = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    deleted_files: dict[str, int] = {}
    if len(sys.argv) > 4 and sys.argv[4]:
        deleted_path = Path(sys.argv[4])
        if deleted_path.exists():
            for line in deleted_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                name, _, raw = line.partition("\t")
                try:
                    deleted_files[name.strip()] = int(raw.strip() or 0)
                except ValueError:
                    deleted_files[name.strip()] = 0

    files = [line.strip() for line in sys.stdin if line.strip()]

    try:
        result = plan_shards(
            files,
            max_files=max_files,
            max_count=max_count,
            max_lines=max_lines,
            deleted_files=deleted_files,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
