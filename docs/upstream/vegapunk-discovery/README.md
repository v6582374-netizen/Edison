# Vegapunk Discovery 运行观察界面

来源与固定版本见 `source.json`。该版本包含 Vegapunk 对嵌入 OpenWorker 的定制，并非 OpenWorker 原始基线自带界面。根许可证 Apache-2.0，嵌入 OpenWorker 子树许可证 MIT，原文均保留；构建时 `surfaces/gui/public/licenses/vegapunk-discovery.txt` 会进入前端分发目录。

## 实际运行路径

`YouTubeView → ProgressPage → discovery/LaunchHero、discovery/RuntimeEvents`，均位于 `surfaces/gui/src/components/youtube/`。`LaunchHero` 从上游 `PrototypeLaunchHero` 直接抽取 DOM、阶段轨道与指标布局；`RuntimeEvents` 从 `PrototypeRuntimeDesk` 抽取事件循环与严重等级呈现。`discovery.css` 直接保留上游呼吸、光环、事件入场、响应式与减少动态效果规则；`hierarchy.css` 保留上游扁平分区规则。上游片段与适配差异一并保存，可逐项比较。

## 宿主适配

研究 Launch、round、milestone、产物语义改为 YouTube 当前任务、真实生成阶段、队列与累计资料。状态来自既有 Edison capability 入口，没有引入第二个后端、科研启动流程、200 ms 轮询或 SSE 原始日志。

上游呼吸动画不是 worker 心跳。Edison 的后台健康监测与实际阶段更新分别呈现；断连、心跳失效及无执行任务时停动画，不能用呼吸证明任务有进展。事件由宿主持久化，按序号从新到旧展示。

观察界面的迁入范围不包含 Discovery 准备、实验、论文、artifact 浏览器或停止科研进程协议；这些不属于本次 YouTube 进度能力。上游 `PrototypeStatusPill` 未迁入，状态直接由概览和现有宿主控件呈现，避免重复组件。

Vegapunk 子模块及本地克隆已移除。Edison 的运行与构建不依赖上游仓库；本目录仅保留已迁入代码的来源、许可证、原始片段与适配补丁。
