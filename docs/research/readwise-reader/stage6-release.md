# Reader 阶段 6：生产发布与线上验收

2026-10-15。Reader 已发布至 [Edison Reader](https://96.44.169.142/?module=reader)。第一期从本地实现进入可使用的自有服务器产品：原 EPUB、阅读位置和批注由服务器保存。本次没有提交或推送 Git，也没有发布其他在途后端改动。

## 发布边界

生产版本为 `reader-20261015-562779ae5b`。先还原旧 Web 构建，确认其入口 HTML、主 JavaScript 和 CSS 与线上逐字节一致，再叠加已验收 Reader。候选后端以线上已安装包为基线，只加入 `coworker/reader.py`、`coworker/server/reader.py`，并在原 `server/app.py` 增加 Reader 挂载。源码镜像同步相同三处。未把工作树里的 agent、manager、shell、课程等其他改动混入部署。

运行环境为现有 Linux Python 3.12、`edison` 用户和同一 FastAPI 服务；只增加已校验的 `defusedxml==0.7.1`，不升级其他依赖。Reader 数据位于 `/srv/edison/state/reader`，由服务用户正常创建。Web 原子切换，保留旧版本及 82 个既有资源，避免已有标签页的资源立即失效。

Caddy 配置及登录保护未改变。公开 HTTPS 的书目、搜索、Notebook、原书与导出接口均验证未登录返回 401。后端部署时正常重启一次，网页登录需重新认证；没有为验收强制结束生产服务。强制结束后的恢复在隔离候选环境验证。

## 验证结果

| 本阶段新执行的验证 | 结果 |
|---|---|
| 隔离候选 Reader E2E，三浏览器，含本机私有书及版式夹具 | 84 / 84 通过 |
| HTTPS 正常登录与完整闭环，合成书及私有书 × 三浏览器 | 6 / 6 通过，含进程退出、重启、登录过期与重试 |
| Reader Python 契约 | 44 / 44 通过 |
| 候选生产构建 | 通过，保留既有大 chunk 提示 |
| 生产 Linux、实际非特权服务用户、隔离数据目录预检 | 导入、原文件、导出、搜索及鉴权通过 |
| 生产 Zen 正常登录与合成书验收 | 导入、高亮、两类笔记、翻页保存、刷新、Notebook 回跳、中文正文与笔记搜索、三种下载通过 |

阶段 5 的 88 项相关 Python、24 项课程/YouTube 界面及 2 项前端认证结果仍是上一阶段证据，不重复计作本轮新测试。

线上只上传 2.5 KB 合成书，没有上传私人电子书。通过真实正文选区保存一条高亮、整本笔记与高亮笔记，刷新后仍能读取已保存内容；翻页后服务器停读锚点记录第 10 段，最远到达为 16.3%。Notebook 回跳原文，正文搜索“思想”命中一处，书库搜索“上线验收”返回两类已保存笔记。

通过 Zen 实际保存 JSON、Markdown 和原 EPUB，再独立读取服务器快照核对：JSON 的书籍与 Notebook 内容相等，Notebook revision 为 3；Markdown 包含同一高亮 ID；原书 SHA-256 为 `e0b39f0e18c421a61cce46131b8239a00b4ee3bd432dffa675986d4452595b45`，与输入相等。检查实际页面，原文、高亮、Notebook 与同步状态正常显示。

生产课程录播数量保持 44，YouTube 已就绪资料保持 46，课程定时任务仍 active。本次不触发新的课程同步或 YouTube 获取，不能把数量核对等同于这些外部获取链路的完整验收。

## 回退与清理

当前 Web：`/srv/edison/app/web-releases/reader-20261015-562779ae5b`。

保留的旧 Web：`/srv/edison/app/web-releases/module-removal-20261013-daf0e6e-r2`。

回退资料：`/srv/edison/app/rollback-reader-20261015-562779ae5b`。需要回退时，在服务器执行：

```sh
python3 /srv/edison/app/reader-stage-20261015/rollback.py
```

脚本先检查没有后续版本漂移，再恢复旧后端和 Web 并重启服务。**回退保留 Reader 数据**；新增但不再使用的解析依赖也保留。本次未执行生产回退，不能声称做过线上回退演练。

验收书按已知 ID、标题和文件散列核对，保存其原书、导出和三张表中的原始记录后，仅移出该测试书；没有删除 Reader 整个目录。清理后书库为 0 本，服务、Caddy 和课程 timer 均 active；发布后日志未发现 Reader HTTP 5xx。测试数据备份在上述回退目录的 `smoke-evidence/`，可按 `cleanup-rows.json` 恢复原记录；这不是产品 JSON 导入功能。

本地验收下载移入证据目录，Downloads 临时上传副本已移除。临时测试端口 8791、8792、8943、5199 无残留监听。仅关闭本次新增的 Zen 验收标签页，保留用户原有标签页。

部署回执、候选范围、构建与测试日志、下载文件和核对摘要保存在本机 `ReaderAnalysis-20261013/evidence/stage6-release/`。发布载荷 SHA-256：

`562779ae5b07c679ab79112533ab2c149d70b3d6fcc2799e738f07a0a0598851`

## 第一期交付边界

已交付可重排 EPUB 的导入、归类、阅读续读、高亮笔记、检索与导出闭环。后续应由真实阅读暴露的问题决定改进，不因分期结束继续堆功能。固定版式、DRM、JSON 导入恢复、离线、跨设备、手机手势及大规模性能仍不在已验收承诺中。
