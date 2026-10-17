# Octo 0.1.1 界面修复说明

插件本地源码目录：`/data/.octop/agents/assistant/generated/octop-channel-octo-repo`。
宿主本地工作树：`/data/.octop/agents/assistant/generated/octo-plugin-ux-host`。

## 插件注册

`plugin.yaml` 提供完整 description 和 icon。`main.py` 仅使用真实支持的 `ctx.channel(kind, channel_cls, label, icon, intro_url, fields)`；没有虚构 description 或 intro_label 参数，也不访问私有 manifest。

说明入口指向 https://github.com/ideshun/octop-channel-octo 。API URL 排在 Bot Token 前面。全部 12 个普通配置字段提供 helpKey 文案，包含“仅回复 @机器人的群消息”的普通群消息、被动上下文、私聊及管理员权限边界。state_dir 不出现在表单中，由运行时自动管理。

## 实际宿主链路与本地修改

原版 b9 的 PluginChannelInfo 不输出 description；前端注册函数丢弃 helpKey/defaultValue/legacyDefaultValue，卡片仅查询 channels.intro_<kind>，缺失时回退为标签“Octo”。PluginManager 的插件卡片与 Agent 的频道卡片是不同渲染链路，修改插件注册标签无法解决后者。

宿主修改通过 `PluginRegistry.get(plugin_id).manifest.description` 读取加载后的 manifest，随 `/plugin-kinds` 目录输出；前端保存为 CHANNEL_DESCRIPTIONS，ChannelCard 和 ChannelDrawer 优先渲染该描述。此逻辑适用于所有频道插件。内置类型不可被覆盖；运行时残留的旧版同名条目可被正式插件目录覆盖，卸载时恢复原元数据。

前端保留字段默认值及旧配置缺失值，显式保存的 false 不被覆盖。字段帮助在标签旁显示问号图标，通过 Tooltip 悬停/聚焦查看，支持 switch、text、password、textarea、json，不再作为常驻段落放在控件下方。宿主对已注册插件使用“查看配置说明”，内置频道保留原凭据入口；不增加 intro_label API。

## 验证边界

测试覆盖真实插件加载器读取临时 manifest 后的描述目录输出、前端目录注册与旧版覆盖恢复、卡片实际描述渲染、字段悬停提示，以及默认值保存。它们是离线测试和 DOM 渲染测试，不是正在运行的生产 UI 验收。

此宿主工作树没有应用到生产，也没有安装插件、重启服务或注册真实 Bot。需要后续审核并应用宿主源码和构建产物才会改变运行中的界面。真实 Octo 联调仍未完成。准确命令与结果另存于本工作树 docs/LOCAL_UX_VALIDATION.md。
