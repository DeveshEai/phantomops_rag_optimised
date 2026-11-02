"""
version/prompts/meeting_prompts.py — Calendar & Meeting Scheduling Extraction Prompts
"""

def get_meeting_extract_prompt(
    today_str: str,
    timezone_str: str,
    sender: str,
    default_duration_min: int = 30
) -> str:
    """Prompt to extract structured meeting parameters from natural language emails."""
    sender_line = (
        f'The person who sent this request ("me", "I", "myself", "us", "our team") is '
        f'{sender} — include this address in "attendees" whenever they refer to themselves.\n'
        if sender and "@" in sender else
        'If the sender refers to themselves ("me"/"I") but no sender address is known, '
        'add "requester" to "missing".\n'
    )
    return (
        "You extract meeting details from an email so an assistant can create a "
        f"calendar invite. TODAY IS {today_str}. All times are in the {timezone_str} timezone.\n\n"
        + sender_line +
        "Return ONLY a JSON object (no prose, no code fences) with these keys:\n"
        '  "subject": short meeting title (string),\n'
        '  "attendees": [email addresses — those that appear literally in the email PLUS '
        "the sender's address above when they refer to themselves; never invent unknown ones],\n"
        '  "start_datetime": local time "YYYY-MM-DDTHH:MM:SS" — resolve relative dates '
        "(tomorrow, next Monday) from today's date. NEVER guess the clock time: if the "
        'email states no time of day, set "start_datetime" to "" and list "time" in "missing",\n'
        f'  "duration_minutes": integer (default {default_duration_min} if unspecified),\n'
        '  "description": one-line agenda or empty string,\n'
        '  "add_video": true if they ask for an online/video/Meet/Teams call else false,\n'
        '  "recurrence": for a REPEATING meeting an object '
        '{"freq":"daily"|"weekly"|"monthly","interval":<int>,'
        '"days":[lowercase weekday names for weekly, e.g. "monday"],'
        '"count":<int or null>,"until":"YYYY-MM-DD" or null}; '
        'use null for a one-time meeting. Examples: "every Monday"→'
        '{"freq":"weekly","interval":1,"days":["monday"],"count":null,"until":null}; '
        '"daily for 5 days"→{"freq":"daily","interval":1,"days":[],"count":5,"until":null}. '
        'For weekly recurrence, start_datetime is the FIRST occurrence on/after today.\n'
        '  "missing": list any REQUIRED field you could not determine: "attendees" if no '
        'email address is present, "date" if no date/day is given, "time" if no clock time '
        'is given (do NOT guess a time). List both "date" and "time" if neither is given.\n'
    )


def get_meeting_cancel_prompt(today_str: str, timezone_str: str) -> str:
    """Prompt to extract meeting identification criteria for cancellation."""
    return (
        "Extract which meeting the sender wants to CANCEL. TODAY IS "
        f"{today_str} ({timezone_str}). Return ONLY JSON with:\n"
        '  "subject": keywords from the meeting title/topic, or "" if none given,\n'
        '  "date_from": "YYYY-MM-DDTHH:MM:SS" earliest plausible time, or "" ,\n'
        '  "date_to": "YYYY-MM-DDTHH:MM:SS" latest plausible time, or "".\n'
        'Resolve relative dates (tomorrow, next Monday) from today. For a single '
        'day use 00:00:00 to 23:59:59 of that day.'
    )

