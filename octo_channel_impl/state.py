"""Per-channel durable inbox. A started turn is never automatically replayed."""

from __future__ import annotations

import json
import logging
from contextlib import closing
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
PENDING_LIMIT = 4096
MESSAGE_LIMIT = 100000
EVENT_LIMIT = 100000


def database_stats(db: sqlite3.Connection, path: Path | None = None) -> dict[str, Any]:
    messages = dict(db.execute("SELECT status,count(*) FROM messages GROUP BY status"))
    events = dict(db.execute("SELECT status,count(*) FROM events GROUP BY status"))
    oldest = db.execute(
        "SELECT min(updated) FROM messages WHERE status='received'"
    ).fetchone()[0]
    has_tombstones = db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='message_tombstones'"
    ).fetchone()
    result = {
        "messages": messages,
        "events": events,
        "pending": messages.get("received", 0) + messages.get("started", 0),
        "oldest_received_seconds": round(max(0, time.time() - oldest), 1)
        if oldest is not None
        else 0,
        "tombstones": db.execute("SELECT count(*) FROM message_tombstones").fetchone()[
            0
        ]
        if has_tombstones
        else 0,
        "limits": {
            "pending": PENDING_LIMIT,
            "messages": MESSAGE_LIMIT,
            "events": EVENT_LIMIT,
        },
    }
    if path is not None:
        result["storage_bytes"] = sum(
            file.stat().st_size
            for file in (path, Path(str(path) + "-wal"))
            if file.exists()
        )
    return result


def backup_database(db: sqlite3.Connection, output: Path) -> None:
    """Never overwrite a backup; keep complete SQLite/WAL contents private."""
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(output)) as target:
            db.backup(target)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


class Inbox:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        import fcntl

        self._lock = path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("Octo state already owned by another process") from None
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                key TEXT PRIMARY KEY, raw TEXT NOT NULL, status TEXT NOT NULL,
                updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS message_status ON messages(status, updated);
            CREATE INDEX IF NOT EXISTS event_status ON events(status, id);
            CREATE TABLE IF NOT EXISTS message_tombstones (key TEXT PRIMARY KEY);
        """)
        # The previous process may have performed effects before it died.
        self.db.execute("UPDATE messages SET status='uncertain' WHERE status='started'")
        self.db.commit()

    def accept(self, key: str, raw: dict[str, Any]) -> bool:
        if self.db.execute(
            "SELECT 1 FROM messages WHERE key=? UNION ALL "
            "SELECT 1 FROM message_tombstones WHERE key=? LIMIT 1",
            (key, key),
        ).fetchone():
            return False
        # Backpressure rather than silent eviction of pending work.
        count = self.db.execute(
            "SELECT count(*) FROM messages WHERE status IN ('received','started')"
        ).fetchone()[0]
        if count >= PENDING_LIMIT:
            raise RuntimeError("Octo inbox capacity exhausted")
        if (
            self.db.execute("SELECT count(*) FROM messages").fetchone()[0]
            >= MESSAGE_LIMIT
        ):
            raise RuntimeError(
                "Octo dedup store capacity reached; archive before accepting more"
            )
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?)",
                (key, json.dumps(raw, ensure_ascii=False), "received", time.time()),
            )
        return cursor.rowcount == 1

    def pending(self, limit=100):
        return [
            (key, json.loads(raw))
            for key, raw in self.db.execute(
                "SELECT key, raw FROM messages WHERE status='received' ORDER BY updated LIMIT ?",
                (limit,),
            )
        ]

    def mark(self, key: str, status: str):
        with self.db:
            if status in {"completed", "ignored"}:
                self.db.execute(
                    "UPDATE messages SET status=?, raw=?, updated=? WHERE key=?",
                    (status, "{}", time.time(), key),
                )
            else:
                self.db.execute(
                    "UPDATE messages SET status=?, updated=? WHERE key=?",
                    (status, time.time(), key),
                )

    def event(self, event_id: int, kind: str, status="ack_pending"):
        if self.db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
            return
        self.maintain()
        if self.db.execute("SELECT count(*) FROM events").fetchone()[0] >= EVENT_LIMIT:
            raise RuntimeError(
                "Octo event store capacity reached; resolve retained events"
            )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                (event_id, kind[:100], status),
            )

    def acked(self, event_id: int):
        with self.db:
            self.db.execute("UPDATE events SET status='acked' WHERE id=?", (event_id,))

    def pending_acks(self):
        return [
            row[0]
            for row in self.db.execute(
                "SELECT id FROM events WHERE status='ack_pending' ORDER BY id LIMIT 100"
            )
        ]

    def maintain(self):
        """Compact ACKed events only; preserve cursor and every unresolved row.

        Dedup keys never expire: server replay retention is not established.
        Offline archive compacts finished rows; unresolved work stays capped.
        """
        if self.db.execute("SELECT count(*) FROM events").fetchone()[0] < 10000:
            return
        with self.db:
            high = self.cursor()
            self.db.execute(
                "INSERT INTO metadata VALUES ('cursor', ?) ON CONFLICT(key) "
                "DO UPDATE SET value=max(value, excluded.value)",
                (high,),
            )
            self.db.execute("DELETE FROM events WHERE status='acked'")
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def cursor(self):
        return self.db.execute(
            "SELECT max(value) FROM (SELECT coalesce(max(id), 0) AS value FROM events "
            "UNION ALL SELECT value FROM metadata WHERE key='cursor')"
        ).fetchone()[0]

    def stats(self) -> dict[str, Any]:
        return database_stats(self.db, self.path)

    def warn_capacity(self) -> None:
        stats = self.stats()
        if (
            stats["pending"] >= PENDING_LIMIT * 0.8
            or sum(stats["messages"].values()) >= MESSAGE_LIMIT * 0.8
            or sum(stats["events"].values()) >= EVENT_LIMIT * 0.8
        ):
            logger.warning(
                "Octo state nearing capacity: pending=%d messages=%d events=%d; "
                "inspect with scripts/state_admin.py status",
                stats["pending"],
                sum(stats["messages"].values()),
                sum(stats["events"].values()),
            )

    def archive(self, output: Path) -> int:
        """Snapshot first, then compact finished rows without losing dedup keys.

        The caller must own this Inbox's exclusive lock (stop the channel).
        Uncertain, invalid, received and unsupported work is never discarded.
        """
        backup_database(self.db, output)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO message_tombstones "
                "SELECT key FROM messages WHERE status IN ('completed','ignored')"
            )
            count = self.db.execute(
                "DELETE FROM messages WHERE status IN ('completed','ignored')"
            ).rowcount
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return count

    def retry_uncertain(self, key: str, backup: Path) -> None:
        row = self.db.execute(
            "SELECT status FROM messages WHERE key=?", (key,)
        ).fetchone()
        if row != ("uncertain",):
            raise ValueError("Only an existing uncertain message may be retried")
        backup_database(self.db, backup)
        self.mark(key, "received")

    def prepare_downgrade(self, backup: Path) -> int:
        """Restore compact keys to rows understood by versions before 0.1.3."""
        backup_database(self.db, backup)
        with self.db:
            count = self.db.execute(
                "INSERT OR IGNORE INTO messages SELECT key, '{}', 'ignored', ? "
                "FROM message_tombstones",
                (time.time(),),
            ).rowcount
            self.db.execute("DELETE FROM message_tombstones")
        return count

    def close(self):
        self.db.close()
        self._lock.close()
