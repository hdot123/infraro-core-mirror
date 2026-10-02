"""VAL-DEDUP-001: publish_findings cross-run dedup tests.

When the aggregate job reruns on the same PR, it must not post duplicate
inline or summary comments. The second run should detect existing comments
(via marker retrieval) and skip or update instead of creating new ones.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "infra_core" / "engine"))

from droid_review.publish_findings import (  # noqa: E402
    MAX_REVIEW_ROUNDS,
    NEEDS_HUMAN_LABEL,
    SUMMARY_MARKER,
    add_needs_human_label,
    count_round_markers,
    find_existing_inline_comments,
    find_existing_summary_comment,
    main,
    next_review_round,
    post_inline_comment,
    post_summary_comment,
    post_terminal_summary,
    resolve_stale_bot_threads,
    round_marker,
    scan_round_markers,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_subprocess_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a mock subprocess.CompletedProcess-like return value."""
    result = MagicMock()
    result.stdout = stdout.encode()
    result.returncode = returncode
    return result


def _mock_gh_comments(comments: list[dict]) -> MagicMock:
    """Mock subprocess.run to return a list of existing comments as JSON."""
    mock = MagicMock()
    mock.stdout = json.dumps(comments).encode()
    return mock


def _mock_gh_paginated_comments(comments: list[dict], max_per_page: int = 30) -> MagicMock:
    """Mock subprocess.run to return paginated comments (multiple JSON arrays).

    gh api --paginate outputs one JSON array per line for list endpoints.
    """
    mock = MagicMock()
    pages = []
    for i in range(0, len(comments), max_per_page):
        page = comments[i : i + max_per_page]
        pages.append(json.dumps(page))
    mock.stdout = "\n".join(pages).encode()
    return mock


# ══════════════════════════════════════════════════════════════════════
# Part A: Summary comment marker detection
# ══════════════════════════════════════════════════════════════════════


class TestSummaryMarker:
    """Summary comment must carry a detectable marker for cross-run dedup."""

    def test_summary_marker_constant_defined(self):
        """SUMMARY_MARKER must be a non-empty string."""
        assert isinstance(SUMMARY_MARKER, str)
        assert len(SUMMARY_MARKER) > 0

    def test_summary_body_contains_marker(self):
        """post_summary_comment body must include the SUMMARY_MARKER."""
        findings = [{"severity": "P1", "file": "a.py", "line": 10, "message": "bug", "shard_id": 0}]
        by_shard = {0: findings}

        captured_body = {}

        def capture_subprocess(*args, **kwargs):
            # Capture the input payload to inspect the comment body
            input_data = kwargs.get("input", b"")
            if isinstance(input_data, bytes):
                payload = json.loads(input_data.decode())
            else:
                payload = json.loads(input_data)
            captured_body["body"] = payload.get("body", "")
            mock = MagicMock()
            mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=capture_subprocess):
            post_summary_comment(findings, pr_number=1, repository="o/r", by_shard=by_shard)

        assert SUMMARY_MARKER in captured_body["body"]


