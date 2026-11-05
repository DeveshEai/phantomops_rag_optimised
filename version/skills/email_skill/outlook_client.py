"""
skills/email_skill/outlook_client.py — Microsoft Graph API Authentication & Helpers

Authentication uses 3 environment variables:
    OUTLOOK_CLIENT_ID
    OUTLOOK_CLIENT_SECRET
    OUTLOOK_TENANT_ID

To generate the initial token, run:
    python skills/email_skill/setup_outlook_oauth.py
"""

import base64
import os
from O365 import Account, FileSystemTokenBackend

from skills.email_skill.html_formatter import format_reply_html

_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB hard cap

# We store the token in the project root
TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'outlook_token.txt')

def get_outlook_service():
    """
    Build an authenticated Outlook API service from environment variables.
    Requires running setup_outlook_oauth.py first to generate the token.
    """
    from core.config import OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID

    if not all([OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID]):
        raise EnvironmentError(
            "Outlook credentials missing in .env.\n"
            "Run: python skills/email_skill/setup_outlook_oauth.py to generate them."
        )

    credentials = (OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET)
    
    token_backend = FileSystemTokenBackend(
        token_path=os.path.dirname(TOKEN_PATH), 
        token_filename='outlook_token.txt'
    )
    
    account = Account(credentials, tenant_id=OUTLOOK_TENANT_ID, token_backend=token_backend)

    if not account.is_authenticated:
        raise PermissionError("O365 account is not authenticated. Please run setup_outlook_oauth.py first.")

    return account.mailbox()


def get_outlook_account():
    """Return the authenticated O365 Account (for calendar/schedule access).

    Calendar operations need the Calendars.ReadWrite scope on the app
    registration and in the saved token — re-run setup_outlook_oauth.py after
    adding that permission in Azure if scheduling returns a permission error.
    """
    from core.config import OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID

    credentials = (OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET)
    token_backend = FileSystemTokenBackend(
        token_path=os.path.dirname(TOKEN_PATH),
        token_filename='outlook_token.txt',
    )
    account = Account(credentials, tenant_id=OUTLOOK_TENANT_ID, token_backend=token_backend)
    if not account.is_authenticated:
        raise PermissionError("O365 account is not authenticated. Please run setup_outlook_oauth.py first.")
    return account

# ── Message Reading ────────────────────────────────────────────────────────────

def list_unread_messages(mailbox, limit: int = 50) -> list:
    """Return unread Inbox messages, OLDEST first, via a server-side unread filter.

    Previously this fetched the 25 most-recent messages and filtered to unread in
    Python — so unread mail that arrived while Sally was offline could fall outside
    that 25-message window and never be processed. Querying `isRead == false`
    server-side and ordering oldest-first guarantees the backlog drains in order
    (each poll handles up to `limit`, marks them read, the next poll takes the rest).
    """
    inbox = mailbox.inbox_folder()
    qb = inbox.new_query()
    q = qb.equals("isRead", False)
    try:
        q = q & qb.orderby(("receivedDateTime", True))
    except Exception:
        pass  # ordering is best-effort; the unread filter is what matters
    return list(inbox.get_messages(limit=limit, query=q))


def list_messages_since(mailbox, since_epoch: int, limit: int = 50) -> list:
    """Return Inbox messages received at/after `since_epoch` (read OR unread), OLDEST first.

    Used for downtime recovery: catches mail that arrived while Sally was offline
    even if it has since been opened/marked read. Dedup at the caller prevents
    re-replying to already-handled mail.
    """
    from datetime import datetime, timezone
    since_dt = datetime.fromtimestamp(int(since_epoch), tz=timezone.utc)
    inbox = mailbox.inbox_folder()
    qb = inbox.new_query()
    q = qb.greater_equal("receivedDateTime", since_dt)
    try:
        q = q & qb.orderby(("receivedDateTime", True))
    except Exception:
        pass
    return list(inbox.get_messages(limit=limit, query=q))

