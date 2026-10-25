# Reader 阶段 5：完整闭环与 Web 部署形态验收

2026-10-15。承接[检索与导出](./stage4-validation.md)。本阶段验证当前电脑上的完整产品闭环，并用真实 HTTPS 代理、网页登录及正常后端启动入口核对持久化。**本地验收与线上发布是两件事：本轮没有修改生产实例，也没有提交或推送代码。**

## 这次验证什么

前四阶段分别证明书籍、续读、批注、搜索与导出能工作。本阶段要求同一本书、同一批笔记从导入一直走到导出，期间经过服务进程退出与重新登录，仍保持原始身份。不能用浏览器当前还显示着文字作为保存成功的依据。

新增 `playwright.reader-acceptance.config.ts` 与 `reader-acceptance/`，不增加产品服务或接口。每个场景使用全新隔离数据目录，通过正常 `python -m coworker.server.run` 启动 Edison，以 Caddy 提供生产 GUI、HTTPS 和 `forward_auth`。浏览器通过真实表单登录，走 HTTPS 同源默认 API，不注入后端地址或桌面 token。代理仅在服务器侧补充后端认证头；测试明确断言浏览器没有发送这个头。

临时登录凭据只用于测试。后端环境采用受限的显式配置，隔离用户状态与连接器凭据。代理仅监听 loopback，管理接口关闭；证书是一天有效的自签测试证书，仅测试浏览器忽略它的证书校验，不安装系统信任、不改变现有代理。使用 Caddy 2.10.2 官方 macOS arm64 二进制，下载包已核对官方 SHA-512 清单。它保存在被 Git 忽略的本机测试工具目录。

此配置验证已知的 Web 部署契约，不声称是当前线上 Caddy 配置的逐字副本，也不证明线上 TLS、网络或权限配置。

## 一条连续的用户流程

合成 EPUB 与本机中文书分别在 Chromium、Firefox、WebKit 中运行：

1. 未登录打开 Reader，进入登录页；未登录 API 返回 401。
2. 表单登录，核对 Secure、HttpOnly、SameSite=Strict cookie；从 Edison 导航导入 EPUB，核对书籍散列，归入稍后。
3. 打开原书，在实际渲染文字中创建高亮，保存高亮笔记与整本笔记，翻页并保存字号和停读位置。
4. 对本次启动的后端进程发送 SIGKILL，再用同一隔离数据目录正常启动。旧登录失效，重新登录后恢复同一本书、同一停读原文和字号；服务器 Notebook 快照与结束进程前相等。
5. 从 Notebook 回跳、查找正文、归档，再从全库笔记结果回到同一高亮。查找不改变批注，跳转目标使用后从 URL 清除。
6. 编辑笔记后使真实登录会话失效。书目、阅读记录、Notebook、原书、搜索和两种导出都返回 401；保存失败可见，当前编辑保留。在另一测试页重新登录后，回原页重试保存，保留原高亮 ID。
7. 通过界面实际下载 JSON、Markdown 与原 EPUB。JSON 与最新服务器 Notebook 相等，书籍身份和归档状态一致；Markdown 包含对应高亮 ID；下载原书的 SHA-256 与输入文件一致。

测试通过真实 GUI 完成业务写入；API 只观察服务器事实及结束测试登录会话，没有用伪造成功响应替代保存。章节文字使用实际 DOM 选区；指针拖选由原有 Reader E2E 覆盖。强制结束测试仅发生在服务器确认写入之后，不声称未确认编辑能从断电中恢复。

## 验收结果