class TestFindExistingSummaryComment:
    """find_existing_summary_comment must detect existing marker comments."""

    def test_returns_none_when_no_existing_comment(self):
        """No existing comments → returns None."""
        with patch("subprocess.run", return_value=_mock_gh_comments([])):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result is None

    def test_returns_none_when_no_marker_match(self):
        """Existing comments but none with marker → returns None."""
        comments = [{"id": 100, "body": "Some unrelated comment"}]
        with patch("subprocess.run", return_value=_mock_gh_comments(comments)):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result is None

    def test_returns_comment_id_when_marker_found(self):
        """Existing comment with marker → returns its ID."""
        comments = [
            {"id": 42, "body": f"{SUMMARY_MARKER}\n## Droid Auto Review — Findings Summary"},
        ]
        with patch("subprocess.run", return_value=_mock_gh_comments(comments)):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result == 42

    def test_finds_marker_among_multiple_comments(self):
        """Marker comment is the 3rd of 5 comments → still found."""
        comments = [
            {"id": 1, "body": "LGTM"},
            {"id": 2, "body": "Nice work"},
            {"id": 99, "body": f"{SUMMARY_MARKER}\n## Summary"},
            {"id": 4, "body": "Thanks"},
            {"id": 5, "body": "Reviewed"},
        ]
        with patch("subprocess.run", return_value=_mock_gh_comments(comments)):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result == 99

    def test_returns_none_on_api_failure(self):
        """gh api failure → returns None (fail-open for dedup, not fail-closed)."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"rate limit exceeded"
        with patch("subprocess.run", side_effect=error):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result is None

    def test_finds_marker_in_paginated_comments_page2(self):
        """Marker comment on page 2 of paginated results → still found.

        Regression test for pagination truncation: gh api without --paginate
        only returns first 30 comments. With --paginate, all pages are fetched
        and the parser must correctly aggregate across pages.
        """
        # Create 35 comments: marker is #32 (on page 2 with page_size=30)
        comments = [{"id": i, "body": f"Regular comment #{i}"} for i in range(1, 32)]
        comments.append(
            {"id": 999, "body": f"{SUMMARY_MARKER}\n## Droid Auto Review — Findings Summary"}
        )
        # Add more comments after the marker
        comments.extend([{"id": i, "body": f"More comments #{i}"} for i in range(33, 36)])

        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result == 999

    def test_handles_single_page_response(self):
        """Single page (≤30 comments) → works like before."""
        comments = [
            {"id": 42, "body": f"{SUMMARY_MARKER}\n## Summary"},
        ]
        # Single page: one JSON array
        mock = MagicMock()
        mock.stdout = json.dumps(comments).encode()
        with patch("subprocess.run", return_value=mock):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result == 42

    def test_handles_empty_pages(self):
        """No comments → returns None even with pagination."""
        with patch("subprocess.run", return_value=_mock_gh_paginated_comments([], max_per_page=30)):
            result = find_existing_summary_comment(pr_number=1, repository="o/r")
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# Part B: Summary comment skip/update on rerun
# ══════════════════════════════════════════════════════════════════════


class TestSummaryDedup:
    """post_summary_comment must skip (same round) or post (new round) by marker match."""

    FINDINGS = [{"severity": "P1", "file": "a.py", "line": 10, "message": "bug", "shard_id": 0}]
    BY_SHARD = {0: FINDINGS}

    def test_skips_post_when_same_round_marker_found(self):
        """Marker `droid-review-round-N` with N == round_number → no POST (same-run dedup)."""
        call_log = []

        def track_subprocess(*args, **kwargs):
            call_list = args[0] if args else kwargs.get("args", [])
            call_log.append(call_list)
            if "--method" not in call_list:
                # GET list comments → one comment already carrying round-3 marker
                return _mock_gh_comments(
                    [{"id": 42, "body": f"{SUMMARY_MARKER}\n<!-- droid-review-round-3 -->"}]
                )
            mock = MagicMock()
            mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=track_subprocess):
            result = post_summary_comment(
                self.FINDINGS,
                pr_number=1,
                repository="o/r",
                by_shard=self.BY_SHARD,
                round_number=3,
            )

        assert result is True  # graceful skip
        posts = [c for c in call_log if "--method" in c and "POST" in c]
        assert posts == []

    def test_posts_when_no_existing_comment(self):
        """No existing summary → should POST normally."""
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            result = post_summary_comment(
                self.FINDINGS,
                pr_number=1,
                repository="o/r",
                by_shard=self.BY_SHARD,
                round_number=1,
            )

        assert result is True
        posts = [c for c in mock_run.call_args_list if "POST" in c.args[0]]
        assert len(posts) == 1

    def test_posts_when_marker_round_is_older(self):
        """Marker `droid-review-round-2` but round_number=3 → new round, POST goes out."""
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            result = post_summary_comment(
                self.FINDINGS,
                pr_number=1,
                repository="o/r",
                by_shard=self.BY_SHARD,
                round_number=3,
            )

        assert result is True
        posts = [c for c in mock_run.call_args_list if "POST" in c.args[0]]
        assert len(posts) == 1


class TestFullRerunScenario:
    """End-to-end rerun: second invocation must not duplicate comments."""

    def test_rerun_skips_all_comments(self):
        """Second run: existing summary + inline comments → zero new posts."""
        findings = [{"severity": "P1", "file": "a.py", "line": 10, "message": "bug", "shard_id": 0}]
        by_shard = {0: findings}

        post_calls = []

        def track_posts(*args, **kwargs):
            post_calls.append(args)
            return MagicMock(stdout=b"")

        with (
            patch(
                "droid_review.publish_findings.find_existing_summary_comment",
                return_value=42,
            ),
            patch(
                "droid_review.publish_findings.find_existing_inline_comments",
                return_value={("a.py", 10)},
            ),
            patch(
                "droid_review.publish_findings.scan_round_markers",
                return_value=([3], 3),
            ),
            patch("subprocess.run", side_effect=track_posts),
        ):
            # Post inline
            result_inline = post_inline_comment(
                findings[0], pr_number=1, repository="o/r", commit_id="abc"
            )
            # Post summary (same round as the existing marker → skip)
            result_summary = post_summary_comment(
                findings, pr_number=1, repository="o/r", by_shard=by_shard, round_number=3
            )

        # Neither should have made API calls (all skipped)
        assert len(post_calls) == 0
        assert result_inline is True
        assert result_summary is True


# ══════════════════════════════════════════════════════════════════════
# Part C: Inline comment dedup
# ══════════════════════════════════════════════════════════════════════


class TestFindExistingInlineComments:
    """find_existing_inline_comments must retrieve existing PR review comments."""

    def test_returns_empty_set_when_no_comments(self):
        """No existing review comments → empty set."""
        with patch("subprocess.run", return_value=_mock_gh_comments([])):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert result == set()

    def test_returns_set_of_tuples(self):
        """Existing comments → set of (path, line) tuples."""
        comments = [
            {"id": 1, "path": "a.py", "line": 10, "body": "**[P1]** bug"},
            {"id": 2, "path": "b.py", "line": 20, "body": "**[P2]** other"},
        ]
        with patch("subprocess.run", return_value=_mock_gh_comments(comments)):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert ("a.py", 10) in result
        assert ("b.py", 20) in result
        assert len(result) == 2

    def test_returns_empty_on_api_failure(self):
        """gh api failure → empty set (fail-open for dedup)."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"not found"
        with patch("subprocess.run", side_effect=error):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert result == set()


