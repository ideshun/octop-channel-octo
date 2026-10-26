import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from octo_channel_impl.state import Inbox
from test_delivery import channel

ROOT = Path(__file__).resolve().parents[1]


def admin(path, *args):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/state_admin.py"),
            "--db",
            str(path),
            *args,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_readonly_status_works_live_but_archive_requires_exclusive_lock(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    try:
        inbox.accept("key", {"text": "private-message-text"})
        status = admin(path, "status")
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["pending"] == 1
        assert "private-message-text" not in status.stdout
        blocked = admin(path, "archive", "--output", str(tmp_path / "backup.sqlite"))
        assert blocked.returncode == 1
        assert "another process" in blocked.stderr
        assert not (tmp_path / "backup.sqlite").exists()
    finally:
        inbox.close()


def test_archive_retains_dedup_and_unresolved_work(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    for key, status in [
        ("done", "completed"),
        ("skip", "ignored"),
        ("maybe", "uncertain"),
        ("pending", "received"),
        ("bad", "invalid"),
    ]:
        inbox.accept(key, {"text": "content"})
        inbox.mark(key, status)
    inbox.event(1, "unsupported", "unsupported")
    inbox.close()
    output = tmp_path / "archive.sqlite"
    result = admin(path, "archive", "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["archived_messages"] == 2
    if os.name != "nt":
        # Windows chmod cannot express POSIX 0600; directory ACLs apply there.
        assert output.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(output) as backup:
        assert backup.execute("SELECT count(*) FROM messages").fetchone()[0] == 5
    inbox = Inbox(path)
    try:
        assert not inbox.accept("done", {})
        assert not inbox.accept("skip", {})
        assert inbox.stats()["tombstones"] == 2
        assert dict(inbox.db.execute("SELECT key,status FROM messages")) == {
            "maybe": "uncertain",
            "pending": "received",
            "bad": "invalid",
        }
        assert inbox.db.execute("SELECT status FROM events").fetchone() == (
            "unsupported",
        )
        with pytest.raises(FileExistsError):
            inbox.archive(output)
    finally:
        inbox.close()


def test_uncertain_retry_requires_confirmation_and_backup(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    inbox.accept("key", {"payload": {"type": 1, "content": "hello"}})
    inbox.mark("key", "uncertain")
    inbox.close()
    backup = tmp_path / "before-retry.sqlite"
    assert admin(path, "retry", "--key", "key", "--backup", str(backup)).returncode != 0
    result = admin(
        path,
        "retry",
        "--key",
        "key",
        "--backup",
        str(backup),
        "--confirm-no-side-effects",
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM messages").fetchone() == ("received",)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT status FROM messages").fetchone() == ("uncertain",)


def test_downgrade_restores_keys_for_old_plugin(tmp_path):
    inbox = Inbox(tmp_path / "inbox.sqlite")
    try:
        inbox.accept("old", {})
        inbox.mark("old", "completed")
        inbox.archive(tmp_path / "archived.sqlite")
        assert inbox.prepare_downgrade(tmp_path / "before-downgrade.sqlite") == 1
        assert inbox.db.execute("SELECT key,status FROM messages").fetchall() == [
            ("old", "ignored")
        ]
        assert inbox.stats()["tombstones"] == 0
        assert not inbox.accept("old", {})
    finally:
        inbox.close()


@pytest.mark.asyncio
async def test_full_poll_pages_catch_up_without_idle_delay_and_sort_cursor(tmp_path):
    obj = channel(tmp_path)
    obj._retry_acks = AsyncMock()
    page = [
        {
            "event_id": index,
            "event_type": "message",
            "message": {
                "message_id": str(index),
                "from_uid": "peer",
                "payload": {"type": 1, "content": "hi"},
            },
        }
        for index in range(50, 0, -1)
    ]
    calls, delays = [], []

    def fetch(*args):
        calls.append(args[2])
        if len(calls) == 1:
            return page
        obj._stopping = True
        return []

    async def sleep(delay):
        delays.append(delay)

    try:
        with (
            patch("octo_channel_impl.api.fetch_events", side_effect=fetch),
            patch.object(asyncio, "sleep", sleep),
        ):
            await obj._events_poll_loop()
        assert calls == [0, 50]
        assert delays == [0, obj._config.poll_interval]
        assert len(obj._inbox.pending()) == 50
        assert obj.runtime_status["poll_failures"] == 0
        assert obj.runtime_status["last_poll_success"] is not None
    finally:
        obj._inbox.close()
