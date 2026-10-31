from unittest.mock import patch
import pytest
from octo_channel_impl.channel import OctoChannel, OctoConfig
from octo_channel_impl.state import Inbox


def channel(tmp_path):
    async def processor(message):
        if False:
            yield

    obj = OctoChannel(
        processor, config=OctoConfig(state_dir=str(tmp_path)), channel_id="instance"
    )
    obj._robot_id = "bot"
    obj._inbox = Inbox(tmp_path / "inbox.sqlite")
    return obj


def message():
    return {
        "message_id": "123",
        "from_uid": "peer",
        "channel_id": "group",
        "channel_type": 2,
        "payload": {"type": 1, "content": "@bot hello"},
    }


def test_text_mention_survives_normalization(tmp_path):
    obj = channel(tmp_path)
    native = obj._native_message(message())
    assert native["text"] == "hello"
    parsed = obj.parse_inbound(native)
    assert parsed.metadata["bot_mentioned"] is True
    obj._inbox.close()


def test_mention_of_another_user_does_not_match_bot_name_prefix(tmp_path):
    obj = channel(tmp_path)
    obj._robot_name = "助手"
    assert not obj._mentioned("@助手甲 hello", {"mention": {"uids": ["other"]}})
    assert not obj._mentioned("@助手甲 hello", {})
    assert obj._mentioned("@助手 hello", {})
    assert obj._mentioned("hello", {"mention": {"uids": ["bot"]}})
    obj._inbox.close()


@pytest.mark.parametrize("order", [("ws", "poll"), ("poll", "ws"), ("ws", "ws")])
def test_cross_transport_dedup(tmp_path, order):
    obj = channel(tmp_path)
    for transport in order:
        obj._accept_message(message(), via=transport)
    assert len(obj._inbox.pending()) == 1
    obj._inbox.close()


@pytest.mark.asyncio
async def test_ack_failure_is_durable_and_does_not_redispatch(tmp_path):
    obj = channel(tmp_path)
    event = {"event_id": 1, "event_type": "message", "message": message()}
    with patch("octo_channel_impl.api.ack_event", side_effect=RuntimeError("offline")):
        await obj._handle_polled_event(event)
    assert obj._inbox.pending_acks() == [1]
    assert len(obj._inbox.pending()) == 1
    with patch("octo_channel_impl.api.ack_event") as ack:
        await obj._retry_acks()
        ack.assert_called_once()
    assert obj._inbox.pending_acks() == []
    obj._inbox.close()


@pytest.mark.asyncio
async def test_unsupported_event_recorded_without_ack_or_execution(tmp_path):
    obj = channel(tmp_path)
    with patch("octo_channel_impl.api.ack_event") as ack:
        await obj._handle_polled_event({"event_id": 2, "event_type": "bot_task"})
        ack.assert_not_called()
    assert obj._inbox.cursor() == 2
    assert obj._inbox.pending() == []
    obj._inbox.close()