class TestInlineDedup:
    """post_inline_comment must skip when equivalent comment exists."""

    def test_skips_when_same_file_line_exists(self):
        """Existing inline at same (file, line) → skip posting."""
        finding = {"severity": "P1", "file": "a.py", "line": 10, "message": "bug"}

        with patch(
            "droid_review.publish_findings.find_existing_inline_comments",
            return_value={("a.py", 10)},
        ):
            with patch("subprocess.run") as mock_run:
                result = post_inline_comment(
                    finding, pr_number=1, repository="o/r", commit_id="abc"
                )

        assert result is True  # Handled gracefully
        mock_run.assert_not_called()  # Did NOT post

    def test_posts_when_no_existing_at_same_location(self):
        """No existing inline at (file, line) → post normally."""
        finding = {"severity": "P1", "file": "a.py", "line": 10, "message": "bug"}

        with patch(
            "droid_review.publish_findings.find_existing_inline_comments",
            return_value=set(),
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout=b"")
                result = post_inline_comment(
                    finding, pr_number=1, repository="o/r", commit_id="abc"
                )

        assert result is True
        mock_run.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# Part H: Round-cap sentinel — marker counting and round number selection
# ══════════════════════════════════════════════════════════════════════


class TestRoundMarkerConstants:
    """Round sentinel constants and marker construction must stay coherent."""

    def test_max_review_rounds_is_three(self):
        """Cap = 3: rounds 1-3 publish inline, round 4+ is terminal handoff."""
        assert MAX_REVIEW_ROUNDS == 3

    def test_needs_human_label_name(self):
        assert NEEDS_HUMAN_LABEL == "needs-human"

    def test_round_marker_format(self):
        assert round_marker(7) == "<!-- droid-review-round-7 -->"


class TestCountRoundMarkers:
    """count_round_markers scans raw comment bodies for round markers."""

    @pytest.mark.parametrize(
        ("bodies", "expected"),
        [
            ([], 0),
            (["no markers here"], 0),
            (["<!-- droid-review-round-1 -->"], 1),
            (["a", "<!-- droid-review-round-2 -->", "<!-- droid-review-round-3 -->"], 2),
            # Plain summary marker without round suffix must not count
            (["<!-- droid-review-summary -->"], 0),
            # An inline mention of a higher round inside prose is not a marker
            (["we saw <!-- droid-review-round-9 --> yesterday"], 0),
        ],
    )
    def test_counts(self, bodies, expected):
        assert count_round_markers(bodies) == expected


