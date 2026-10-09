# minimalism 动效机会筛选

本轮按 `find-animation-opportunities` 做只读筛选；下列建议没有作为动效实现。界面结构与排版的改动由用户的独立请求及 `apple-design` 指导。

范围：当前任务、记录面板、队列增删与排序、完成动作、空状态、宿主导航。React + CSS + Pointer Events；没有动画库。宿主已有 `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`。

## 机会

| # | 位置 | 当前表现 | 目的 | 频率 | 建议 |
| --- | --- | --- | --- | --- | --- |
| 1 | `surfaces/gui/src/components/focus/TaskQueue.tsx:20` | 手柄拖动时只有行透明度和落点提示，事项没有跟随手指 | Feedback / Spatial consistency | 仅指针拖拽，预计每天数次；键盘排序不动画 | 可选的拖动副本：`transform: translate(dx, dy)` 逐帧 1:1 更新，拖动期间 `0ms linear`，保留抓取偏移；松手只将副本 `opacity: 1 → 0`，`120ms var(--ease-out)`，不移动正在阅读的其余文字。reduced motion 下保留静态落点，副本只做 `80ms var(--ease-out)` 透明度变化，取消副本位移。 |

四道门：低频且仅指针发起；目的为直接操作反馈；拖动没有延时、结束小于 300ms；只在用户明确拖拽的队列中发生，不干扰主任务。它是可选的可用性提升，现有落点提示已足以完成排序。

## 明确拒绝

- `FocusView.tsx:55`，当前任务的逐字出现、呼吸、鼠标追光。**Function 失败**：文字需要持续可读，运动会竞争注意力。
- `FocusView.tsx:56`，完成后弹跳、勾号庆祝或任务滑入。**Purpose / Function 失败**：下一件文字已清晰表达状态，额外运动没有必要。
- `FocusView.tsx:59`，记录面板弹出与收起。**Frequency 失败**：突发想法可频繁记录，并包含键盘输入与 Escape 返回；立即响应更适合。
- `TaskQueue.tsx:15`，新增事项的 stagger、键盘排序的行移动。**Frequency 失败**：高频记录与键盘操作不应等待动画。
- `focus.css:13`，任务字号的平滑缩放。**Function 失败**：排版应一次稳定落位，不能在阅读时变化。

## 结论

主画面需要稳定、即时的反馈，没有需要增加的装饰动效。唯一保留的可选机会是队列拖拽时让事项跟随手指；这不改变已经完成的减法与文字层级。若后续决定实施，可交接：`improve-animations plan minimalism 队列的指针拖拽副本，主任务与键盘操作保持静止`。

## 用户指定的悬停反馈

后续用户明确询问任务文字悬停反馈。该独立交互请求按 apple-design 实现为近乎不可察觉的提亮：`opacity: .92 → 1`，进入和离开都采用 `160ms var(--ease-out)`；仅 `@media (hover: hover) and (pointer: fine)` 生效，reduced motion 为 `80ms`。没有移动、缩放、字距变化、追光或持续循环。它提供一次指针反馈，不改变文字可读性，不增加点击行为。

双重滚动已合并为单一列表滚动且隐藏滚动条；窗口外 pointerdown 收起工具面板，不额外添加退出动画。

## 正式站点反馈修订（2026-10-09）

用户反馈纯提亮难以感知，并明确要求可见的悬停动效。按此反馈将实际任务与空队列提示统一为 `scale(1) → scale(1.025)`（220ms，宿主 ease-out）与 `opacity: .9 → 1`（160ms）。仅精确指针启用，移出恢复；reduced motion 取消缩放，保留 80ms 提亮。主画面不持续运动。侧栏恢复宿主默认布局及用户折叠偏好，不再自动进入全屏。
