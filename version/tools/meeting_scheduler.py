"""
tools/meeting_scheduler.py — Calendar meeting scheduling

Creates a real calendar event AND sends invitations to the attendees, on
whichever provider Sally is running (EMAIL_PROVIDER):
    gmail   → Google Calendar  (calendar v3 API, same OAuth token as Gmail)
    outlook → Outlook Calendar (O365 Account.schedule())

Public entry point:
    schedule_meeting(subject, attendees, start_iso, duration_min, description,
                     add_video) -> dict {ok, start, end, link, error}

Times are naive ISO strings ("2026-06-09T15:00:00") interpreted in
MEETING_TIMEZONE.
"""

from datetime import datetime, timedelta

from core.config import (
    EMAIL_PROVIDER, MEETING_TIMEZONE, MEETING_DEFAULT_DURATION_MIN,
)


def filter_attendees_by_domain(attendees, allowed_domains=None):
    """Split attendees into (allowed, blocked) by email domain. `allowed_domains`
    defaults to agent_config.yaml's meeting_attendee_domains; an empty list means
    no restriction. This is the guardrail that stops the agent inviting external
    addresses to a meeting."""
    if allowed_domains is None:
        from core.config import get_meeting_allowed_domains
        allowed_domains = get_meeting_allowed_domains()
    attendees = [a.strip() for a in (attendees or []) if a and "@" in a]
    if not allowed_domains:
        return attendees, []
    allowed = {d.lower().lstrip("@") for d in allowed_domains}
    kept, blocked = [], []
    for a in attendees:
        (kept if a.rsplit("@", 1)[-1].lower() in allowed else blocked).append(a)
    return kept, blocked