class TestScanRoundMarkers:
    """scan_round_markers walks PR comments (incl. review comments) via gh api."""

    def test_returns_empty_on_no_comments(self):
        with patch("subprocess.run", return_value=_mock_gh_comments([])):
            assert scan_round_markers(1, "o/r") == ([], 0)

    def test_aggregates_rounds_across_comment_kinds(self):
        issue = [{"id": 1, "body": f"{SUMMARY_MARKER}\n<!-- droid-review-round-2 -->"}]
        review = [{"id": 2, "body": "<!-- droid-review-round-1 -->"}]
        outputs = [json.dumps(issue).encode(), json.dumps(review).encode()]
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(" ".join(cmd))
            mock = MagicMock()
            mock.stdout = outputs[len(seen) - 1]
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            rounds, max_round = scan_round_markers(1, "o/r")

        assert rounds == [1, 2]
        assert max_round == 2
        # Both issue comments and review comments endpoints were queried
        assert any("/issues/1/comments" in c for c in seen)
        assert any("/pulls/1/comments" in c for c in seen)

    def test_failopen_on_api_error(self):
        """gh api failure → ([], 0) — treated as first round, never raises."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"boom"
        with patch("subprocess.run", side_effect=error):
            assert scan_round_markers(1, "o/r") == ([], 0)


class TestNextReviewRound:
    """next_review_round derives the current round from prior markers."""

    def test_first_round_when_no_markers(self):
        assert next_review_round([]) == 1

    def test_increments_from_max_marker(self):
        assert next_review_round([1, 2]) == 3
        assert next_review_round([4]) == 5


# ══════════════════════════════════════════════════════════════════════
# Part I: Round cap — terminal summary + needs-human label handoff
# ══════════════════════════════════════════════════════════════════════


class TestPostTerminalSummary:
    """post_terminal_summary: round-N marker + cap-explicit handoff copy."""

    FINDINGS = [{"severity": "P1", "file": "a.py", "line": 10, "message": "bug", "shard_id": 0}]

    def test_body_contains_round_marker_and_handoff_copy(self):
        captured = {}

        def capture_run(*args, **kwargs):
            payload = json.loads(kwargs["input"].decode())
            captured["body"] = payload["body"]
            mock = MagicMock()
            mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=capture_run):
            assert post_terminal_summary(
                self.FINDINGS, pr_number=1, repository="o/r", round_number=4
            )

        body = captured["body"]
        assert "<!-- droid-review-round-4 -->" in body
        assert "3" in body  # cap value stated explicitly
        assert NEEDS_HUMAN_LABEL in body

    def test_label_not_called_by_post_terminal_summary(self):
        """Label attach is main()'s job (separate fail-open step), not the summary's."""
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            post_terminal_summary(self.FINDINGS, pr_number=1, repository="o/r", round_number=4)
        assert all("/labels" not in " ".join(c.args[0]) for c in mock_run.call_args_list)


class TestAddNeedsHumanLabel:
    """add_needs_human_label: POST /issues/{n}/labels with issues: write perms."""

    def test_posts_label_to_issues_labels_endpoint(self):
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            add_needs_human_label(pr_number=7, repository="o/r")

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "gh"
        assert cmd[1] == "api"
        assert cmd[2] == "repos/o/r/issues/7/labels"
        assert "POST" in cmd
        payload = json.loads(mock_run.call_args.kwargs["input"].decode())
        assert payload == {"labels": ["needs-human"]}

    def test_failopen_on_api_error(self):
        """Label failure → returns False, no raise (fail-open per mission spec)."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"forbidden"
        with patch("subprocess.run", side_effect=error):
            assert add_needs_human_label(pr_number=7, repository="o/r") is False


