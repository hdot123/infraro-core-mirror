# do-not-merge 逃生舱使用说明

`do-not-merge` label 是 auto-merge 的「仅开 PR，不合并」开关：给 PR 打上该标签，
auto-merge 对它全腿静默（不合并、不更新分支、不发冲突评论）；移除标签后自动恢复
合并候选资格。

## 什么时候用

- PR 需要在 main 上长期在飞（如灰度观察、分批评审），不希望被全绿 sweep 卷走合并
- 需要暂时冻结某个 PR 的自动合并行为，等待人工决策

## 怎么用

```bash
# 冻结：打标签（auto-merge 下一轮评估起跳过，run 日志会输出跳过行：PR 号 + 原因）
gh pr edit <PR 号> --add-label do-not-merge

# 解冻：移除标签（恢复候选资格，下一轮评估照常）
gh pr edit <PR 号> --remove-label do-not-merge
```

## 行为细节

- label 名匹配大小写不敏感；命中优先级最高，任何 mergeable 状态下都跳过
- 跳过是绿腿静默：run 日志留一行跳过记录（含 PR 号与原因），不产生失败 check
- 引擎仓 label 清单由 setup-labels 机制维护（红底白字）
