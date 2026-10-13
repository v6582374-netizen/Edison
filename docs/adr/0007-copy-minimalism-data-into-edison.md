# 一次性复制 Minimalism 资料，由 Edison 独立持有

**Status: superseded by [ADR 0015](0015-remove-focus-park-items-locally.md).** items is no longer shipped.

**Status: accepted.**

融合时一次性复制 Minimalism 现有完整资料库与图片，原件保留；迁入后的资料由 Edison 独立管理，两者不再同步。相比直接共用原资料目录，这一选择让 Edison 后续的数据演进不再受旧应用约束，同时保留迁移来源。

迁移完成后，任一应用中的新增、修改与删除均不传播至另一份资料。保留的原件是迁移时的资料来源，并非 Edison 后续修改的持续备份。