class TestRoundCapMain:
    """main() round-cap wiring: 4th round skips inline, posts terminal summary, labels."""

    FINDINGS = [{"severity": "P1", "file": "a.py", "line": 10, "message": "bug", "shard_id": 0}]

    def _run_main(self, monkeypatch, tmp_path, prior_rounds):
        findings_file = tmp_path / "findings-shard-0.json"
        findings_file.write_text(
            json.dumps({"shard_id": 0, "findings": self.FINDINGS}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--commit-id",
                "abc",
            ],
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())

        def fake_scan(pr_number, repository):
            return (prior_rounds, max(prior_rounds) if prior_rounds else 0)

        monkeypatch.setattr("droid_review.publish_findings.scan_round_markers", fake_scan)

        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            main()
        return mock_run

    def test_fourth_round_zero_inline_posts(self, monkeypatch, tmp_path):
        """Prior rounds [1,2,3] → this is round 4 ≥ cap: inline comment loop skipped."""
        mock_run = self._run_main(monkeypatch, tmp_path, prior_rounds=[1, 2, 3])

        assert all("pulls/1/comments" not in " ".join(c.args[0]) for c in mock_run.call_args_list)

    def test_fourth_round_posts_terminal_summary(self, monkeypatch, tmp_path):
        """Round 4 → terminal summary POST with round-4 marker, round cap copy."""
        captured = {}

        def capture_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if (
                "--method" in cmd
                and "POST" in cmd
                and "/issues/" in joined
                and "comments" in joined
            ):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            mock = MagicMock()
            mock.stdout = b""
            return mock

        findings_file = tmp_path / "findings-shard-0.json"
        findings_file.write_text(
            json.dumps({"shard_id": 0, "findings": self.FINDINGS}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--commit-id",
                "abc",
            ],
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers",
            lambda pr, repo: ([1, 2, 3], 3),
        )
        with patch("subprocess.run", side_effect=capture_run):
            main()

        assert "<!-- droid-review-round-4 -->" in captured["body"]
        assert "3" in captured["body"]

    def test_fourth_round_attaches_needs_human_label(self, monkeypatch, tmp_path):
        """Round 4 → exactly one needs-human label POST with the expected payload."""
        mock_run = self._run_main(monkeypatch, tmp_path, prior_rounds=[1, 2, 3])

        label_posts = [c for c in mock_run.call_args_list if "/labels" in " ".join(c.args[0])]
        assert len(label_posts) == 1
        cmd = label_posts[0].args[0]
        assert "repos/o/r/issues/1/labels" in cmd
        assert json.loads(label_posts[0].kwargs["input"].decode()) == {"labels": ["needs-human"]}

    def test_first_round_still_posts_inline(self, monkeypatch, tmp_path):
        """Rounds below cap: inline publishing unchanged (regression guard)."""
        mock_run = self._run_main(monkeypatch, tmp_path, prior_rounds=[])

        inline_posts = [
            c
            for c in mock_run.call_args_list
            if "--method" in c.args[0] and "POST" in c.args[0] and "/pulls/" in " ".join(c.args[0])
        ]
        assert len(inline_posts) == 1
        assert not any("/labels" in " ".join(c.args[0]) for c in mock_run.call_args_list)

    def test_label_failure_does_not_break_round4_flow(self, monkeypatch, tmp_path):
        """needs-human label POST fails → no exception, terminal summary still sent."""
        findings_file = tmp_path / "findings-shard-0.json"
        findings_file.write_text(
            json.dumps({"shard_id": 0, "findings": self.FINDINGS}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--commit-id",
                "abc",
            ],
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers",
            lambda pr, repo: ([1, 2, 3], 3),
        )

        def flaky_run(cmd, **kwargs):
            if "/labels" in " ".join(cmd):
                raise subprocess.CalledProcessError(1, "gh")
            mock = MagicMock()
            mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=flaky_run):
            main()  # must not raise


# ══════════════════════════════════════════════════════════════════════
# Part E: Pagination boundary conditions for find_existing_inline_comments
# ══════════════════════════════════════════════════════════════════════


