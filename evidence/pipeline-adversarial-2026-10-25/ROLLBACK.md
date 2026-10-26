# ROLLBACK · 一步回滚说明（archive-pre-cutover → 工作树）

适用：整体替换合入后发现问题，或大哥要求**整集回滚、不留半截**。  
权威方案：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md` §0.1。

## 存档位置

```
evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover/
```

清单：同目录 `MANIFEST.md`（原路径 ↔ 存档相对路径一一对应）。

## 一步打回（在仓根执行）

```bash
# 1. 确认你在要回滚的仓库根，且已读懂 MANIFEST
ARCHIVE="evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover"

# 2. 按 MANIFEST 把存档文件覆盖回原路径（保持相对路径）
#    下列 rsync 等价于：对 archive 内每个普通文件，复制到仓根同相对路径
rsync -a --relative \
  "$ARCHIVE/./.github/workflows/" \
  "$ARCHIVE/./actions/" \
  "$ARCHIVE/./scripts/" \
  "$ARCHIVE/./src/" \
  "$ARCHIVE/./tests/" \
  "$ARCHIVE/./webhook-scripts/" \
  "$ARCHIVE/./.evolution/" \
  ./

# 更朴素的等价写法（逐文件，与 MANIFEST 一致）：
while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  mkdir -p "$(dirname "$rel")"
  cp -p "$ARCHIVE/$rel" "$rel"
done < <(cd "$ARCHIVE" && find . -type f ! -name 'MANIFEST.md' -print | sed 's|^\./||' | sort)

# 3. 删除本集「新增」且存档中不存在的胶水（以 REPLACEMENT-INVENTORY「增」行为准）
rm -f scripts/quality_gate_judge.py
rm -f tests/test_quality_gate_judge.py
rm -f .github/workflows/pr-queue-controller.yml
rm -f scripts/pr_queue_controller.sh
rm -f tests/test_pr_queue_controller.py
rm -f scripts/droid_absence_sentinel.sh
rm -f tests/test_droid_absence_sentinel.py
rm -f tests/test_evolution_scanner_dedup_inflight.py

# 4. 信封文件：若替换集已删 `droid-review-pr-rollup.yml`，上一步 cp 会从存档恢复它。

# 5. 验证三门产出点文件已回旧内容
test -f .github/workflows/quality-gate.yml
test -f .github/workflows/ci.yml
test -f .github/workflows/droid-review-pr-rollup.yml
grep -n 'name: quality-gate' .github/workflows/quality-gate.yml
grep -n 'name: substrate-gate-suite' .github/workflows/ci.yml
grep -n 'name: droid-review' .github/workflows/droid-review-pr-rollup.yml

# 6. 提交/推送回滚变更（仅当大哥下令；本脚手架阶段不 commit）
# git add -A && git commit ... && git push ...
```

## 语义保证

1. **整集回滚**：用存档覆盖 MANIFEST 中全部路径；再按替换清单删除「仅新集存在」的文件。不要只回滚其中一个 workflow。
2. **三门不断档**：回滚后 `quality-gate` / `substrate-gate-suite` / `droid-review` 仍由旧文件产出，ruleset `23079535` 无需改绑。
3. **不依赖 tag**：当前脚手架以目录存档为准；若后续打了 `pre-cutover` tag，可用 `git checkout <tag> -- <paths>` 作为等价路径，但仍建议对照本 MANIFEST。

## 不做的事

- 不 `git reset --hard` 清掉无关工作区（除非大哥明确要求）。
- 不改 ruleset / `integration_id`（回滚旧胶水后本就不该动）。
- 不单独恢复「半个」FIFO 或「半个」Dedup——半截即假绿/假堵风险。
