# Reader 阶段 1：持久书库验收

2026-10-14。**本地实现与验收完成，未部署、未推送。** 完整 Reader 第一期尚未完成；下一阶段是可靠续读。原会话为“逆向 Readwise Reader 项目”，本轮承接用户“继续下一阶段”的请求。

## 已交付的产品边界

Edison 有独立 Reader 入口。新 EPUB 导入服务器后，原始字节保持不变，书籍身份与文件名分离。书库支持书名/作者过滤、收件箱／稍后／归档；打开书后支持目录、翻页、字号和原书下载。刷新或重新进入书籍链接可重开同一本书。

相同文件内容重复导入返回既有身份，保留归类；同名不同内容是两本书。两个页面同时修改归类时，旧版本返回冲突并提示刷新。归档不会删除原书。不存在删除接口。

**书库保存不等于续读保存。** 当前不保存阅读位置、阅读进度或排版偏好，没有高亮/笔记 UI、Notebook、书内搜索和批注导出。试验页中的 locator 恢复能力不冒充这些产品能力。

## 实际验收

| 场景 | 结果与限度 |
|---|---|
| 生产构建 | TypeScript + Vite 通过；原有大 bundle 提示仍存在 |
| 真实 GUI + 真实 FastAPI | Chromium、Firefox、WebKit 各完成导航→导入→归类→刷新→重复导入→双页冲突→阅读→下载→损坏文件→快速进出；无 API mock |
| 原书保真 | 浏览器实际下载文件与上传文件 SHA-256 一致 |
| 用户中文书 | Firefox 生产构建额外导入本机测试副本、跳章节、改字号、刷新重开、下载散列核对通过；私有材料未放进仓库 |
| 服务端契约 | 13 项 Reader 测试通过：存储重建、相同/不同内容身份、并发重复导入、版本冲突、损坏与不支持的书拒绝、体积限制、缺失原书恢复、鉴权 |
| 原有模块 | 11 项课程档案页面回归通过（这部分沿用原有 mock）；5 项服务器鉴权/CORS 回归、2 项前端鉴权回归通过；原有 YouTube reader 测试保留并通过 |
| 内容内核 | 9 组基础矩阵、3 组元素选区回归、12 组版式/拒绝行为、3 组安全探针通过，详见[内核验证](./kernel-validation.md) |

测试服务器使用独立临时目录、合成 token 和真实 SQLite/原文件，关闭后清理，不接入用户正式数据。上述结果不等于线上发布验证或跨设备验收。已只读核对线上 Caddy 的前置登录检查与反向代理，未改变其配置；未登录的 Reader URL 返回 401。

## 支持范围与内容边界

- 可重排 EPUB 2/3；支持样本包含横排中文、竖排、RTL 和内嵌字体。固定版式、图片/SVG 作为独立章节、DRM 拒绝导入。
- 原文件上限 64 MB，展开总量上限 256 MB，最多 10,000 个条目。检查 ZIP 路径、重复资源、CRC 和安全 XML，不解包到任意文件路径。
- 书内 XHTML/SVG 经固定 DOMPurify 规则处理。渲染和未来搜索使用同一份清洗文本；内容身份由原书散列和 `reader-safe-epub-v1` 组成。
- 书内 CSP 禁止脚本、连接、表单、框架等；保留书内资源及内联样式，资源从归档解析为 blob。外部链接不打开。原书下载为附件。
- 安全探针曾证明只靠宿主 `script-src self` 不足，当前规则已阻止该攻击样本；这不是完整安全审计。升级清洗器或更改定位相关清洗规则时必须重新验证，并评估内容规则版本迁移。

## 集成归属与上游来源

| 模块 | 归属 |
|---|---|
| `coworker/reader.py` | Reader 的不可变原书、书目、归类及并发版本；SQLite + originals |
| `coworker/server/reader.py` | 挂到现有 FastAPI，继承宿主认证；未另建登录体系 |
| `src/components/reader/ReaderView.tsx` | Edison 阅读界面与内核生命周期，沿用宿主导航、主题及 `api.ts` |
| `src/components/reader/safe-epub.js` | 有边界的归档加载与固定清洗规则，不改原书 |
| Foliate-js | 固定 `78914aef4466eb960965702401634c2cb348e9b1`，实际复用 EPUB、CFI、目录、分页、覆盖层与搜索模块；MIT |
| DOMPurify | 固定 3.4.17，实际用于正文/SVG 清洗；Apache-2.0 或 MPL-2.0 |
| zip.js / fflate | 随固定 Foliate 提交携带的 vendor 字节；BSD-3-Clause / MIT。fflate 来自上游通用格式工厂的静态依赖，当前 EPUB 路径不调用它 |

许可证随 `public/licenses/` 打包。没有复制 Readwise 的产品实现。依赖版本与来源保存在 `package.json`/lockfile。

Vite 有两项窄适配：只替换 Foliate 通用工厂的可选 PDF 适配器（不影响宿主现有 PDF 查看器）；为已复现的分页器退出回调增加存活检查。后者包括 resize、样式帧、字体就绪和未建成 view 的销毁。补丁在构建和开发模式均应用，精确匹配固定上游源码，升级后匹配失败会阻断构建，不能静默忽略。安装后的 `node_modules` 没有手改。

## 重跑

```sh
python3 tests/fixtures/reader/generate.py
.venv/bin/pytest tests/test_reader_library.py tests/test_reader.py tests/test_web_auth.py -q
cd surfaces/gui
npm run build
npx playwright test -c playwright.reader.config.ts --grep 'real server:'
npm test -- src/api.auth.test.ts
npx playwright test e2e/curriculum-archive.spec.ts
```

可选私有书验收：设置 `EDISON_READER_TEST_EPUB` 为本机测试副本绝对路径，运行 `npx playwright test -c playwright.reader.config.ts --grep 'optional private' --project=firefox`。不把书籍或其页面截图提交到公开仓库。

完整测试输出、前后错误证据和代表截图保留于本机调查目录的 `evidence/stage1-*`。正式源码中只带合成测试书及生成脚本。

## 视觉检查与清理

已查看 Chromium 书库宽屏、Firefox 阅读宽屏、WebKit 760×900 窄屏及真实中文书页面。标题、目录、正文、混排文字和图片可见，主要操作未遮挡。WebKit resize 后首帧截图曾截到重排中间态；等待分页稳定后复查，段落完整。窄屏保留可收起目录；本轮没有宣称完成手机端验收。

所有浏览器均为本轮启动的 headless 实例，由测试框架关闭；没有新增可见窗口或预览。隔离测试服务用毕停止。源码保持本地未提交状态，原有其他模块的改动保留。

## 后续出口

阶段 2 必须把“书籍身份”“停读位置”“最远进度”分开，保存至同一服务器；刷新恢复精确位置、排版变化不丢段落、旧页面不覆盖新进度、保存失败可见。通过这一层后再进入高亮与笔记，不能把百分比当成批注锚点。

阶段 2 后续已于 2026-10-14 完成本地验收，见[可靠续读验收](./stage2-validation.md)。本页保留阶段 1 当时的范围与结论。
