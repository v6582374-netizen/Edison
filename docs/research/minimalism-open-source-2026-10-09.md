# Minimalism：高信噪比单任务工具的开源复用研究

核验日期：2026-10-09。研究对象是当前模块的既定需求，而不是泛化的效率软件排行榜。

## 结论与决策

已经存在优秀的开源任务产品，也存在接近我们单任务体验的小项目。但在这轮筛选中，没有发现同时满足下述全部条件、且成熟到值得直接替换当前模块的候选：随时收集、手动拖拽排序、默认只突出当前一件、完成自动推进、不要求启动专注、无强制计时/奖励/全屏、优雅文字、便于嵌入现有 React 应用。

推荐顺序：

1. **成熟产品基准：Super Productivity。** 最值得亲自试用已有产品；具有成熟的任务管理和专注工作流。它与我们的严格体验仍有差异，尤其当前完成处理会打开任务选择器。不要把“支持专注模式”直接当作“满足连续单任务队列”。
2. **最贴近交互理念的参考：niyet。** 已实现新增到队尾、限制可见任务数量、完成后露出下一件、衬线文字。MIT 且 React，可摘取有价值的组件/逻辑，但目前没有找到用户拖拽排序功能；音效、花园、装饰和语言也需要处理。整体换用的收益有限。
3. **以后复用成熟的数据底座：Vikunja。** 已有自托管、任务接口、列表拖拽和任务位置接口。若未来确需多设备同步、备份与任务管理，可让我们保留一张很薄的单任务视图，底下接成熟系统。当前仅为单人短队列引入完整服务器产品，也有额外维护成本。

以上是基于需求的工程判断，不是医疗效果判断。项目自称 ADHD-friendly 不作为疗效证据。

## 如何定义高信噪比

- 当前任务始终稳定。新想法记入队尾，录入完成后回到原任务。
- 排序是临时展开的管理动作；执行时不展示完整积压列表。
- 完成当前任务直接露出下一件，减少再次决策。
- 不强制番茄钟、连续打卡、分数、花园、庆祝动画、每日复盘或能量问卷。
- 文字承担视觉主体；保留宿主导航，不要求额外开始/退出专注。
- 数据可以迁移、备份；许可证和维护状态允许长期使用。

这些是来自本会话的产品约束。字体精致只是其中一项，流程是否制造新决策更重要。

## 候选对照

| 候选 | 新增/拖拽 | 单件与推进 | 与需求的主要差距 | 处置 |
|---|---|---|---|---|
| Super Productivity | 已有任务捕获、拖拽任务列表 | 已有专注页；本次源码的完成动作打开任务选择器 | 计时工作流较重；不是原样连续推进；Angular/NgRx 与现有 React 不同 | 直接使用的首要试用对象；不整包嵌入 |
| niyet | 新事项追加队尾；本次检查的客户端未找到任务拖拽排序 | 可只显示一件；完成移除后下一件出现 | 花园、声音、卡片、装饰；土耳其语界面；较新的小项目 | 字体和单任务队列参考；有限源码复用 |
| Vikunja | 官方文档明确支持 Quick Add 和手动拖拽；API 支持位置更新 | 本次核验未确认原生自动单件执行页 | 需要薄视图适配；完整任务平台有部署成本 | 同步/存储需求出现后优先考虑 |
| Kagelin | 列表/看板与拖拽；快速新增 | 专注围绕 Pomodoro 和选中任务；本次未确认完成自动队列推进 | 习惯、日历、统计、计时增加概念；preview；AGPL | 次级参考，暂不采用 |
| WeekToDo | 自定义清单与拖拽 | 主要是周计划；检查范围内未找到单件执行流程 | 周视图与持续单件执行不同；所查分支较久未更新 | 排除直接替换 |
| Open Sunsama | 日计划看板与拖拽 | 源码明确完成后自动进入下一未完成任务 | 全屏层、计时、日程、完成庆祝；非商业许可证 | 行为参考；不作为自由开源复用首选 |
| OneTaskataTime（panthomg） | 本地收集；单文件未找到拖拽处理 | 单任务与隐藏积压方向接近 | 加入能量、时钟、统计等；README 有占位安装内容 | 原型参考，不作为成熟依赖 |
| CurrentTask | 桌面当前任务提醒 | 单件显示方向吻合 | 维护模式、Windows 优先、macOS 新版构建缺失 | 不作为当前 Web 替代 |
| Tear Off | 官网描述堆栈排序 | 官网描述逐件完成、无游戏化 | 本次未定位到可核验的公开源码仓库与许可证 | 理念参考，不列为已确认开源候选 |

表内“未找到/未确认”限定于本次检查范围；不是对所有版本、插件或分支的绝对否定。Vikunja 的功能来自官方文档/API，其余关键候选另有源码检查。没有通过星数替代品质判断。

