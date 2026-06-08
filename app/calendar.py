# app/calendar.py — read events, free/busy, and (gated) create event.
from datetime import UTC, datetime, timedelta

from googleapiclient.discovery import build

from .config import app_tz, settings
from .google_auth import credentials_for


def _svc(user_id: str):
    return build("calendar", "v3", credentials=credentials_for(user_id))


def free_busy(user_id: str, start: datetime, end: datetime) -> list[dict]:
    """Return the busy blocks ({start, end}) on the primary calendar in [start, end]."""
    body = {
        "timeMin": _to_rfc3339(start),
        "timeMax": _to_rfc3339(end),
        "items": [{"id": "primary"}],
    }
    res = _svc(user_id).freebusy().query(body=body).execute()
    return res["calendars"]["primary"]["busy"]  # list of {start, end}


def list_events(user_id: str, start: datetime, end: datetime) -> list[dict]:
    """Return actual events (with titles) on the primary calendar in [start, end]."""
    res = (
        _svc(user_id)
        .events()
        .list(
            calendarId="primary",
            timeMin=_to_rfc3339(start),
            timeMax=_to_rfc3339(end),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )
    out = []
    for ev in res.get("items", []):
        start_field = ev.get("start", {})
        end_field = ev.get("end", {})
        out.append(
            {
                "summary": ev.get("summary", "(no title)"),
                # all-day events use "date"; timed events use "dateTime"
                "start": start_field.get("dateTime") or start_field.get("date"),
                "end": end_field.get("dateTime") or end_field.get("date"),
                "location": ev.get("location"),
                "attendees": [
                    a.get("email") for a in ev.get("attendees", []) if a.get("email")
                ],
            }
        )
    return out


def find_slots(
    user_id: str,
    duration_min: int = 30,
    days: int = 5,
    work_hours: tuple[int, int] | None = None,
) -> list[dict]:
    """Return conflict-free slots in working hours over the next N days.

    Working hours and slot times are in the configured app timezone, so a 9–18
    workday means 9am–6pm *local*, and returned slots carry the local offset.
    """
    s = settings()
    work_hours = work_hours or (s.work_hours_start, s.work_hours_end)
    tz = app_tz()
    now = datetime.now(tz)
    busy = free_busy(user_id, now, now + timedelta(days=days))
    return compute_open_slots(busy, duration_min, days, work_hours, now=now, tz=tz)


def compute_open_slots(
    busy: list[dict],
    duration_min: int,
    days: int,
    work_hours: tuple[int, int],
    now: datetime | None = None,
    tz=None,
) -> list[dict]:
    """Walk each working day, subtract busy blocks, emit slots of `duration_min`.

    Days and working hours are interpreted in `tz` (defaults to UTC, which keeps
    the unit tests deterministic). Busy blocks are compared as absolute instants,
    so cross-timezone overlap is handled correctly.
    """
    tz = tz or UTC
    now = now or datetime.now(tz)
    now = now.astimezone(tz)
    start_hour, end_hour = work_hours
    duration = timedelta(minutes=duration_min)
    busy_intervals = [(_parse(b["start"]), _parse(b["end"])) for b in busy]

    slots: list[dict] = []
    for day_offset in range(days):
        day = (now + timedelta(days=day_offset)).date()
        cursor = datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
        day_end = datetime(day.year, day.month, day.day, end_hour, tzinfo=tz)
        # Don't propose times in the past.
        if cursor < now:
            cursor = _ceil_to_half_hour(now)
        while cursor + duration <= day_end:
            slot_end = cursor + duration
            if not _overlaps_any(cursor, slot_end, busy_intervals):
                # Emit in local time with offset (e.g. ...+05:30) for clarity.
                slots.append(
                    {"start": cursor.isoformat(), "end": slot_end.isoformat()}
                )
            cursor += timedelta(minutes=30)
    return slots


def create_event(
    user_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    attendees: list[str],
) -> dict:
    """Create an event. Requires calendar.events. Called only from the queue.

    A timeZone is attached so Google interprets the time correctly even if the
    incoming timestamp lacks an explicit offset.
    """
    tz_name = settings().timezone
    event = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": tz_name},
        "end": {"dateTime": end_iso, "timeZone": tz_name},
        "attendees": [{"email": a} for a in attendees],
    }
    return (
        _svc(user_id).events().insert(calendarId="primary", body=event).execute()
    )


# --- helpers ------------------------------------------------------------------

def _overlaps_any(start, end, intervals) -> bool:
    return any(start < b_end and b_start < end for b_start, b_end in intervals)


def _parse(s: str) -> datetime:
    """Parse an RFC3339 timestamp into an aware UTC datetime."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ceil_to_half_hour(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    if dt.minute == 0 or dt.minute == 30:
        return dt
    if dt.minute < 30:
        return dt.replace(minute=30)
    return (dt + timedelta(hours=1)).replace(minute=0)
