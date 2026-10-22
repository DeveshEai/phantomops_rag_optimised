"""
run_agent.py — PhantomOps Email Agent Entry Point

Starts the email polling loop and routes each inbound email through the
Master LangGraph → Email Skill sub-graph.

Usage:
    python run_agent.py

Run alongside the Streamlit UI (if needed) in a separate terminal or
as the sole container process in docker-compose.
"""

import time
import sys
import os
import re

# Force UTF-8 stdout so special characters don't crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import (
    EMAIL_POLL_INTERVAL,
    EMAIL_WATERMARK_LAG_SEC,
    MAX_TRANSIENT_RETRIES,
    transient_retry_backoff_seconds,
    OUTLOOK_CLIENT_ID,
    OUTLOOK_CLIENT_SECRET,
    OUTLOOK_TENANT_ID,
    SENDER_ALLOWLIST,
    is_sender_allowed,
    get_allow_all_senders,
    get_enabled_models,
    get_effective_allowlist,
    get_agent_name,
    get_email_signature,
    get_known_agents,
    get_routing_model,
)
from skills.email_skill.email_client import (
    get_email_service,
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
from core.config import INBOX_ATTACHMENTS_DIR
from core.database.sqlite_db import init_db, draft_exists_for_message
from core.database.sqlite_db import (
    claim_message,
    get_config,
    set_config,
    get_resumable_drafts_for_message,
    mark_message_drafts_sent,
    update_draft_status,
    release_message,
    mark_message_handled,
    is_message_handled,
    thread_has_sent_reply,
    get_retry_state,
    set_retry_state,
    clear_retry_state,
)
from skills.email_skill.graph import build_email_skill
from core.master_graph import build_master_graph


# ── Console logging to a file ──────────────────────────────────────────────────

class _Tee:
    """Mirror a stream's writes to both the console and a log file."""

    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh

    def write(self, data):
        try:
            self._stream.write(data)
        except Exception:
            pass
        try:
            self._fh.write(data)
            self._fh.flush()
        except Exception:
            pass

    def flush(self):
        for t in (self._stream, self._fh):
            try:
                t.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _setup_file_logging():
    """Mirror all console output (stdout + stderr) to a dated log file under
    OUTPUT_DIR/logs, so a record survives after the window is closed. Best-effort:
    on any failure the console is left untouched. Returns the log path or None."""
    try:
        from core.config import OUTPUT_DIR
        from datetime import datetime, date
        log_dir = os.path.join(OUTPUT_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"agent-{date.today().isoformat()}.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)
        fh.write(f"\n===== session started {datetime.now().isoformat(timespec='seconds')} =====\n")
        sys.stdout = _Tee(sys.stdout, fh)
        sys.stderr = _Tee(sys.stderr, fh)
        return path
    except Exception as e:
        print(f"[Init] Could not start file logging ({e}) — console only.")
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _observe_message(thread_id: str, sender: str, body: str):
    """Observer mode: silently store a thread message the agent received but did
    not act on (humans talking to each other while the agent is on the thread).
    Later invocations in the thread then have the full discussion as context."""
    try:
        from core.database.sqlite_db import (
            get_or_create_session, load_session_history, save_session_history, log_message,
        )
        session_id = get_or_create_session(thread_id, sender)
        content = f"[Observed — {sender} wrote (no reply sent)]\n{(body or '')[:4000]}"
        log_message(session_id, "user", content)
        history = load_session_history(session_id) or []
        history.append({"role": "user", "content": content})
        save_session_history(session_id, history)
        print(f"[Observe] Stored thread message from {sender} for context.")
    except Exception as e:
        print(f"[Observe] Failed to store observed message: {e}")


_FILE_REF_RE = re.compile(
    r"\b(attach(?:ed|ment)?|the file|the doc(?:ument)?|the pdf|the docx|the excel|"
    r"the spreadsheet|the sheet|the report|the deck|the proposal|the slides?|"
    r"the above file|enclosed)\b",
    re.IGNORECASE,
)


def _references_a_file(body: str) -> bool:
    """True if the email text refers to a file (so we should look for one)."""
    return bool(_FILE_REF_RE.search(body or ""))


def _reply_unsupported_format(msg, unsupported_names: list) -> bool:
    """Reply politely when the user uploads a file format Sally cannot read."""
    files = ", ".join(unsupported_names) if unsupported_names else "the attached file"
    body = (
        "Hello,\n\n"
        f"Thank you for your email. Unfortunately I am unable to process "
        f"{files} — that file format is not supported.\n\n"
        "Please re-send your file in one of these supported formats:\n"
        "- PDF (.pdf)\n"
        "- Word (.docx)\n"
        "- Excel (.xlsx)\n"
        "- CSV (.csv)\n"
        "- PowerPoint (.pptx)\n\n"
        "Once you share a supported file, I'll be glad to help.\n\n"
        f"{get_email_signature()}"
    )
    try:
        send_reply(msg, body)
        print(f"[Email] Sent unsupported-format reply.")
        return True
    except Exception as e:
        print(f"[Email] Failed to send unsupported-format reply: {e}")
        return False


def _resend_saved_drafts(msg, message_id: str) -> bool:
    """Resend saved drafts (pending OR failed) without re-running the agent pipeline.

    A prior attempt may have generated the reply — and committed side effects
    (calendar invite, task output, folder delivery) — before the send failed or
    the process crashed. Resending from the saved drafts avoids both regenerating
    the answer and replaying those side effects. Callers gate this on a Sent-Items
    reconciliation so a reply that DID go out is never sent twice.
    """
    drafts = get_resumable_drafts_for_message(message_id)
    if not drafts:
        return False

    non_error_drafts = [
        d for d in drafts
        if "error" not in (d.get("agents_used") or "").split(",")
    ]
    send_drafts = non_error_drafts or drafts

    divider = "\n\n" + ("─" * 60) + "\n\n"
    body = divider.join((d.get("ai_draft") or "").strip() for d in send_drafts if d.get("ai_draft")).strip()
    if not body:
        body = (
            "Hello,\n\nI completed your request earlier, but the reply failed to send. "
            "Please let me know if you'd like me to regenerate it.\n\n"
            f"{get_email_signature()}"
        )

    files = []
    for draft in send_drafts:
        for path in (draft.get("attachment_path") or "").split(";"):
            path = path.strip()
            if path and os.path.isfile(path) and path not in files:
                files.append(path)

    try:
        send_reply(msg, body, files)
        for draft in drafts:
            update_draft_status(draft["id"], "sent")
        print(f"[Email] Resent saved failed draft(s): {len(drafts)}")
        return True
    except Exception as e:
        print(f"[Email] Failed to resend saved draft(s): {e}")
        for draft in drafts:
            try:
                update_draft_status(draft["id"], "failed")
            except Exception:
                pass
        return False


def process_email(master_graph, detail: dict):
    """Invoke the master graph for one parsed email."""
    return master_graph.invoke({
        "skill": "email",
        "skill_input": {
            "thread_id":         detail["thread_id"],
            "sender":            detail["sender"],
            "subject":           detail["subject"],
            "body":              detail["body"],
            "body_raw":          detail.get("body_raw", ""),
            "body_quoted":       detail.get("body_quoted", ""),
            "message_id_header": detail["message_id_header"],
            "references":        detail["references"],
            # Original recipients — used by auto_send for reply-all CC
            "original_to":       detail.get("to", []),
            "original_cc":       detail.get("cc", []),
            # These are filled in by skill nodes:
            "session_id":        "",
            "history":           [],
            "inbox_attachments": detail.get("inbox_attachments", []),
            "attachment_status":  detail.get("attachment_status", "none"),
            "attachment_context": "",
            "answer":            "",
            "attachments":       [],
            "status":            "new",
            # Phase 2 — filled by resolve_task_node
            "task_id":           "",
            "task_number":       0,
            "task_label":        "",
            "task_intent":       "",
            "deliverable_type":   "general",
            # How many times this exact message already auto-retried a transient
            # provider overload (see MAX_TRANSIENT_RETRIES) — lets auto_send_node
            # know whether to defer again or give up and send an honest notice.
            "retry_attempt":     detail.get("retry_attempt", 0),
        },
        "skill_output": {},
    })


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    _log_path = _setup_file_logging()
    print("=" * 55)
    print("  PhantomOps — Email Agent")
    print("=" * 55)
    if _log_path:
        print(f"[Init] Console logs are also saved to: {_log_path}")

    # 1. Fail fast if required credentials are missing.
    # Sally is model-agnostic — at least ONE LLM provider key is required.
    # Email credentials depend on the active provider (Outlook vs Gmail).
    from core.config import EMAIL_PROVIDER, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_TOKEN_PATH
    if EMAIL_PROVIDER == "gmail":
        gmail_missing = [name for name, val in [
            ("GMAIL_CLIENT_ID",     GMAIL_CLIENT_ID),
            ("GMAIL_CLIENT_SECRET", GMAIL_CLIENT_SECRET),
        ] if not val]
        if gmail_missing:
            print(f"[FATAL] Missing required environment variables: {', '.join(gmail_missing)}")
            print("  -> Set them in your .env file and restart.")
            sys.exit(1)
        if not os.path.isfile(GMAIL_TOKEN_PATH):
            print(f"[FATAL] Gmail token not found at {GMAIL_TOKEN_PATH}.")
            print("  -> Run: python skills/email_skill/setup_gmail_oauth.py")
            sys.exit(1)
    else:
        outlook_missing = [name for name, val in [
            ("OUTLOOK_CLIENT_ID",     OUTLOOK_CLIENT_ID),
            ("OUTLOOK_CLIENT_SECRET", OUTLOOK_CLIENT_SECRET),
            ("OUTLOOK_TENANT_ID",     OUTLOOK_TENANT_ID),
        ] if not val]
        if outlook_missing:
            print(f"[FATAL] Missing required environment variables: {', '.join(outlook_missing)}")
            print("  -> Set them in your .env file and restart.")
            sys.exit(1)

    enabled = get_enabled_models()
    if not enabled:
        print("[FATAL] No LLM provider configured.")
        print("  -> Set at least one API key in your .env file:")
        print("       GROQ_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY")
        sys.exit(1)

    print(f"[Init] Active LLM providers: {', '.join(enabled)}")

    # 2. Sender allowlist status — show the EFFECTIVE list (dashboard overrides .env)
    eff_allow, allow_source = get_effective_allowlist()
    if eff_allow:
        print(f"[Init] Sender allowlist active — source: {allow_source} "
              f"({len(eff_allow)} entr{'y' if len(eff_allow) == 1 else 'ies'}): {', '.join(eff_allow)}")
    elif get_allow_all_senders():
        print("[WARNING] Sender allowlist is EMPTY and ALLOW_ALL_SENDERS is ON — the agent will reply "
              "to ANY sender on the internet. This is intentional open mode; set SENDER_ALLOWLIST "
              "(or sender_allowlist in agent_config.yaml) to restrict.")
    else:
        print("[WARNING] Sender allowlist is EMPTY — failing CLOSED: the agent will reply to NOBODY. "
              "Add SENDER_ALLOWLIST in .env (or sender_allowlist in agent_config.yaml) to whitelist "
              "senders, or set ALLOW_ALL_SENDERS=true to deliberately reply to everyone.")

    # 3. Initialise the SQLite database (idempotent — creates the DB file if absent)
    print("\n[Init] Initialising SQLite database...")
    try:
        init_db()
    except Exception as e:
        print(f"[FATAL] SQLite init failed: {e}")
        print("  -> Check that OUTPUT_DIR is writable.")
        sys.exit(1)

    # 4. SQLite is the only store — sessions, drafts, tasks, config, claims.
    from core.config import DATABASE_DIR, DB_FILENAME
    print(f"[Init] Storage: SQLite ({DATABASE_DIR}\\{DB_FILENAME}) — no DB server or Redis needed.")

    # 4b. Ensure the file delivery / intake folders exist (manager drop points).
    from core.config import DELIVERY_DIR, INTAKE_DIR
    os.makedirs(DELIVERY_DIR, exist_ok=True)
    os.makedirs(INTAKE_DIR, exist_ok=True)
    print(f"[Init] File delivery folder: {DELIVERY_DIR}")
    print(f"[Init] File intake folder:   {INTAKE_DIR}")

    # 5. RAG knowledge base — optional. Populated by dropping files in INTAKE_DIR
    # (auto-ingested, see the poll loop below) or via email/web "save to RAG"
    # commands (agents/assistant_agent.py). Needs chromadb + langchain-ollama
    # installed and Ollama running; if either is missing, ingestion/retrieval
    # no-op with a log line instead of failing the agent (see rag/embedding.py).
    print("[Init] RAG knowledge base: optional, populated via INTAKE_DIR / email / web-link ingestion.")

    # 6. Authenticate the configured email provider (Outlook or Gmail)
    print(f"[Init] Authenticating email provider: {EMAIL_PROVIDER}...")
    try:
        email_service = get_email_service()
        print(f"[Init] {EMAIL_PROVIDER.capitalize()} authenticated.")
    except Exception as e:
        print(f"[FATAL] {EMAIL_PROVIDER} authentication failed: {e}")
        print("  -> Run the matching setup script (setup_outlook_oauth.py / setup_gmail_oauth.py).")
        sys.exit(1)

    # 6b. Resolve Sally's own mailbox address — used by the CC guardrail to tell
    # whether she's a direct recipient (To) or just CC'd on a thread.
    agent_address = (get_account_address(email_service) or "").lower()
    cc_clf_model  = enabled[0] if enabled else "groq"
    print(f"[Init] Mailbox address: {agent_address or '(unknown)'} | CC guardrail: "
          f"{'on' if agent_address else 'off (address unknown)'}")

    # 7. Build LangGraph skill + master router
    email_skill  = build_email_skill(email_service)
    master_graph = build_master_graph(email_skill)

    print(f"\n[PhantomOps] Ready -- polling {EMAIL_PROVIDER.capitalize()} every {EMAIL_POLL_INTERVAL}s\n")

    # In-memory guard: message IDs processed or permanently skipped this run
    # Once an ID is in this set it will never be picked up again in this process,
    # even if mark_as_read fails and Outlook keeps showing it as unread.
    seen_ids: set = set()

    # Downtime-safe watermark: process every email received at/after this epoch,
    # read OR unread. Persisted in SQLite so mail that arrived while Sally was
    # offline (even if later opened/marked read) is still picked up on restart.
    # First ever run starts from "now" so the existing inbox history isn't replayed.
    import time as _time
    try:
        watermark = int(get_config("last_email_ts"))
        print(f"[Init] Resuming from email watermark {watermark} — will catch any mail since.")
    except (TypeError, ValueError):
        watermark = int(_time.time())
        set_config("last_email_ts", str(watermark))
        print(f"[Init] Email watermark initialised to now ({watermark}).")

    # Advance the watermark past a fully-handled message — but only while the
    # current batch is still clean. Messages are processed oldest-first, so if an
    # earlier one fails we stop advancing, leaving the watermark behind the failure
    # so the whole remainder is retried on the next poll/restart (never skipped).
    batch_ok = [True]

    # Holds the message id of the email currently being processed, so _handled()
    # can durably mark it handled (the loop sets this at the top of each iteration).
    _cur_msg = [""]

    def _handled(ts: int):
        nonlocal watermark
        # Terminal outcome → durably record this message as handled so it is never
        # reprocessed/re-replied after a restart (independent of the watermark, the
        # read flag, or whether it produced a 'sent' draft). _handled is only called
        # on terminal outcomes; retryable failures skip it (batch_ok=False instead).
        mark_message_handled(_cur_msg[0])
        if batch_ok[0] and ts and ts > watermark:
            watermark = ts
            set_config("last_email_ts", str(watermark))

    # Track the effective allowlist so live dashboard edits are visible in the logs.
    last_allowlist = eff_allow

    # RAG intake-folder auto-ingest: scanned on its own (coarser) interval, not
    # every poll cycle — see RAG_INTAKE_SCAN_INTERVAL_SEC in core/config.py.
    from core.config import RAG_INTAKE_SCAN_INTERVAL_SEC
    next_rag_scan_ts = 0  # due immediately on the first loop iteration

    # 6. Polling loop
    while True:
        try:
            # Surface dashboard-driven allowlist changes the moment they take effect.
            cur_allow, cur_src = get_effective_allowlist()
            if cur_allow != last_allowlist:
                shown = ', '.join(cur_allow) if cur_allow else '(empty — allow all)'
                print(f"[Allowlist] Updated via {cur_src}: {shown}")
                last_allowlist = cur_allow

            # RAG intake-folder auto-ingest — best-effort, must never break the
            # email poll loop (a bad file or missing optional RAG deps just skip).
            if _time.time() >= next_rag_scan_ts:
                try:
                    from rag.ingestion import scan_intake_folder
                    scan_intake_folder()
                except Exception as e:
                    print(f"[RAG] Intake scan failed ({e}) — will retry next interval.")
                next_rag_scan_ts = _time.time() + RAG_INTAKE_SCAN_INTERVAL_SEC

            # Capture the poll's start time BEFORE fetching, so we can advance the
            # watermark to it after a clean batch (robust forward progress even if
            # individual messages have no parseable received timestamp).
            _poll_started = int(_time.time())

            # Fetch from `watermark - lag`, not exactly `watermark`: mail delivery
            # is not monotonic, so a message stamped before the high-water mark can
            # surface only after it has advanced. The lookback re-scans a small
            # recent window (deduped durably by handled_messages, and in-process by
            # seen_ids) to catch it.
            fetch_from = max(0, watermark - EMAIL_WATERMARK_LAG_SEC)
            messages = list_messages_since(email_service, fetch_from)
            batch_ok[0] = True

            if messages:
                print(f"[Poll] {len(messages)} email(s) since watermark.")

                for msg in messages:
                    # O365 messages use object_id instead of id
                    msg_id = msg.object_id
                    _cur_msg[0] = msg_id

                    # Skip if already seen this session (in-memory guard)
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    # Cheap received timestamp from the message metadata (used to
                    # advance the watermark even if get_message_detail can't parse it).
                    _recv = getattr(msg, "received", None)
                    msg_ts = int(_recv.timestamp()) if _recv else 0

                    # Durable handled-gate: a message terminally handled in any prior
                    # run is NEVER reprocessed/re-replied after a restart — regardless
                    # of the watermark window or read/unread state. Downtime mail Sally
                    # never handled is not recorded, so it is still processed.
                    if is_message_handled(msg_id):
                        print(f"[Poll] Already handled in a previous run — skipping: {msg_id}")
                        _handled(msg_ts)
                        continue

                    # Tracks whether THIS process currently owns the cross-process
                    # claim, so the catch-all except below can release it on an
                    # unexpected crash instead of leaving it locked for the full
                    # 10-minute TTL (claim_message ttl_seconds=600).
                    claimed_message_id = None
                    try:
                        detail = get_message_detail(msg)
                        msg_ts = int(detail.get("received_ts") or msg_ts)

                        # Skip empty bodies (delivery receipts, etc.)
                        if not detail["body"].strip():
                            mark_as_read(msg)
                            _handled(msg_ts)
                            continue

                        # Sender allowlist: only configured senders may trigger Sally.
                        # Prevents an arbitrary inbound email from driving the pipeline.
                        if not is_sender_allowed(detail["sender"]):
                            print(f"[Poll] Sender not in allowlist — skipping: {detail['sender']}")
                            mark_as_read(msg)
                            _handled(msg_ts)
                            continue

                        # ── Thread-aware engagement ───────────────────────────
                        # STICKY THREAD: we already replied in this thread, so no
                        # name is needed — but each message must still be FOR the
                        # agent (vs humans talking to each other → observe).
                        sticky = thread_has_sent_reply(detail["thread_id"])
                        if sticky:
                            # Did the sender hit reply on the AGENT'S own email?
                            replying_to_agent = bool(agent_address) and (
                                agent_address in (detail.get("body_quoted") or "").lower()[:400]
                            )
                            from agents.assistant_agent import is_addressed_in_thread
                            if not is_addressed_in_thread(detail["body"], cc_clf_model, replying_to_agent):
                                # Mid-thread handoff? If another known agent is NAMED in
                                # the new text, confirm with the router and delegate it to
                                # that agent's label/folder — without storing it in our
                                # own context (a handoff is fully theirs).
                                known = get_known_agents()
                                handoff_to = ""
                                if known and any(
                                    re.search(rf"\b{re.escape(n.lower())}\b", detail["body"].lower())
                                    for n in known
                                ):
                                    from agents.email_router import classify_email_recipient
                                    addressed_to = classify_email_recipient(
                                        detail["body"], detail["subject"],
                                        get_agent_name(), known, get_routing_model()
                                    )
                                    if addressed_to != "none" and addressed_to.lower() != get_agent_name().lower():
                                        handoff_to = addressed_to
                                if handoff_to:
                                    try:
                                        label_id = get_or_create_label(email_service, handoff_to)
                                        if not label_id:
                                            raise RuntimeError(f"could not get/create label '{handoff_to}'")
                                        apply_label_and_archive(msg, label_id)
                                        print(f"[Route] Mid-thread handoff to '{handoff_to}' — delegated, not observed.")
                                        _handled(msg_ts)
                                    except Exception as e:
                                        print(f"[Route] Failed to file handoff for '{handoff_to}' ({e}) — will retry next poll.")
                                        seen_ids.discard(msg_id)
                                        batch_ok[0] = False
                                    continue
                                print(f"[Thread] Not addressed — observing: {detail['subject'][:50]}")
                                _observe_message(detail["thread_id"], detail["sender"], detail["body"])
                                mark_as_read(msg)
                                _handled(msg_ts)
                                continue
                            print("[Thread] Follow-up in our thread — engaging.")

                        # ── Agent name routing (LLM) — NEW threads only ───────
                        # Determine which agent the email is addressed to.
                        known = get_known_agents()
                        if known and not sticky:  # Only route when other agents are configured
                            from agents.email_router import classify_email_recipient
                            routing_provider = get_routing_model()
                            cur_agent = get_agent_name()
                            addressed_to = classify_email_recipient(
                                detail["body"], detail["subject"],
                                cur_agent, known, routing_provider
                            )

                            if addressed_to.lower() == cur_agent.lower():
                                pass  # Our agent → continue to pipeline
                            elif addressed_to != "none":
                                # Different agent → file it under that agent's label/folder and skip
                                try:
                                    label_id = get_or_create_label(email_service, addressed_to)
                                    if not label_id:
                                        raise RuntimeError(f"could not get/create label '{addressed_to}'")
                                    apply_label_and_archive(msg, label_id)
                                    print(f"[Route] Email addressed to '{addressed_to}' — labelled and skipped.")
                                    _handled(msg_ts)
                                except Exception as e:
                                    # Filing failed — do NOT advance the watermark, so this
                                    # email is retried next poll instead of being lost.
                                    print(f"[Route] Failed to file email for '{addressed_to}' ({e}) — will retry next poll.")
                                    seen_ids.discard(msg_id)
                                    batch_ok[0] = False
                                continue
                            else:
                                # No agent named → observe (store for context) and skip
                                print(f"[Route] No agent addressed — observing: {detail['subject'][:50]}")
                                _observe_message(detail["thread_id"], detail["sender"], detail["body"])
                                mark_as_read(msg)
                                _handled(msg_ts)
                                continue

                        # CC guardrail: if Sally is only CC'd (not a direct To recipient),
                        # reply ONLY when the message actually addresses/asks the assistant —
                        # so she doesn't reply-all on human-to-human threads she's CC'd into.
                        # (Sticky threads already passed the addressed-check above.)
                        to_l = detail.get("to", []) or []
                        cc_l = detail.get("cc", []) or []
                        if not sticky and agent_address and agent_address in cc_l and agent_address not in to_l:
                            from agents.assistant_agent import should_respond_when_cc
                            if not should_respond_when_cc(detail["body"], cc_clf_model):
                                print(f"[CC] Only CC'd and not addressed — observing, no reply: {detail['subject'][:50]}")
                                _observe_message(detail["thread_id"], detail["sender"], detail["body"])
                                mark_as_read(msg)
                                _handled(msg_ts)
                                continue
                            print("[CC] CC'd but addressed — responding.")

                        # DB-level deduplication: skip if we already saved a draft
                        # for this exact message_id (survives restarts and catches
                        # mail re-fetched after the watermark boundary).
                        if draft_exists_for_message(detail["message_id_header"]):
                            print(f"[Poll] Duplicate skipped — reply already sent for message {msg_id}")
                            mark_as_read(msg)
                            seen_ids.add(msg_id)  # suppress forever in this session even if mark_as_read fails
                            _handled(msg_ts)
                            continue

                        # Cross-process claim — prevents two Sally instances from both
                        # processing the same email. Claim auto-expires (10 min) so a
                        # crashed process doesn't permanently block reprocessing.
                        if not claim_message(detail["message_id_header"], ttl_seconds=600):
                            print(f"[Poll] Skipped — another instance is processing message {msg_id}")
                            seen_ids.discard(msg_id)   # let this process retry once the claim frees/expires
                            batch_ok[0] = False        # don't advance past mail another worker owns
                            continue
                        claimed_message_id = detail["message_id_header"]

                        # Crash/failure recovery. Unsent drafts (pending OR failed)
                        # for this message mean a PRIOR run already generated the
                        # reply — and may have SENT it before the process died or
                        # the DB recorded 'sent' (the duplicate-send window). Before
                        # doing anything, reconcile with the mailbox's Sent Items
                        # (the real source of truth):
                        #   • already sent  → mark drafts sent, skip (NO duplicate).
                        #   • not sent yet  → resend the SAVED draft (no regen, no
                        #                     replayed side effects like invites).
                        # On a Sent-check error we fall back to resending the saved
                        # draft — at-least-once, never worse than before.
                        resumable_drafts = get_resumable_drafts_for_message(detail["message_id_header"])
                        if resumable_drafts:
                            if was_reply_already_sent(email_service, detail["thread_id"], msg_ts):
                                print(f"[Email] Reply already in Sent Items — reconciling, not resending: {msg_id}")
                                marked = mark_message_drafts_sent(detail["message_id_header"])
                                print(f"[Email] Marked {marked} saved draft(s) as sent (no duplicate).")
                                mark_as_read(msg)
                                seen_ids.add(msg_id)
                                release_message(detail["message_id_header"])
                                _handled(msg_ts)
                                continue
                            print(f"[Email] Resuming interrupted send from saved draft(s) for message {msg_id}")
                            if _resend_saved_drafts(msg, detail["message_id_header"]):
                                mark_as_read(msg)
                                _handled(msg_ts)
                                print(f"[Done]  Resent saved draft to {detail['sender']}\n")
                            else:
                                seen_ids.discard(msg_id)
                                release_message(detail["message_id_header"])
                                batch_ok[0] = False
                            continue

                        # Transient-provider-overload backoff: a prior attempt on
                        # this exact message deferred (every enabled provider was
                        # momentarily overloaded — see LLMTransientError). Wait out
                        # its backoff window before spending another LLM call on it,
                        # instead of hammering an already-saturated provider every
                        # poll cycle.
                        _retry_state = get_retry_state(detail["message_id_header"])
                        if _retry_state and time.time() < _retry_state["next_try_ts"]:
                            wait_left = int(_retry_state["next_try_ts"] - time.time())
                            print(f"[Email] Cooling down after transient failure "
                                  f"(attempt {_retry_state['attempts']}/{MAX_TRANSIENT_RETRIES}, "
                                  f"retrying in ~{max(1, wait_left // 60)} min): {msg_id}")
                            seen_ids.discard(msg_id)
                            release_message(detail["message_id_header"])
                            batch_ok[0] = False
                            continue
                        detail["retry_attempt"] = _retry_state["attempts"] if _retry_state else 0

                        print(
                            f"[Email] From: {detail['sender']} "
                            f"| Subject: {detail['subject'][:60]}"
                        )

                        # Re-fetch the message to get the latest attachment state.
                        # Outlook sometimes reports has_attachments=False on the first poll
                        # because Exchange hasn't finished processing the attachment yet.
                        # A fresh fetch returns the accurate metadata.
                        try:
                            fresh_msg = get_message_by_id(email_service, msg_id)
                            if fresh_msg:
                                msg = fresh_msg
                        except Exception as _e:
                            print(f"[Email] Re-fetch failed ({_e}) — using cached message object.")

                        # Download any supported attachments (PDF/PPTX/DOCX/XLSX/CSV)
                        inbox_files = download_attachments(msg, INBOX_ATTACHMENTS_DIR)
                        # Is there a REAL (non-inline) attachment? A stray inline image
                        # (e.g. a signature logo) must NOT count as a file — otherwise a
                        # logo makes a plain "schedule a meeting" email look like a failed
                        # upload and triggers a wrong "please re-send the file" reply.
                        # Computed once (it makes a Graph call); a downloaded file is real.
                        _has_real_att = True if inbox_files else has_real_attachment(msg)
                        if inbox_files:
                            print(f"[Email] {len(inbox_files)} attachment(s) downloaded.")
                        elif _has_real_att:
                            # No supported file downloaded — check if it's an unsupported format
                            unsupported = get_unsupported_attachment_names(msg)
                            if unsupported:
                                print(f"[Email] Unsupported attachment(s): {unsupported} — sending rejection.")
                                if _reply_unsupported_format(msg, unsupported):
                                    mark_as_read(msg)
                                    _handled(msg_ts)
                                else:
                                    seen_ids.discard(msg_id)
                                    release_message(detail["message_id_header"])
                                    batch_ok[0] = False
                                continue
                            print("[Email] Message had attachments, but none were downloaded.")

                        # Thread-fetch fallback: the email references a file but none
                        # is attached to THIS message (e.g. a reply that dropped the
                        # original attachment). Look back through the same thread for
                        # the most recent attachment the agent received. Works when the
                        # agent was on the earlier messages (CC'd); it cannot reach a
                        # message the agent never received.
                        if (not inbox_files
                                and not _has_real_att
                                and _references_a_file(detail.get("body", ""))):
                            try:
                                tf = download_thread_attachments(
                                    email_service, detail.get("thread_id", ""),
                                    INBOX_ATTACHMENTS_DIR, detail.get("id"),
                                )
                                if tf:
                                    inbox_files = tf
                                    print(f"[Email] Reusing {len(tf)} attachment(s) from earlier in the thread.")
                            except Exception as _te:
                                print(f"[Email] Thread attachment fetch failed: {_te}")

                        detail["inbox_attachments"] = inbox_files
                        detail["attachment_status"] = (
                            "downloaded"
                            if inbox_files
                            else "download_failed"   # a REAL file was attached but didn't download
                            if _has_real_att
                            else "none"              # no real attachment (or only an inline logo)
                        )

                        result = process_email(master_graph, detail)
                        skill_out = (result or {}).get("skill_output") or {}
                        send_ok = bool(skill_out.get("send_ok", True))

                        if not send_ok:
                            if skill_out.get("failure_kind") == "transient":
                                # Every enabled provider was momentarily overloaded —
                                # no reply was sent on purpose. Record the attempt with
                                # a backoff so the next poll waits instead of hammering
                                # an already-saturated provider immediately.
                                attempts = detail.get("retry_attempt", 0) + 1
                                delay = transient_retry_backoff_seconds(attempts)
                                set_retry_state(detail["message_id_header"], attempts,
                                                int(time.time()) + delay)
                                print(f"[Email] All providers transiently overloaded — "
                                      f"auto-retrying in ~{delay // 60} min "
                                      f"(attempt {attempts}/{MAX_TRANSIENT_RETRIES}): {detail['sender']}")
                            else:
                                # Delivery failed — do NOT mark read or advance the watermark,
                                # so this email is retried on the next poll instead of being lost.
                                print(f"[Email] Send failed — will retry: {detail['sender']}")
                            seen_ids.discard(msg_id)
                            release_message(detail["message_id_header"])
                            batch_ok[0] = False
                        else:
                            # Done one way or another (success, or the retry budget
                            # was exhausted and an honest notice went out) — the
                            # backoff ledger for this message is no longer needed.
                            clear_retry_state(detail["message_id_header"])
                            mark_as_read(msg)
                            _handled(msg_ts)
                            if skill_out.get("llm_failed"):
                                print(f"[FAILED] No LLM provider completed this task — "
                                      f"failure notice sent to {detail['sender']}; task NOT marked completed.\n")
                            else:
                                print(f"[Done]  Processed email from {detail['sender']}\n")

                    except Exception as e:
                        print(f"[ERROR] Failed on message {msg_id}: {e}")
                        if claimed_message_id:
                            # An unexpected crash, not one of the specific failure
                            # branches above that already release the claim — free
                            # it now so THIS process (or another) can retry right
                            # away instead of waiting out the full 10-minute TTL.
                            release_message(claimed_message_id)
                        seen_ids.discard(msg_id)
                        batch_ok[0] = False  # stop advancing — retry this + later mail next poll

            # Robust forward progress: after a fully-clean poll, advance the
            # watermark to the poll's START time even if individual messages had no
            # parseable received timestamp. This stops the fetch window from growing
            # unbounded (and re-scanning ever-older mail) when per-message timestamps
            # can't move the watermark. Safe: mail that arrived during processing has
            # received >= _poll_started and is re-fetched on the next poll; the
            # durable handled_messages record prevents any re-reply.
            if batch_ok[0] and _poll_started > watermark:
                watermark = _poll_started
                set_config("last_email_ts", str(watermark))

        except Exception as e:
            print(f"[POLL ERROR] {e}")

        time.sleep(EMAIL_POLL_INTERVAL)


if __name__ == "__main__":
    main()
