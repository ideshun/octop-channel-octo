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
        intro_url="https://github.com/Mininglamp-OSS/octo-docs",
        fields=[
            {
                "name": "bot_token",
                "label": "Bot Token",
                "type": "password",
                "required": True,
                "placeholder": "在 Octo BotFather 创建机器人获取 Token",
                "helpKey": DESCRIPTION,
            },
            {
                "name": "api_url",
                "label": "API URL",
                "required": True,
                "defaultValue": "https://im.deepminer.com.cn/api",
                "placeholder": "https://im.deepminer.com.cn/api",
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
                "helpKey": "channels.octo_owner_only_help",
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
                "label": "群聊仅 @ 唤醒",
                "type": "switch",
                "defaultValue": True,
                "legacyDefaultValue": True,
                "helpKey": "channels.octo_require_mention_help",
            },
            {
                "name": "history_limit",
                "label": "群聊上下文条数",
                "defaultValue": "10",
                "placeholder": "10",
                "helpKey": "附带最近的群聊消息，0 关闭，范围 0–100。",
            },
            {
                "name": "ignore_mention_all",
                "label": "忽略 @所有人",
                "type": "switch",
                "defaultValue": False,
            },
            {
                "name": "show_typing",
                "label": "显示正在输入",
                "type": "switch",
                "defaultValue": True,
                "legacyDefaultValue": True,
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
