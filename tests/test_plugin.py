from pathlib import Path
from unittest.mock import patch
import pytest
from octop_harness.plugins.loader import load_plugin_dir
from octop_harness.plugins.registry import PluginRegistry
from octo_channel_impl.api import BotRegisterInfo
from test_delivery import channel


def test_real_plugin_loader_registers_channel():
    registry = PluginRegistry()
    previous = registry.get("octop-channel-octo")
    try:
        loaded = load_plugin_dir(
            Path(__file__).resolve().parents[1], install_deps=False
        )
        assert loaded.manifest.kind == "channel"
        assert loaded.channels[0].kind == "octo"
        assert loaded.channels[0].channel_cls.channel_type == "octo"
    finally:
        registry.unregister("octop-channel-octo")
        if previous:
            registry.register(previous)


@pytest.mark.asyncio
async def test_invalid_registration_leaves_no_background_tasks(tmp_path):
    obj = channel(tmp_path)
    obj._inbox.close()
    obj._inbox = None
    obj._config.bot_token = "test-only"
    obj._config.api_url = "https://example.invalid"
    with patch(
        "octo_channel_impl.api.register_bot",
        return_value=BotRegisterInfo("bot", "", "", "", "owner"),
    ):
        with pytest.raises(RuntimeError, match="registration requires"):
            await obj.start()
    assert obj._inbox is None
    assert obj._dispatch_task is None


def test_duplicate_state_owner_is_rejected(tmp_path):
    from octo_channel_impl.state import Inbox

    first = Inbox(tmp_path / "state.sqlite")
    try:
        with pytest.raises(RuntimeError, match="another process"):
            Inbox(tmp_path / "state.sqlite")
    finally:
        first.close()
