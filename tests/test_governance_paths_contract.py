"""governance 触发面 ⊆ 保护面契约测试（v3 步骤 1 安全最小集，B1 假绿修复）。

背景：evolution-governance.yml 的触发 paths 含 `actions/**`，但 governance-check
action 的默认 protected-patterns 不含它——非 owner 修改 actions/ 分发副本时
workflow 会被触发却判定放行（假绿信号）。本文件锁定两个边界：

1. 触发 paths 集合 ⊆ 有效 protected-patterns（默认值 ∪ with: 覆盖值），
   且每个触发模式的代表性文件在判定器下对非 owner 必须阻断（fail-closed）；
2. droid-review.yml 的 fork guard 存在（自动合并路径的唯一外部边界，
   非同仓 head.repo → exit 1 fail-closed）。

注意：YAML `on:` 键被 PyYAML 解析为 True（YAML 1.1 规范），访问触发配置需用
doc.get(True) fallback（先例：tests/test_droid_review_fork_guard.py）。
"""

from __future__ import annotations

import fnmatch
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
GOVERNANCE_WORKFLOW = REPO_ROOT / ".github/workflows/evolution-governance.yml"
GOVERNANCE_ACTION = REPO_ROOT / "actions/governance-check/action.yml"
JUDGE_SCRIPT = REPO_ROOT / "actions/governance-check/governance_check.py"
DROID_REVIEW_WORKFLOW = REPO_ROOT / ".github/workflows/droid-review.yml"

NON_OWNER = "attacker"


def _load_judge_module() -> Any:
    """加载随 action 分发的判定器脚本（自包含，无第三方依赖）。"""
    spec = importlib.util.spec_from_file_location("governance_check_shipped", JUDGE_SCRIPT)
    assert spec is not None and spec.loader is not None, f"无法加载判定器：{JUDGE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _triggers(doc: dict) -> dict:
    """读取触发配置（`on:` 被 PyYAML 解析为 True）。"""
    return doc.get("on") or doc.get(True) or {}


def _trigger_paths(workflow_doc: dict) -> list[str]:
    pr_target = _triggers(workflow_doc).get("pull_request_target") or {}
    return list(pr_target.get("paths") or [])


def _default_patterns(action_doc: dict) -> tuple[str, ...]:
    raw = action_doc["inputs"]["protected-patterns"]["default"]
    return tuple(p.strip() for p in str(raw).split(",") if p.strip())


def _workflow_override_patterns(workflow_doc: dict) -> tuple[str, ...] | None:
    """从 governance-check 调用步的 with: 块提取 protected-patterns 覆盖值。"""
    for job in workflow_doc["jobs"].values():
        for step in job.get("steps") or []:
            if "governance-check" in str(step.get("uses", "")):
                with_block = step.get("with") or {}
                raw = with_block.get("protected-patterns")
                if raw is None:
                    return None
                return tuple(p.strip() for p in str(raw).split(",") if p.strip())
    raise AssertionError("evolution-governance.yml 未找到 governance-check 调用步")


def _covers(protected_pattern: str, candidate: str) -> bool:
    """protected pattern 是否覆盖 candidate 模式。

    语义与判定器 _match_any 一致：目录模式（`prefix/**`）覆盖 prefix 自身
    与其下全部条目；其余按 fnmatch 处理。candidate 本身可能是 glob 模式，
    故对 `prefix/**` 用前缀判定，对精确模式用相等判定。
    """
    if protected_pattern == candidate:
        return True
    if protected_pattern.endswith("/**"):
        prefix = protected_pattern[: -len("/**")]
        return candidate == prefix or candidate.startswith(prefix + "/")
    return fnmatch.fnmatchcase(candidate, protected_pattern)


def _representative_path(pattern: str) -> str:
    """为触发模式生成代表性文件路径（glob 通配替换为 x）。"""
    if pattern.endswith("/**"):
        return pattern[: -len("/**")] + "/example.txt"
    return pattern.replace("**", "x").replace("*", "x").replace("?", "x")


