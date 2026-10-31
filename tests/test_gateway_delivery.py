"""Exercise the real gateway queue/worker boundary, not only adapter helpers."""

import asyncio
from contextlib import asynccontextmanager
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest
from octop_gateway.manager import ChannelManager
from octop_gateway.models import MessageEvent

from octo_channel_impl.channel import OctoChannel, OctoConfig
from octo_channel_impl.state import Inbox


async def wait_for(predicate):
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.005)


def raw(mid, text="hello", uid="peer", group=False):
    return {
        "message_id": str(mid),
        "from_uid": uid,
        "channel_id": "group" if group else "bot",
        "channel_type": 2 if group else 1,
        "payload": {"type": 1, "content": text},
    }


@asynccontextmanager
async def gateway(tmp_path, workers=1, **config):
    seen = []

    async def processor(message):
        assert obj._inbox.db.execute(
            "SELECT status FROM messages WHERE key=?",
            (message.metadata["inbox_key"],),
        ).fetchone() == ("started",)
        seen.append(message)
        yield MessageEvent.completed()

    obj = OctoChannel(
        processor,
        config=OctoConfig(state_dir=str(tmp_path), show_typing=False, **config),
        channel_id="test",
    )
    obj._robot_id, obj._owner_uid = "bot", "owner"
    obj._inbox = Inbox(tmp_path / "inbox.sqlite")
    obj._lookup_member_name = lambda *_: ""
    obj._backfill_history = AsyncMock()
    mgr = ChannelManager({"test": obj})
    mgr._running = True
    mgr._loop = asyncio.get_running_loop()
    # A single-slot queue makes any missing backpressure observable.
    mgr._queues["test"] = asyncio.Queue(maxsize=1)
    obj.set_enqueue_callback(lambda payload: mgr.enqueue("test", payload))
    obj._dispatch_task = asyncio.create_task(obj._dispatch_loop())
    obj._notice_task = asyncio.create_task(obj._notice_loop())
    tasks = [asyncio.create_task(mgr._worker_loop("test")) for _ in range(workers)]
    try:
        yield obj, mgr, seen
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await obj.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 10])
@pytest.mark.parametrize("require", [False, True])
async def test_group_activation_is_independent_of_history(tmp_path, history, require):
    async with gateway(tmp_path, history_limit=history, require_mention=require) as (
        obj,
        _,
        seen,
    ):
        obj._accept_message(raw(1, group=True), via="ws")
        await wait_for(
            lambda: (
                obj._inbox.db.execute(
                    "SELECT status FROM messages WHERE key='bot:1'"
                ).fetchone()[0]
                in {"ignored", "completed"}
            )
        )
        assert len(seen) == (0 if require else 1)
        assert not obj._scheduled
        assert not obj._inbox.pending()


@pytest.mark.asyncio
async def test_group_bot_messages_do_not_trigger_mentions_or_command_notices(tmp_path):
    async with gateway(tmp_path, require_mention=True, owner_commands_only=True) as (
        obj,
        _,
        seen,
    ):

        async def classify(message):
            return message.metadata["sender_id"] == "other-bot"

        obj.resolve_sender_is_bot = AsyncMock(side_effect=classify)
        obj._send_notice = AsyncMock()
        obj._accept_message(raw(1, "@bot hello", uid="other-bot", group=True), via="ws")
        obj._accept_message(
            raw(2, "@bot /model x:y", uid="other-bot", group=True), via="ws"
        )
        await wait_for(lambda: obj._inbox.stats()["pending"] == 0)
        assert seen == []
        obj._send_notice.assert_not_awaited()

        obj._accept_message(raw(3, "@bot hello", uid="peer", group=True), via="ws")
        await wait_for(lambda: len(seen) == 1)
        assert seen[0].metadata["sender_is_bot"] is False


@pytest.mark.asyncio
async def test_burst_does_not_merge_senders_drop_queue_items_or_replay(tmp_path):
    async with gateway(tmp_path, history_limit=0, require_mention=False) as (
        obj,
        _,
        seen,
    ):
        for index in range(12):
            obj._accept_message(
                raw(
                    index,
                    text=f"text {index}",
                    uid="owner" if index % 2 else "peer",
                    group=True,
                ),
                via="ws",
            )
        await wait_for(lambda: len(seen) == 12 and not obj._scheduled)
        assert [message.text for message in seen] == [
            f"text {index}" for index in range(12)
        ]
        assert [message.metadata["sender_is_owner"] for message in seen] == [
            bool(index % 2) for index in range(12)
        ]
        assert dict(
            obj._inbox.db.execute(
                "SELECT status,count(*) FROM messages GROUP BY status"
            )
        ) == {"completed": 12}
    inbox = Inbox(tmp_path / "inbox.sqlite")
    try:
        assert inbox.pending() == []
    finally:
        inbox.close()


@pytest.mark.asyncio
async def test_passive_chatter_releases_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr("octo_channel_impl.state.PENDING_LIMIT", 2)
    async with gateway(tmp_path) as (obj, _, seen):
        for index in range(4):
            obj._accept_message(raw(index, group=True), via="ws")
            # received can be empty while the worker is still in started;
            # capacity is released only after the ignored transition finishes.
            await wait_for(lambda: obj._inbox.stats()["pending"] == 0)
        assert seen == []
        assert obj._inbox.stats()["pending"] == 0
        assert not obj._scheduled


