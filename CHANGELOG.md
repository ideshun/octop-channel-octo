# Changelog

## 1.1.1 — 2026-10-31

- 兼容尚未提供 `owner_commands_only` 字段的 Gateway 版本；保留新版本宿主上的原有设置。
- 在群消息交接前忽略已确认的机器人发送者，避免机器人互相触发；使用结构化 @ 目标并避免名称前缀误判。
- 说明群聊上下文条数的适用范围及其与专家会话历史的区别。

## 1.1.0 — 2026-10-31

- 对接群聊参与策略的收件箱接管回调，跳过/延迟消息释放容量；通过已有成员缓存解析机器人身份，保留原有 @ 和命令权限处理。
- 过滤撤回、输入状态和系统通知，持久记录撤回标记；丢弃待处理的撤回消息，取消尚在生成的回复并清理群上下文，不取消 Gateway worker。


## 1.0.4

- Use non-blocking msvcrt state locks on Windows and retain flock on Linux/macOS, fixing channel-save probes failing with No module named fcntl.
- Isolate Check Connection probes from online consumers: validate candidate credentials and connection metadata without opening state, starting another WebSocket, polling or ACKing events.
- Preserve exclusive state ownership for real consumers; release handles after initialization failures, shutdown and process exit.
- Use portable file URIs for state diagnostics and document Windows directory ACLs.
- Add real-gateway regressions for online checks, repeated probes, candidate authentication failure and in-progress message preservation, plus a Windows CI job.

## 1.0.3

- Keep durable lifecycle transitions for real gateway messages; quarantine interrupted turns and finalize passive group chatter.
- Disable message batching, preserve sender trust per turn, and bound gateway handoff with optional explicit enqueue receipts.
- Separate group activation from history retention; reserve commands for the owner without replying to unmentioned group commands.
- Deliver bounded, independent permission notices with correct DM/group/topic routing.
- Reuse DNS-pinned HTTP connections, enforce request deadlines and Retry-After, and avoid retrying invalid successful responses.
- Catch up full event pages immediately; sort cursor updates, batch ACK retries with bounded concurrency, and expose component health.
- Add private status, safe archive, retained dedup tombstones, and explicitly confirmed uncertain-work recovery tooling.
- Include optional host compatibility patches, real gateway regressions, Linux/macOS CI and packaged-host smoke tests.
- Document the host-enforced tool permission boundary for non-owner chat.

## 0.1.2

- Publish a regular release containing the changes made after the v0.1.1 preview archive.
- Add matching Chinese/English READMEs with web ZIP/URL installation and Linux/macOS availability.
- Put API URL before Bot Token, clarify mention-only behavior, and link configuration help to this repository.
- Supply help text for configuration tooltips; document the companion host rendering requirement (Octop #2162).
- Include both READMEs and operational documentation in the installation archive.

## 0.1.1

- Restore exact plugin/channel avatar and full plugin description.
- Restore the complete channel form, helpers and defaults, including mention and history controls.
- Automatically persist state under OCTOP_HOME or ~/.octop; keep bot scope stable across token rotation.
- Validate optional absolute state directory overrides; remove the mandatory UI directory field.
- Document b9 dashboard metadata gaps and provide a separate, unapplied compatible host patch.

## 0.1.0

- Standalone channel plugin targeting Octop 1.0.2b9 / gateway 1.0.3 / harness 1.0.4.
- Private/group/topic messages, media sends, access gates and mention context.
- Durable shared WS/poll inbox, commit-before-ACK, retryable REST ACKs, restart quarantine.
- Exact-host self-host allowlist, DNS-pinned network transports, redirect refusal and token isolation.
- Strict UI config parsing; bounded event maintenance preserving unresolved work and cursor.
- Deterministic source-only ZIP builder, upstream Apache attribution and isolated host integration tests.
- No live-bot end-to-end or production migration acceptance performed.
