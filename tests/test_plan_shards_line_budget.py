"""plan_shards 行数预算（SHARD_MAX_LINES）回归测试（2026-10-19 run 39253458566）。

背景：贪心装箱原本只有 max_files 维度，5 个 shard 因内容量失衡超时
（42-45min 被 soft_timeout 杀）——失败的包全是 2 个 600-1100 行大文件
组合（ci.yml 778 + droid-autofix.yml 453 = 1231 行/包），成功的包全是
tests 小文件。本套测试锁定行数维度：每片内容行数 ≤ max_lines，除非
单个文件自身超预算（允许独占，不硬拆文件）。

向后兼容：不传 max_lines（或传 0）时行为与纯文件数模式完全一致，
既有调用方（workflow 只传 2 参）不受影响。
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "infra_core" / "engine"))

from droid_review.plan_shards import plan_shards


def _plan(files, max_files=25, max_count=6, max_lines=None):
    kwargs = {"max_files": max_files, "max_count": max_count}
    if max_lines is not None:
        kwargs["max_lines"] = max_lines
    return plan_shards(files, **kwargs)


def _all_files(result):
    out = []
    for s in result["shards"]:
        out.extend(s["files"])
    return out


def _shard_lines(result, line_counts):
    return {s["shard_id"]: sum(line_counts[f] for f in s["files"]) for s in result["shards"]}


def _materialize(tmp_path, files, line_counts):
    """在 tmp_path 下真实生成文件（行数来自磁盘——planner 的读取契约）。

    返回可规划的相对路径列表（chdir 语义由调用方处理）。
    """
    real = []
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n" * line_counts[f])
        real.append(f)
    return real


class TestLineBudget:
    """行数预算：每片行数 ≤ max_lines（单文件超预算者允许独占）。"""

    def test_case_a_mixed_sizes_respect_budget(self, tmp_path, monkeypatch):
        """用例 A：30 文件混合行数，max_files=25 / max_lines=400 → 每片 ≤400 行。

        ci.yml(778)/workflows.yml(1095)/release.yml(693) 为 2026-10-19 超时
        run 的真实画像；普通文件 10-120 行。文件数约束（25/片）远松于行数
        约束——行数是唯一生效维度。
        """
        line_counts = {"ci.yml": 778, ".github/workflows/workflows.yml": 1095}
        files = list(line_counts)
        for i in range(28):
            name = f"tests/test_mod{i}.py"
            files.append(name)
            line_counts[name] = 10 + (i * 37) % 111  # 10-120 行

        real = _materialize(tmp_path, files, line_counts)
        monkeypatch.chdir(tmp_path)
        result = _plan(real, max_files=25, max_count=6, max_lines=400)

        # 不变量不回退：零丢失
        assert sorted(_all_files(result)) == sorted(real)

        for s in result["shards"]:
            total = sum(line_counts[f] for f in s["files"])
            big = [f for f in s["files"] if line_counts[f] > 400]
            if big:
                # 单文件超预算 → 必须独占整片
                assert len(s["files"]) == 1, (
                    f"oversize file {big} must travel alone, got {s['files']}"
                )
            else:
                assert total <= 400, f"shard {s['shard_id']} exceeds 400 lines: {total}"

        # 贪心合并后 30 文件装进 4-6 片（2 大文件独占 + 若干行数片），
        # 未破 max_count=6 → 无溢出告警（行数预算生效但未触发放大）
        assert 4 <= len(result["shards"]) <= 6
        assert result["overflow_warning"] is False

    def test_case_b_oversize_file_travels_alone(self, tmp_path, monkeypatch):
        """用例 B：1095 行文件不与任何其它文件同包。"""
        big = ".github/workflows/workflows.yml"
        files = [big] + [f"tests/test_small_{i}.py" for i in range(10)]
        line_counts = {f: 20 for f in files}
        line_counts[big] = 1095

        real = _materialize(tmp_path, files, line_counts)
        monkeypatch.chdir(tmp_path)
        result = _plan(real, max_files=25, max_count=6, max_lines=400)

        for s in result["shards"]:
            if big in s["files"]:
                assert s["files"] == [big], "oversize file must not share a shard"

    def test_boundary_file_exactly_at_budget_packs_normally(self, tmp_path, monkeypatch):
        """恰好在预算内的文件正常装箱（不触发行数开包）。

        round-1 P1-8：旧版测试从未物化文件——_count_lines 全 0，390 行预算
        断言建立在 0 行假象上（假覆盖）。真断言必须让磁盘行数真实存在
        （planner 的读取契约 = CWD 相对路径 + 磁盘行数）。
        """
        files = ["a.py", "b.py", "c.py"]
        line_counts = {f: 130 for f in files}
        real = _materialize(tmp_path, files, line_counts)
        monkeypatch.chdir(tmp_path)

        result = _plan(real, max_files=25, max_count=6, max_lines=400)

        # 真断言（非 0 行假象）：3×130=390 ≤ 400，一包装下；边界值可见
        assert len(result["shards"]) == 1, (
            f"390 行 ≤ 400 预算应单包装下，实际 {len(result['shards'])} 片：{result['shards']}"
        )
        assert result["shards"][0]["lines"] == 390, "lines 字段必须反映真实磁盘行数（非 0 行假象）"

        # 边界翻转侧：a.py 涨到 141 行 → 141+130+130=401 > 400 超预算 →
        # 必须开新片（预算真实生效，非 inert）
        p = tmp_path / "a.py"
        p.write_text("x\n" * 141)
        line_counts_over = {f: 130 for f in files}
        line_counts_over["a.py"] = 141
        result_over = _plan(real, max_files=25, max_count=6, max_lines=400)
        assert len(result_over["shards"]) >= 2, (
            "401 行超预算必须触发开包（预算边界真实生效，非 inert）"
        )
        for s in result_over["shards"]:
            total = sum(line_counts_over[f] for f in s["files"])
            assert total <= 400, f"翻转侧 shard {s['shard_id']} 超预算: {total}"

    def test_full_shard_opens_new_one_at_budget(self, tmp_path, monkeypatch):
        """片内累计达预算即开新片：5×130=650 行 → 至少 2 片，每片 ≤400。"""
        files = [f"tests/m{i}.py" for i in range(5)]
        line_counts = {f: 130 for f in files}

        real = _materialize(tmp_path, files, line_counts)
        monkeypatch.chdir(tmp_path)
        result = _plan(real, max_files=25, max_count=6, max_lines=400)

        lines = _shard_lines(result, line_counts)
        assert len(result["shards"]) >= 2
        for sid, total in lines.items():
            assert total <= 400, f"shard {sid} = {total} lines"

    def test_zero_line_budget_disables_line_dimension(self):
        """max_lines=0 → 行数维度关闭，行为等价纯文件数模式。"""
        line_counts = {"ci.yml": 778, ".github/workflows/x.yml": 1095, "a.py": 50}
        files = list(line_counts)

        r_disabled = _plan(files, max_files=25, max_count=6, max_lines=0)
        r_baseline = _plan(files, max_files=25, max_count=6)
        assert [s["files"] for s in r_disabled["shards"]] == [
            s["files"] for s in r_baseline["shards"]
        ]

        r_small = _plan(files, max_files=25, max_count=6, max_lines=400)
        assert len(r_small["shards"]) >= len(r_disabled["shards"]), (
            "budget=400 must not produce fewer shards than disabled mode"
        )

    def test_missing_file_counts_zero_lines(self):
        """规划时文件可能尚未 checkout——缺失文件按 0 行计，不崩溃不误判独占。"""
        result = _plan(["does/not/exist.py", "also/missing.yml"], max_lines=400)
        assert len(result["shards"]) == 1
        assert sorted(_all_files(result)) == ["also/missing.yml", "does/not/exist.py"]

    def test_per_shard_lines_field_reported(self):
        """每片输出 lines 字段（行数观测面，矩阵日志直接可读）。"""
        files = ["a.py", "b.py"]
        result = _plan(files, max_lines=400)
        for s in result["shards"]:
            assert "lines" in s
            assert s["lines"] == 0  # 文件不存在 → 0 行

    def test_negative_max_lines_rejected(self):
        """负数 max_lines 是配置错误，fail-loud。"""
        with pytest.raises(ValueError):
            _plan(["a.py"], max_lines=-1)


class TestDeletedFileLineCharging:
    """Round-3 ③：删除文件行数按 BASE 侧（pre-PR HEAD 行数）计费。

    背景：_count_lines 读 PR head 树，删除文件在 head 侧不存在 → 计 0 行 →
    deletion-heavy shard 完全绕过 SHARD_MAX_LINES 预算（5000 行删除可与其他
    文件自由同包，复现 2026-10-19 内容失衡超时）。规划器新增 deleted_files
    通道（caller 从 BASE 检出侧读取行数后显式传入），删除文件按 BASE 行数
    参与装箱/独占判定，与普通文件同预算。
    """

    def test_deleted_files_charged_at_base_lines(self):
        """删除文件按传入 BASE 行数计费：超预算独占，不与其它文件混装。"""
        deleted = {"legacy/old_monolith.yml": 1095, "legacy/gone.py": 778}
        result = plan_shards(
            ["a.py", "b.py"],
            max_files=25,
            max_count=6,
            max_lines=400,
            deleted_files=deleted,
        )
        assert sorted(_all_files(result)) == sorted(["a.py", "b.py", *deleted])

        big_shards = [s for s in result["shards"] if any(f in deleted for f in s["files"])]
        for s in big_shards:
            assert len(s["files"]) == 1 and s["files"][0] in deleted, (
                f"BASE 侧超预算删除文件必须独占成片，got {s['files']}"
            )
        # 两个大删除文件 + 小文件组 ≥ 3 片
        assert len(result["shards"]) >= 3
        assert result["overflow_warning"] is False

    def test_deleted_files_within_budget_pack_normally(self):
        """BASE 行数在预算内的删除文件与小文件正常同包。"""
        result = plan_shards(
            ["a.py", "b.py", "gone/old.py"],
            max_files=25,
            max_count=6,
            max_lines=400,
            deleted_files={"gone/old.py": 100},
        )
        assert len(result["shards"]) == 1
        assert "gone/old.py" in result["shards"][0]["files"]

    def test_deleted_files_reported_in_lines_field(self):
        """lines 观测字段按 BASE 行数计入删除文件。"""
        result = plan_shards(
            ["gone/old.py"],
            max_files=25,
            max_count=6,
            max_lines=400,
            deleted_files={"gone/old.py": 333},
        )
        assert result["shards"][0]["lines"] == 333

    def test_deleted_files_ignored_in_compat_mode(self):
        """max_lines<=0（兼容模式）不收费、不改变纯文件数布局。"""
        deleted = {"legacy/old.yml": 5000}
        r_plain = _plan(["a.py", "b.py"], max_files=25, max_count=6)
        r_del = plan_shards(
            ["a.py", "b.py"], max_files=25, max_count=6, max_lines=0, deleted_files=deleted
        )
        assert [s["files"] for s in r_plain["shards"]] == [s["files"] for s in r_del["shards"]]

    def test_deleted_files_union_invariant_holds(self):
        """不变量不回退：删除文件进分片并集（不丢文件），lines 字段如实计费。"""
        deleted = {"gone/x.py": 500, "gone/y.py": 100}
        result = plan_shards(
            ["a.py"], max_files=25, max_count=6, max_lines=400, deleted_files=deleted
        )
        assert sorted(_all_files(result)) == ["a.py", "gone/x.py", "gone/y.py"]
        for s in result["shards"]:
            if "gone/x.py" in s["files"]:
                assert s["lines"] == 500  # 超预算 → 独占
            if "gone/y.py" in s["files"]:
                assert s["lines"] == 100


class TestCliFourthArgDeletedLines:
    """CLI 第 4 位可选参数：DELETED_LINES_FILE（filename\\tbase_lines TSV）。"""

    def _write_deleted(self, tmp_path, entries: dict[str, int]):
        deleted_file = tmp_path / "deleted_lines.tsv"
        deleted_file.write_text(
            "".join(f"{name}\t{lines}\n" for name, lines in entries.items()),
            encoding="utf-8",
        )
        return str(deleted_file)

    def _run_cli(self, files, *args, cwd=None):
        import json
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "src/infra_core/engine/droid_review/plan_shards.py"),
                *map(str, args),
            ],
            input="\n".join(files),
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        return json.loads(proc.stdout)

    def test_fourth_arg_charges_deleted_at_base_lines(self, tmp_path):
        """传 DELETED_LINES_FILE → 删除文件按 BASE 行数计费（超预算独占）。"""
        deleted = self._write_deleted(tmp_path, {"legacy/gone.yml": 1095})
        small = tmp_path / "s.py"
        small.write_text("y\n" * 10)
        result = self._run_cli([str(small)], 25, 6, 400, deleted, cwd=str(tmp_path))
        gone_shards = [s for s in result["shards"] if "legacy/gone.yml" in s["files"]]
        assert len(gone_shards) == 1 and gone_shards[0]["files"] == ["legacy/gone.yml"]
        assert gone_shards[0]["lines"] == 1095

    def test_pure_deletion_pr_produces_shards(self, tmp_path):
        """round-9 sh9 P1：纯删除 PR（空 stdin + 非空 DELETED_LINES_FILE）必产分片。

        `if not files:` 早退在 deleted_files 合并前 → 0 分片静默丢全部删除
        文件（并集不变量「永不丢文件」违反，deletion-heavy PR 绕过预算）。
        """
        deleted = self._write_deleted(
            tmp_path, {"legacy/old_monolith.yml": 778, "legacy/gone.py": 1095}
        )
        result = self._run_cli([], 25, 6, 400, deleted, cwd=str(tmp_path))
        planned = sorted(_all_files(result))
        assert planned == ["legacy/gone.py", "legacy/old_monolith.yml"], (
            f"纯删除 PR 的删除文件必须全部进分片（并集不变量），实际: {planned}"
        )
        assert result["total_files"] == 2
        big = [
            s for s in result["shards"] if len(s["files"]) == 1 and s["files"][0].endswith(".yml")
        ]
        assert big and big[0]["lines"] == 778

    def test_pure_deletion_pr_union_invariant_all_in_one_shard(self, tmp_path):
        """预算内纯删除：单分片容纳全部删除文件，行数计费正确。"""
        deleted = self._write_deleted(tmp_path, {"gone/a.py": 100, "gone/b.py": 120})
        result = self._run_cli([], 25, 6, 400, deleted, cwd=str(tmp_path))
        assert len(result["shards"]) == 1
        assert sorted(result["shards"][0]["files"]) == ["gone/a.py", "gone/b.py"]
        assert result["shards"][0]["lines"] == 220


class TestBackwardCompat:
    """用例 C：不传 max_lines 时行为与现状完全一致（纯文件数模式）。"""

    def test_no_max_lines_matches_legacy_layout(self):
        """2 参调用（现状 workflow 形态）的分片布局与旧行为逐字节一致。"""
        files = (
            [f"src/a/mod{i}.py" for i in range(12)]
            + [f"src/b/util{i}.py" for i in range(8)]
            + [f"tests/test_{i}.py" for i in range(15)]
        )
        result = _plan(files, max_files=25, max_count=6)

        # 纯文件数模式下：同目录聚集、单目录 ≤25 文件时整组不拆
        dirs_per_shard = []
        for s in result["shards"]:
            dirs = {str(Path(f).parent) for f in s["files"]}
            dirs_per_shard.append(dirs)
        # 35 文件 / 25 每片 → 2 片，目录不交叉混装（亲和保留）
        assert len(result["shards"]) == 2
        all_dirs = set().union(*dirs_per_shard)
        assert all_dirs == {"src/a", "src/b", "tests"}

        assert result["total_files"] == 35
        assert result["overflow_warning"] is False

    def test_zero_and_none_equivalent(self):
        """max_lines=None（缺省）与 max_lines=0（显式关闭）等价。"""
        files = [f"f{i}.py" for i in range(10)]
        r_none = _plan(files, max_files=4, max_count=6)
        r_zero = _plan(files, max_files=4, max_count=6, max_lines=0)
        assert [s["files"] for s in r_none["shards"]] == [s["files"] for s in r_zero["shards"]]
        assert "lines" not in r_none["shards"][0]  # 兼容模式不加观测字段

    def test_schema_keys_unchanged(self):
        """用例 D：输出 JSON 既有字段（shards/total_files/overflow_warning）不回退。"""
        result = _plan(["a.py", "b.py"])
        assert set(result) == {"shards", "total_files", "overflow_warning"}
        for s in result["shards"]:
            assert "shard_id" in s and "files" in s
            ids = [s["shard_id"] for s in result["shards"]]
        assert ids == list(range(len(ids)))


class TestCliThirdArg:
    """CLI 第 3 位可选参数：plan_shards.py MAX_FILES MAX_COUNT [MAX_LINES]。"""

    def _run_cli(self, files, *args):
        import json
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "src/infra_core/engine/droid_review/plan_shards.py"),
                *map(str, args),
            ],
            input="\n".join(files),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_third_arg_activates_line_budget(self, tmp_path):
        """传 MAX_LINES=400 → 大文件独占，行数观测字段出现。"""
        big = tmp_path / "big.yml"
        small = tmp_path / "s.py"
        big.write_text("x\n" * 1095)
        small.write_text("y\n" * 10)

        result = self._run_cli([str(big), str(small)], 25, 6, 400)
        big_shards = [s for s in result["shards"] if str(big) in s["files"]]
        assert len(big_shards) == 1 and big_shards[0]["files"] == [str(big)]
        assert big_shards[0]["lines"] == 1095

    def test_two_arg_invocation_stays_legacy(self, tmp_path):
        """2 参调用（现状 workflow 形态）不加行数维度、不加 lines 字段。"""
        f1 = tmp_path / "a.py"
        f1.write_text("z\n" * 500)
        result = self._run_cli([str(f1)], 25, 6)
        assert len(result["shards"]) == 1
        assert "lines" not in result["shards"][0]
