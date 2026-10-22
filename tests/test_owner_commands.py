import asyncio
from unittest.mock import patch

import pytest
from octo_channel_impl.channel import _OWNER_COMMAND_NOTICE, OctoConfig

from test_delivery import channel


def _native(text: str, *, from_uid: str = "peer") -> dict:
    return {
        "from_uid": from_uid,
        "channel_id": "dm",
        "channel_type": 1,
        "text": text,
        "is_group": False,
    }


def test_config_parses_owner_commands_only(tmp_path):
    config = OctoConfig(state_dir=str(tmp_path), owner_commands_only="true")
    assert config.owner_commands_only is True
    assert OctoConfig(state_dir=str(tmp_path)).owner_commands_only is False
    with patch("os.environ", {}):
        with pytest.raises(ValueError, match="owner_commands_only must be a boolean"):
            OctoConfig(state_dir=str(tmp_path), owner_commands_only="sometimes")


def test_owner_commands_gate_allows_chat_and_owner(tmp_path):
    obj = channel(tmp_path)
    obj._owner_uid = "owner"
    obj._config.owner_commands_only = True
    try:
        assert obj._owner_command_notice(_native("你好")) is None
        assert (
            obj._owner_command_notice(_native("/model x:y", from_uid="owner")) is None
        )
        assert obj._owner_command_notice(_native("/model x:y")) == _OWNER_COMMAND_NOTICE
        # Administrators are not exempt: only the owner may drive commands.
        obj._config.admins = "admin1"
        assert (
            obj._owner_command_notice(_native("/skills", from_uid="admin1")) is not None
        )
    finally:
        obj._inbox.close()


def test_owner_commands_gate_is_off_by_default(tmp_path):
    obj = channel(tmp_path)
    obj._owner_uid = "owner"
    try:
        assert obj._owner_command_notice(_native("/model x:y")) is None
    finally:
        obj._inbox.close()


@pytest.mark.asyncio
async def test_non_owner_command_is_answered_without_a_turn(tmp_path):
    obj = channel(tmp_path)
    obj._owner_uid = "owner"
    obj._config.owner_commands_only = True
    enqueued: list[dict] = []
    notices: list[tuple[str, str]] = []
    obj._enqueue_callback = enqueued.append

    async def fake_notice(native, text):
        notices.append((native["channel_id"], text))

    obj._send_notice = fake_notice
    try:
        obj._inbox.accept(
            "k1",
            {
                "payload": {"type": 1, "content": "/model foo:bar"},
                "from_uid": "peer",
                "channel_id": "dm",
                "channel_type": 1,
            },
        )
        task = asyncio.create_task(obj._dispatch_loop())
        notices_task = asyncio.create_task(obj._notice_loop())
        try:
            for _ in range(100):
                await asyncio.sleep(0.02)
                if notices:
                    break
        finally:
            task.cancel()
            notices_task.cancel()
            await asyncio.gather(task, notices_task, return_exceptions=True)
        assert enqueued == []
        assert notices == [("dm", _OWNER_COMMAND_NOTICE)]
    finally:
        obj._inbox.close()
