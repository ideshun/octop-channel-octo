"""Octop channel extension entry point; never patches host implementation files."""

from __future__ import annotations


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
        intro_url="https://github.com/Mininglamp-OSS/octo-docs",
        fields=[
            {"name": "api_url", "label": "API URL", "required": True},
            {
                "name": "bot_token",
                "label": "Bot Token",
                "type": "password",
                "required": True,
            },
            {"name": "state_dir", "label": "持久化状态目录", "required": True},
            {"name": "allowed_hosts", "label": "允许私网/明文的精确主机名（逗号分隔）"},
            {"name": "admins", "label": "额外管理员 UID"},
            {"name": "owner_only", "label": "仅管理员触发", "type": "switch"},
            {"name": "group_whitelist", "label": "允许的群或话题 ID"},
        ],
    )
