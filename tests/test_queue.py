# tests/test_queue.py — the approval gate routes correctly and only acts on approve.
#
# We stub the DB cursor and the Google clients so the test is hermetic: it
# verifies the *control flow* (gated calls happen only inside approve, only for
# pending rows, and recipient confirmation runs before a send).
import contextlib

from app import queue


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


@contextlib.contextmanager
def _fake_pg(cursor):
    yield cursor


def test_approve_unknown_action_returns_error(monkeypatch):
    cur = FakeCursor(rows=[None])  # SELECT finds nothing
    monkeypatch.setattr(queue, "pg", lambda: _fake_pg(cur))
    out = queue.approve("me", "missing-id")
    assert "error" in out


def test_approve_send_email_confirms_recipient_then_sends(monkeypatch):
    # SELECT returns one pending send_email; UPDATE returns nothing.
    cur = FakeCursor(
        rows=[("send_email", {"draft_id": "d1", "to": "sam@acme.com", "thread_id": "t1"})]
    )
    monkeypatch.setattr(queue, "pg", lambda: _fake_pg(cur))

    calls = {"confirm": None, "send": None}
    monkeypatch.setattr(
        queue, "confirm_recipient",
        lambda to, user_id=None, thread_id=None: calls.__setitem__("confirm", (to, thread_id)),
    )
    monkeypatch.setattr(
        queue.gmail, "send_draft",
        lambda user_id, draft_id: calls.__setitem__("send", draft_id) or {"id": draft_id},
    )

    out = queue.approve("me", "a1")
    assert out["executed"] == "send_email"
    assert calls["confirm"] == ("sam@acme.com", "t1")  # confirmed before send
    assert calls["send"] == "d1"


def test_approve_create_event_inserts(monkeypatch):
    cur = FakeCursor(
        rows=[
            (
                "create_event",
                {
                    "summary": "Sync",
                    "start": "2026-06-08T15:00:00Z",
                    "end": "2026-06-08T15:30:00Z",
                    "attendees": ["sam@acme.com"],
                },
            )
        ]
    )
    monkeypatch.setattr(queue, "pg", lambda: _fake_pg(cur))
    seen = {}
    monkeypatch.setattr(
        queue.calendar, "create_event",
        lambda *a: seen.update({"args": a}) or {"id": "evt1"},
    )
    out = queue.approve("me", "a2")
    assert out["executed"] == "create_event"
    assert seen["args"][1] == "Sync"


def test_reject_marks_rejected(monkeypatch):
    cur = FakeCursor(rows=[])
    monkeypatch.setattr(queue, "pg", lambda: _fake_pg(cur))
    out = queue.reject("a3")
    assert out == {"rejected": "a3"}
