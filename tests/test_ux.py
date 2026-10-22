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


@pytest.mark.asyncio
async def test_auto_state_is_stable_across_token_rotation(tmp_path):
    async def processor(message):
        if False:
            yield

    paths = []
    for token in ("first-secret", "rotated-secret"):
        obj = OctoChannel(processor, config=OctoConfig(bot_token=token))
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
    manifest = yaml.safe_load((root / "plugin.yaml").read_text())
    assert manifest["icon"] == captured["icon"] == module.ICON_URL
    assert manifest["description"] == module.DESCRIPTION
    assert manifest["version"] == "1.0.3"
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