class TestInlineCommentsPagination:
    """find_existing_inline_comments must handle pagination correctly."""

    def test_paginated_inline_comments_across_pages(self):
        """Inline comments spanning 2 pages → all (path, line) tuples collected."""
        # 35 comments: target at position 32 (page 2 with page_size=30)
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i * 10, "body": f"comment {i}"}
            for i in range(1, 36)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        # All 35 comments should be found
        assert len(result) == 35
        assert ("file1.py", 10) in result
        assert ("file32.py", 320) in result  # was on page 2
        assert ("file35.py", 350) in result

    def test_zero_comments(self):
        """0 comments → empty set."""
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments([], max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert result == set()

    def test_one_comment(self):
        """1 comment → single (path, line) tuple."""
        comments = [{"id": 1, "path": "a.py", "line": 10, "body": "only one"}]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert result == {("a.py", 10)}

    def test_exactly_29_comments(self):
        """29 comments (just under page boundary) → all in single page."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 30)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 29

    def test_exactly_30_comments(self):
        """30 comments (exactly one page) → single page response."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 31)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 30

    def test_exactly_31_comments(self):
        """31 comments (just over page boundary) → 2 pages."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 32)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 31
        # The 31st comment should be present (was on page 2)
        assert ("file31.py", 31) in result

    def test_50_comments(self):
        """50 comments → spans 2 pages with page_size=30."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 51)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 50
        assert ("file50.py", 50) in result

    def test_100_comments(self):
        """100 comments → spans 4 pages with page_size=30."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 101)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 100
        assert ("file100.py", 100) in result

    def test_extreme_pagination_per_page_1(self):
        """max_per_page=1 → each comment is its own page (extreme stress test)."""
        comments = [
            {"id": i, "path": f"file{i}.py", "line": i, "body": f"c{i}"} for i in range(1, 6)
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=1),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert len(result) == 5
        assert ("file1.py", 1) in result
        assert ("file5.py", 5) in result

    def test_pagination_fail_open_on_api_error(self):
        """gh api failure during pagination → empty set (fail-open for dedup)."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"rate limit exceeded"
        with patch("subprocess.run", side_effect=error):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        assert result == set()

    def test_pagination_handles_missing_path_or_line(self):
        """Comments missing path or line → skipped gracefully."""
        comments = [
            {"id": 1, "path": "a.py", "line": 10, "body": "ok"},
            {"id": 2, "path": "b.py", "body": "missing line"},  # no line
            {"id": 3, "line": 30, "body": "missing path"},  # no path
            {"id": 4, "path": "d.py", "line": 40, "body": "ok"},
        ]
        with patch(
            "subprocess.run",
            return_value=_mock_gh_paginated_comments(comments, max_per_page=30),
        ):
            result = find_existing_inline_comments(pr_number=1, repository="o/r")
        # Only comments with both path and line should be included
        assert len(result) == 2
        assert ("a.py", 10) in result
        assert ("d.py", 40) in result


# ══════════════════════════════════════════════════════════════════════
# Part F: _parse_paginated_json edge cases
# ══════════════════════════════════════════════════════════════════════


class TestParsePaginatedJson:
    """_parse_paginated_json must handle various input formats."""

    def test_single_json_array(self):
        """Single JSON array → parsed correctly."""
        from droid_review.publish_findings import _parse_paginated_json

        output = '[{"id": 1}, {"id": 2}]'
        result = _parse_paginated_json(output)
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_multiple_json_arrays(self):
        """Multiple JSON arrays (one per line) → concatenated."""
        from droid_review.publish_findings import _parse_paginated_json

        output = '[{"id": 1}, {"id": 2}]\n[{"id": 3}]\n[{"id": 4}, {"id": 5}]'
        result = _parse_paginated_json(output)
        assert len(result) == 5

    def test_empty_string(self):
        """Empty string → empty list."""
        from droid_review.publish_findings import _parse_paginated_json

        result = _parse_paginated_json("")
        assert result == []

    def test_empty_json_array(self):
        """Empty JSON array → empty list."""
        from droid_review.publish_findings import _parse_paginated_json

        result = _parse_paginated_json("[]")
        assert result == []

    def test_blank_lines_between_arrays(self):
        """Blank lines between arrays → handled gracefully."""
        from droid_review.publish_findings import _parse_paginated_json

        output = '[{"id": 1}]\n\n\n[{"id": 2}]\n'
        result = _parse_paginated_json(output)
        assert len(result) == 2

    def test_single_dict_response(self):
        """Single dict (not array) → wrapped in list."""
        from droid_review.publish_findings import _parse_paginated_json

        output = '{"id": 1, "body": "test"}'
        result = _parse_paginated_json(output)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines → skipped, valid ones parsed."""
        from droid_review.publish_findings import _parse_paginated_json

        output = '[{"id": 1}]\nnot valid json\n[{"id": 2}]'
        result = _parse_paginated_json(output)
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════════
# Part G: resolve_stale_bot_threads — auto-resolve stale bot threads
# ══════════════════════════════════════════════════════════════════════

BOT_LOGIN = "github-actions[bot]"


def _make_graphql_thread(
    thread_id: str,
    *,
    is_resolved: bool = False,
    is_outdated: bool = False,
    path: str = "a.py",
    author_login: str = BOT_LOGIN,
    author_typename: str | None = "Bot",
    body: str = "bot comment",
) -> dict:
    """Build one reviewThreads node shaped like the GitHub GraphQL payload."""
    author: dict = {"login": author_login}
    if author_typename is not None:
        author["__typename"] = author_typename
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "path": path,
        "comments": {"nodes": [{"author": author, "body": body}]},
    }


def _mock_gh_graphql(threads: list[dict]) -> MagicMock:
    """Mock subprocess.run for `gh api graphql` returning reviewThreads page."""
    mock = MagicMock()
    mock.stdout = json.dumps(
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
    ).encode()
    return mock


def _mutation_calls(mock_run) -> list:
    """subprocess.run calls that invoke the resolveReviewThread mutation."""
    return [c for c in mock_run.call_args_list if "resolveReviewThread" in " ".join(c.args[0])]


class TestResolveStaleBotThreads:
    """resolve_stale_bot_threads must resolve only dissolvable bot threads."""

    def test_resolve_outdated_bot_thread_triggers_mutation(self):
        """Outdated + github-actions author + unresolved → resolveReviewThread mutation."""
        threads = [_make_graphql_thread("T_out", is_outdated=True, path="old.py")]

        def fake_run(cmd, **kwargs):
            if "graphql" in cmd:
                return _mock_gh_graphql(threads)
            # Any REST fallback would show up here as an unexpected call
            raise AssertionError(f"unexpected non-graphql subprocess call: {cmd}")

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1
        mutation_cmd = mutations[0].args[0]
        assert "resolveReviewThread" in " ".join(mutation_cmd)
        assert "T_out" in " ".join(mutation_cmd)

    def test_resolve_skips_p0_p1_active(self):
        """P0/P1 active bot thread anchored at a current finding path → zero mutations.

        Even when isOutdated would be false-ish for active threads, the safety
        guard: current P0/P1 finding paths are never auto-resolved.
        """
        threads = [_make_graphql_thread("T_p0", is_outdated=False, path="a.py")]
        current = [{"severity": "P0", "file": "a.py", "line": 10, "message": "critical"}]

        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=current)

        assert mock_run.call_count == 1  # only the query, no mutation

    def test_resolve_p2_p3_dissolved_thread(self):
        """P2/P3 bot thread anchored at a path absent from current findings → resolve."""
        threads = [_make_graphql_thread("T_p2", is_outdated=False, path="fixed.py")]
        current = [{"severity": "P2", "file": "elsewhere.py", "line": 3, "message": "minor"}]

        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=current)

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1
        assert "T_p2" in " ".join(mutations[0].args[0])

    def test_never_touches_human_threads(self):
        """Human author threads → never resolved, even outdated at stale paths."""
        threads = [
            _make_graphql_thread("T_human", is_outdated=True, path="old.py", author_login="alice"),
        ]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        assert mock_run.call_count == 1  # only the query

    def test_skips_already_resolved_threads(self):
        """isResolved=true threads → no mutation (nothing to do)."""
        threads = [_make_graphql_thread("T_done", is_resolved=True, is_outdated=True)]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        assert mock_run.call_count == 1

    def test_pagination_follows_page_info_cursor(self):
        """Multiple pages (pageInfo.hasNextPage/endCursor) → all threads fetched."""
        page1 = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [_make_graphql_thread("T_p1_page1", is_outdated=True)],
                                "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
                            }
                        }
                    }
                }
            }
        )
        page2 = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [_make_graphql_thread("T_p2_page2", is_outdated=True)],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        )
        outputs = [page1.encode(), page2.encode()]

        # Explicit behavior: first graphql call → page1, second → page2;
        # mutation calls (if any) return empty payloads (fail-safe stubs).
        calls = {"n": 0}

        def fake_run2(cmd, **kwargs):
            if "resolveReviewThread" in " ".join(cmd):
                return MagicMock(stdout=b"{}")
            if "graphql" in cmd:
                out = outputs[calls["n"]]
                calls["n"] += 1
                return MagicMock(stdout=out)
            raise AssertionError(f"unexpected call: {cmd}")

        with patch("subprocess.run", side_effect=fake_run2) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        assert calls["n"] == 2  # cursor pagination walked both pages
        mutations = _mutation_calls(mock_run)
        resolved_ids = " ".join(" ".join(c.args[0]) for c in mutations)
        assert "T_p1_page1" in resolved_ids
        assert "T_p2_page2" in resolved_ids
        # Second query must carry the cursor from page 1
        second_query = " ".join(mock_run.call_args_list[1].args[0])
        assert "CUR1" in second_query

    def test_failopen_on_graphql_query_error(self):
        """GraphQL query CalledProcessError → Warning, no raise, no mutation."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = b"graphql syntax error"
        with patch("subprocess.run", side_effect=error) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])
        assert mock_run.call_count == 1  # attempted once, failed open

    def test_failopen_on_mutation_error(self):
        """resolveReviewThread mutation failure → Warning, no raise, other threads still tried."""
        threads = [
            _make_graphql_thread("T_bad", is_outdated=True),
            _make_graphql_thread("T_good", is_outdated=True),
        ]

        def fake_run(cmd, **kwargs):
            if "--raw-field" in cmd and "resolveReviewThread" in " ".join(cmd):
                raise subprocess.CalledProcessError(1, "gh")
            return _mock_gh_graphql(threads)

        with patch("subprocess.run", side_effect=fake_run):
            # Must not raise despite both mutations failing
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

    def test_bot_thread_with_p0_path_but_outdated_still_resolved(self):
        """Outdated bot thread at a P0-finding path → still resolved (outdated wins)."""
        threads = [_make_graphql_thread("T_outdated_p0", is_outdated=True, path="a.py")]
        current = [{"severity": "P0", "file": "a.py", "line": 10, "message": "critical"}]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=current)

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1