def _parse_start_end(start_iso: str, duration_min: int):
    """Return (start_dt, end_dt) as timezone-aware datetimes in MEETING_TIMEZONE."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(MEETING_TIMEZONE)
    start = datetime.fromisoformat(start_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    end = start + timedelta(minutes=int(duration_min or MEETING_DEFAULT_DURATION_MIN))
    return start, end


# ── Recurrence ────────────────────────────────────────────────────────────────
# Recurrence is described once as a provider-neutral dict and translated per
# backend: Google takes an iCalendar RRULE string, Outlook takes the O365
# Recurrence object (set_daily/set_weekly/set_monthly + a range).

_DAY_ABBR = {"monday": "MO", "tuesday": "TU", "wednesday": "WE", "thursday": "TH",
             "friday": "FR", "saturday": "SA", "sunday": "SU"}


def _normalize_recurrence(rec):
    """Validate/clean the recurrence dict from the extractor. Returns a dict with
    freq/interval/days/count/until, or None for a one-off meeting."""
    if not rec or not isinstance(rec, dict):
        return None
    freq = str(rec.get("freq") or "").strip().lower()
    if freq not in ("daily", "weekly", "monthly"):
        return None
    try:
        interval = max(1, int(rec.get("interval") or 1))
    except (TypeError, ValueError):
        interval = 1
    days = [str(d).strip().lower() for d in (rec.get("days") or [])
            if str(d).strip().lower() in _DAY_ABBR]
    count = rec.get("count") if isinstance(rec.get("count"), int) and rec.get("count") > 0 else None
    until = str(rec.get("until") or "").strip() or None
    return {"freq": freq, "interval": interval, "days": days, "count": count, "until": until}


def _rrule(rec) -> str:
    """Build a Google/iCalendar RRULE from a normalized recurrence dict."""
    parts = [f"FREQ={rec['freq'].upper()}", f"INTERVAL={rec['interval']}"]
    if rec["freq"] == "weekly" and rec["days"]:
        parts.append("BYDAY=" + ",".join(_DAY_ABBR[d] for d in rec["days"]))
    if rec["count"]:
        parts.append(f"COUNT={rec['count']}")
    elif rec["until"]:
        parts.append("UNTIL=" + rec["until"].replace("-", "") + "T235959Z")
    return "RRULE:" + ";".join(parts)


def describe_recurrence(rec) -> str:
    """Human-readable summary for the confirmation message ('weekly on Monday')."""
    if not rec:
        return "one-time"
    n = rec["interval"]
    if rec["freq"] == "weekly":
        days = ", ".join(d.capitalize() for d in rec["days"]) if rec["days"] else "that weekday"
        out = (f"weekly on {days}" if n == 1 else f"every {n} weeks on {days}")
    elif rec["freq"] == "daily":
        out = "daily" if n == 1 else f"every {n} days"
    else:
        out = "monthly" if n == 1 else f"every {n} months"
    if rec["count"]:
        out += f", {rec['count']} times"
    elif rec["until"]:
        out += f", until {rec['until']}"
    return out


def _apply_outlook_recurrence(event, rec, start_dt):
    """Configure recurrence on an O365 event from a normalized recurrence dict."""
    from datetime import date as _date
    rng = {"start": start_dt.date()}
    if rec["count"]:
        rng["occurrences"] = rec["count"]
    elif rec["until"]:
        try:
            rng["end"] = _date.fromisoformat(rec["until"])
        except ValueError:
            pass
    if rec["freq"] == "daily":
        event.recurrence.set_daily(rec["interval"], **rng)
    elif rec["freq"] == "weekly":
        days = rec["days"] or [start_dt.strftime("%A").lower()]
        event.recurrence.set_weekly(rec["interval"], days_of_week=days,
                                    first_day_of_week="sunday", **rng)
    elif rec["freq"] == "monthly":
        event.recurrence.set_monthly(rec["interval"], day_of_month=start_dt.day, **rng)


# ── Google Calendar ───────────────────────────────────────────────────────────

def _schedule_google(subject, attendees, start_dt, end_dt, description, add_video, rec=None):
    from skills.email_skill.gmail_client import get_calendar_service
    service = get_calendar_service()

    event = {
        "summary": subject,
        "description": description or "",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": MEETING_TIMEZONE},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": MEETING_TIMEZONE},
        "attendees": [{"email": e} for e in attendees],
    }
    if rec:
        event["recurrence"] = [_rrule(rec)]

    conf_version = 0
    if add_video:
        import uuid
        event["conferenceData"] = {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        conf_version = 1

    created = service.events().insert(
        calendarId="primary", body=event,
        sendUpdates="all",                 # email invites to attendees
        conferenceDataVersion=conf_version,
    ).execute()

    return {
        "link": created.get("hangoutLink") or created.get("htmlLink", ""),
        "event_id": created.get("id", ""),
    }


# ── Outlook Calendar ──────────────────────────────────────────────────────────

def _schedule_outlook(subject, attendees, start_dt, end_dt, description, add_video, rec=None):
    from skills.email_skill.outlook_client import get_outlook_account
    account = get_outlook_account()
    schedule = account.schedule()
    calendar = schedule.get_default_calendar()

    event = calendar.new_event()
    event.subject = subject
    event.body = description or ""
    event.start = start_dt
    event.end = end_dt
    for addr in attendees:
        event.attendees.add(addr)
    if add_video:
        try:
            event.is_online_meeting = True
        except Exception:
            pass
    if rec:
        _apply_outlook_recurrence(event, rec, start_dt)
    event.save()   # O365 sends invites to attendees on save

    link = ""
    try:
        if getattr(event, "online_meeting", None):
            link = event.online_meeting.get("joinUrl", "") if isinstance(event.online_meeting, dict) \
                   else getattr(event.online_meeting, "join_url", "")
    except Exception:
        link = ""
    return {"link": link, "event_id": getattr(event, "object_id", "")}


# ── Public entry point ────────────────────────────────────────────────────────

def schedule_meeting(subject: str, attendees: list, start_iso: str,
                     duration_min: int = 0, description: str = "",
                     add_video: bool = False, recurrence: dict = None) -> dict:
    """Create a calendar event + send invites. `recurrence` (optional) makes it a
    repeating series. Returns a result dict."""
    attendees = [a.strip() for a in (attendees or []) if a and "@" in a]
    # Guardrail: never invite addresses outside the allowed domain(s).
    attendees, _blocked_ext = filter_attendees_by_domain(attendees)
    if not attendees:
        return {"ok": False, "error": "no valid attendee email addresses"
                + (f" (all were outside the allowed domain: {', '.join(_blocked_ext)})" if _blocked_ext else "")}
    if not start_iso:
        return {"ok": False, "error": "no start date/time"}

    try:
        start_dt, end_dt = _parse_start_end(start_iso, duration_min)
    except Exception as e:
        return {"ok": False, "error": f"could not parse start time '{start_iso}': {e}"}

    rec = _normalize_recurrence(recurrence)
    subject = subject or "Meeting"
    try:
        if EMAIL_PROVIDER == "gmail":
            res = _schedule_google(subject, attendees, start_dt, end_dt, description, add_video, rec)
        else:
            res = _schedule_outlook(subject, attendees, start_dt, end_dt, description, add_video, rec)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "subject": subject,
        "attendees": attendees,
        "start": start_dt.strftime("%A, %d %B %Y at %I:%M %p %Z"),
        "end": end_dt.strftime("%I:%M %p"),
        "link": res.get("link", ""),
        "recurrence": describe_recurrence(rec),
    }


# ── Finding & cancelling meetings ─────────────────────────────────────────────
# Cancellation can hit ANY meeting in the mailbox, so it always goes through a
# search → confirm → cancel sequence (the caller stores the candidates between
# the request and the confirmation). If the mailbox owns the meeting (organiser)
# we cancel it for everyone and notify attendees; otherwise we can only decline
# our own seat.

def _window(date_from_iso: str, date_to_iso: str):
    """Resolve the search window. Defaults to now → +60 days."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(MEETING_TIMEZONE)
    start = datetime.fromisoformat(date_from_iso) if date_from_iso else datetime.now(tz)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if date_to_iso:
        end = datetime.fromisoformat(date_to_iso)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
    else:
        end = start + timedelta(days=60)
    return start, end


