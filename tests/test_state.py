from octo_channel_impl.state import Inbox


def test_compaction_preserves_cursor_and_unresolved_work(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    inbox.accept("uncertain", {"content": "retain"})
    inbox.mark("uncertain", "started")
    with inbox.db:
        inbox.db.executemany(
            "INSERT INTO events VALUES (?, 'message', 'acked')",
            ((i,) for i in range(1, 10001)),
        )
        inbox.db.execute("UPDATE events SET status='ack_pending' WHERE id=2")
        inbox.db.execute("UPDATE events SET status='unsupported' WHERE id=3")
    inbox.maintain()
    assert inbox.cursor() == 10000
    assert inbox.pending_acks() == [2]
    assert inbox.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    inbox.close()
    inbox = Inbox(path)
    assert inbox.cursor() == 10000
    assert inbox.db.execute("SELECT raw, status FROM messages").fetchone() == (
        '{"content": "retain"}',
        "uncertain",
    )
    inbox.close()


def test_dedup_survives_restart_and_unstarted_work_recovers(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    assert inbox.accept("bot:m1", {"payload": {"type": 1, "content": "hello"}})
    assert not inbox.accept("bot:m1", {})
    inbox.close()
    inbox = Inbox(path)
    assert len(inbox.pending()) == 1
    assert not inbox.accept("bot:m1", {})
    inbox.close()


def test_started_work_is_quarantined_after_restart(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    inbox.accept("bot:m1", {})
    inbox.mark("bot:m1", "started")
    inbox.close()
    inbox = Inbox(path)
    assert inbox.pending() == []
    assert inbox.db.execute("SELECT status FROM messages").fetchone()[0] == "uncertain"
    inbox.close()


def test_ack_retry_and_cursor_survive_restart(tmp_path):
    path = tmp_path / "inbox.sqlite"
    inbox = Inbox(path)
    inbox.event(12, "message")
    inbox.close()
    inbox = Inbox(path)
    assert inbox.cursor() == 12
    assert inbox.pending_acks() == [12]
    inbox.acked(12)
    assert inbox.pending_acks() == []
    inbox.close()
