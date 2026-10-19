# Reader EPUB 受控运行验证

2026-10-13。使用用户授权的本机电子书，在既有 Zen 登录会话中验证 Reader 官方 Web 产品。此验证为独立实现提供行为参照，不代表 Edison 已具有这些功能。

## 测试材料与隔离

选用 Downloads/e-books 中的《阅读是安静的自我觉醒》，EPUB 2.0，27 个 spine 项，37 个 ZIP 项，原文件 443,829 字节。原件 SHA-256：`7ec391b7835c4d997135d015fd2387c71b099e2d54ef4e5707631fd27b481660`，测试后重新计算一致。

只上传派生副本；改动 OPF 中的标题与唯一标识，标题为 **[REA TEST 20261013] 阅读是安静的自我觉醒**。逐 ZIP 项比较确认只有 OPF 有差异，正文与资源字节保持原样。测试文档 ID：`01m4sz3naypc667bxh0h0xnt7a`。没有在用户已有书籍中创建批注或改变归类。

测试副本 SHA-256：`ff26c954c688efd1cfe6bbe36e2df346ce419ac46835f04981bae0e0be882abe`。

## 结果

| 场景 | 方法与观察 | 可得结论 |
|---|---|---|
| 导入 | Zen 中 Add → Upload，经系统文件选择器上传副本；Inbox 数量 55→56，产生独立测试文档 | 本书通过实际上传、解析与建档 |
| 目录与内容 | 23 个可见目录入口；跳转“关于阅读”，中文正文及图片可见 | 本书的目录、正文与图片可读取；未遍历全部章节与链接 |
| 创建批注 | 使用 Reader 的焦点导航和 H 快捷键，分别建立一条图片高亮、一条文字段落高亮；各附一条 ASCII 标记笔记 | 两种焦点高亮与笔记操作成功；鼠标拖拽精确选区未验证 |
| 重排与刷新 | Source Serif、Light、行距 1.4、Medium 行宽不变；字号 20→24px 后刷新，Notebook 保留两条批注与笔记，文字高亮仍出现在同一段，字号仍为 24px | 本段批注与设置跨本次重排、刷新保持；没有测量逐字符位置误差 |
| 续读 | 刷新后仍处于测试正文附近，UI 进度为 6%；连接接口返回 0.06747035773092026 | 停留信息存在并可恢复到已读内容附近；不是浏览器重启或精确滚动坐标验收 |
| 服务器记录 | 刷新后，用连接的 Readwise 接口只读取得本测试文档高亮 | 返回两条持久记录，图片、文字各一条，两条笔记均存在 |
| 中文书内查找 | 在实际输入事件下查找“消遣”，显示 `1 of 5+`；下一结果变为 `2 of 5+` | 本书支持该中文词语匹配与结果切换；`5+` 不是已枚举的最终总数 |
| 归类 | 连接接口依次改为 Later、Archive、New，每次按文档 ID 读回 | 三次状态一致，阅读进度均保持；此项验证数据语义，没有把它冒充三次 UI 操作 |
| Markdown 导出 | Notebook 的下载入口，经系统保存对话框取得文件 | 736 字节，包含测试标题，两条笔记各出现一次；验证了可用下载及内容，而非可恢复备份 |

图片笔记标记为 `REA reverse test 2026-10-13`，文字笔记标记为 `REA TEXT ANCHOR 20261013`。导出 SHA-256：`6d35ed7b645653b17662688f5c4a5e959233684fa2432edca33be129a3674d81`。

原始导出包含书籍摘录和资源访问链接，仅保存在本机调查目录；本报告不复制它们。一次通过 AX 直接设置搜索框值没有触发完整的 React 输入行为，随后使用真实键盘/粘贴事件得到上述结果，因此没有将那次中间状态记为 Reader 搜索缺陷。

## 清理

已恢复字号 **20px**。仅删除带测试标题的副本，Inbox 回到 55；在 Trash 页面核对该标题存在，保留正常恢复途径，没有清空回收站。测试副本的批注随该文档进入删除流程。本次新建的 Zen 标签页已关闭，用户原有标签页保留；分析时启动的 Reader 桌面应用已关闭，最终应用清单显示未运行。原书散列保持一致。

## 证据与限制

结构化记录保存在[运行账本](/Users/shiwen/Downloads/CodexProjects/ReaderAnalysis-20261013/evidence/runtime-verification-ledger.json)、[归类读写核对](/Users/shiwen/Downloads/CodexProjects/ReaderAnalysis-20261013/evidence/test-classification-verification.json)、[高亮服务器摘要](/Users/shiwen/Downloads/CodexProjects/ReaderAnalysis-20261013/evidence/test-highlight-server-summary.json)和[导出核对](/Users/shiwen/Downloads/CodexProjects/ReaderAnalysis-20261013/evidence/test-export-verification.json)。这些为 CUA 原生界面观察、连接接口与本地文件核验，**不是 REA 的 CDP 浏览器捕获**。

本次仅测试一本 EPUB 2.0，没有覆盖 EPUB 3、固定版式、竖排、脚注、多处重复引文、跨章节选择、浏览器重启或离线。静态源码来自桌面版 0.1.3293；动态测试来自当前 Web 构建，不能据本次结果宣称两个构建完全等价。后续候选 EPUB 内核须在 Edison 中独立验证这些契约。
