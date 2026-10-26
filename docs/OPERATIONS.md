# 状态与运行边界

默认状态目录为 `$OCTOP_HOME/plugin-state/octo`，未设置 OCTOP_HOME 时使用 `~/.octop/plugin-state/octo`；API URL 与 Bot ID 的哈希决定子目录。轮换 Token 保持相同 Bot ID 时可继续使用原状态。JSON/API 的 state_dir 覆盖必须为绝对目录，保留该目录及持久化卷。

WS 消息先写入 SQLite 再协议 ACK；REST 消息和事件先持久化再尝试队列 ACK，失败会跨重启重试。两个来源共用每 Bot 消息去重键。未开始的 received 任务重启后恢复；已开始的 started 任务中断后变为 uncertain，不自动重放。人工恢复前先核对外部副作用，停止消费者并备份数据库及 WAL。

completed/ignored 清理消息正文但保留去重键。未归档消息记录达到 100,000 或待处理任务达到 4,096 时施加背压；未经服务端重放保留策略验证不会自动过期去重记录。80% 容量时记录预警。已 ACK 事件达到 10,000 条时压缩并保留独立高水位；待 ACK、不支持的事件和不确定任务继续保留，100,000 条未解决事件也会触发背压。SQLite 页面仍可能含删除后的字节，应保护整个卷和备份。

状态库使用本地操作系统的非阻塞独占锁：Linux/macOS 使用 `fcntl.flock`，Windows 使用 `msvcrt.locking` 锁定 `.lock` 文件第一个字节。关闭文件或进程退出时由操作系统释放，不通过删除锁文件解除占用。该锁只防止同一状态数据库被同时使用，无法防止其他目录或机器消费同一 Bot。迁移必须停止旧消费者及其守护机制。

公网地址要求 HTTPS/WSS 和公网 DNS 解析结果。HTTP/WS 固定已验证的 DNS 结果，禁用环境代理，并保留 TLS 主机名校验；allowed_hosts 对精确主机放行私网及明文。HTTP 重定向一律拒绝，包括预签名上传的重定向，请配置最终存储主机。上传 PUT 不携带 Bot Token；注册响应的 API 地址不替换配置的凭据目标。入站媒体仅在交给宿主前检查 URL 语法和 IP 字面量；宿主下载器仍负责 DNS、重定向及下载策略。内联 data URL 不会转发。

HTTP 请求在线程中执行，连接按精确目标与访问策略复用，空闲连接最多保留 30 秒、总量 16 个。DNS 工作者有界；每次请求有总截止时间，覆盖连接、响应头和响应体；重试共享预算并遵守 Retry-After。取消协程不等于撤销服务端操作。SQLite 提交会短暂占用事件循环。不提供外部副作用的恰好一次执行保证。

## 消息交接与群聊

“检查连接”使用未注册入队回调的临时实例，只验证所填写 Token 的注册响应和连接地址配置；不打开 SQLite、不取得状态锁、不启动第二个 WebSocket，也不轮询/确认消息。因此可以在通道在线时反复检查，不会与正常消费者争用状态库。该检查不代表已重新建立并验收了一条 WebSocket 会话。正式运行的通道由 gateway 先注册入队回调，仍必须取得状态库独占锁。

每条消息独立进入 gateway，禁止批处理以保留完整的持久化 key 和发送者权限。进入宿主准备流程前保持 received，开始后先落盘 started，再执行后续操作。普通群消息在上下文策略处理完后标记 ignored，不持续占用待处理容量。

“仅回复 @”与历史条数独立；历史为 0 时仍遵守触发规则。非所有者的命令不会进入对话，只有满足触发规则时才发送提示。提示经独立的有界队列发送；队列满时丢弃提示并累加诊断计数，命令仍被阻止。

原版 gateway 不返回入队结果，因此同时只保留一条尚未被 gateway worker 取走的交接。等待超过 30 秒会报告 octo_gateway_handoff_stalled；不因超时重发。宿主可能正忙，也可能有外部生产者填满队列，先检查宿主状态，再决定停机核对。可选的明确入队回执补丁见 [宿主补丁](../compat/README.md)。

## 健康诊断

`channel.runtime_status` 提供 WS 状态、分发任务、轮询失败次数/最近成功时间、交接等待、提示队列及数据库统计，不包含 Token、消息正文或 UID。分发任务退出会将运行状态置为错误；轮询异常会报告降级。带诊断补丁的宿主在频道状态 API 的 `runtime.diagnostics` 中提供相同数据，现有管理界面是否展示扩展字段由宿主决定。

轮询收到满页立即继续追赶，空页按配置等待；失败指数退避，最长 300 秒。单批先持久化、按 event_id 排序推进游标，再以最多四个并行请求确认；失败的 ACK 保留重试。

在线只读查看状态（将路径替换成实际数据库）：

```sh
python scripts/state_admin.py --db /绝对路径/inbox.sqlite status
```

## 离线归档与恢复

归档和重试要求先停止对应 Bot 的所有消费者，工具会取得独占锁。备份使用 SQLite backup API，包含 WAL 中的已提交内容，Unix 权限为 0600，拒绝覆盖已有文件。Windows 的访问权限继承目标目录 ACL；状态和备份应放在受限的用户/服务账户目录，POSIX 模式位不能代替 Windows ACL。

```sh
python scripts/state_admin.py --db /绝对路径/inbox.sqlite archive --output /绝对路径/archive.sqlite
```

归档前先完整备份；随后将 completed/ignored 压缩为持久化去重 key，释放消息记录容量。received、started/uncertain、invalid、待 ACK 和 unsupported 事件保留，不自动重放。去重 key 和磁盘仍会增长，需要持续监测 storage_bytes；本命令不承诺缩小 SQLite 文件或删除历史备份。

确认某条 uncertain 工作没有造成外部副作用后，显式重新排队；启动 Bot 后才会执行：

```sh
python scripts/state_admin.py --db /绝对路径/inbox.sqlite retry --key BOT_ID:MESSAGE_ID --backup /绝对路径/before-retry.sqlite --confirm-no-side-effects
```

如需回滚到 0.1.2 或更早版本，先将压缩 key 恢复为旧版认识的 ignored 记录，再使用旧版；不要直接用旧插件打开已归档数据库：

```sh
python scripts/state_admin.py --db /绝对路径/inbox.sqlite prepare-downgrade --backup /绝对路径/before-downgrade.sqlite
```

恢复后消息行数可能达到旧版容量上限并触发背压，需要在迁移计划中处理；工具优先保留去重语义。不要用归档前的旧备份直接替换已有新处理记录的生产数据库。
