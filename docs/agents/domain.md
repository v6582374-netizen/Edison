# Domain Docs

## Layout

本仓库使用 single-context。入口是根目录 `GLOSSARY.md`，它将现行术语委托给 `CONTEXT.md`；架构决策统一位于 `docs/adr/`。不因同时包含 Python 后端和 Web 前端而拆成多个领域上下文。

## Before exploring

先读 `GLOSSARY.md` 及其指向的 `CONTEXT.md`，再按任务读取相关 ADR。当前自托管 Web 产品边界以 ADR 0013 及后续有效决策为准；历史桌面决策可能已被取代。

缺少文档时继续工作，不为缺失本身打断用户；新术语与重大权衡由 domain-modeling 在实际明确后再写入。尊重现有 GLOSSARY 的委托关系，避免维护两份术语定义。

## Consumer rules

Issue 标题、设计、测试与实现中的领域词汇应保持一致，使用既有术语，避免被标记为 Avoid 的同义词。如果方案与有效 ADR 冲突，明确指出冲突并讨论是否重开决策，不静默覆盖。

领域文档是产品知识，不承担工作票状态；工作进展、认领、依赖和决策票结论以 GitHub Issues 为准。