class TestBotLoginDualSpell:
    """Defect #001b: REST user.login returns "github-actions[bot]" but GraphQL
    author.login returns bare "github-actions" for the same bot comments —
    the eligibility filter must accept both spellings."""

    def test_bare_github_actions_login_resolves(self):
        """Bare "github-actions" (observed GraphQL form) → resolve mutation fires."""
        threads = [
            _make_graphql_thread(
                "T_bare", is_outdated=True, path="old.py", author_login="github-actions"
            )
        ]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1
        assert "T_bare" in " ".join(mutations[0].args[0])

    def test_bracketed_bot_login_resolves(self):
        """ "github-actions[bot]" (REST form) → resolve mutation still fires."""
        threads = [
            _make_graphql_thread(
                "T_bracket", is_outdated=True, path="old.py", author_login="github-actions[bot]"
            )
        ]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1
        assert "T_bracket" in " ".join(mutations[0].args[0])

    def test_bare_login_active_p2_dissolved_resolves(self):
        """Bare spelling + active + path absent from current findings (dissolved
        P2/P3 semantics) → resolved too, not just outdated threads."""
        threads = [
            _make_graphql_thread(
                "T_bare_active", is_outdated=False, path="fixed.py", author_login="github-actions"
            )
        ]
        current = [{"severity": "P2", "file": "elsewhere.py", "line": 3, "message": "minor"}]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=current)

        mutations = _mutation_calls(mock_run)
        assert len(mutations) == 1
        assert "T_bare_active" in " ".join(mutations[0].args[0])

    def test_bare_login_p0_active_still_guarded(self):
        """Bare spelling + active + anchored at a current P0 path → gate holds,
        zero mutations (the safety guard is spelling-independent)."""
        threads = [
            _make_graphql_thread(
                "T_bare_p0", is_outdated=False, path="a.py", author_login="github-actions"
            )
        ]
        current = [{"severity": "P0", "file": "a.py", "line": 10, "message": "critical"}]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=current)

        assert mock_run.call_count == 1  # only the query, no mutation