class TestTriggerPathsSubsetOfProtectedPatterns:
    """触发面必须 ⊆ 保护面：否则触发却放行 = 假绿（B1）。"""

    def test_workflow_paths_subset_of_protected_patterns(self):
        workflow_doc = yaml.safe_load(GOVERNANCE_WORKFLOW.read_text(encoding="utf-8"))
        action_doc = yaml.safe_load(GOVERNANCE_ACTION.read_text(encoding="utf-8"))

        trigger_paths = _trigger_paths(workflow_doc)
        assert trigger_paths, "evolution-governance.yml 必须声明 pull_request_target.paths"

        default_patterns = _default_patterns(action_doc)
        override = _workflow_override_patterns(workflow_doc)
        if override is not None:
            # with: 覆盖默认值。union 与 override 等值的前提是 override ⊇ default；
            # 若覆盖值丢失默认模式，默认保护面会被静默放宽——单独断言，失败信息直指丢失项
            missing = [p for p in default_patterns if p not in override]
            assert not missing, (
                "governance-check with: protected-patterns 覆盖值丢失默认模式"
                f"（静默放宽默认保护面）：{missing}"
            )
        effective = tuple(dict.fromkeys(default_patterns + (override or ())))

        uncovered = [t for t in trigger_paths if not any(_covers(p, t) for p in effective)]
        assert not uncovered, (
            "evolution-governance.yml 触发 paths 存在未被有效 protected-patterns "
            f"覆盖的模式（假绿风险）：{uncovered}；有效 patterns={list(effective)}"
        )

        # 判定器级验证：每个触发模式的代表性文件对非 owner 必须阻断（fail-closed）
        judge = _load_judge_module()
        allowed_violations = []
        for pattern in trigger_paths:
            sample = _representative_path(pattern)
            allowed, reason = judge.check_governance(
                changed_files=[sample],
                pr_author=NON_OWNER,
                owner_login=judge.DEFAULT_OWNER_LOGIN,
                protected_patterns=effective,
            )
            if allowed:
                allowed_violations.append(f"{pattern}（样例 {sample}）：{reason}")
        assert not allowed_violations, (
            "触发 paths 的代表性文件在判定器下对非 owner 放行（假绿）：\n"
            + "\n".join(allowed_violations)
        )


class TestDroidReviewForkGuardPresence:
    """droid-review.yml fork guard 存在性锁定（自动合并路径的唯一外部边界）。

    droid-review.yml 是 required check 的实现面（pull_request_target + runner
    环境中带 API key）；fork PR 必须被 fail-closed 拒绝，否则外部作者可借 PR
    head 内容操纵审查结论。guard 被静默移除 = 边界消失（governance 已有先例）。
    姊妹覆盖：tests/test_droid_review_fork_guard.py 另锁 droid-review-shards.yml。
    """

    def test_droid_review_fork_guard_present(self):
        doc = yaml.safe_load(DROID_REVIEW_WORKFLOW.read_text(encoding="utf-8"))
        setup_job = doc["jobs"]["setup"]
        resolve_step = next((s for s in setup_job["steps"] if s.get("id") == "resolve"), None)
        assert resolve_step is not None, "droid-review.yml setup 必须保留 'resolve' 步骤"
        run_block = resolve_step["run"]

        # guard 仅在 pull_request_target 事件下生效（其余事件不受影响）
        assert "pull_request_target" in run_block
        # 身份判定：PR head 仓必须等于本仓（非同仓即 fork）
        assert "github.event.pull_request.head.repo.full_name" in run_block
        assert "github.repository" in run_block
        # fail-closed：不匹配必须 exit 1（空 head.repo 同样落入 != 分支）
        assert "exit 1" in run_block
        assert "::error::" in run_block
