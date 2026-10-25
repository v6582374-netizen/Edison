# YouTube 持续可用：平台授权与内容获取边界

核验日期：2026-10-18。用途：Wayfinder《平台允许哪些可持续授权与内容获取方式》的研究依据；不代表已选定或实现架构。直接读取 Google 官方文档和 yt-dlp 上游 Wiki；未访问私人凭据、Cloud 控制台或真实 YouTube 视频。

## 结论

**官方 OAuth 可以支撑订阅同步的长期后台授权，但不能赋予订阅者下载任意他人视频字幕的权限。** 因而“首次连接后持续可用”至少涉及两种独立能力：官方账号授权，以及内容获取会话/请求环境。界面可以统一引导，不能把两者当成同一种令牌续期。

已接受的产品边界适用于系统自身可修复的过期和重启：正常维护由后台完成；Google 撤销授权或要求安全验证时由用户接管。内容没有字幕、地域/权限限制、请求限流及平台改动也可能暂时阻止交付，但不应统称“认证失效”。

## 已核验的平台事实

### 1. OAuth：可续期，不是永久有效

- Google 的 Web server OAuth 文档明确：`access_type=offline` 允许在用户不在浏览器前时刷新 access token；首次交换授权码时返回 refresh token。应安全持久化 refresh token，进程重启后继续使用，不把再次登录当成普通 access token 过期的处理方式。[官方流程](https://developers.google.com/identity/protocols/oauth2/web-server#offline)
- **External 用户类型 + Testing 发布状态**签发的 refresh token **7 天过期**；仅当申请范围全部属于姓名、邮箱、基本用户资料（`openid`、`userinfo.email`、`userinfo.profile` 或等价范围）时例外。YouTube 范围不在此例外内。该规则不能反向证明 Edison 当前项目处于 Testing，需控制台核验。[官方过期规则](https://developers.google.com/identity/protocols/oauth2#expiration)
- Production 不提供永不过期承诺。官方列举：用户撤销、6 个月未使用、有效 refresh token 数量超限、限时授权到期、管理员限制等；改密码这一项特指包含 Gmail scopes 的令牌，不能泛化为所有 YouTube 令牌。每个 Google 账号、每个 OAuth client ID 最多 100 个 refresh token，继续签发会无提示淘汰最旧的令牌。[官方过期规则](https://developers.google.com/identity/protocols/oauth2#expiration)
- 限时授权可能返回 `refresh_token_expires_in`。切换发布状态后，旧 Testing 令牌是否已被重新签发、当前令牌是否仍受旧寿命约束，本次没有证据，不能假定控制台改成 Production 就修复了既有凭据。[限时授权](https://developers.google.com/identity/protocols/oauth2/web-server#time-based-access)

### 2. 官方字幕 API：授权范围与视频权限是两道门

- `captions.list` 返回字幕轨道列表，**不返回字幕正文**；要求 `youtube.force-ssl` 或 `youtubepartner`，权限不足可返回 403。不能把存在公开视频 ID、已订阅频道，或已有 `youtube.readonly` 当成此方法已获授权。[captions.list](https://developers.google.com/youtube/v3/docs/captions/list)
- `captions.download` 明确要求：**“This method requires the user to have permission to edit the video.”** 同样要求上述 scopes。扩大 scope 只能允许应用代表用户行使其原有权限，不能让普通订阅者获得他人视频的编辑权。[captions.download](https://developers.google.com/youtube/v3/docs/captions/download)
- 因此，单纯加强 OAuth 续期，或改用官方字幕 API，**不能覆盖任意订阅公开视频的文稿提取**。这是权限边界推导，不是对某一字幕请求失败原因的诊断。

### 3. 网页 Cookie、PO Token 与 OAuth 不能互相替代

- yt-dlp 上游明确写明：YouTube 新限制使其 **OAuth 登录已不可用，应使用 Cookie**。这描述的是 yt-dlp 的 YouTube 提取登录；不能推导为官方 YouTube Data API 的 OAuth 失效。[Extractors：Logging in with OAuth](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#logging-in-with-oauth)
- 同页说明：打开的 YouTube 标签页会频繁轮换账号 Cookie。上游给出的稳定导出办法是独立无痕会话登录、导出后关闭且不再打开该会话，避免其被浏览器继续轮换。这没有承诺 Cookie 永不失效，也不是“维持活跃 profile 并定期导出”的验证报告。[Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
- 上游 FAQ 支持从指定浏览器/profile 读取 Cookie，以及使用 Cookie 文件；通用导出可能包含浏览器**所有站点** Cookie。工程上专用 profile 的职责是隔离持久会话和减少取错账号，并不是延长平台授权寿命。[Cookie 接入](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- PO Token 是对请求来源的证明，不是账号授权。上游列举 GVS、Player、**Subs（字幕）**三个使用上下文；不同 client 的要求不同。缺失时可能出现 403。上游还描述会话或视频绑定、有限寿命，并建议动态 provider，反对长期手工复用 token；这些是变化中的上游观察，不是 Google 的稳定 API 契约。[PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- FAQ 指出媒体请求可能依赖相同 IP、Cookie、HTTP headers；429 也可能来自 IP 过量使用。故“浏览器登录成功”不能证明“服务器上的字幕请求成功”，也不能把下载请求的所有 403/429 都解释成 Cookie 过期。[请求环境](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#i-extracted-a-video-url-but-it-does-not-play-on-another-machine--in-my-web-browser)、[429](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#http-error-429-too-many-requests-or-402-payment-required)
- Google 公开 OAuth 流程交换的是授权码、access token、refresh token，用途是授权 API 调用。**此次核验的官方公开接口未提供“OAuth token 换 YouTube 网页登录 Cookie”的标准能力**；这不是对 Google 内部接口是否存在的断言。一个产品连接向导可以串联 OAuth consent 和网页登录，但不能承诺只完成一种就自动取得另一种。[官方流程](https://developers.google.com/identity/protocols/oauth2/web-server#exchange-authorization-code)

## 架构推论：下一轮决策应遵守的约束

| 能力 | 最小职责 | 不能据此承诺 |
|---|---|---|
| 官方 refresh token | 延续用户授予 API 的权限，持久保存并自动换取短期令牌 | 任意视频字幕权限、网页登录、永不撤销 |
| 专用持久浏览器 profile | 保存单一内容会话，必要时供用户安全验证 | 云 IP 被接受、验证码自动消失、永不掉线 |
| Cookie 快照 | 把该会话交给实际内容请求者 | 浏览器轮换后旧副本仍然有效 |
| 请求环境 | 让出口、客户端和必要 headers 与当前请求要求一致 | 有 Cookie 就必然通过平台风控 |
| PO Token/provider（若需要） | 满足特定 client、视频、上下文的来源证明 | 增加账号权限或代替 OAuth/Cookie |

推荐把持久会话视为**候选维护机制**，先做最小 E2E 试验，再决定是否常驻浏览器或引入 provider。网页会话、Cookie 快照和内容 worker 需要明确谁负责更新、何时重新读取；这属于一致性管理，不是不断要求用户重新认证。正常网络/限流故障应等待恢复；缺字幕应给出内容级结果；只有明确授权失效或安全挑战才请求对应的人为介入。

## 待实测与未证实事项

1. Edison 的 OAuth 项目发布状态、实际 scopes、refresh token 获取/持久化路径，以及频繁失效是否来自固定 7 天、凭据覆盖或其他原因；本次不接触实际账号，未作诊断。
2. 在**正式部署的同一服务器与网络出口**，专用 profile 能否完成登录、能否被实际字幕 worker 正确使用，以及何种 client/字幕请求需要 PO Token；不能用本地浏览器成功替代服务器 E2E。
3. 同一会话经历浏览器 Cookie 轮换、worker 重启、服务器重启、空闲、限流后能否自动恢复；持续观测多久才足以接受，需单独制定验收。
4. 目标账号、浏览器/yt-dlp 组合是否触发安全验证，及有限内存主机能否承受浏览器。上游提醒用账号进行提取存在账号被临时或永久封禁的风险，不能把主账号常驻当成零成本前提。[上游说明](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

本次已回答“平台允许什么、哪些不能承诺”；尚未决定 Edison 的最终内容会话方案，也未验证生产环境可用率。
