"""
skills/email_skill/email_client.py — Email provider router

Dispatches to the configured email backend (EMAIL_PROVIDER in .env):
    outlook → outlook_client.py  (Microsoft Graph / O365)
    gmail   → gmail_client.py    (Gmail API)

Both backends expose the same interface, so the rest of Sally imports the
provider-neutral names from here and never needs to know which is active.
"""

from core.config import EMAIL_PROVIDER

if EMAIL_PROVIDER == "gmail":
    from skills.email_skill.gmail_client import (
        get_gmail_service as get_email_service,
        list_unread_messages,
        list_messages_since,
        get_message_detail,
        get_message_by_id,
        download_attachments,
        download_thread_attachments,
        get_unsupported_attachment_names,
        has_real_attachment,
        send_reply,
        mark_as_read,
        get_account_address,
        get_or_create_label,
        apply_label_and_archive,
        was_reply_already_sent,
    )
    print("[EMAIL] Provider: Gmail")
else:
    from skills.email_skill.outlook_client import (
        get_outlook_service as get_email_service,
        list_unread_messages,
        list_messages_since,
        get_message_detail,
        get_message_by_id,
        download_attachments,
        download_thread_attachments,
        get_unsupported_attachment_names,
        has_real_attachment,
        send_reply,
        mark_as_read,
        get_account_address,
        get_or_create_label,
        apply_label_and_archive,
        was_reply_already_sent,
    )
    print("[EMAIL] Provider: Outlook")