def _dedupe_by_event_id(items: list) -> list:
    """Collapse repeats sharing an event_id (e.g. many occurrences of one
    recurring series → one entry), preserving order."""
    seen, out = set(), []
    for it in items:
        eid = it.get("event_id")
        if eid in seen:
            continue
        seen.add(eid)
        out.append(it)
    return out


def _events_outlook(subject_query, start_dt, end_dt):
    from skills.email_skill.outlook_client import get_outlook_account
    calendar = get_outlook_account().schedule().get_default_calendar()
    # Use Graph's calendarView (start/end window) — a $filter on start/dateTime
    # fails because Graph stores it as a string, not a DateTimeOffset. calendarView
    # also expands recurring events. Subject is filtered client-side below.
    out = []
    for ev in calendar.get_events(limit=100, include_recurring=True,
                                  start_recurring=start_dt, end_recurring=end_dt):
        subj = ev.subject or ""
        if subject_query and subject_query.lower() not in subj.lower():
            continue
        try:
            attendees = [a.address for a in ev.attendees if getattr(a, "address", None)]
        except Exception:
            attendees = []
        # For a recurring meeting, target the SERIES MASTER so cancelling removes
        # the whole series (calendarView returns individual occurrences).
        master_id = getattr(ev, "series_master_id", None)
        out.append({
            "event_id": master_id or ev.object_id,
            "subject": subj or "(no subject)",
            "start": ev.start.strftime("%A, %d %B %Y at %I:%M %p") if ev.start else "(no time)",
            "attendees": attendees,
            "is_organizer": bool(getattr(ev, "is_organizer", True)),
            "is_recurring": bool(master_id),
        })
    return _dedupe_by_event_id(out)