| 验收层 | 结果 |
|---|---|
| HTTPS 完整链路：合成书 + 实际中文书 × 三浏览器 | **6 / 6 通过**，包含强制结束后端、登录失效和重试 |
| 原有 Reader E2E | **84 / 84 通过**，含实际中文书、EPUB2、竖排、RTL 和内嵌字体回归 |
| Reader 与宿主相关 Python 回归 | **88 / 88 通过** |
| 课程与 YouTube 界面回归 | **24 / 24 通过** |
| 前端认证回归 | **2 / 2 通过** |
| TypeScript 与 Vite 生产构建 | 通过；既有大 chunk 提示保留 |
| 新增验收工具独立 TypeScript 检查 | 通过；补充 Node 开发类型依赖，不增加产品运行依赖 |
| `git diff --check` | 通过 |

已逐一查看 Chromium 与 WebKit 的实际中文书闭环截图：原文、高亮、Notebook、同步状态与导出按钮可见，未发现正文裁切或操作遮挡。原有窄屏、字号和主题回归继续通过；没有把桌面窄窗口等同于真实手机验收。

本次启动的 headless 浏览器、后端与 Caddy 均已退出，8791、8792、8943、5199 无遗留监听。临时服务器数据目录和私钥已由验收工具删除；没有打开可见窗口或预览面板。

相关 Python 回归包含 Reader、YouTube 原有阅读模块、课程归档、Web 登录和发布目录切换；界面宿主回归包含课程表、课程档案、YouTube 列表与详情。宿主界面测试沿用已有网络 mock，因此不将其描述成线上 YouTube 获取或课程同步验收。

阶段 5 没有修改 Reader 产品实现。本次新增的是完整链路验收工具与交付边界，保留其他模块在途改动。无需为通过验收再堆一层产品抽象。

## 复现

先在本机准备可执行 Caddy（可通过 `EDISON_CADDY_BIN` 指定已核对的二进制）。8792 与 8943 必须空闲；测试发现占用会失败，不连接已有实例。

```sh
cd surfaces/gui
npm run build
EDISON_CADDY_BIN=/absolute/path/to/caddy \
EDISON_READER_TEST_EPUB=/absolute/path/to/private-test-book.epub \
npx playwright test -c playwright.reader-acceptance.config.ts

EDISON_READER_TEST_EPUB=/absolute/path/to/private-test-book.epub \
EDISON_READER_LAYOUT_DIR=/absolute/path/to/layout-fixtures \
npx playwright test -c playwright.reader.config.ts

npx playwright test -c playwright.config.ts \
  e2e/curriculum-archive.spec.ts e2e/curriculum.spec.ts \
  e2e/youtube.spec.ts e2e/youtube-documents.spec.ts --workers=2
npx vitest run src/api.auth.test.ts
```

私有书路径是显式可选项，不提供时对应测试跳过。当前私有书案例沿用本机调查副本的第六个目录项；不是对任意 EPUB 都成立的通用自动验收。实际原书保持不动。

证据保存在本机 `ReaderAnalysis-20261013/evidence/stage5-final/`：测试输出、合成和私有书页面、实际下载的笔记、身份核对摘要与构建文件散列清单。私有内容不进入 Git。

## 交付判断与发布边界

第一期在已限定的可重排 EPUB 范围内具备完整本地阅读闭环。待处理的是生产实例的发布与发布后验收，而不是继续往第一期添加功能。

发布时仍必须核对：候选前后端来自同一份已验收工作树；现有生产数据根和 Reader 存储权限；实际 Caddy 的受保护路径和上传边界；旧版本回退路径；发布后用测试书复查正常登录、保存、重启与下载。当前工作树同时含课程、认证、agent 等其他在途修改，不能将整个目录盲目覆盖生产实例。这些是未执行的发布步骤，不是本次测试结果。

不扩大既有承诺：固定版式、DRM、跨章节选区、全库正文联合搜索、JSON 导入恢复、离线、跨设备、大规模性能、手机手势均不作为已验收能力。已有 500 kB 构建块提示继续记录，没有因测试通过而消失。

## 后续状态

2026-10-15 阶段 6 已完成生产发布与线上验收，见[发布记录](./stage6-release.md)。本报告中的“未部署”保留为阶段 5 当时的事实。
