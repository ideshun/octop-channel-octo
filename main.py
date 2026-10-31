"""Octop channel extension entry point; never patches host implementation files."""

from __future__ import annotations

ICON_URL = "https://im.deepminer.com.cn/api/v1/users/28owvwbvxks35128dd4_bot/avatar?v=1792250160237"
DESCRIPTION = "配置 Octo IM 机器人频道，支持私聊与群聊，实时 WebSocket 接入，需在 Octo BotFather 创建机器人获取 Token。"


def setup(ctx):
    import importlib

    if not callable(getattr(ctx, "channel", None)):
        raise RuntimeError(
            "Octo requires Octop channel plugin support (harness#52, gateway#22, Octop#1823)"
        )
    module = importlib.import_module(f"{__name__}.octo_channel_impl.channel")
    ctx.channel(
        "octo",
        module.OctoChannel,
        label="Octo",
        icon=ICON_URL,
        intro_url="https://github.com/ideshun/octop-channel-octo",
        fields=[
            {
                "name": "api_url",
                "label": "API URL",
                "required": True,
                "defaultValue": "https://im.deepminer.com.cn/api",
                "placeholder": "https://im.deepminer.com.cn/api",
                "helpKey": "Octo 服务的 API 基址，以 /api 结尾，不含查询参数或片段；自托管时修改此项。",
            },
            {
                "name": "bot_token",
                "label": "Bot Token",
                "type": "password",
                "required": True,
                "placeholder": "在 Octo BotFather 创建机器人获取 Token",
                "helpKey": "在对应 Octo 服务的 BotFather 创建机器人并获取 Token，请勿公开或提交到代码仓库。",
            },
            {
                "name": "admins",
                "label": "管理员 UID 列表",
                "placeholder": "uid1, uid2",
                "defaultValue": "",
                "helpKey": "额外管理员 UID，以逗号或空格分隔；Bot 所有者始终是管理员。",
            },
            {
                "name": "owner_only",
                "label": "仅回复管理员",
                "type": "switch",
                "defaultValue": False,
                "helpKey": "开启后仅 Bot 所有者和管理员 UID 列表中的用户可触发回复，对私聊和群聊均生效。",
            },
            {
                "name": "owner_commands_only",
                "label": "仅所有者可使用命令",
                "type": "switch",
                "defaultValue": False,
                "helpKey": "开启后只有 Bot 所有者能发送 / 命令和调整对话模式，管理员不豁免。其他成员可正常聊天；此开关不限制普通聊天的工具权限，需宿主另行实施。",
            },
            {
                "name": "group_whitelist",
                "label": "群聊白名单",
                "placeholder": "group1, group2",
                "defaultValue": "",
                "helpKey": "以逗号或空格分隔，留空允许所有群聊；群组白名单同时允许其话题。",
            },
            {
                "name": "require_mention",
                "label": "仅回复 @机器人的群消息",
                "type": "switch",
                "defaultValue": True,
                "legacyDefaultValue": True,
                "helpKey": "开启后，普通群消息不触发回复；启用群聊上下文时仍可作为上下文记录。私聊不受此开关影响，管理员权限限制仍然生效。",
            },
            {
                "name": "history_limit",
                "label": "群聊上下文条数",
                "defaultValue": "10",
                "placeholder": "10",
                "helpKey": "每次最多附带的近期群消息数，0 关闭，范围 0–100；自动参与还受插件的消息数和有效时间限制。此设置不清空专家已有会话历史。",
            },
            {
                "name": "ignore_mention_all",
                "label": "忽略 @所有人",
                "type": "switch",
                "defaultValue": False,
                "helpKey": "开启后，群聊中的 @所有人 不作为唤醒机器人的依据；直接 @机器人仍按其他规则处理。",
            },
            {
                "name": "show_typing",
                "label": "显示正在输入",
                "type": "switch",
                "defaultValue": True,
                "legacyDefaultValue": True,
                "helpKey": "处理消息期间向 Octo 发送正在输入提示。",
            },
            {
                "name": "heartbeat_interval",
                "label": "心跳间隔（秒）",
                "defaultValue": "30",
                "placeholder": "30",
                "helpKey": "范围 10–300 秒。",
            },
            {
                "name": "poll_interval",
                "label": "事件补偿轮询间隔（秒）",
                "defaultValue": "5",
                "placeholder": "5",
                "helpKey": "范围 2–300 秒；WebSocket 实时接收同时启用。",
            },
            {
                "name": "allowed_hosts",
                "label": "允许私网/明文的精确主机名",
                "defaultValue": "",
                "placeholder": "octo.internal, files.internal",
                "helpKey": "自托管时按需填写，逗号或空格分隔；不含协议、端口或通配符。",
            },
        ],
    )
