# YouTube 正式切换验证

这是维护者使用的协议，不是生产执行记录。实际迁移与验收结论仍记录在 [正式生产切换](https://github.com/v6582374-netizen/Edison/issues/59) 和 [持续观察验收](https://github.com/v6582374-netizen/Edison/issues/60)。

## 进入维护

已运行新程序时，通过现有认证后的 Web“维护工具”开始切换暂停。旧程序没有维护入口时，维护者先记下开始停止旧写者的 Unix 时间，正常等待旧服务及其子进程退出；仅在证明没有旧写者后启动新服务，并设置 `EDISON_CUTOVER_STARTED_AT=该时间`。新宿主在任何采集线程启动前持久化暂停，截止为该时间加 1800 秒。这个变量仅能初始化尚无切换记录的资料库；它不会覆盖已完成、已中止或正在进行的尝试。保留现有资料库、凭据命名空间和正式服务 UID。

`cutover.status` 显示 `drained=true` 后取得一致性备份并验证恢复，再执行显式历史迁移。Google 授权、内容 profile 与资料备份分别管理。准备正式 profile 时使用稳定目录中新建的空 profile，不复制原型或 Mac Cookie。

## 正式验证

所有操作仍走现有认证后的 `/v1/youtube/capability`，以下名字是 capability，参数位于 `arguments`。Web 维护区提供同样的入口。普通连接、手动请求和自动采集保持阻止，阅读、取消、断开和用户当前许可变更仍可用。

1. `cutover.validation.begin` 携带 `expected_client_id`。宿主要求当前写者已停止、备份真实恢复验证成功、历史迁移完成、服务账号凭据读取/原子替换/私有权限检查通过，且当前 client 匹配。传入布尔检查清单不能替代这些证据。
2. `cutover.validate` 携带 `capability` 和嵌套 `arguments`。仅允许 `connection.check`、现有 client 的 `connection.import_authorization`，以及内容浏览器的 open/close/screen/click/text/key/scroll/youtube/confirm/verify。`connection.check` 强制实际刷新后读取非空订阅，不使用先前的 available 状态。导入本身也实际刷新和读取订阅。网络、配额和存储失败不会要求无条件重新授权。
3. 用户在服务器 Google 页面完成登录，确认两条连接使用同一 Google 账号，并对一个明确的视频调用 `content_session.confirm`。宿主关闭浏览器、取得快照，并由新的字幕子进程获取匹配、非空、可解析字幕。系统不自动核验两个 Google 账号一致。
4. 浏览器保持关闭，再用 `content_session.verify` 实际获取同一视频；它会运行另一个字幕子进程，不能用缓存或旧状态替代。
5. `cutover.prepare_restart` 检查当前 profile、同账号确认和上述真实证据，封住进一步验证操作。维护者正常重启服务。只有在全部工作、请求租约和 profile 正常关闭后，宿主才写下正常退出回执。新进程仅在同一构建、服务 UID、稳定 profile 路径及原截止时间内接受该回执；没有回执的退出、构建变化或超时都中止尝试，继续保持采集暂停。
6. 重启后再次用 `connection.check` 强制实际刷新和非空订阅读取，再对同一视频执行 `content_session.verify`。字幕代次、profile 目录身份和视频必须仍匹配。重开内容浏览器或更换官方授权会作废内容证据，需要在原窗口内重新证明并计划重启；不会延长窗口。
7. `cutover.complete` 仅在服务端已记录全部证据、当前连接仍匹配时完成。新工作线程先创建并等待闸门，持久化释放成功后才恢复；存储或线程创建失败继续暂停。线程重新读取用户现有 Auto Update、手动请求和取消，不改写这些选择。完成仅表示进入后续观察，不能宣称 14 天验收已通过。

维护中断开 YouTube 会立刻作废证明，网络请求的迟到结果不能恢复连接或释放暂停。30 分钟截止不因浏览器、网络、重启、重复 begin 或重试操作延长。中止后的实例保留最新知识资产和兼容阅读，不能自动恢复旧 Cookie 通道，也不进行整库回卷。

## 兼容阅读

分阶段正式验证及正常重启回执将资料契约提升为 2。声明契约 1 的早期程序不能解释这些维护状态，若运行在当前资料库上只提供兼容阅读，采集与连接写入保持暂停。程序恢复与备份边界见 [一致性备份与程序回退](youtube-backup-and-rollback.md)。