def get_message_detail(msg) -> dict:
    """
    Extracts relevant details from an O365 Message object.
    Returns: {id, thread_id, sender, subject, body, body_raw, message_id_header, references}
    body     — plain text of the user's new text only (what get_body_text returns)
    body_raw — full raw HTML including the quoted thread; used for model detection
    """
    def _emails(recipients):
        out = []
        for r in (recipients or []):
            addr = getattr(r, "address", None)
            if addr:
                out.append(addr.lower())
        return out

    def _body_to_text(body_obj) -> str:
        """Return text from O365's body shapes without assuming `.text` exists."""
        if body_obj is None:
            return ""
        if isinstance(body_obj, str):
            return body_obj
        for attr in ("text", "content", "value"):
            val = getattr(body_obj, attr, None)
            if val:
                return str(val)
        return str(body_obj) if body_obj else ""

    body_obj = getattr(msg, "body", None)

    # IMPORTANT: get_body_text() returns the FULL plain-text body INCLUDING the
    # quoted reply chain (it does NOT strip it). On a Reply-All the whole prior
    # conversation would otherwise land in `body` and pollute classification
    # (e.g. old meeting text triggering a false schedule). Split it: the NEW text
    # is the query (used for decisions); the quoted thread is kept as context.
    from skills.email_skill.gmail_client import _split_quoted
    try:
        _plain = msg.get_body_text() or ""
    except AttributeError as e:
        # Some Graph/O365 messages can arrive without a body payload; O365's
        # get_body_text then raises "'NoneType' object has no attribute 'text'".
        # Treat those as empty/odd messages so the poller can mark/skip them.
        print(f"[Outlook] Could not read plain body for {getattr(msg, 'object_id', '(no id)')}: {e}")
        _plain = _body_to_text(body_obj)
    _new, _quoted = _split_quoted(_plain)
    _raw = _body_to_text(body_obj)

    return {
        "id": msg.object_id,
        "thread_id": msg.conversation_id,
        "sender": msg.sender.address if msg.sender else "(unknown)",
        "subject": msg.subject or "(no subject)",
        "body": (_new or _plain).strip(),
        "body_raw": _raw,
        "body_quoted": _quoted,   # quoted thread → background context only
        "message_id_header": msg.object_id,
        "references": "",
        "received_ts": int(msg.received.timestamp()) if getattr(msg, "received", None) else 0,
        "to": _emails(getattr(msg, "to", None)),
        "cc": _emails(getattr(msg, "cc", None)),
    }

def get_message_by_id(mailbox, msg_id: str):
    """Fetch a specific message by its object_id so we can reply to it."""
    return mailbox.get_message(msg_id)


def get_account_address(mailbox=None) -> str:
    """The Outlook mailbox's own address (used for To/Cc role detection)."""
    try:
        user = get_outlook_account().get_current_user_data()
        if user is None:
            return ""
        return (getattr(user, "mail", None) or getattr(user, "user_principal_name", "") or "").lower()
    except Exception:
        return ""

# ── Message Writing ────────────────────────────────────────────────────────────

