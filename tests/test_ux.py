import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from octo_channel_impl.channel import OctoChannel, OctoConfig, _state_directory
from octo_channel_impl.api import BotRegisterInfo


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "octop"))


def test_state_home_fallback_and_expansion(tmp_path, monkeypatch):
    assert _state_directory() == tmp_path / "octop/plugin-state/octo"
    monkeypatch.delenv("OCTOP_HOME")
    assert _state_directory() == tmp_path / "user/.octop/plugin-state/octo"
    assert _state_directory("~/inbox") == tmp_path / "user/inbox"
    monkeypatch.setenv("TEST_OCTO_STATE", str(tmp_path / "custom"))
    assert _state_directory("$TEST_OCTO_STATE") == tmp_path / "custom"


@pytest.mark.parametrize("value", ["relative", "./local", "https://host/state"])
def test_state_rejects_relative(value):
    with pytest.raises(ValueError, match="absolute"):
        OctoConfig(state_dir=value)


def test_state_rejects_files_and_dangling_links(tmp_path):
    file = tmp_path / "file"
    file.write_text("not a directory")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing")
    for path in (file, file / "child", link):
        with pytest.raises(ValueError, match="normal directory"):
            OctoConfig(state_dir=str(path))


def test_group_context_config_supports_gateway_without_owner_command_field(
    monkeypatch,
):
    import octo_channel_impl.channel as channel_module

    class LegacyGroupContextConfig:
        def __init__(self, *, enabled, visibility, history, history_limit, activation):
            self.enabled = enabled
            self.visibility = visibility
            self.history = history
            self.history_limit = history_limit
            self.activation = activation

    current = channel_module.GroupContextConfig
    monkeypatch.setattr(channel_module, "GroupContextConfig", LegacyGroupContextConfig)
    config = channel_module.OctoConfig()
    assert config.group_context.enabled
    assert config.group_context.activation == "mention"

    monkeypatch.setattr(channel_module, "GroupContextConfig", current)
    if "owner_commands_only" in getattr(current, "__dataclass_fields__", {}):
        assert channel_module.OctoConfig().group_context.owner_commands_only is False


def test_saved_group_history_limit_and_plugin_switch_are_both_preserved():
    config = OctoConfig.from_dict(
        {"history_limit": "2", "group_context": {"social_trigger_enabled": True}}
    )
    assert config.history_limit == 2
    assert config.group_context.history_limit == 2
    plugin_settings = getattr(config, "plugin_group_settings", None)
    if plugin_settings is not None:
        assert plugin_settings["social_trigger_enabled"] is True


@pytest.mark.asyncio
async def test_auto_state_is_stable_across_token_rotation(tmp_path):
    async def processor(message):
        if False:
            yield

    paths = []
    for token in ("first-secret", "rotated-secret"):
        obj = OctoChannel(processor, config=OctoConfig(bot_token=token))
        obj.set_enqueue_callback(lambda _: None)
        with (
            patch(
                "octo_channel_impl.api.register_bot",
                return_value=BotRegisterInfo(
                    "stable-bot", "im-token", "wss://example.com/ws", "", "owner"
                ),
            ),
            patch("octo_channel_impl.socket.WKSocket.start"),
        ):
            await obj.start()
            scope = hashlib.sha256(
                b"https://im.deepminer.com.cn/api|stable-bot"
            ).hexdigest()[:32]
            path = tmp_path / "octop/plugin-state/octo" / scope / "inbox.sqlite"
            assert path.is_file()
            paths.append(path)
            await obj.stop()
    assert paths[0] == paths[1]
    assert not any("secret" in str(p) for p in (tmp_path / "octop").rglob("*"))


def test_complete_manifest_and_schema():
    import importlib.util
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "ux_plugin", root / "main.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    captured = {}
    try:
        module.setup(
            SimpleNamespace(channel=lambda *args, **kwargs: captured.update(kwargs))
        )
    finally:
        for key in list(sys.modules):
            if key == spec.name or key.startswith(spec.name + "."):
                sys.modules.pop(key)
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    import tomllib

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["icon"] == captured["icon"] == module.ICON_URL
    assert manifest["description"] == module.DESCRIPTION
    assert manifest["version"] == project["project"]["version"]
    fields = {f["name"]: f for f in captured["fields"]}
    assert set(fields) == set(OctoConfig.__annotations__) - {"state_dir"}
    assert fields["require_mention"]["defaultValue"] is True
    assert fields["history_limit"]["defaultValue"] == "10"
    assert fields["api_url"]["defaultValue"] == str(OctoConfig().api_url)
    assert captured["intro_url"] == "https://github.com/ideshun/octop-channel-octo"
    assert list(fields)[:2] == ["api_url", "bot_token"]
    assert fields["require_mention"]["label"] == "仅回复 @机器人的群消息"
    assert all(f.get("helpKey") for f in fields.values())
    assert "私聊不受" in fields["require_mention"]["helpKey"]
    assert "上下文" in fields["require_mention"]["helpKey"]
    assert fields["admins"]["helpKey"]
    assert fields["owner_only"]["helpKey"]
    assert {f["name"] for f in fields.values() if f.get("required")} == {
        "bot_token",
        "api_url",
    }
