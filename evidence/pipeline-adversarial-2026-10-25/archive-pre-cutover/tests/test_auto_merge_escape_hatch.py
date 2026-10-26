"""auto-merge do-not-merge 逃生舱契约测试（#314 事故根因修复）。

背景（#314 事故）：auto-merge 判据只有 check 全绿，没有任何「仅开 PR，
不合并」信号——在飞 PR 被全绿 sweep 直接卷走合并。本文件锁定逃生舱行为：

1. triage 对带 do-not-merge label 的 PR 一律跳过（do_not_merge 类别，
   action=skip），且优先级最高——label 是机器可读的「仅开 PR，不合并」；
2. 无 label / 其他 label 的 PR 行为完全不变；label 移除后恢复候选资格
   （两向用例：label 命中跳过 / 无 label 不受影响）；
3. workflow 载体（auto-merge-pipeline.yml）：triage 取数含 labels 字段；
   跳过日志行含 PR 号与原因；
4. merge action（actions/auto-merge）兜底层同样在合并前检查 label
   （防 triage 误分类的第二道防线；激活依赖 uses pin 前进）；
5. setup-labels 清单含 do-not-merge（红底白字 + 语义描述）。

契约：do-not-merge 是 GitHub label 名（API 值），字面量出现在断言与
实现中均属业务语义，不属载荷净化范围。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent
TRIAGE_SH = REPO_ROOT / "src" / "infra_core" / "shell" / "auto_merge_triage.sh"
PIPELINE_YML = REPO_ROOT / ".github" / "workflows" / "auto-merge-pipeline.yml"
ACTION_YML = REPO_ROOT / "actions" / "auto-merge" / "action.yml"
SETUP_LABELS_YML = REPO_ROOT / ".github" / "workflows" / "setup-labels.yml"

GREEN_ROLLUP = [
    {"name": name, "conclusion": "SUCCESS"}
    for name in ("substrate-gate-suite", "quality-gate", "droid-review")
]

# REST 三门全 success（v3.2.2 §3.2 绿判唯一入口）
GREEN_CHECK_RUNS = {
    "total_count": 3,
    "check_runs": [
        {
            "id": idx + 1,
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-10-19T00:00:00Z",
        }
        for idx, name in enumerate(("substrate-gate-suite", "quality-gate", "droid-review"))
    ],
}


def _pr(number: int, labels: list[str] | None = None, **overrides) -> dict:
    """构造 gh pr view 取数形态的 PR 对象（默认三门 REST 全绿可合并）。"""
    pr = {
        "number": number,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "statusCheckRollup": GREEN_ROLLUP,
        "labels": [{"name": name} for name in (labels or [])],
        "headRefOid": "0123456789abcdef0123456789abcdef01234567",
        "checkRuns": GREEN_CHECK_RUNS,
    }
    pr.update(overrides)
    return pr


def _run_triage_raw(prs: list, *extra_args: str) -> str:
    result = subprocess.run(
        ["bash", str(TRIAGE_SH), *extra_args],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"triage 必须 exit 0，实际 stderr: {result.stderr}"
    return result.stdout


def _pipeline_triage_run() -> str:
    """提取 pipeline 载体 id=triage 步骤的 run 脚本（YAML 解析后）。"""
    doc = yaml.safe_load(PIPELINE_YML.read_text(encoding="utf-8"))
    triage_steps = [
        step
        for job in doc["jobs"].values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and step.get("id") == "triage"
    ]
    assert len(triage_steps) == 1, "必须恰好包含一个 id=triage 的 run 步骤"
    return triage_steps[0]["run"]


class TestEscapeHatchTriage:
    """triage 行为：label 命中跳过 / 无 label 不受影响（VAL-H-001 两向）。"""

    def test_labeled_green_pr_skipped(self):
        """方向一：全绿可合并 PR 带 do-not-merge label → 跳过，绝不进 mergeable。"""
        output = json.loads(_run_triage_raw([_pr(1, labels=["do-not-merge"])]))
        assert [p["number"] for p in output["do_not_merge"]] == [1]
        assert output["do_not_merge"][0]["action"] == "skip"
        assert output["mergeable"] == [], "带 do-not-merge 的 PR 不得进 mergeable（#314 根因）"

    def test_unlabeled_green_pr_unaffected(self):
        """方向二：无 label 的全绿 PR → 照常 mergeable（默认行为不变）。"""
        output = json.loads(_run_triage_raw([_pr(2, labels=[])]))
        assert [p["number"] for p in output["mergeable"]] == [2]
        assert output["mergeable"][0]["action"] == "merge"
        assert output["do_not_merge"] == []

    def test_other_labels_do_not_trigger(self):
        """其他 label（bug 等）不触发逃生舱。"""
        output = json.loads(_run_triage_raw([_pr(3, labels=["bug", "area/ci"])]))
        assert [p["number"] for p in output["mergeable"]] == [3]
        assert output["do_not_merge"] == []

    def test_label_removal_restores_candidacy(self):
        """label 移除恢复候选：同一 PR 先带 label（跳过）后移除（恢复 mergeable）。"""
        labeled = json.loads(_run_triage_raw([_pr(4, labels=["do-not-merge"])]))
        assert [p["number"] for p in labeled["do_not_merge"]] == [4]
        # 模拟移除 label 后的下一次评估：同一 PR、label 没了、其余不变
        removed = json.loads(_run_triage_raw([_pr(4, labels=[])]))
        assert [p["number"] for p in removed["mergeable"]] == [4]
        assert removed["do_not_merge"] == []

    def test_label_takes_precedence_over_all_states(self):
        """逃生舱优先级最高：任何状态下带 label 都跳过（含 update-branch/notify 腿）。"""
        variants = [
            _pr(10, labels=["do-not-merge"], mergeStateStatus="BEHIND"),
            _pr(11, labels=["do-not-merge"], mergeable="CONFLICTING"),
            _pr(12, labels=["do-not-merge"], mergeStateStatus="BLOCKED"),
            _pr(13, labels=["do-not-merge"], mergeable="UNKNOWN"),
            _pr(
                14,
                labels=["do-not-merge"],
                statusCheckRollup=[{"conclusion": "FAILURE"}],
                checkRuns={
                    "total_count": 1,
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "droid-review",
                            "status": "completed",
                            "conclusion": "failure",
                            "started_at": "2026-10-19T00:00:00Z",
                        }
                    ],
                },
            ),
        ]
        output = json.loads(_run_triage_raw(variants))
        assert [p["number"] for p in output["do_not_merge"]] == [10, 11, 12, 13, 14]
        # 关键：带 label 的 BEHIND PR 不得进 behind（update-branch 也是对 PR 的写操作，
        # 「仅开 PR」语义 = auto-merge 全腿静默）
        assert output["behind"] == []
        assert output["conflicting"] == []
        assert output["pending"] == []
        assert output["stalled"] == []

    def test_label_match_case_insensitive(self):
        """label 名匹配大小写不敏感（GitHub label 名大小写漂移容错）。"""
        output = json.loads(_run_triage_raw([_pr(15, labels=["DO-NOT-MERGE"])]))
        assert [p["number"] for p in output["do_not_merge"]] == [15]

    def test_missing_labels_field_unaffected(self):
        """取数形态无 labels 字段（旧形状）→ 正常分类，triage 对输入形状 total。"""
        pr = _pr(16)
        del pr["labels"]
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["mergeable"]] == [16]
        assert output["do_not_merge"] == []

    def test_missing_check_runs_field_pending_not_mergeable(self):
        """取数形态无 checkRuns 字段（REST 富集缺失/取数失败）→ 非 mergeable。

        fail-closed：REST 三门取不到（字段缺/形状异常）时绝不能退化为
        旧 rollup 绿判——这是 #372 假绿合入封堵的关键负向用例。
        """
        pr = _pr(17)
        del pr["checkRuns"]
        output = json.loads(_run_triage_raw([pr]))
        assert output["mergeable"] == [], "REST 三门缺失时不得判 mergeable（fail-closed）"

    def test_empty_result_shape_includes_do_not_merge(self):
        """空结果骨架含 do_not_merge 键（类别集完备）。"""
        output = json.loads(_run_triage_raw([]))
        assert set(output.keys()) == {
            "mergeable",
            "behind",
            "conflicting",
            "pending",
            "stalled",
            "unknown",
            "do_not_merge",
        }

    def test_get_action_returns_skip_for_labeled(self):
        """--get-action 模式：带 label PR → skip。"""
        stdout = _run_triage_raw([_pr(1, labels=["do-not-merge"])], "--get-action", "1")
        assert stdout.strip() == "skip"

    def test_get_category_returns_do_not_merge(self):
        """--get-category 模式：带 label PR → do_not_merge。"""
        stdout = _run_triage_raw([_pr(1, labels=["do-not-merge"])], "--get-category", "1")
        assert stdout.strip() == "do_not_merge"


class TestLabelShapeDoNotMergeFailClosed:
    """round-9 sh9 P1：label_names 三形态 fail-closed 锁定（逃生门不得 fail-open）。

    round-6 shard-9 ③ 的输入防御（label_names 归一）自身引入 fail-open：
    标量 labels（labels 漂移成 "do-not-merge" 字符串字面值）归一为 []，
    字符串数组条目被 map(.name? // empty) 丢弃 → has_do_not_merge 漏判 →
    PR 误判 mergeable（do-not-merge 逃生门失效，比假绿更危险）。修复前
    旧代码整个 jq 炸到 EMPTY_RESULT 反而 fail-closed。

    终态归一（label_names 返回面恒为字符串数组）：
      - 标量字符串 → [该值]（按真值判，不丢语义）
      - 数组：对象条目取 .name，纯字符串条目按字面值保留，null/数字丢弃
      - 非字符串标量 → []（round-6 防御面保持：非数组标量不炸穿全程序）
    """

    def test_scalar_label_string_do_not_merge_detected(self):
        """标量字符串 labels == "do-not-merge" → 必须命中逃生舱（fail-closed）。"""
        pr = _pr(21)
        pr["labels"] = "do-not-merge"
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["do_not_merge"]] == [21], (
            "标量字符串 labels 携带 do-not-merge 必须判 do_not_merge"
            "（round-9 sh9 P1：归一为无标签 = 逃生门 fail-open）"
        )
        assert output["mergeable"] == []

    def test_scalar_label_string_case_insensitive(self):
        """标量字符串命中同样大小写不敏感（与对象数组形态同轴）。"""
        pr = _pr(22)
        pr["labels"] = "DO-NOT-MERGE"
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["do_not_merge"]] == [22]

    def test_string_array_labels_do_not_merge_detected(self):
        """字符串数组 labels（["do-not-merge"]）→ 按字面值命中逃生舱。"""
        pr = _pr(23)
        pr["labels"] = ["do-not-merge"]
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["do_not_merge"]] == [23], (
            "字符串数组 labels 被 map(.name? // empty) 丢弃 = 逃生门失效形态"
        )
        assert output["mergeable"] == []

    def test_mixed_object_and_string_array_labels(self):
        """混合数组：对象条目取 .name，字符串条目按字面值，null 条目丢弃。"""
        pr = _pr(24)
        pr["labels"] = [{"name": "area/ci"}, "do-not-merge", None]
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["do_not_merge"]] == [24]

    def test_string_array_without_do_not_merge_unaffected(self):
        """字符串数组不含 do-not-merge → 正常 mergeable（防过杀回归）。"""
        pr = _pr(25)
        pr["labels"] = ["bug", "area/ci"]
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["mergeable"]] == [25]
        assert output["do_not_merge"] == []

    def test_non_string_scalar_labels_stay_no_label_semantics(self):
        """非字符串标量（数字）→ 无标签语义（round-6 防御面不回摆、不塌缩全分类）。"""
        pr = _pr(26)
        pr["labels"] = 42
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["mergeable"]] == [26], (
            "labels 为数字标量时分类必须照常进行（round-6 防御面：修复不得回摆成塌缩）"
        )
        assert output["do_not_merge"] == []

    def test_object_label_without_name_ignored(self):
        """对象条目缺 name → 无名字处理（不命中、不炸穿全程序）。"""
        pr = _pr(28)
        pr["labels"] = [{"color": "ff0000"}]
        output = json.loads(_run_triage_raw([pr]))
        assert [p["number"] for p in output["mergeable"]] == [28], (
            "缺 name 的对象条目按无名字处理，分类照常（round-6 防御面不回摆）"
        )
        assert output["do_not_merge"] == []

    def test_scalar_string_labels_category_via_get_category(self):
        """--get-category 查询模式同判（同一归一函数服务两消费点）。"""
        pr = _pr(27)
        pr["labels"] = "do-not-merge"
        stdout = _run_triage_raw([pr], "--get-category", "27")
        assert stdout.strip() == "do_not_merge"


class TestEscapeHatchWorkflowContract:
    """workflow 载体契约：triage 取数含 labels、跳过日志行含 PR 号与原因。"""

    def test_pipeline_triage_fetch_includes_labels(self):
        """triage 步骤所有 gh pr view 取数必须含 labels 字段（初始 + UNKNOWN 重取）。"""
        run = _pipeline_triage_run()
        fetch_lines = [
            line
            for line in run.splitlines()
            if 'gh pr view "$PR_NUMBER"' in line and "--json" in line
        ]
        assert fetch_lines, "pipeline triage 步骤必须存在 gh pr view 取数"
        for line in fetch_lines:
            assert "labels" in line.split("--json", 1)[1], (
                f"triage 取数缺 labels 字段（逃生舱失明）: {line.strip()}"
            )

    def test_pipeline_skip_log_line_with_pr_and_reason(self):
        """do_not_merge 分支：ACTION=skip 且日志行含 PR 号与原因（VAL-H-001 日志契约）。"""
        run = _pipeline_triage_run()
        match = re.search(
            r'do_not_merge\)\s*\n\s*ACTION="skip"\s*\n(?P<body>.*?);;',
            run,
            re.DOTALL,
        )
        assert match, 'triage 步骤缺 do_not_merge case 分支（ACTION="skip"）'
        assert "$PR_NUMBER" in match.group("body"), "跳过日志行必须含 PR 号"
        assert "do-not-merge" in match.group("body"), "跳过日志行必须含原因（label 名）"

    def test_action_guard_checks_label_before_merge(self):
        """merge action 兜底层：取数含 labels 且 do-not-merge 守卫在 gh pr merge 之前。"""
        doc = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
        run = doc["runs"]["steps"][0]["run"]
        assert "--json state,mergeable,headRefOid,labels" in run, (
            "action 的 PR 取数必须含 labels 字段"
        )
        guard_pos = run.index("do-not-merge")
        merge_pos = run.index("gh pr merge")
        assert guard_pos < merge_pos, "do-not-merge 守卫必须在合并命令之前"

    def test_setup_labels_manifest_contains_do_not_merge(self):
        """setup-labels 清单含 do-not-merge：红底（b60205，深红白字）+ 语义描述。"""
        doc = yaml.safe_load(SETUP_LABELS_YML.read_text(encoding="utf-8"))
        script = doc["jobs"]["create-labels"]["steps"][0]["with"]["script"]
        match = re.search(
            r'\{\s*name:\s*"do-not-merge",\s*color:\s*"([0-9a-fA-F]+)",'
            r'\s*description:\s*"([^"]*)"',
            script,
        )
        assert match, "setup-labels defaultLabels 清单缺 do-not-merge 条目"
        assert match.group(1).lower() == "b60205", "do-not-merge 必须红底（深红 b60205 渲染白字）"
        assert "仅开 PR，不合并" in match.group(2), "描述必须含语义：仅开 PR，不合并"