def download_attachments(msg, save_dir: str) -> list:
    """
    Download all PDF, PPTX, DOCX attachments from a message to save_dir.
    Returns a list of absolute file paths for the downloaded files.
    """
    from tools.file_reader import SUPPORTED_EXTENSIONS
    os.makedirs(save_dir, exist_ok=True)
    paths = []

    def _safe_name(name: str) -> str:
        cleaned = (name or "attachment").replace("/", "-").replace("\\", "-")
        return cleaned.strip() or "attachment"

    def _unique_path(name: str) -> str:
        safe_name = _safe_name(name)
        base, ext = os.path.splitext(safe_name)
        dest = os.path.join(save_dir, safe_name)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(save_dir, f"{base}_{counter}{ext}")
            counter += 1
        return dest

    def _content_bytes(att):
        content = getattr(att, "content", None)
        if content:
            if isinstance(content, bytes):
                return content
            if isinstance(content, str):
                return base64.b64decode(content)

        attachment_id = getattr(att, "attachment_id", None)
        if attachment_id and getattr(msg, "object_id", None):
            try:
                url = msg.build_url(f"/messages/{msg.object_id}/attachments/{attachment_id}")
                response = msg.con.get(url)
                if response:
                    detail = response.json()
                    detail_content = detail.get("contentBytes") or detail.get("content_bytes")
                    if detail_content:
                        return base64.b64decode(detail_content)
            except Exception as fetch_err:
                print(f"[Attachment] Could not fetch attachment bytes for {getattr(att, 'name', 'attachment')}: {fetch_err}")

            try:
                value_url = msg.build_url(f"/messages/{msg.object_id}/attachments/{attachment_id}/$value")
                value_response = msg.con.get(value_url)
                if value_response and getattr(value_response, "content", None):
                    return value_response.content
            except Exception as value_err:
                print(f"[Attachment] Could not fetch attachment stream for {getattr(att, 'name', 'attachment')}: {value_err}")

        return None

    def _graph_attachment_bytes(attachment_id: str):
        try:
            value_url = msg.build_url(f"/messages/{msg.object_id}/attachments/{attachment_id}/$value")
            value_response = msg.con.get(value_url)
            if value_response and getattr(value_response, "content", None):
                return value_response.content
        except Exception as value_err:
            print(f"[Attachment] Could not fetch attachment stream {attachment_id}: {value_err}")
        return None

    def _download_via_graph() -> list:
        saved_paths = []
        if not getattr(msg, "object_id", None):
            return saved_paths

        try:
            url = msg.build_url(f"/messages/{msg.object_id}/attachments")
            while url:
                response = msg.con.get(url)
                if not response:
                    break

                payload = response.json()
                for item in payload.get("value", []):
                    name = item.get("name") or "attachment"
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in SUPPORTED_EXTENSIONS:
                        print(f"[Attachment] Skipping unsupported type: {name}")
                        continue
                    if item.get("isInline"):
                        print(f"[Attachment] Skipping inline attachment: {name}")
                        continue

                    content = None
                    encoded = item.get("contentBytes") or item.get("content_bytes")
                    if encoded:
                        content = base64.b64decode(encoded)
                    elif item.get("id"):
                        content = _graph_attachment_bytes(item["id"])

                    if not content:
                        print(f"[Attachment] No downloadable content found for: {name}")
                        continue

                    if len(content) > _MAX_ATTACHMENT_BYTES:
                        print(f"[Attachment] Skipping {name}: exceeds 25 MB limit ({len(content):,} bytes)")
                        continue

                    dest = _unique_path(name)
                    with open(dest, "wb") as f:
                        f.write(content)
                    if os.path.getsize(dest) > 0:
                        saved_paths.append(dest)
                        print(f"[Attachment] Saved: {os.path.basename(dest)} ({len(content)} bytes)")
                    else:
                        print(f"[Attachment] Skipped empty saved file: {name}")

                url = payload.get("@odata.nextLink")
        except Exception as e:
            print(f"[Attachment] Direct Graph download failed: {e}")

        return saved_paths

    try:
        print(
            "[Attachment] Checking message "
            f"{getattr(msg, 'object_id', '(no id)')} "
            f"has_attachments={getattr(msg, 'has_attachments', None)}"
        )

        paths = _download_via_graph()
        if paths:
            return paths

        if not getattr(msg, "has_attachments", False):
            print("[Attachment] Graph returned no supported attachments and has_attachments is false.")
            return paths

        if not msg.attachments.download_attachments():
            print("[Attachment] Outlook reported attachments, but download returned no data.")
            return paths
        print(f"[Attachment] Found {len(msg.attachments)} attachment(s).")

        for att in msg.attachments:
            name = getattr(att, "name", None) or "attachment"
            ext  = os.path.splitext(name)[1].lower()

            if ext not in SUPPORTED_EXTENSIONS:
                print(f"[Attachment] Skipping unsupported type: {name}")
                continue

            content = _content_bytes(att)
            if not content:
                print(f"[Attachment] No downloadable content found for: {name}")
                continue

            if len(content) > _MAX_ATTACHMENT_BYTES:
                print(f"[Attachment] Skipping {name}: exceeds 25 MB limit ({len(content):,} bytes)")
                continue

            dest = _unique_path(name)
            with open(dest, "wb") as f:
                f.write(content)
            if os.path.getsize(dest) > 0:
                paths.append(dest)
                print(f"[Attachment] Saved: {os.path.basename(dest)} ({len(content)} bytes)")
            else:
                print(f"[Attachment] Skipped empty saved file: {name}")

    except Exception as e:
        print(f"[Attachment] Download failed: {e}")
    return paths


