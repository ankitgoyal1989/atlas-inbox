# tests/test_calendar.py — the deterministic slot logic, tested directly.
from datetime import UTC, datetime

from app.calendar import _overlaps_any, _parse, compute_open_slots


def _now():
    # A fixed Monday 08:00 UTC so generated slots are stable.
    return datetime(2026, 6, 8, 8, 0, tzinfo=UTC)


def test_compute_open_slots_skips_busy_blocks():
    busy = [{"start": "2026-06-08T09:00:00Z", "end": "2026-06-08T12:00:00Z"}]
    slots = compute_open_slots(
        busy, duration_min=30, days=1, work_hours=(9, 18), now=_now()
    )
    intervals = [(_parse(b["start"]), _parse(b["end"])) for b in busy]
    # Every returned slot must be conflict-free.
    for s in slots:
        assert not _overlaps_any(_parse(s["start"]), _parse(s["end"]), intervals)
    # And there must be availability after the busy block ends.
    assert any(_parse(s["start"]).hour >= 12 for s in slots)


def test_compute_open_slots_respects_work_hours():
    slots = compute_open_slots([], duration_min=60, days=1, work_hours=(9, 18), now=_now())
    for s in slots:
        start = _parse(s["start"])
        end = _parse(s["end"])
        assert start.hour >= 9
        assert end.hour <= 18


def test_compute_open_slots_no_past_times():
    # now is 08:00; first slot should not start before work hours / now.
    slots = compute_open_slots([], duration_min=30, days=1, work_hours=(9, 18), now=_now())
    assert slots
    assert _parse(slots[0]["start"]) >= _now()


def test_overlaps_any():
    a = _parse("2026-06-08T10:00:00Z")
    b = _parse("2026-06-08T10:30:00Z")
    busy = [(_parse("2026-06-08T10:15:00Z"), _parse("2026-06-08T11:00:00Z"))]
    assert _overlaps_any(a, b, busy) is True
    free = [(_parse("2026-06-08T11:00:00Z"), _parse("2026-06-08T12:00:00Z"))]
    assert _overlaps_any(a, b, free) is False
