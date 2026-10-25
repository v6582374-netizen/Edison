# EPUB 内核验证与选型

2026-10-14。已接手“逆向 Readwise Reader 项目”。**阶段 0 在明确的支持范围内通过，采用固定 Foliate-js；阶段 1 另见[持久书库验收](./stage1-validation.md)。** 不是对所有 EPUB 或完整第一期的完成声明。

## 支持范围

固定上游提交 `78914aef4466eb960965702401634c2cb348e9b1`，MIT。支持可重排 EPUB 2/3，章节为 HTML/XHTML。竖排、RTL、内嵌字体已用合成样本验证。固定版式虽然能显示，但上游内容接口缺少可用于同一高亮契约的 index/overlayer，本轮明确拒绝；图片/SVG 独立章节与 DRM 同样拒绝。

单次文字选择限定在一个 spine 资源内。整段、多段、元素边界、跨内联标签、中文/英文、emoji 和组合字符能保持原文字节语义；不规范化空白或 Unicode。图片选区、空选区、错误 document 均拒绝。没有实现跨 spine 合并选择。

## 最终结果

| 验证 | Chromium | Firefox | WebKit |
|---|---|---|---|
| EPUB 2 NCX 合成书 | 14 项通过 | 14 项通过 | 14 项通过 |
| EPUB 3 nav 合成书 | 14 项通过 | 14 项通过 | 14 项通过 |
| 用户中文书测试副本 | 8 项通过 | 8 项通过 | 8 项通过 |
| 元素边界选择、保存、刷新、恢复及拒绝边界 | 通过 | 通过 | 通过 |
| 竖排、RTL、内嵌字体及固定版式明确拒绝 | 4 组通过 | 4 组通过 | 4 组通过 |
| 主动内容/外部资源安全探针 | 通过 | 通过 | 通过 |

基础矩阵共 108 项断言，涵盖目录、图片、脚注、改字号/宽度、范围恢复与中文搜索。版式矩阵另验证重叠批注各自保留身份。合成书未经 EPUBCheck 认证，不能称为官方标准符合性测试集。

这些 locator 测试在刷新后由测试进程重新注入序列化记录，证明定位器可重建；**不证明产品已保存续读和批注**。另有上轮 Zen 真实鼠标拖选与字号重排的合成书验证，不等于在 Zen 重跑整套矩阵。

## 关键修正

原始 Foliate CFI 对元素容器边界可能编码为空范围。先用正常文件导入、整段选择、保存/刷新复现三个引擎的失败，再通过 `selection.js` 将边界转换为同一原文的 UTF-16 文本节点端点；保存前强制 CFI 往返比对。原始错误 CFI 仍会明确拒绝，基础矩阵保留这一防线。生产界面的高亮功能尚未接入，规范化函数目前只在试验页使用。

DOMPurify 清洗曾丢失中文书中 `epub:type` 的命名空间，导致 Firefox 的两个章节解析为空；补回根命名空间后，逐章解析及完整三书矩阵通过。重跑时曾出现一次 Chromium 文件输入在 reload 后未触发的超时，保留失败结果；同一矩阵重跑全部通过，没有跳过断言。

## 内容边界

初始宿主 CSP 的 `script-src self` 允许 EPUB 引用同源脚本，三个引擎均复现执行。这是同源 blob 的真实风险，不能把宿主 CSP 当成书籍隔离。

当前由固定 DOMPurify 3.4.17 去除主动内容，给书内文档首部加入独立 CSP：禁止脚本、连接、框架、表单、对象；样式仅允许内联/blob，图片/字体仅允许 blob/data。归档资源由受限 ZIP loader 加载，不为书籍主动请求网络。渲染和 `createDocument` 使用同一份缓存清洗结果；原书保持原字节，内容身份绑定 `reader-safe-epub-v1`。

最终安全探针未执行脚本、未收到外部资源响应。Chromium 能观察到被 CSP 阻止的尝试；Firefox/WebKit 没有相应请求事件。不能把 Chromium 的“尝试”误报为服务器已收到请求。专用 probe 只使用合成脚本和测试内容，不读取真实凭据。此结果不是完整安全审计。

## 证据与重跑

隔离目录：[kernel-spike](/Users/shiwen/Downloads/CodexProjects/ReaderAnalysis-20261013/kernel-spike/README.md)。其上游源码保持未修改；生产接入另有明确的生命周期构建适配，见阶段 1 记录。

- 基础矩阵：`results/results.json`；历史基线：`results/baseline-20261014.json`。
- 元素边界：`results/selection-regression-before.json`、`results/selection-regression.json`。
- 版式：`results/layout-validation.json`，fixed 的通过表示正确拒绝，不是完整固定版式支持。
- 安全：`results/security-validation-before.json`、`results/security-validation.json`。
- 用户书籍和含正文的截图只留本机，不进入公共源码。

在隔离目录启动 `python3 serve.py`，依次执行 `node verify.mjs`、`node selection-regression.mjs`、`node layout-validation.mjs`、`node security-validation.mjs`。服务只监听回环地址，用毕停止。
