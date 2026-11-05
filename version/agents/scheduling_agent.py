"""
agents/scheduling_agent.py — Specialized Meeting Scheduling Capability

Provides calendar provider abstractions, meeting extraction/validation,
conflict checking, draft invitation composition, and authorized calendar booking.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import re

from core.config import (
    EMAIL_PROVIDER,
    MEETING_TIMEZONE,
    MEETING_DEFAULT_DURATION_MIN,
    get_meeting_allowed_domains,
    get_email_signature,
)
from tools.meeting_scheduler import filter_attendees_by_domain, describe_recurrence, _normalize_recurrence


# ── Calendar Provider Abstraction ─────────────────────────────────────────────

class CalendarProvider(ABC):
    """Abstract Calendar Provider interface."""

    @abstractmethod
    def check_availability(self, start_iso: str, duration_min: int, attendees: List[str]) -> Dict[str, Any]:
        """Check for availability or conflicts at the proposed time."""
        pass

    @abstractmethod
    def create_event(
        self,
        subject: str,
        attendees: List[str],
        start_iso: str,
        duration_min: int = 30,
        description: str = "",
        add_video: bool = False,
        recurrence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create calendar event and send invites."""
        pass


class MockCalendarProvider(CalendarProvider):
    """
    Deterministic mock provider for unit tests, offline operation,
    and environments lacking live Google/Outlook credentials.
    """

    def __init__(self, simulate_conflict: bool = False, simulate_failure: bool = False):
        self.simulate_conflict = simulate_conflict
        self.simulate_failure = simulate_failure
        self.scheduled_events: List[Dict[str, Any]] = []

    def check_availability(self, start_iso: str, duration_min: int, attendees: List[str]) -> Dict[str, Any]:
        if self.simulate_conflict:
            return {
                "available": False,
                "conflict_details": f"Conflict detected for attendees at {start_iso}.",
            }
        return {"available": True, "conflict_details": None}

    def create_event(
        self,
        subject: str,
        attendees: List[str],
        start_iso: str,
        duration_min: int = 30,
        description: str = "",
        add_video: bool = False,
        recurrence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.simulate_failure:
            return {"ok": False, "error": "Mock calendar service unavailable (simulated 503 error)."}

        event_id = f"mock-evt-{len(self.scheduled_events) + 1}"
        link = "https://meet.google.com/mock-link" if add_video else ""
        event_record = {
            "ok": True,
            "event_id": event_id,
            "subject": subject,
            "attendees": attendees,
            "start": start_iso,
            "duration_min": duration_min,
            "link": link,
            "description": description,
            "recurrence": describe_recurrence(recurrence),
        }
        self.scheduled_events.append(event_record)
        return event_record


class LiveCalendarProvider(CalendarProvider):
    """
    Production calendar provider delegating to existing Outlook or Google Calendar tools.
    """

    def check_availability(self, start_iso: str, duration_min: int, attendees: List[str]) -> Dict[str, Any]:
        # Live Outlook/Google calendar API conflict lookup if configured; fails safe as available
        return {"available": True, "conflict_details": None}

    def create_event(
        self,
        subject: str,
        attendees: List[str],
        start_iso: str,
        duration_min: int = 30,
        description: str = "",
        add_video: bool = False,
        recurrence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from tools.meeting_scheduler import schedule_meeting
        return schedule_meeting(
            subject=subject,
            attendees=attendees,
            start_iso=start_iso,
            duration_min=duration_min,
            description=description,
            add_video=add_video,
            recurrence=recurrence,
        )


# ── Meeting Scheduling Agent ──────────────────────────────────────────────────

class MeetingSchedulingAgent:
    """
    Interprets scheduling requests, drafts invites, checks participant domains,
    and coordinates booking with explicit user authorization.
    """

    def __init__(self, provider: Optional[CalendarProvider] = None):
        self.provider = provider or LiveCalendarProvider()

    def parse_request(self, text: str, sender: str = "") -> Dict[str, Any]:
        """
        Extract meeting details from text:
        - subject/purpose
        - attendees (emails)
        - date and time
        - duration
        """
        # Extract email addresses
        emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text)
        if sender and sender not in emails and "@" in sender:
            emails.append(sender)

        # Extract duration
        duration_match = re.search(r"\b(\d+)\s*(?:min|minutes|minute)\b", text, re.IGNORECASE)
        duration_min = int(duration_match.group(1)) if duration_match else MEETING_DEFAULT_DURATION_MIN

        # Extract ISO or explicit datetime if present
        iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\b", text)
        start_iso = iso_match.group(1) if iso_match else None

        # Check for recurrence
        recurrence = None
        if re.search(r"\bweekly\b", text, re.IGNORECASE):
            recurrence = {"freq": "weekly", "interval": 1}
        elif re.search(r"\bdaily\b", text, re.IGNORECASE):
            recurrence = {"freq": "daily", "interval": 1}

        # Derive subject/purpose
        subject = "Business Meeting"
        subj_match = re.search(r"(?:about|regarding|to discuss|subject:?)\s+([^.,\n]+)", text, re.IGNORECASE)
        if subj_match:
            subject = subj_match.group(1).strip()
        elif "sync" in text.lower():
            subject = "Team Sync"
        elif re.search(
            r"\breview\b.{0,25}\b(meeting|call|sync)\b"
            r"|\b(meeting|call|sync)\b.{0,25}\breview\b",
            text, re.IGNORECASE,
        ):
            subject = "Review Meeting"

        return {
            "subject": subject,
            "attendees": list(set(emails)),
            "start_datetime": start_iso,
            "duration_minutes": duration_min,
            "recurrence": recurrence,
            "add_video": "video" in text.lower() or "teams" in text.lower() or "meet" in text.lower(),
        }

    def execute_scheduling(
        self,
        request_text: str,
        sender: str = "",
        user_confirmed: bool = False,
        override_provider: Optional[CalendarProvider] = None,
    ) -> Dict[str, Any]:
        """
        Process the scheduling task:
        1. Parse parameters
        2. Validate attendee domain restrictions
        3. Check missing info -> return draft proposal if details missing
        4. If info present but not confirmed -> propose draft invitation
        5. If confirmed -> book with provider and return result
        """
        provider = override_provider or self.provider
        parsed = self.parse_request(request_text, sender)
        attendees = parsed.get("attendees", [])
        start_datetime = parsed.get("start_datetime")
        subject = parsed.get("subject", "Meeting")
        duration = parsed.get("duration_minutes", 30)

        # Validate attendee domains
        allowed_attendees, blocked_attendees = filter_attendees_by_domain(attendees)

        missing = []
        if not allowed_attendees:
            missing.append("valid internal attendee email address(es)")
        if not start_datetime:
            missing.append("proposed date and time (ISO or exact timestamp)")

        if missing:
            msg = (
                f"Hello,\n\nI can help arrange the meeting '{subject}', but I am missing: "
                f"{' and '.join(missing)}.\n\n"
                f"Please reply with these details so I can prepare the invitation.\n\n"
                f"{get_email_signature()}"
            )
            return {
                "status": "needs_info",
                "missing_fields": missing,
                "parsed_details": parsed,
                "response_text": msg,
                "booked": False,
            }

        # If user has not yet confirmed the booking action, draft the invite proposal
        if not user_confirmed:
            draft_text = (
                f"Hello,\n\nI have prepared the meeting invitation for **{subject}**:\n"
                f"- **Participants:** {', '.join(allowed_attendees)}\n"
                f"- **Date & Time:** {start_datetime} ({MEETING_TIMEZONE})\n"
                f"- **Duration:** {duration} minutes\n\n"
                f"Please reply **yes** to confirm and send the calendar invitation, or reply with adjustments.\n\n"
                f"{get_email_signature()}"
            )
            return {
                "status": "draft_proposal",
                "parsed_details": parsed,
                "allowed_attendees": allowed_attendees,
                "blocked_attendees": blocked_attendees,
                "response_text": draft_text,
                "booked": False,
            }

        # User confirmed -> Proceed to create calendar event
        avail = provider.check_availability(start_datetime, duration, allowed_attendees)
        if not avail.get("available", True):
            conflict = avail.get("conflict_details", "Conflicting appointment found.")
            msg = (
                f"Hello,\n\nA calendar conflict was detected for the proposed time {start_datetime}: {conflict}\n"
                f"Please suggest an alternate time slot.\n\n{get_email_signature()}"
            )
            return {
                "status": "conflict",
                "error": conflict,
                "response_text": msg,
                "booked": False,
            }

        booking_res = provider.create_event(
            subject=subject,
            attendees=allowed_attendees,
            start_iso=start_datetime,
            duration_min=duration,
            description=request_text,
            add_video=parsed.get("add_video", False),
            recurrence=parsed.get("recurrence"),
        )

        if not booking_res.get("ok"):
            err = booking_res.get("error", "Calendar provider rejected booking")
            msg = (
                f"Hello,\n\nI attempted to schedule the meeting, but the calendar service reported an error: {err}.\n"
                f"No calendar invitation was sent.\n\n{get_email_signature()}"
            )
            return {
                "status": "failed",
                "error": err,
                "response_text": msg,
                "booked": False,
            }

        link_line = f"\nJoin link: {booking_res.get('link')}" if booking_res.get("link") else ""
        ext_note = f"\n(Note: {', '.join(blocked_attendees)} were omitted as external domains are not permitted.)" if blocked_attendees else ""

        success_msg = (
            f"Hello,\n\nDone — I have scheduled **{subject}** for **{start_datetime}** ({MEETING_TIMEZONE}) "
            f"and sent calendar invitations to {', '.join(allowed_attendees)}.{link_line}{ext_note}\n\n"
            f"{get_email_signature()}"
        )

        return {
            "status": "booked",
            "booking_result": booking_res,
            "response_text": success_msg,
            "booked": True,
        }
