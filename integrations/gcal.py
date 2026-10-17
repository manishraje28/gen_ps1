"""Real Google Calendar tools for the live demo (dedicated demo account only).

Same names and signatures as demo/env.py (create_calendar_event, list_calendar_events), same
"preview then commit" model as integrations/gmail.py:
  * create_calendar_event always stages the event in ``env.calendar`` first (what the gate's
    shadow run diffs); the API is called only when that list is the one bound with :func:`attach`.
    A shadow deep copy is a different object, so it can never create an event or send an invite.
  * invitees outside GMAIL_ALLOWED_DOMAINS are refused right before the API call.

Uses the Gmail token (``python -m integrations.gmail --consent`` asks for the calendar scope too).
Without that scope in the token, :func:`available` is False and the simulated calendar is used.
"""
from __future__ import annotations

import threading
import time

import re
from datetime import datetime, timedelta
from typing import Annotated, Any

from agentdojo.functions_runtime import Depends

from demo import env as sim
from demo.env import Event
from integrations import gmail

_service: Any = None
_local = threading.local()
_live_ids: set[int] = set()


def set_service(service: Any) -> None:
    global _service
    _service = service


def available() -> bool:
    return _service is not None or (gmail.credentials_file().exists() and gmail.CALENDAR_SCOPE in gmail.granted_scopes())


def service() -> Any:
    """One client per thread: googleapiclient's httplib2 transport is not thread-safe, and the two lanes
    run in parallel (a shared client garbles TLS: SSLError WRONG_VERSION_NUMBER)."""
    if _service is not None:  # injected (tests)
        return _service
    svc = getattr(_local, "service", None)
    if svc is None:
        from googleapiclient.discovery import build

        svc = _local.service = build("calendar", "v3", credentials=gmail.credentials(), cache_discovery=False)
    return svc


def attach(env: Any) -> Any:
    if available():
        _live_ids.add(id(env.calendar))
    return env


def is_live(calendar: list) -> bool:
    return id(calendar) in _live_ids


def _when(date: str) -> dict:
    """"2026-10-16" -> all-day; "2026-10-16 15:00" -> one hour from then (IST)."""
    date = date.strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})", date)
    if m:
        start = datetime.fromisoformat(f"{m[1]}T{int(m[2].split(':')[0]):02d}:{m[2].split(':')[1]}")
        fmt = "%Y-%m-%dT%H:%M:00"
        return {"start": {"dateTime": start.strftime(fmt), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": (start + timedelta(hours=1)).strftime(fmt), "timeZone": "Asia/Kolkata"}}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        day = datetime.fromisoformat(date)
        return {"start": {"date": date}, "end": {"date": (day + timedelta(days=1)).strftime("%Y-%m-%d")}}
    raise ValueError(f"Date must look like 2026-10-16 or 2026-10-16 15:00, got {date!r}")


def to_event(item: dict) -> Event:
    start = item.get("start", {})
    when = start.get("dateTime", start.get("date", ""))[:16].replace("T", " ")
    return Event(id=item["id"], title=item.get("summary", ""), date=when,
                 participants=[a["email"] for a in item.get("attendees", []) or [] if a.get("email")],
                 description=item.get("description", "") or "")


def _upcoming() -> list[Event]:
    now = datetime.utcnow().isoformat() + "Z"
    items = service().events().list(calendarId="primary", timeMin=now, maxResults=20,
                                    singleEvents=True, orderBy="startTime").execute().get("items", [])
    return [to_event(i) for i in items]


_snapshot: tuple[float, list[Event]] = (0.0, [])


def snapshot(ttl: float = 30.0) -> list[Event]:
    """Upcoming events for the UI's calendar panel, cached briefly like gmail.snapshot."""
    global _snapshot
    if time.monotonic() - _snapshot[0] > ttl:
        _snapshot = (time.monotonic(), _upcoming())
    return _snapshot[1]


def list_calendar_events(calendar: Annotated[list[Event], Depends("calendar")]) -> list[Event]:
    """Lists upcoming calendar events (id, title, date, participants, description)."""
    return _upcoming()


def create_calendar_event(calendar: Annotated[list[Event], Depends("calendar")], title: str, date: str, participants: list[str]) -> str:
    """Creates a calendar event and invites participants.

    :param title: event title.
    :param date: date (and optional time) of the event, e.g. "2026-10-16 15:00".
    :param participants: list of participant email addresses.
    """
    body = {"summary": title, **_when(date), "attendees": [{"email": p} for p in participants]}
    staged = Event(id=f"ev{len(calendar) + 1}", title=title, date=date, participants=participants)
    calendar.append(staged)
    if is_live(calendar):
        gmail.check_allowed(list(participants))
        created = service().events().insert(calendarId="primary", body=body, sendUpdates="all").execute()
        staged.id = created.get("id", staged.id)
    return f"Created '{title}' on {date} with {', '.join(participants)}"


CALENDAR_TOOLS = [list_calendar_events, create_calendar_event]


def tools(base: list | None = None) -> list:
    base = list(base or sim.PLAYGROUND_TOOLS)
    if not available():
        return base
    real = {f.__name__: f for f in CALENDAR_TOOLS}
    return [real.get(f.__name__, f) for f in base]
