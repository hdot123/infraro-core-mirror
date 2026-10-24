"""P0 对抗：publish_findings 在 P0/P1 findings 时必须非零退出（droid-review job 红腿）。"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine"
sys.path.insert(0, str(ENGINE_DIR))

from droid_review.publish_findings import main  # noqa: E402


def _write_findings(tmp_path: Path, findings: list[dict]) -> None:
    (tmp_path / "findings-shard-0.json").write_text(
        json.dumps({"shard_id": 0, "findings": findings}),
        encoding="utf-8",
    )


def _run_main(monkeypatch, tmp_path: Path, findings: list[dict]) -> None:
    _write_findings(tmp_path, findings)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "publish_findings.py",
            "--pattern",
            "findings-shard-0.json",
            "--pr-number",
            "42",
            "--repository",
            "o/r",
        ],
    )
    monkeypatch.setattr(
        "droid_review.publish_findings.scan_round_markers", lambda pr, repo: ([], 0)
    )
    monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
    with patch("subprocess.run", return_value=MagicMock(stdout=b"")):
        main()


class TestPublishFindingsBlockingExit:
    @pytest.mark.parametrize("severity", ["P0", "P1"])
    def test_blocking_severity_exits_nonzero(self, monkeypatch, tmp_path, severity):
        finding = {
            "severity": severity,
            "file": "a.py",
            "line": 1,
            "message": "issue",
        }
        with pytest.raises(SystemExit) as exc:
            _run_main(monkeypatch, tmp_path, [finding])
        assert exc.value.code == 1

    def test_p2_only_exits_zero(self, monkeypatch, tmp_path):
        finding = {
            "severity": "P2",
            "file": "a.py",
            "line": 1,
            "message": "nit",
        }
        _run_main(monkeypatch, tmp_path, [finding])

    def test_no_findings_exits_zero(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-*.json",
                "--pr-number",
                "42",
                "--repository",
                "o/r",
            ],
        )
        main()
