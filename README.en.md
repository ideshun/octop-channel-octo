# Octo IM Channel Plugin

[中文](README.md) | English

Connect Octo private chats, groups, and community topics to Octop. Supports text, images, voice, video, and files, with real-time WebSocket reception, REST polling fallback, administrator controls, group allowlists, group context, message deduplication, and acknowledgment retries.

**Supports Linux and macOS; both have been tested successfully.**

## Requirements

Python 3.12+; Octop **1.0.2b9**, octop-gateway **1.0.3**, and octop-harness **1.0.4**, or compatible later versions. The host plugin manager installs dependencies declared in `plugin.yaml`.

## Installation

The Octop web administration interface is recommended. Choose one method.

### Install from a URL

Open **Administration → Plugins**, click **Install**, and enter:

```text
https://github.com/ideshun/octop-channel-octo/archive/refs/heads/main.zip
```

This URL tracks the main branch. To pin a version, use a ZIP for a specific commit or release.

### Upload a ZIP

1. [Download the repository ZIP](https://github.com/ideshun/octop-channel-octo/archive/refs/heads/main.zip).
2. Open **Administration → Plugins → Install from ZIP**.
3. Select the downloaded ZIP and install it.

### Install from the command line

Download and extract the source, then run in the same environment as the Octop server:

```sh
octop plugin install /absolute/path/to/plugin
```

The CLI does not accept a local ZIP pathname; extract it first. After installation, ensure the plugin is enabled. If Octo does not appear in the channel list, reload plugins through the web administration interface or restart Octop, then add an Octo channel to the desired expert. CLI `octop plugin reload` only reloads its own process, not the running server.

## Configuration

### API URL

Defaults to `https://im.deepminer.com.cn/api`. For self-hosted Octo, enter the server's API base ending in `/api`, without a query string or fragment.

### Bot Token

Create a bot through BotFather on the corresponding Octo server and obtain its token. Do not disclose the token or commit it to source control.

### Replies and access

- **Administrator UIDs:** comma- or whitespace-separated IDs. The bot owner is always an administrator.
- **Reply to administrators only:** off by default. When enabled, only the owner and configured administrators may trigger replies in private and group chats.
- **Commands are owner-only:** off by default. When enabled, only the bot owner may send `/` commands; everyone else (administrators included) keeps normal chat and public research, while command messages get a short notice and never run as a turn.
- **Group allowlist:** empty allows all groups. Enter group or topic IDs; allowing a parent group also allows its topics.
- **Reply only to group messages that mention the bot:** on by default. Ordinary group messages do not trigger replies but may be retained as context when enabled. Private chats are unaffected; administrator restrictions still apply.
- **Group context messages:** defaults to 10, range 0–100; 0 disables context.
- **Ignore mention-all:** off by default. When enabled, mention-all does not trigger a reply.

### Connection

- **Show typing:** on by default.
- **Heartbeat interval:** 30 seconds by default, range 10–300.
- **Event fallback polling interval:** 5 seconds by default, range 2–300; runs alongside WebSocket reception.
- **Exact hosts allowed for private networks/plaintext:** normally empty. For private or HTTP self-hosted services, enter trusted hosts separated by commas or whitespace, without schemes, ports, or wildcards. This also permits their resolved private and loopback addresses; only trust services you control.

The plugin supplies field descriptions. The companion host fix displays them as question-mark tooltips and points the configuration-help link to [this repository](https://github.com/ideshun/octop-channel-octo). Stock b9 has limitations displaying plugin descriptions, help, and defaults; full presentation requires the host fix and a rebuild. The plugin never patches host source files. See [UI compatibility](docs/UX_PARITY.md).

## Data storage

A SQLite database is created automatically under the Octop data directory to store deduplication, acknowledgment retries, and processing progress. No state-directory input is required. Preserve data across updates and token rotation. Keep existing custom `state_dir` overrides unchanged. See [operational details](docs/OPERATIONS.md).

## Updates and migration

Install the new ZIP through the web interface and follow the overwrite prompt for the existing plugin ID. From the CLI:

```sh
octop plugin install --force /absolute/path/to/new/plugin
```

Reload plugins or restart afterward. Preserve configuration and state.

When migrating from the old channel, back up data and stop the old Octo hook and its restore/watchdog mechanism before enabling this plugin. Preserve the channel ID, `kind=octo`, and sessions. Legacy state is not automatically imported into the new inbox. **Never run two consumers for the same bot, including on different machines.** For rollback, stop this plugin before restoring the old consumer.

## Validation and limitations

Platform availability has been tested. Server-specific WS/REST/media interoperability, replay retention, load behavior, and migration still need acceptance testing in the deployment environment. Offline tests do not replace end-to-end acceptance.

Interrupted work with an unknown outcome is marked `uncertain` and is not replayed automatically; reconcile external side effects first. `completed` only means the host processing path returned, not that external actions happened exactly once.

Interactive cards, document synchronization, Bot Tasks, and automatic menus are not supported yet.

## Development

Run in an environment with the required host dependencies:

```sh
python -m pytest
ruff check .
ruff format --check .
python scripts/build_zip.py
```

The package builder uses an explicit source allowlist and excludes credentials, state, tests, and caches.

## License

Apache-2.0. See `LICENSE`, `NOTICE`, and [provenance](docs/PROVENANCE.md). The WebSocket transport is adapted from Mininglamp-OSS/openclaw-channel-octo. Third-party dependencies are not vendored.