## 关键候选的源码证据

### Super Productivity

[官网](https://super-productivity.com/)和[下载页](https://super-productivity.com/download/)提供无账号、离线和 Web 使用方式；[正式发布页](https://github.com/super-productivity/super-productivity/releases)显示持续发布。仓库 [LICENSE](https://github.com/super-productivity/super-productivity/blob/master/LICENSE) 为 MIT。

在本次固定提交中：

- [task-list.component.html](https://github.com/super-productivity/super-productivity/blob/6b34ebe113634f15efbc61b29c756b4eb35dee8b/src/app/features/tasks/task-list/task-list.component.html#L26)使用 Angular CDK 的拖放列表与任务拖拽，包含触摸延迟设置。
- [focus-mode.model.ts](https://github.com/super-productivity/super-productivity/blob/6b34ebe113634f15efbc61b29c756b4eb35dee8b/src/app/features/focus-mode/focus-mode.model.ts#L24)列出 Flowtime、Pomodoro、Countdown。Flowtime 是正向累计时间，并不等于完全去掉时间概念。
- [finishCurrentTask](https://github.com/super-productivity/super-productivity/blob/6b34ebe113634f15efbc61b29c756b4eb35dee8b/src/app/features/focus-mode/focus-mode-main/focus-mode-main.component.ts#L428)完成当前事项后调用任务选择器。这与“完成后无需选择，立刻显示下一件”的目标有区别。
- [package.json](https://github.com/super-productivity/super-productivity/blob/6b34ebe113634f15efbc61b29c756b4eb35dee8b/package.json)包含 Angular、Material 和 NgRx。将专注组件单独搬到 React 中并非直接复制即可使用。

浏览器查看了官方 Web 应用的初始界面，确有 Simple Todo List、Time Tracker、Productivity Suite 三种设置入口。未修改设置、未输入个人任务；因此不把本次查看描述为完整交互验收。

判断：成熟且值得试用；如果选择它，应优先把它当完整产品使用，或在其原生插件/主题能力内做减法。跨框架整包移植会增加维护面。

### niyet

[仓库与产品说明](https://github.com/burakgizlice/niyet)介绍单件任务、隐藏队列和本地保存；[许可证](https://github.com/burakgizlice/niyet/blob/main/LICENSE)为 MIT。[在线应用](https://niyet.burakgizlice.com/)实际可打开，本次查看为空队列。

- [Queue.jsx](https://github.com/burakgizlice/niyet/blob/7ca68204f348763d72a423f0156ed400ecd37c62/src/components/Queue.jsx#L71)以 queue[0] 作为当前事项，并通过 slice 控制显示数量。
- [CountSelector.jsx](https://github.com/burakgizlice/niyet/blob/7ca68204f348763d72a423f0156ed400ecd37c62/src/components/CountSelector.jsx)可选 1、2、3、5 件；设为 1 接近我们的执行视图。
- [useQueue.js](https://github.com/burakgizlice/niyet/blob/7ca68204f348763d72a423f0156ed400ecd37c62/src/hooks/useQueue.js#L120)将新记录追加到队尾。完成逻辑删除对应事项，后续队首自然显示；同文件也明确播放完成声音。
- [index.css](https://github.com/burakgizlice/niyet/blob/7ca68204f348763d72a423f0156ed400ecd37c62/src/index.css#L25)采用 Fraunces 展示字体和 Hanken Grotesk 正文；值得借鉴字体角色分工。中文适配未验证，不应承诺复制字体就能改善中文。
- 完整扫描本次 src 客户端并读 Queue/useQueue 后，未发现任务排序入口与排序操作；搜到的拖动是下拉刷新，不能算任务拖拽。
- [supabase.js](https://github.com/burakgizlice/niyet/blob/7ca68204f348763d72a423f0156ed400ecd37c62/src/lib/supabase.js)在缺少环境变量时直接抛错。匿名用户可本地使用，与“源码完全无需服务配置即可启动”是两回事。

判断：理念近，但不是满足全部需求的现成替代。适合研究字体、队列显示、可选同步；不建议为复用少量数组操作引入整个认证与奖励系统。

### Vikunja

[官网](https://vikunja.io/)确认自托管与 AGPLv3。[官方 Views 文档](https://vikunja.io/help/views/)明确列表手动拖拽及快速新增。[官方 API 文档](https://try.vikunja.io/api/v2/docs)提供任务读写以及任务位置更新接口。

因此可以把任务实体、完成状态、排序与持久化交给成熟底座，前台只投影队列中的当前事项。这是架构建议，本次没有实现或端到端验证该适配。若进入下一步，应固定稳定发布版及对应 API；演示站 API 为开发版本，不能直接用其版本号作为生产依赖承诺。

判断：最有价值的是减少数据层重复开发。其完整 UI 是否安静，与我们采用它的数据能力，是两个独立决定。

### Open Sunsama

[LICENSE](https://github.com/ShadowWalker2014/open-sunsama/blob/main/LICENSE)限制商业用途；[官方解释](https://opensunsama.com/open-source-task-manager)也承认不符合严格的开源定义。归类为源码可见的候选，不能与 MIT/AGPL 项目混为一谈。

[专注页源码](https://github.com/ShadowWalker2014/open-sunsama/blob/0eb237d/apps/web/src/routes/app/focus.%24taskId.tsx)完成后等待约 800ms 跳到下一项；[完成页](https://github.com/ShadowWalker2014/open-sunsama/blob/0eb237d/apps/web/src/routes/app/focus.complete.tsx)包含 confetti、弹跳表情和统计。其自动推进很接近我们的目标，但默认表现需要明显删减。

判断：可借鉴行为设计，当前不推荐作为底座。

## 其他排除证据

- [Kagelin README](https://github.com/Achyuth072/kagelin)明确 preview，包含习惯、日历、统计和 Pomodoro；[FocusTaskPicker](https://github.com/Achyuth072/kagelin/blob/7ac6475/src/components/FocusTaskPicker.tsx)围绕选中任务，不能据此声称已有纯队列自动执行。[LICENSE](https://github.com/Achyuth072/kagelin/blob/main/LICENSE)为 AGPL。
- [WeekToDo README](https://github.com/manuelernestog/weektodo)定义为周计划，支持拖拽与本地保存。所查 main 提交日期为 2024-02-15，说明这条分支近期活跃度有限，不据此声称项目永久停止维护。
- [OneTaskataTime](https://github.com/panthomg/OneTaskataTime) README 宣称 MIT，但 GitHub API 未识别许可证，仓库根目录未见独立 LICENSE。此处记录为许可文本仍需补核，而不是直接指控无授权。
- [CurrentTask README](https://github.com/mistermicheels/current-task)明示 maintenance mode 和 macOS 新版构建限制。
- [Tear Off 官网](https://repo.tearoff.today/)及[关于页](https://repo.tearoff.today/about/)描述无分数、无连续打卡、逐件任务；公开仓库和复用许可尚未核验。
- [Clear](https://github.com/decuyperanthony/clear)的单任务专注仍列在 roadmap，不能把路线图算作已实现。

## 对我们接下来工作的建议

暂停继续围绕装饰性动效迭代。先判断现成产品的工作流是否已经够用：Super Productivity 最适合验证成熟产品能否直接解决需求；niyet 最适合验证单件队列与文字的取舍。

若坚持在 Edison 内使用，保留当前很薄的执行界面是合理的定制。真正应避免重复开发的是将来不断增长的同步、备份、数据迁移、撤销恢复和多端一致性。出现这些需求时，再做 Vikunja 适配验证。不要在尚无需求时把完整任务平台塞进来。

下一轮原型验收应只使用中性测试事项，执行同一组动作：新增 A/B/C → 将 C 拖到首位 → 执行中新增 D → 当前 C 不变 → 完成 C 后自动出现 A → 刷新顺序不丢 → 中文长文字与小屏不破版。任何候选无法通过这组动作，就不应称为完整替代。

## 方法与局限

广泛检索项目官网及 GitHub；六个仓库浅克隆后检查许可证、依赖及相关实现；Super Productivity 与 niyet 另有官方 Web 初始界面查看。未安装候选依赖、未运行它们的测试、未导入个人数据、未修改生产站点。本报告不能代替长期使用和稳定性验收。

代码图工具在本会话不可用；本次是外部仓库研究，采用限定源码范围阅读。下面固定本次提交与提交日期（日期不是质量评分，也不等同发布日）。后续分支可能变化。

| 项目 | 检查提交 | 提交日期 |
|---|---|---|
| super-productivity | 6b34ebe113634f15efbc61b29c756b4eb35dee8b | 2026-10-08T22:42:57+02:00 |
| niyet | 7ca68204f348763d72a423f0156ed400ecd37c62 | 2026-05-30T11:17:49+03:00 |
| kagelin | 7ac6475cc97ed6fd06097640052f867471c392e1 | 2026-10-09T10:36:53+05:30 |
| weektodo | d384fc72d04e9c8a8431d456a3218c08d0c77e29 | 2024-02-15T16:11:04-05:00 |
| open-sunsama | 0eb237d9eb67373fb0c78690b6126d7d6e361368 | 2026-09-30T19:46:02Z |
| OneTaskataTime | ee3e79b360316bed7431a0cea9ae04d07b0fe59c | 2026-08-18T21:02:29+05:30 |
