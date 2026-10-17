# Octo 0.1.1 UX parity evidence

The previous 0.1.0 registration exposed seven fields and required state_dir. That was a UX regression. Version 0.1.1 registers twelve ordinary fields and removes state_dir from the form.

| Setting | Previous registration / legacy dashboard | 0.1.1 registration and runtime |
| --- | --- | --- |
| Plugin icon | Missing | Exact requested avatar URL in plugin.yaml |
| Channel icon | Missing registration | Same exact URL passed to ctx.channel(icon=...) |
| Description | Short delivery implementation text | 配置 Octo IM 机器人频道，支持私聊与群聊，实时 WebSocket 接入，需在 Octo BotFather 创建机器人获取 Token。 |
| Bot Token | Password, required | Password, required; BotFather guidance |
| API URL | Required, no default | https://im.deepminer.com.cn/api |
| Admins | No helper | Extra UIDs; bot owner always admin |
| owner_only | No helper | false; existing localized explanation |
| Group whitelist | Bare label | Empty allows all; parent group allows topics |
| require_mention | Missing plugin field; legacy UI had switch | true, with existing localized explanation |
| history_limit | Missing plugin field; legacy UI had text input | 10, range 0–100, 0 disables |
| ignore_mention_all | Missing | false |
| show_typing | Missing | true |
| heartbeat_interval | Missing | 30 seconds, range 10–300 |
| poll_interval | Missing | 5 seconds, range 2–300 |
| allowed_hosts | Bare label | Empty; exact host helper and example |
| state_dir | Mandatory UI input | Automatic OCTOP_HOME/plugin-state/octo or ~/.octop/plugin-state/octo; optional JSON override |

Production legacy `/data/.octop/plugins/octo-channel/main.py` was inspected read-only: it directly modifies the gateway registry and has no field schema. The installed dashboard bundle contains the legacy Octo seven-field form (token, API, admins, owner_only, whitelist, require_mention, history_limit) and localized descriptions. The legacy Chinese intro uses a semicolon after WebSocket 接入; the requested plugin description uses a comma exactly.

## Verified host limitations

Installed harness PluginContext.channel accepts only kind, channel_cls, label, icon, intro_url and fields. PluginContext has no public manifest property; `_manifest` is private. LoadedPlugin.manifest.description is available to the host via PluginRegistry.get(plugin_id), so a host bridge can expose it without changing the harness API.

The real host bridge PluginChannelInfo.to_dict omits description. Fields pass through unmodified to JSON, but the installed dashboard registerPluginChannelKind maps only name, label, type, placeholder and required. It drops helpKey, defaultValue and legacyDefaultValue. It also rejects a plugin kind already present in CHANNEL_KEYS, including the locally retained legacy Octo kind. ChannelCard and ChannelDrawer intros use translation keys instead of plugin metadata. PluginManager separately reads plugin.yaml icon and description correctly.

A separate unapplied host patch is prepared at:

`/data/.octop/agents/assistant/generated/octop-octo-ux-host-patch/octo-ux.patch`

It bridges the owning loaded manifest description, preserves field help/default metadata, renders plugin description on card/drawer, and temporarily overlays existing catalogue entries while restoring them on plugin unload. Python changes target the installed bridge; dashboard changes target the b9 source checkout in octop-b9-upgrade/Octop. This is a reviewable host source patch, not a runtime plugin monkey patch. It needs a host dashboard rebuild and host release/application before full visible parity is available.

The actual isolated loader JSON is captured outside the distributable in the adjacent host-patch directory. Browser verification was attempted but Chrome/Chromium is unavailable. No production plugin, channel, token or configuration was modified.
