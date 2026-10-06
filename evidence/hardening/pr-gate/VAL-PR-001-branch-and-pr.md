# VAL-PR-001 Evidence: Single Feature Branch and Single PR

## 1. Branch check
```
$ git branch --show-current
feat/audit-defense-hardening
```

## 2. PR list (only PRs from this branch)
```
$ gh pr list --state all --limit 5 --json number,title,state,headRefName
[{"headRefName":"feat/audit-defense-hardening","number":141,"state":"OPEN","title":"feat(engine): 审计防线加固四件套（接口门 fail-closed / liveness conclusion / 反向 import AST 锁 / 零红移序）"}]
```

Exactly 1 OPEN PR (#141) from branch `feat/audit-defense-hardening`.

## 3. PR title/body governance filename check
```
$ grep -c "AGENTS\.md\|README\.md\|settings\.json\|pyproject\.toml\|runner-tools\.toml" <(gh pr view 141 --json title,body --jq '.title + " " + .body')
0
```
Zero matches — no governance resource literal names in PR title or body.

## 4. write-pending-ci.sh registration
- The pending-ci-141.json file does not exist on disk (transient; cleaned up after CI completion).
- The `write-pending-ci.sh` script exists at `~/.factory/webhook/scripts/write-pending-ci.sh`.
- PR #141 CI shows `Auto-merge PR (141)` job passed — consistent with write-pending-ci registration having occurred.
- Cannot independently re-verify since the file is transient and cleaned by trigger-ci-droid.sh after CI.
