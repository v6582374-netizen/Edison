# Edison

Edison 将 YouTube 阅读资料库与课程周览整合在同一工作区。

minimalism 专注模块已移除；items 物品模块仅保留在本机忽略目录 `.local/items/`，不进入远程仓库或 Web 部署。

- 侧边栏 **YouTube**：订阅导入、字幕获取、中文 Markdown、阅读队列、版本与来源追溯。
- **设置 → 模型**：聊天与 YouTube 共用模型配置；YouTube 不需要第二份模型 API Key。
- YouTube 登录由系统钥匙串保存，进入订阅页与后台定时同步账号订阅；旧 Vault 连接可一次迁移。正式构建的 Google 登录配置见 [连接说明](docs/architecture/youtube-connection.md)。模型凭据遵循 Openworker 原有存储机制。

## 下载

[Edison v0.2.1 — 首个桌面发行版](https://github.com/vincentshuo/Edison/releases/tag/v0.2.1) 面向 Apple Silicon、macOS 14 及以上系统。下载 DMG 后将 Edison 拖入 Applications，也可使用 ZIP 包；校验文件随版本提供。

此版本采用本地 ad-hoc 签名，未经 Apple 公证。macOS 可能要求在“系统设置 → 隐私与安全性”中确认打开。更新通过手动下载安装完成。

## 本地开发

```sh
uv sync --extra dev --extra messaging --extra bedrock --extra browser
cd surfaces/gui
npm ci
npm run tauri dev
```

macOS 客户端入口是 `surfaces/gui`。原有 `desktop` 是旧独立客户端，保留供迁移参考，不再作为 Edison 的入口。现有 YouTube 资料库继续保留在原有用户数据目录，可用 `YOUTUBE_WORKBENCH_WORKSPACE` 指定其他目录。

## 本地应用包

```sh
bash packaging/build_local_macos.sh
```

生成并打开 `surfaces/gui/src-tauri/target/release/bundle/macos/Edison.app`。设置 `EDISON_OPEN_APP=0` 可只构建、不启动。发布版本同样使用此本地构建流程，不依赖上游自动更新服务。

## 验证

低频弹窗已统一焦点、关闭与可打断过渡；浏览器验收通过，原生窗口手感待验收。见 [实施记录](reports/modal-polish-2026-09-21/verification.md)。

```sh
uv run pytest
npm --prefix surfaces/gui test
npm --prefix surfaces/gui run build
```

Openworker 上游来源、许可证与原始说明保存在 [docs/upstream/openworker](docs/upstream/openworker)。原 StrataRead 使用说明见 [docs/legacy-strataread-README.md](docs/legacy-strataread-README.md)。
