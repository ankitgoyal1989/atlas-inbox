# worker/sync.py — inbox sync + nightly style-corpus refresh.
#
# Two entry points, selected by CLI flag:
#   python -m worker.sync                 → continuous inbox sync loop
#   python -m worker.sync --refresh-style → one-shot style-corpus rebuild (cron)
import argparse
import json
import sys
import time

from app import gmail
from app.config import settings
from app.db import pg
from app.style import index_sent_email

SYNC_INTERVAL_SECONDS = 120


def sync_inbox(user_id: str) -> int:
    """Fetch unread threads and upsert lightweight rows into `threads`."""
    unread = gmail.list_unread(user_id, max_results=25)
    count = 0
    with pg() as cur:
        for m in unread:
            cur.execute(
                """
                INSERT INTO threads (id, subject, last_from, snippet, unread, metadata, synced_at)
                VALUES (%s, %s, %s, %s, true, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    subject = EXCLUDED.subject,
                    last_from = EXCLUDED.last_from,
                    snippet = EXCLUDED.snippet,
                    unread = true,
                    synced_at = now()
                """,
                (
                    m["thread_id"],
                    m.get("subject"),
                    m.get("from"),
                    m.get("snippet"),
                    json.dumps({"message_id": m["id"]}),
                ),
            )
            count += 1
    return count


def refresh_style_corpus(user_id: str, max_threads: int = 100) -> int:
    """Embed the user's recent sent mail into the style corpus.

    Wipes and rebuilds so the corpus tracks the user's current voice. Reads from
    the Sent mailbox via the Gmail API.
    """
    from googleapiclient.discovery import build

    from app.google_auth import credentials_for

    svc = build("gmail", "v1", credentials=credentials_for(user_id))
    res = (
        svc.users()
        .messages()
        .list(userId="me", q="in:sent", maxResults=max_threads)
        .execute()
    )

    with pg() as cur:
        cur.execute("DELETE FROM style_corpus")

    count = 0
    for m in res.get("messages", []):
        thread_id = (
            svc.users().messages().get(userId="me", id=m["id"], format="metadata")
            .execute()["threadId"]
        )
        text = gmail.get_thread_text(user_id, thread_id)
        if text.strip():
            index_sent_email(text[:4000])  # cap per-doc size for embedding
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas Inbox background worker")
    parser.add_argument(
        "--refresh-style",
        action="store_true",
        help="One-shot: rebuild the style corpus from sent mail (cron job).",
    )
    args = parser.parse_args()
    user_id = settings().default_user_id

    if args.refresh_style:
        n = refresh_style_corpus(user_id)
        print(f"style corpus refreshed: {n} messages embedded")
        return

    # Continuous inbox sync loop.
    print("inbox sync worker started", flush=True)
    while True:
        try:
            n = sync_inbox(user_id)
            print(f"synced {n} unread threads", flush=True)
        except Exception as exc:  # keep the worker alive across transient errors
            print(f"sync error: {exc}", file=sys.stderr, flush=True)
        time.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
