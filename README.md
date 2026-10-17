# Octo channel for Octop

Standalone `kind: channel` plugin for Octo private chats, groups and community topics. Includes text, image, voice, video and file payloads, mention/access gates, group context, WuKongIM WebSocket reception and durable REST event fallback. No host source patches are required.

Targets Python 3.12+ on Linux with Octop **1.0.2b9**, gateway **1.0.3**, harness **1.0.4** (or compatible later releases). The host must expose `PluginContext.channel` and gateway channel registration/unregistration. Dependencies are listed in `plugin.yaml`; they are installed by the host plugin manager. `pyproject.toml` supplies developer tooling; this is a host plugin directory, not a pip-distributed wheel.

## Install

Clone or unpack this repository into a directory accessible to Octop, then run with the same runtime and environment as the server:

```sh
octop plugin install /absolute/path/octop-channel-octo
octop plugin list
```

If the server is already running, use **Admin → Plugins → Reload** (`POST /api/plugins/reload`) or restart the server. The CLI `octop plugin reload` reloads only its own offline process; restart the server to apply it. Configure an Octo channel on the Agent channel page. The b9 CLI also accepts an HTTP(S) ZIP URL. It does **not** accept a local ZIP pathname: unpack it and install the directory. Use `--force` for an intentional reinstall of the same plugin ID.

Required: `api_url`, `bot_token`, `state_dir`. Choose an absolute dedicated state directory on a persistent volume. Keep tokens in channel credentials, never in source control. `api_url` is the server base such as `https://octo.example/api`, without query or fragment.

| Option | Default | Meaning |
| --- | --- | --- |
| `allowed_hosts` | empty | Comma/space separated exact DNS hosts or IPs explicitly trusted for private networks and HTTP/WS; no URL, wildcard or port |
| `admins` | empty | Additional administrator UIDs; bot owner is always administrator |
| `owner_only` | false | Only administrators may trigger turns, including DMs |
| `group_whitelist` | empty | Allowed group/topic IDs; a parent group permits its topics |
| `require_mention` | true | Group turns require a bot mention |
| `ignore_mention_all` | false | Ignore group mention-all triggers |
| `history_limit` | 10 | Context rows, 0 disables, maximum 100 |
| `poll_interval` | 5 | Event polling seconds, 2–300 |
| `heartbeat_interval` | 30 | Heartbeat seconds, 10–300 |
| `show_typing` | true | Typing hints during processing |

Advanced options may be supplied through the host JSON config/API. Integer UI strings and explicit boolean strings (`true/false`, `1/0`, `yes/no`, `on/off`) are validated; truthiness is never used to interpret `"false"`.

For a private self-hosted server, explicitly set e.g. `allowed_hosts: "octo.internal, files.internal, im.internal"`. This grants access to all resolved addresses of those exact hosts, including loopback; choose trusted server-controlled hosts only. Public destinations require HTTPS/WSS and globally routable DNS answers. HTTP and WS connections pin their validated DNS results, disable environment proxies, and retain TLS hostname verification. All HTTP redirects are refused, including presigned upload redirects, so bot Authorization headers cannot follow a redirect. Add the final storage host instead. Upload PUTs never include the bot token. Registration-provided API URLs do not replace the configured credential destination. Inbound media URLs are syntax/address-literal checked before passing to the host; host media consumers retain responsibility for their own DNS, redirects and download policy. Inline data URLs are not forwarded.

## Delivery and state

WS reception commits to SQLite before protocol ACK. REST reception persists the message and event before attempting queue ACK; ACK failures survive restarts and are retried. Both feeds share the same per-bot message key. Unstarted `received` work recovers on restart. A turn that was `started` when interrupted becomes `uncertain` and is never automatically replayed. `completed` means the host processing path returned; it cannot prove external business side effects. Inspect uncertain rows and reconcile side effects before any manual recovery; back up the stopped database and its WAL first.

Completed/ignored rows clear message bodies but keep deduplication keys. At 100,000 message keys or 4,096 pending turns the plugin applies backpressure; it never expires deduplication records without a verified server replay retention contract. ACKed events compact at 10,000 records while preserving a separate high-water cursor. Pending ACKs, unsupported events and uncertain work are retained; 100,000 unresolved event records also apply backpressure. Archive/reconcile with the operator before capacity is exhausted. SQLite pages may retain deleted bytes until operator-managed compaction; protect the entire volume and backups. Local Linux file locks prevent simultaneous ownership of the same state DB, not the same bot on another machine or directory.

Unsupported card/doc/task/settings events are recorded without action or ACK; the local poll cursor advances. They require an appropriate consumer/server retention policy. This plugin does not implement cards, GROUP.md/THREAD.md synchronization, business tasks or automatic menus.

## Migration

Validate with a separate test bot first. Stop the previous Octo hook and its restore/watchdog mechanism before enabling this channel; **never run overlapping consumers for one bot**. Preserve the old channel ID, `kind=octo`, control-plane database and sessions when migrating an existing channel. Keep the legacy state backed up; it is not automatically imported into this plugin's SQLite inbox. Retain the new state volume across reinstalls/restarts. For rollback, stop the new channel before restoring the old consumer. This repository does not alter production configuration or register a bot during build/tests.

## Develop and package

Use a development environment with the target Octop/gateway/harness packages available:

```sh
python -m pip install pytest pytest-asyncio ruff
python -m pytest
ruff check .
ruff format --check .
python scripts/build_zip.py
```

The ZIP builder uses an explicit source allowlist, rejects symlinks and produces deterministic bytes. It excludes `.git`, credentials, state, caches and tests. Unpack the generated `dist/octop-channel-octo-0.1.0.zip` for local CLI installation. Tests use fake identities and offline fixtures; the host integration test loads the real PluginContext/loader/router only in a subprocess with temporary `OCTOP_HOME`. No real bot or production registration is used.

Remaining validation: real-server WS/REST/media interoperability, server replay/retention semantics, load testing and migration rehearsal. urllib requests run in worker threads with bounded responses and timeouts; cancelling a coroutine cannot interrupt an already-running request. SQLite commits briefly occupy the event loop. These limits do not provide exactly-once execution of external side effects.

## License and provenance

Apache-2.0; see LICENSE, NOTICE and [pinned source evidence](docs/PROVENANCE.md). The socket transport is a modified Python port of Mininglamp-OSS/openclaw-channel-octo's TypeScript transport; API contracts also reference that project. Dependencies are not vendored. No upstream copyright identity or year has been invented.