def was_reply_already_sent(mailbox, conversation_id: str, after_epoch: int = 0) -> bool:
    """Best-effort: True if a reply for this conversation is ALREADY in Sent Items.

    Crash-recovery reconciliation: if Sally sent the reply but died before the DB
    recorded status='sent', a naive restart would resend it. Before resending we
    ask the mailbox itself (the real source of truth) whether a message in this
    conversation was sent AT/AFTER the inbound email's time. `after_epoch` scopes
    the check to THIS inbound so an earlier reply to a previous message in the
    same thread doesn't count. On any error returns False (caller then falls back
    to resending the saved draft — at-least-once, never worse than before)."""
    if not conversation_id:
        return False
    try:
        from datetime import datetime, timezone
        sent = mailbox.sent_folder()
        # conversationId + $orderby is rejected by Graph (too complex) — filter only.
        q = sent.new_query().equals("conversationId", conversation_id)
        msgs = list(sent.get_messages(limit=25, query=q))
        if not msgs:
            return False
        if after_epoch:
            cutoff = datetime.fromtimestamp(int(after_epoch), tz=timezone.utc)
            for m in msgs:
                ts = getattr(m, "sent", None) or getattr(m, "received", None) or getattr(m, "created", None)
                if ts and ts >= cutoff:
                    return True
            return False
        return True
    except Exception as e:
        print(f"[Outlook] Sent-folder reconcile check failed ({e}) — assuming not sent.")
        return False