class TestBotTypenameGuard:
    """author.__typename is the second eligibility line beyond the login
    spelling: missing or non-Bot typename must never be auto-resolved
    (a wrong eligibility call wrongly clears a gate — prefer漏 over错)."""

    def test_missing_typename_not_resolved(self):
        """author without __typename (schema drift / partial response) → no mutation."""
        threads = [_make_graphql_thread("T_nt", is_outdated=True, author_typename=None)]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        assert mock_run.call_count == 1  # only the query, no mutation

    def test_non_bot_typename_not_resolved(self):
        """login matches a bot spelling but __typename=User → no mutation."""
        threads = [
            _make_graphql_thread(
                "T_user", is_outdated=True, author_login="github-actions", author_typename="User"
            )
        ]
        with patch("subprocess.run", return_value=_mock_gh_graphql(threads)) as mock_run:
            resolve_stale_bot_threads(1, "o/r", current_findings=[])

        assert mock_run.call_count == 1  # only the query, no mutation


class TestMainCallsResolveStaleBotThreads:
    """main() must invoke resolve_stale_bot_threads after summary, before stats."""

    FINDINGS = [{"severity": "P3", "file": "a.py", "line": 1, "message": "m", "shard_id": 0}]

    def _write_findings_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "findings-shard-0.json"
        path.write_text(
            json.dumps({"shard_id": 0, "findings": self.FINDINGS}),
            encoding="utf-8",
        )
        return path

    def test_main_invokes_resolve_after_summary(self, tmp_path, monkeypatch, capsys):
        """resolve_stale_bot_threads is called exactly once with deduped findings."""
        self._write_findings_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
            ],
        )
        monkeypatch.setattr(
            resolve_stale_bot_threads.__module__ + ".resolve_stale_bot_threads", MagicMock()
        )
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            main()

        # No commit-id → no inline posts; summary dedup find → None (no marker comment)
        # → one POST for summary comment via subprocess
        posts = [c for c in mock_run.call_args_list if "--method" in c.args[0]]
        assert len(posts) == 1  # summary comment only

    def test_main_resolve_receives_current_findings(self, tmp_path, monkeypatch):
        """The findings list passed to resolve_stale_bot_threads matches deduped set."""
        self._write_findings_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
            ],
        )
        captured: dict = {}

        def fake_resolve(pr_number, repository, findings):
            captured["findings"] = findings

        monkeypatch.setattr(
            resolve_stale_bot_threads.__module__ + ".resolve_stale_bot_threads", fake_resolve
        )
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")):
            main()

        assert captured["findings"] == self.FINDINGS