def _events_google(subject_query, start_dt, end_dt):
    from skills.email_skill.gmail_client import get_calendar_service
    service = get_calendar_service()
    resp = service.events().list(
        calendarId="primary",
        timeMin=start_dt.isoformat(), timeMax=end_dt.isoformat(),
        q=subject_query or None, singleEvents=True, orderBy="startTime",
        maxResults=100,
    ).execute()
    out = []
    for ev in resp.get("items", []):
        start = ev.get("start", {})
        when = start.get("dateTime") or start.get("date") or ""
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).strftime(
                "%A, %d %B %Y at %I:%M %p") if when else "(no time)"
        except Exception:
            pass
        creator = (ev.get("creator") or {})
        # singleEvents=True returns instances; recurringEventId points at the
        # series — target it so cancelling removes the whole series.
        master_id = ev.get("recurringEventId")
        out.append({
            "event_id": master_id or ev.get("id", ""),
            "subject": ev.get("summary") or "(no subject)",
            "start": when,
            "attendees": [a.get("email") for a in ev.get("attendees", []) if a.get("email")],
            "is_organizer": bool(creator.get("self") or (ev.get("organizer") or {}).get("self")),
            "is_recurring": bool(master_id),
        })
    return _dedupe_by_event_id(out)


def find_meetings(subject_query: str = "", date_from_iso: str = "",
                  date_to_iso: str = "") -> list:
    """Return calendar events matching an optional subject substring within a
    date window (default now → +60 days). Each item: event_id, subject, start,
    attendees, is_organizer."""
    try:
        start_dt, end_dt = _window(date_from_iso, date_to_iso)
        if EMAIL_PROVIDER == "gmail":
            return _events_google((subject_query or "").strip(), start_dt, end_dt)
        return _events_outlook((subject_query or "").strip(), start_dt, end_dt)
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]