@pytest.mark.asyncio
async def test_slow_notice_never_blocks_owner_and_dm_notice_routes_to_sender(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async with gateway(tmp_path, owner_commands_only=True) as (obj, _, seen):
        original = obj._send_notice

        async def slow(native, text):
            entered.set()
            await release.wait()
            await original(native, text)

        obj._send_notice = slow
        command = raw(1, "/model x:y")
        command.pop("channel_id")
        command.pop("channel_type")
        with patch("octo_channel_impl.api.send_text") as send:
            obj._accept_message(command, via="poll")
            await asyncio.wait_for(entered.wait(), 2)
            obj._accept_message(raw(2, uid="owner"), via="ws")
            await wait_for(lambda: len(seen) == 1)
            assert seen[0].metadata["sender_is_owner"] is True
            release.set()
            await asyncio.wait_for(obj._notice_queue.join(), 2)
            assert send.call_args.args[2:4] == ("peer", 1)


@pytest.mark.asyncio
async def test_unmentioned_commands_do_not_send_group_notices(tmp_path):
    async with gateway(tmp_path, owner_commands_only=True) as (obj, _, seen):
        with patch("octo_channel_impl.api.send_text") as send:
            obj._accept_message(raw(1, "/model x:y", group=True), via="ws")
            await wait_for(lambda: not obj._inbox.pending())
            assert seen == []
            send.assert_not_called()


@pytest.mark.asyncio
async def test_negative_enqueue_receipt_leaves_message_retryable(tmp_path):
    async with gateway(tmp_path) as (obj, mgr, seen):
        obj._enqueue_callback = lambda _: False
        obj._accept_message(raw(1), via="ws")
        await asyncio.sleep(0.05)
        assert not obj._scheduled
        assert len(obj._inbox.pending()) == 1
        obj.set_enqueue_callback(lambda payload: mgr.enqueue("test", payload))
        obj._dispatch_wakeup.set()
        await wait_for(lambda: len(seen) == 1 and not obj._scheduled)


@pytest.mark.asyncio
async def test_handoff_does_not_block_stop_behind_session_lock(tmp_path):
    release = asyncio.Event()
    started = asyncio.Event()
    async with gateway(tmp_path, workers=3) as (obj, mgr, seen):

        async def processor(message):
            if message.text == "long turn":
                started.set()
                await release.wait()
            seen.append(message)
            yield MessageEvent.completed()

        async def pre_lock(_, message):
            if message.text == "/stop":
                release.set()

        obj._processor = processor
        mgr._on_pre_lock = pre_lock
        obj._accept_message(raw(1, "long turn", uid="owner"), via="ws")
        await asyncio.wait_for(started.wait(), 2)
        obj._accept_message(raw(2, "queued turn", uid="owner"), via="ws")
        await wait_for(
            lambda: "bot:2" in obj._scheduled and obj._awaiting_worker is None
        )
        obj._accept_message(raw(3, "/stop", uid="owner"), via="ws")
        await wait_for(lambda: len(seen) == 3 and not obj._scheduled)
        assert release.is_set()


@pytest.mark.asyncio
async def test_patched_gateway_returns_real_queue_receipts():
    if inspect.signature(ChannelManager.enqueue).return_annotation not in (
        "bool | None",
        bool | None,
    ):
        pytest.skip("Optional gateway enqueue receipt patch is not applied")
    mgr = ChannelManager()
    mgr._loop = asyncio.get_running_loop()
    mgr._queues["test"] = asyncio.Queue(maxsize=1)
    assert mgr.enqueue("test", {}) is True
    assert mgr.enqueue("test", {}) is False
    assert mgr.enqueue("missing", {}) is False


def test_abrupt_exit_quarantines_running_turn(tmp_path):
    code = """
import asyncio, os, sys
from pathlib import Path
from octo_channel_impl.channel import OctoChannel, OctoConfig
from octo_channel_impl.state import Inbox
from octop_gateway.manager import ChannelManager
async def main():
    async def processor(message):
        os._exit(0)
        yield
    obj = OctoChannel(processor, config=OctoConfig(show_typing=False), channel_id="test")
    obj._inbox = Inbox(Path(sys.argv[1]) / "inbox.sqlite")
    obj._robot_id = "bot"
    mgr = ChannelManager({"test": obj})
    mgr._running = True
    mgr._queues["test"] = asyncio.Queue()
    obj.set_enqueue_callback(lambda payload: mgr.enqueue("test", payload))
    obj._accept_message({"message_id":"1", "from_uid":"peer", "payload":{"type":1,"content":"hello"}}, via="ws")
    asyncio.create_task(obj._dispatch_loop())
    await mgr._worker_loop("test")
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    inbox = Inbox(tmp_path / "inbox.sqlite")
    try:
        assert inbox.pending() == []
        assert inbox.db.execute("SELECT status FROM messages").fetchone() == (
            "uncertain",
        )
    finally:
        inbox.close()


@pytest.mark.asyncio
async def test_diagnostics_surface_failed_dispatch_and_poll(tmp_path):
    async with gateway(tmp_path) as (obj, _, _):
        obj._poll_failures = 1
        assert obj.runtime_error == "octo_poll_degraded"
        assert "bot_token" not in json.dumps(obj.runtime_status)
        obj._dispatch_task.cancel()
        await asyncio.gather(obj._dispatch_task, return_exceptions=True)
        assert obj.runtime_error == "octo_dispatch_failed"
