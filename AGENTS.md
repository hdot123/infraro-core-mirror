# infraro-core — agent briefing

Python **3.12** (`requires-python == 3.12.*`). Dev setup: `pip install -e ".[dev]"`. Import package: `infra_core`. Layering and delivery boundaries: [`docs/architecture.md`](docs/architecture.md).

## Conventions

- Prefer minimal, task-scoped diffs; do not commit gitignored memory-hook runtime artifacts (`memory/`, `INDEX.md`, `NOW.md`, `project-map/`, etc.).
- Run `ruff check` / `ruff format` on touched Python (see `pyproject.toml`).
- Do not weaken boundary or governance guards (`scripts/check_boundary.py`, tests marked `security` / `business_policy`).

## Memory Hook (optional)

If your **personal** Factory CLI setup includes memory-hook, it may inject extra guidance here. That is optional—not a repo hard dependency; teammates and CI do not need a specific path under `~/.factory/` to build or test this project.

## Testing

- Focused changes: run only the tests you touched, e.g.
  `pytest tests/<module>_test.py -x -q` (replace with the real file under `tests/`).
- Full gate: do **not** run the full suite locally. The full/nightly gate runs in
  CI·QA (GitHub Actions on the project's self-hosted runners). This is an
  intentional narrow departure from the docs' default "run full gate locally
  before broad refactors"—narrow reason: the full suite already runs in CI·QA.
- Before completing work, run the focused tests for the changed module and
  report the output. If no focused test exists, state that explicitly.
- Add a regression test for every bug fix when practical.
- Do not start long-running full-suite runs on this machine.
