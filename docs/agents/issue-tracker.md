# Issue tracker: GitHub

本仓库的唯一 issue tracker 是 https://github.com/v6582374-netizen/Edison 。规格、决策地图和工作票发布为 GitHub Issues；不得退回 `.scratch/` 或其他本地 Markdown tracker。`docs/agents/*.md` 是技能配置，不是票据。

使用 `gh` CLI，并显式指定 `--repo v6582374-netizen/Edison`；REST 使用 `repos/v6582374-netizen/Edison`。仓库从 YouTube-StrataRead 重命名，旧 URL 不再作为新链接的目标。

## Conventions

- 创建：`gh issue create --repo v6582374-netizen/Edison --title TITLE --body-file BODY_FILE`。
- 阅读：`gh issue view NUMBER --repo v6582374-netizen/Edison --comments`；同时读取标签、状态、assignees 和依赖。
- 查询：`gh issue list --repo v6582374-netizen/Edison --state all --limit 100 --json number,title,state,url,labels,assignees`；需要时分页，不以截断列表证明不存在。
- 评论：`gh issue comment NUMBER --repo v6582374-netizen/Edison --body-file BODY_FILE`。
- 更新正文：`gh issue edit NUMBER --repo v6582374-netizen/Edison --body-file BODY_FILE`。
- 关闭：先发表结论评论，再执行 `gh issue close NUMBER --repo v6582374-netizen/Edison --reason completed`。
- 多行正文使用临时 UTF-8 文件与 `--body-file`，或结构化 API JSON；临时文件只用于传输，远端 Issue 是权威记录。
- 引用票据时使用带 URL 的标题，不用裸编号代替名称。未推送的本地路径/commit 不能成为远端读者唯一的依据；结论与必要上下文必须在 Issues 中可读。
- 发布前查询已有同题票据，避免重复；不修改无关历史票据。

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding operations

- Map：单个 `wayfinder:map` Issue，正文包含 Destination、Notes、Decisions so far、Not yet specified、Out of scope。地图只索引已决结论，不复制子票答案。
- Child：每个决策是独立 Issue，使用 `wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task` 标签。用原生 sub-issues 关联父地图：`gh api --method POST repos/v6582374-netizen/Edison/issues/PARENT/sub_issues -F sub_issue_id=CHILD_DATABASE_ID`。
- Blocking：使用原生依赖：`gh api --method POST repos/v6582374-netizen/Edison/issues/CHILD/dependencies/blocked_by -F issue_id=BLOCKER_DATABASE_ID`。ID 是 REST `.id`，不是 Issue 编号或 node_id。
- Frontier：读取地图原生 sub-issues（分页），按子票顺序筛选 open、assignees 为空、且所有 blocked_by 票据均已 closed 的子票。可读取 `issue_dependencies_summary.blocked_by` 的开放阻塞数量，必要时查询依赖明细验证。
- Claim：开始工作前执行 `gh issue edit NUMBER --repo v6582374-netizen/Edison --add-assignee @me`；assignee 是认领状态。
- Resolve：将答案作为评论发布并关闭子票，再向地图 Decisions so far 添加命名链接及一句摘要。研究资产应上传到可共享位置或将必要内容放入结论评论。
- 先创建全部票据，再添加父子关系和依赖，并回读核验。不得仅在正文中画关系而遗漏原生关系。
- GitHub API 不可用时保留远端状态并报告阻碍，不静默切换本地 tracker。

## When a skill says publish or fetch

Publish 意味着创建或更新 GitHub Issue；fetch 意味着读取远端正文、评论、状态与关系。本地旧地图已迁出，不再用于决策或认领。