def cancel_meeting(event_id: str, comment: str = "") -> dict:
    """Cancel (organiser) or decline (attendee) a meeting by its event id.
    Returns {ok, action, subject, error}."""
    if not event_id:
        return {"ok": False, "error": "no event id"}
    try:
        if EMAIL_PROVIDER == "gmail":
            from skills.email_skill.gmail_client import get_calendar_service
            service = get_calendar_service()
            service.events().delete(
                calendarId="primary", eventId=event_id, sendUpdates="all",
            ).execute()
            return {"ok": True, "action": "cancelled", "subject": ""}

        from skills.email_skill.outlook_client import get_outlook_account
        calendar = get_outlook_account().schedule().get_default_calendar()
        ev = calendar.get_event(event_id)
        if ev is None:
            return {"ok": False, "error": "event not found"}
        if getattr(ev, "is_organizer", True):
            ok = ev.cancel_event(comment=comment or None)
            action = "cancelled"
        else:
            ok = ev.decline_event(comment=comment or None)
            action = "declined"
        return {"ok": bool(ok), "action": action, "subject": ev.subject or "(no subject)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Updating a meeting ────────────────────────────────────────────────────────
# Change an existing meeting in place (and its whole series, for recurring).
# `changes` keys are all optional: new_subject, new_start (ISO), new_duration_min,
# add_attendees[], remove_attendees[], new_description, add_video (bool).

def _update_outlook(event_id, changes):
    from datetime import timedelta
    from skills.email_skill.outlook_client import get_outlook_account
    cal = get_outlook_account().schedule().get_default_calendar()
    ev = cal.get_event(event_id)
    if ev is None:
        return {"ok": False, "error": "event not found"}

    applied = []
    if changes.get("new_subject"):
        ev.subject = changes["new_subject"]; applied.append("title")

    new_start, new_dur = changes.get("new_start"), changes.get("new_duration_min")
    if new_start:
        if new_dur:
            dur = int(new_dur)
        elif ev.start and ev.end:
            dur = max(1, int((ev.end - ev.start).total_seconds() // 60))
        else:
            dur = 30
        s, e = _parse_start_end(new_start, dur)
        ev.start, ev.end = s, e; applied.append("time")
    elif new_dur and ev.start:
        ev.end = ev.start + timedelta(minutes=int(new_dur)); applied.append("duration")

    if changes.get("new_description"):
        ev.body = changes["new_description"]; applied.append("agenda")

    add = [a.strip() for a in (changes.get("add_attendees") or []) if "@" in a]
    rem = [a.strip() for a in (changes.get("remove_attendees") or []) if "@" in a]
    if add:
        ev.attendees.add(add); applied.append(f"added {len(add)} attendee(s)")
    if rem:
        ev.attendees.remove(rem); applied.append(f"removed {len(rem)} attendee(s)")

    if changes.get("add_video") is not None:
        try:
            ev.is_online_meeting = bool(changes["add_video"]); applied.append("online meeting")
        except Exception:
            pass

    if not applied:
        return {"ok": False, "error": "no changes to apply"}
    ev.save()  # O365 emails the update to attendees
    return {"ok": True, "subject": ev.subject or "(no subject)",
            "start": ev.start.strftime("%A, %d %B %Y at %I:%M %p") if ev.start else "",
            "applied": applied}


def _update_google(event_id, changes):
    from datetime import datetime, timedelta
    from skills.email_skill.gmail_client import get_calendar_service
    service = get_calendar_service()
    ev = service.events().get(calendarId="primary", eventId=event_id).execute()
    body, applied = {}, []

    if changes.get("new_subject"):
        body["summary"] = changes["new_subject"]; applied.append("title")

    new_start, new_dur = changes.get("new_start"), changes.get("new_duration_min")
    if new_start:
        if new_dur:
            dur = int(new_dur)
        else:
            try:
                cs = datetime.fromisoformat(ev["start"]["dateTime"])
                ce = datetime.fromisoformat(ev["end"]["dateTime"])
                dur = max(1, int((ce - cs).total_seconds() // 60))
            except Exception:
                dur = 30
        s, e = _parse_start_end(new_start, dur)
        body["start"] = {"dateTime": s.isoformat(), "timeZone": MEETING_TIMEZONE}
        body["end"]   = {"dateTime": e.isoformat(), "timeZone": MEETING_TIMEZONE}
        applied.append("time")
    elif new_dur:
        try:
            cs = datetime.fromisoformat(ev["start"]["dateTime"])
            e = cs + timedelta(minutes=int(new_dur))
            body["end"] = {"dateTime": e.isoformat(), "timeZone": MEETING_TIMEZONE}
            applied.append("duration")
        except Exception:
            pass

    if changes.get("new_description"):
        body["description"] = changes["new_description"]; applied.append("agenda")

    add = [a.strip() for a in (changes.get("add_attendees") or []) if "@" in a]
    rem = {a.strip().lower() for a in (changes.get("remove_attendees") or []) if "@" in a}
    if add or rem:
        current = [a for a in ev.get("attendees", []) if a.get("email", "").lower() not in rem]
        have = {a.get("email", "").lower() for a in current}
        for a in add:
            if a.lower() not in have:
                current.append({"email": a})
        body["attendees"] = current
        if add: applied.append(f"added {len(add)} attendee(s)")
        if rem: applied.append(f"removed {len(rem)} attendee(s)")

    if not applied:
        return {"ok": False, "error": "no changes to apply"}
    updated = service.events().patch(
        calendarId="primary", eventId=event_id, body=body, sendUpdates="all",
    ).execute()
    return {"ok": True, "subject": updated.get("summary") or "(no subject)",
            "start": (updated.get("start", {}) or {}).get("dateTime", ""), "applied": applied}


def update_meeting(event_id: str, changes: dict) -> dict:
    """Apply changes to an existing meeting (whole series for recurring). Returns
    {ok, subject, start, applied[], error}."""
    if not event_id:
        return {"ok": False, "error": "no event id"}
    changes = dict(changes or {})
    # Guardrail: only attendees in the allowed domain(s) may be ADDED.
    if changes.get("add_attendees"):
        changes["add_attendees"], _ = filter_attendees_by_domain(changes["add_attendees"])
    try:
        if EMAIL_PROVIDER == "gmail":
            return _update_google(event_id, changes)
        return _update_outlook(event_id, changes)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
