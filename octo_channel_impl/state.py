"""Per-channel durable inbox. A started turn is never automatically replayed."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Inbox:
    def __init__(self, path: Path):
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
        """)
        # The previous process may have performed effects before it died.
        self.db.execute("UPDATE messages SET status='uncertain' WHERE status='started'")
        self.db.commit()

    def accept(self, key: str, raw: dict[str, Any]) -> bool:
        if self.db.execute("SELECT 1 FROM messages WHERE key=?", (key,)).fetchone():
            return False
        # Backpressure rather than silent eviction of pending work.
        count = self.db.execute(
            "SELECT count(*) FROM messages WHERE status IN ('received','started')"
        ).fetchone()[0]
        if count >= 4096:
            raise RuntimeError("Octo inbox capacity exhausted")
        if self.db.execute("SELECT count(*) FROM messages").fetchone()[0] >= 100000:
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
        if self.db.execute("SELECT count(*) FROM events").fetchone()[0] >= 100000:
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

        Message tombstones are intentionally never expired: server replay retention
        is not established. Their hard cap applies backpressure, not silent loss.
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

    def close(self):
        self.db.close()
        self._lock.close()
