# Minimalism 交付说明

> 当前物品模块的显示名称为 **items**（2026-10-09 更名）。本文中的 Minimalism 为集成时的历史名称和原资料来源；存储与备份格式不变。

Minimalism 已成为 Edison 的独立侧边栏模块。物品记忆、YouTube 阅读与对话共用桌面宿主，物品资料具有独立归属；模块不包含原项目尚未实现的 Skills 管理规划。

## 使用方式

首次打开 **Minimalism** 时，应用自动复制本机原资料库和图片，显示迁入进度；成功后不重复导入。原件保持不变，之后两份资料独立管理。源资料不存在时直接进入空画廊。

画廊支持搜索、最近添加、排序、集合管理与拖放归集。档案包含多段记忆、照片、购买信息及物品专属字段；归档与回收站相互独立。基本字段离焦保存，记忆和自定义资料通过保存按钮提交；切换 Edison 模块保留当前档案草稿及封面预览。语言与配色跟随 Edison，折叠侧边栏后为窗口控制保留独立空间。

在 **设置 → 图像生成** 中填写新的端点、图像模型及 API Key。旧生图服务配置和凭据不会读取或迁入；旧封面提示词保留在 Minimalism 模块设置。生成使用最多四张档案照片作为参考；结果经预览、采用后才替换封面。未配置生图服务不影响现有图片、浏览、编辑和备份。

桌面导出使用原生文件夹选择器，将完整资料保存为 `.minimalism` 目录包；浏览器开发模式下载 ZIP。两种格式都可恢复，恢复会明确提示全量替换。备份包含资料与图片，不包含生图服务凭据。迁入和恢复均先校验完整资料，再一次性切换到新资料库。

## 资料与实现归属

- `coworker/minimalism`：兼容原 Swift JSON 记录的 SQLite 资料库、照片、原子迁移与恢复、封面生成。
- `coworker/server/minimalism.py`：沿用 Edison 启动令牌认证的桌面接口。
- `surfaces/gui/src/components/MinimalismView.tsx` 与 `minimalism/`：宿主视觉体系下的画廊、档案与设置。
- 物品资料默认位于 Edison 状态目录的 `minimalism/`，可用 `EDISON_MINIMALISM_WORKSPACE` 指定隔离目录；不会跟随某个对话项目切换。
- `EDISON_MINIMALISM_SOURCE` 和 `EDISON_MINIMALISM_PREFERENCES` 仅用于显式指定迁移来源或隔离测试。默认读取原应用目录及非服务偏好，不访问旧 Keychain。

## 已完成验证

- 原 Minimalism 当前源码在隔离目录中构建，17 项命令行检查通过。原生 UI 启动受旧应用 Keychain 读取阻塞，因此不将其记为 UI 验收通过。
- 实际资料临时迁移：12 件物品、2 个集合、19 张图片；备份恢复后的记录一致，原目录文件校验一致。正式 Edison 资料仍由首次进入模块时自动创建。
- 全量 Python 测试运行：2096 项通过、1 项需要真实 OpenAI 凭据的测试跳过；此后对集合排序、原生备份等收尾变更追加模块定向验证。
- 前端现有 192 项测试通过；中英文词条一致性、语言选择与认证传输检查通过。
- Chromium 与 macOS WebKit：在 900 和 1440 像素窗口中执行真实后端流程，覆盖迁入、配置生图、创建集合及物品、保存记忆、模块切换保留草稿、上传照片、生成取消和采用、归档恢复、备份恢复与中英文界面。
- 外部付费生图供应商在测试中使用本地模拟服务；物品接口、HTTP 认证、SQLite、图片文件与生成协议均使用真实实现。尚未对用户重新配置的外部服务进行付费调用。
- 原生备份的文件创建与恢复通过真实文件系统往返验证；仅文件夹选择动作在浏览器测试中模拟。macOS TIFF 照片转换另经系统图像工具实际验证，迁移不会改写原照片。
- 打包后的本地服务通过实际资料隔离迁入检查，确认启动正常、设置访问不触发迁移、旧生图凭据不迁入。

## 开发验证

```sh
uv run --no-sync pytest tests/test_minimalism.py -q
npm --prefix surfaces/gui test
npm --prefix surfaces/gui run e2e -- --config playwright.minimalism.config.ts
npm --prefix surfaces/gui run build
```

端到端测试使用临时资料和独立端口；运行前需已有项目 Python 环境及 Playwright 的 Chromium/WebKit 浏览器。
