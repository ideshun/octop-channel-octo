"""Inspect or maintain an Octo inbox without printing message contents."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from octo_channel_impl.state import Inbox, database_stats  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Existing inbox.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Read-only status; works while the bot is running")
    archive = sub.add_parser("archive", help="Stop bot first; retain every dedup key")
    archive.add_argument("--output", required=True, type=Path)
    downgrade = sub.add_parser(
        "prepare-downgrade", help="Stop bot; restore compact dedup keys before rollback"
    )
    downgrade.add_argument("--backup", required=True, type=Path)
    retry = sub.add_parser(
        "retry", help="Stop bot first; requeue verified uncertain work"
    )
    retry.add_argument("--key", required=True)
    retry.add_argument("--backup", required=True, type=Path)
    retry.add_argument("--confirm-no-side-effects", action="store_true", required=True)
    args = parser.parse_args(argv)
    inbox = None
    try:
        path = args.db.expanduser().resolve(strict=True)
        if args.command == "status":
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
            ) as db:
                print(json.dumps(database_stats(db, path), ensure_ascii=False))
            return 0
        inbox = Inbox(path)
        if args.command == "archive":
            count = inbox.archive(args.output.expanduser().absolute())
            print(json.dumps({"archived_messages": count, "dedup_keys_retained": True}))
        elif args.command == "prepare-downgrade":
            count = inbox.prepare_downgrade(args.backup.expanduser().absolute())
            print(json.dumps({"restored_dedup_rows": count}))
        else:
            inbox.retry_uncertain(args.key, args.backup.expanduser().absolute())
            print(json.dumps({"requeued": True}))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        # Avoid echoing rows, payloads, keys or environment configuration.
        print(f"State operation failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    finally:
        if inbox is not None:
            inbox.close()


if __name__ == "__main__":
    raise SystemExit(main())