def download_thread_attachments(mailbox, conversation_id: str, save_dir: str,
                                exclude_id: str = None, limit: int = 25) -> list:
    """Download supported attachments from the MOST RECENT earlier message in the
    same Outlook conversation that has any. Used when the current email references
    a file ("the attached file") that wasn't attached to this reply itself.

    Only finds messages in THIS mailbox — works when the agent was on the earlier
    thread messages (e.g. CC'd throughout); it cannot reach a message the agent
    never received.
    """
    if not conversation_id:
        return []
    try:
        from datetime import datetime, timezone
        inbox = mailbox.inbox_folder()
        # NOTE: Graph rejects $filter on conversationId combined with $orderby
        # ("restriction or sort order is too complex"), so filter only and sort
        # most-recent-first in Python.
        q = inbox.new_query().equals("conversationId", conversation_id)
        msgs = list(inbox.get_messages(limit=limit, query=q))
        msgs.sort(key=lambda m: getattr(m, "received", None)
                  or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    except Exception as e:
        print(f"[Outlook] Thread attachment lookup failed: {e}")
        return []
    for m in msgs:
        if exclude_id and getattr(m, "object_id", None) == exclude_id:
            continue
        if not getattr(m, "has_attachments", False):
            continue
        try:
            got = download_attachments(m, save_dir)
            if got:
                print(f"[Outlook] Pulled {len(got)} attachment(s) from an earlier message in the thread.")
                return got
        except Exception as e:
            print(f"[Outlook] Could not download an earlier thread attachment: {e}")
    return []


def get_unsupported_attachment_names(msg) -> list:
    """
    Return the names of non-inline attachments whose extension is NOT supported.
    Used to send a polite rejection when a user uploads a file Sally can't read.
    """
    from tools.file_reader import SUPPORTED_EXTENSIONS
    import os as _os
    names = []

    # Primary: Graph API listing (matches what download_attachments uses)
    try:
        if getattr(msg, "object_id", None):
            url = msg.build_url(f"/messages/{msg.object_id}/attachments")
            response = msg.con.get(url)
            if response:
                for item in response.json().get("value", []):
                    if item.get("isInline"):
                        continue
                    name = item.get("name") or ""
                    ext  = _os.path.splitext(name)[1].lower()
                    if ext and ext not in SUPPORTED_EXTENSIONS:
                        names.append(name)
                return names
    except Exception as e:
        print(f"[Attachment] Unsupported-check via Graph failed: {e}")

    # Fallback: O365 attachment objects
    try:
        for att in getattr(msg, "attachments", []) or []:
            name = getattr(att, "name", "") or ""
            ext  = _os.path.splitext(name)[1].lower()
            if ext and ext not in SUPPORTED_EXTENSIONS:
                names.append(name)
    except Exception:
        pass
    return names


def has_real_attachment(msg) -> bool:
    """True if the message has at least one NON-INLINE attachment — a real file the
    sender attached — as opposed to ONLY inline images (e.g. a signature logo),
    which Graph's `has_attachments` wrongly counts. Used so a stray inline logo is
    not mistaken for a failed file upload (which would wrongly trigger the
    'please re-send the file' reply and block the actual request)."""
    try:
        if getattr(msg, "object_id", None):
            response = msg.con.get(msg.build_url(f"/messages/{msg.object_id}/attachments"))
            if response:
                items = response.json().get("value", [])
                return any(not item.get("isInline") for item in items)
    except Exception as e:
        print(f"[Attachment] real-attachment check via Graph failed: {e}")
    # Fallback: can't reliably tell inline via O365 objects → treat any as real.
    try:
        return len(getattr(msg, "attachments", []) or []) > 0
    except Exception:
        return bool(getattr(msg, "has_attachments", False))


# ── Folder Management (for email routing) ─────────────────────────────────────
# Outlook's equivalent of Gmail labels: each agent gets a mail folder named
# after it, and routed emails are MOVED into that folder. In-memory cache so
# each folder name is resolved against the API only once per session.

_folder_cache: dict[str, str] = {}


def get_or_create_label(mailbox, label_name: str) -> str:
    """Find or create an Outlook mail folder by name. Returns the folder ID.

    Provider-neutral name (Gmail calls these labels) so run_agent.py can call
    it without knowing which backend is active.
    """
    key = label_name.lower()
    if key in _folder_cache:
        return _folder_cache[key]

    # Look for an existing folder with this name (Graph matches case-insensitively)
    try:
        folder = mailbox.get_folder(folder_name=label_name)
        if folder:
            _folder_cache[key] = folder.folder_id
            return folder.folder_id
    except Exception as e:
        print(f"[Outlook] Failed to look up folder '{label_name}': {e}")

    # Folder doesn't exist — create it at the mailbox root
    try:
        created = mailbox.create_child_folder(label_name)
        if created:
            _folder_cache[key] = created.folder_id
            print(f"[Outlook] Created folder '{label_name}' (id={created.folder_id})")
            return created.folder_id
    except Exception as e:
        print(f"[Outlook] Failed to create folder '{label_name}': {e}")
    return ""


def apply_label_and_archive(msg, label_id: str):
    """Mark the message read and move it into the folder (Outlook's 'archive')."""
    if not label_id:
        return
    try:
        msg.mark_as_read()
        if not msg.move(label_id):
            raise RuntimeError("O365 move() returned False")
        print(f"[Outlook] Moved message {msg.object_id} to folder {label_id}")
    except Exception as e:
        print(f"[Outlook] Failed to move message {msg.object_id}: {e}")
        raise  # caller must not advance the watermark past an unfiled email


def mark_as_read(msg):
    """Mark the message as read so Sally doesn't reprocess it."""
    try:
        result = msg.mark_as_read()
        if not result:
            print(f"[WARNING] mark_as_read returned False for {msg.object_id}")
    except Exception as e:
        print(f"[WARNING] mark_as_read failed for {msg.object_id}: {e}")

def send_reply(msg, body: str, attachments: list[str] | None = None, cc: list[str] | None = None):
    """
    Send a reply email within the same thread.
    The O365 library handles threading automatically when calling .reply()
    Raises RuntimeError if the send fails so the caller can mark the draft as failed.

    Args:
        cc: Optional list of email addresses to CC on the reply.
            Used for reply-all when Sally is CC'd on a thread.
    """
    # IMPORTANT: reply to the SENDER ONLY (to_all=False). O365's default
    # reply(to_all=True) uses Graph's reply-all endpoint, which auto-populates
    # the reply with EVERY original recipient (incl. external/out-of-domain ones)
    # before our code runs — bypassing the domain guardrail. We instead control
    # the recipient list ourselves: sender in To, plus only the (already
    # domain-filtered) `cc` list the caller passes for reply-all coverage.
    reply = msg.reply(to_all=False)
    reply.body = format_reply_html(body)
    if cc:
        for addr in cc:
            reply.cc.add(addr)
    for path in attachments or []:
        if os.path.isfile(path):
            reply.attachments.add(path)
            print(f"[Email] Attached reply file: {path}")
        else:
            print(f"[Email] Reply attachment missing, skipping: {path}")
    result = reply.send()
    if not result:
        raise RuntimeError(
            f"send_reply failed — O365 reply.send() returned False for message {msg.object_id}. "
            "Check Outlook permissions (Mail.Send scope required)."
        )
