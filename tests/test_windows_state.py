"""Windows lock selection plus native process ownership and probe regressions."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from octop_gateway.channels import BUILTIN_CHANNELS
from octop_gateway.manager import ChannelManager

from octo_channel_impl import state
from octo_channel_impl.api import BotRegisterInfo
from octo_channel_impl.api import OctoApiError
from octo_channel_impl.channel import OctoChannel, OctoConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["native", "windows-adapter"])
async def test_check_connection_does_not_compete_with_online_channel(
    tmp_path, monkeypatch, request, backend
):
    if backend == "windows-adapter":
        request.getfixturevalue("windows_backend")

    async def processor(message):
        if False:
            yield

    monkeypatch.setitem(BUILTIN_CHANNELS, "octo", OctoChannel)
    config = {
        "bot_token": "test-only",
        "api_url": "https://example.com/api",
        "state_dir": str(tmp_path / "state"),
    }
    live = OctoChannel(processor, config=OctoConfig(**config), channel_id="live")
    live.set_enqueue_callback(lambda _: None)
    manager = ChannelManager({"live": live}, processor=processor)
    with (
        patch(
            "octo_channel_impl.api.register_bot",
            return_value=BotRegisterInfo(
                "bot", "im-token", "wss://example.com/ws", "", "owner"
            ),
        ),
        patch("octo_channel_impl.socket.WKSocket.start"),
        patch("octo_channel_impl.api.fetch_events", return_value=[]),
        patch("octo_channel_impl.api.send_heartbeat"),
    ):
        await live.start()
        live._ws._connected = True
        inbox, socket = live._inbox, live._ws
        inbox.accept("started", {"text": "retain"})
        inbox.mark("started", "started")
        try:
            # Both draft probes and the existing channel's Check Connection
            # button must work while the real consumer owns its database.
            for channel_id in ("live", "__probe__", "live"):
                await manager.probe_channel("octo", config, channel_id=channel_id)
            assert manager.get_channel("live") is live
            assert live.is_connected
            assert live._inbox is inbox and live._ws is socket
            assert inbox.db.execute("SELECT status FROM messages").fetchone() == (
                "started",
            )
            # A real second consumer must still be refused.
            other = OctoChannel(
                processor, config=OctoConfig(**config), channel_id="other"
            )
            other.set_enqueue_callback(lambda _: None)
            try:
                with pytest.raises(RuntimeError, match="another process"):
                    await other.start()
            finally:
                await other.stop()
        finally:
            await live.stop()


@pytest.mark.asyncio
async def test_probe_authenticates_candidate_without_starting_consumer(
    tmp_path, monkeypatch
):
    async def processor(message):
        if False:
            yield

    monkeypatch.setitem(BUILTIN_CHANNELS, "octo", OctoChannel)
    manager = ChannelManager(processor=processor)
    config = {
        "bot_token": "candidate-token",
        "api_url": "https://example.com/api",
        "state_dir": str(tmp_path / "probe-only"),
    }
    with (
        patch(
            "octo_channel_impl.api.register_bot",
            return_value=BotRegisterInfo(
                "bot", "im-token", "wss://example.com/ws", "", "owner"
            ),
        ) as register,
        patch(
            "octo_channel_impl.channel.Inbox",
            side_effect=AssertionError("probe opened state"),
        ),
        patch(
            "octo_channel_impl.channel.WKSocket",
            side_effect=AssertionError("probe opened websocket"),
        ),
        patch(
            "octo_channel_impl.api.fetch_events",
            side_effect=AssertionError("probe consumed events"),
        ),
        patch(
            "octo_channel_impl.api.ack_event",
            side_effect=AssertionError("probe ACKed events"),
        ),
        patch(
            "octo_channel_impl.api.send_heartbeat",
            side_effect=AssertionError("probe started heartbeat"),
        ),
    ):
        await manager.probe_channel("octo", config, channel_id="existing-id")
        assert register.call_args.args[1] == "candidate-token"
        register.side_effect = OctoApiError(401, "/v1/bot/register")
        with pytest.raises(OctoApiError):
            await manager.probe_channel(
                "octo", {**config, "bot_token": "invalid"}, channel_id="existing-id"
            )
        assert register.call_args.args[1] == "invalid"
    assert not (tmp_path / "probe-only").exists()


@pytest.fixture
def windows_backend(monkeypatch):
    if os.name != "nt":
        # Exercise the Windows adapter on Unix; native Windows runs use the
        # real msvcrt module. This shim is not a Windows kernel test.
        import fcntl

        def locking(fd, mode, count):
            assert mode == 2 and count == 1
            assert os.lseek(fd, 0, os.SEEK_CUR) == 0
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        monkeypatch.setattr(state, "_WINDOWS", True, raising=False)
        monkeypatch.setitem(
            sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=locking)
        )
    monkeypatch.setitem(sys.modules, "fcntl", None)


def test_windows_inbox_never_imports_fcntl_and_retains_ownership(
    tmp_path, windows_backend
):
    path = tmp_path / "inbox.sqlite"
    path.with_suffix(".lock").write_bytes(b"existing lock file")
    first = state.Inbox(path)
    try:
        first.accept("m1", {"text": "hello"})
        with pytest.raises(RuntimeError, match="another process"):
            state.Inbox(path)
    finally:
        first.close()
    reopened = state.Inbox(path)
    try:
        assert reopened.pending() == [("m1", {"text": "hello"})]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_windows_save_probe_does_not_open_state(
    tmp_path, monkeypatch, windows_backend
):
    async def processor(message):
        if False:
            yield

    monkeypatch.setitem(BUILTIN_CHANNELS, "octo", OctoChannel)
    manager = ChannelManager(processor=processor)
    config = {
        "bot_token": "test-only",
        "api_url": "https://example.com/api",
        "state_dir": str(tmp_path / "状态数据库"),
    }
    with (
        patch(
            "octo_channel_impl.api.register_bot",
            return_value=BotRegisterInfo(
                "bot", "im-token", "wss://example.com/ws", "", "owner"
            ),
        ),
        patch("octo_channel_impl.socket.WKSocket.start"),
        patch("octo_channel_impl.api.fetch_events", return_value=[]),
        patch("octo_channel_impl.api.send_heartbeat"),
    ):
        await manager.probe_channel("octo", config)
        await manager.probe_channel("octo", config)
    databases = list(tmp_path.rglob("inbox.sqlite"))
    assert databases == []
    assert not (tmp_path / "状态数据库").exists()


def test_failed_database_open_releases_state_lock(tmp_path, windows_backend):
    path = tmp_path / "inbox.sqlite"
    with patch.object(
        state.sqlite3,
        "connect",
        side_effect=sqlite3.OperationalError("test open failure"),
    ):
        with pytest.raises(sqlite3.OperationalError) as failure:
            state.Inbox(path)
    # Keep the traceback alive: correctness must not depend on garbage collection.
    assert failure.value is not None
    inbox = state.Inbox(path)
    inbox.close()


def test_native_lock_excludes_other_process_and_releases_on_close(tmp_path):
    path = tmp_path / "inbox.sqlite"
    code = """
from pathlib import Path
import sys
from octo_channel_impl.state import Inbox
try:
    inbox = Inbox(Path(sys.argv[1]))
except RuntimeError as exc:
    assert "another process" in str(exc)
    sys.exit(2)
inbox.close()
"""
    first = state.Inbox(path)
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert blocked.returncode == 2, blocked.stdout + blocked.stderr
    finally:
        first.close()
    accepted = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_native_lock_releases_after_abrupt_exit(tmp_path):
    path = tmp_path / "inbox.sqlite"
    code = """
import os, sys
from pathlib import Path
from octo_channel_impl.state import Inbox
inbox = Inbox(Path(sys.argv[1]))
inbox.accept("m1", {"text":"hello"})
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.with_suffix(".lock").is_file()
    reopened = state.Inbox(path)
    try:
        assert reopened.pending() == [("m1", {"text": "hello"})]
    finally:
        reopened.close()
