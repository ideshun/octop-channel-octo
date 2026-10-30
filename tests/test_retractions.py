"""Keep Octo control notifications and withdrawn input out of agent turns."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from octop_gateway.models import MessageEvent

from octo_channel_impl.state import Inbox
from test_delivery import channel, message
from test_gateway_delivery import gateway, raw, wait_for


def recall(target, chat="group"):
    return {
        **raw("recall-" + target, group=True),
        "channel_id": chat,
        "payload": {
            "type": 99,
            "cmd": "messageRevoke",
            "param": {"message_id": target},
        },
    }


def test_recall_before_input_survives_restart_and_is_scoped_to_the_chat(tmp_path):
    obj = channel(tmp_path)
    obj._accept_message(recall("123"), via="ws")
    assert obj._inbox.pending() == []
    obj._inbox.close()
    obj._inbox = Inbox(tmp_path / "inbox.sqlite")
    assert obj._native_message(message()) is None
    assert obj._native_message({**message(), "channel_id": "other"}) is not None
    obj._inbox.close()


@pytest.mark.asyncio
async def test_queued_input_is_dropped_when_recalled_before_preparation(tmp_path):
    obj = channel(tmp_path)
    obj._accept_message(message(), via="ws")
    native = obj._native_message(message())
    native["inbox_key"] = "bot:123"
    inbound = obj.parse_inbound(native)
    obj._accept_message(recall("123"), via="poll")
    assert await obj._prepare_inbound(inbound) is None
    assert obj._inbox.pending() == []
    obj._inbox.close()


@pytest.mark.asyncio
async def test_recall_cancels_the_turn_and_keeps_the_gateway_worker_alive(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def process(message):
        if message.metadata["message_id"] == "1":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        yield MessageEvent.text("next reply")
        yield MessageEvent.completed()

    async with gateway(tmp_path, require_mention=False) as (obj, _, _seen):
        obj._processor = process
        obj._send_text = AsyncMock()
        obj._accept_message(raw("1", group=True), via="ws")
        async with asyncio.timeout(3):
            await started.wait()
        obj._accept_message(recall("1"), via="ws")
        async with asyncio.timeout(3):
            await cancelled.wait()
        await wait_for(lambda: not obj._active_inputs)
        obj._accept_message(raw("2", group=True), via="ws")
        await wait_for(lambda: obj._send_text.await_count == 1)
        assert obj._send_text.await_args.args[1] == "next reply"


@pytest.mark.parametrize(
    "payload", [{"type": 99, "cmd": "typing"}, {"type": 1006}, {"type": 2000}]
)
def test_system_notifications_and_removed_history_are_not_user_messages(
    tmp_path, payload
):
    obj = channel(tmp_path)
    assert obj._native_message({**message(), "payload": payload}) is None
    assert obj._entries_from_history([{**message(), "revoke": 1}]) == []
    obj._inbox.close()
