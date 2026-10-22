"""
skills/email_skill/graph.py -- Email Skill LangGraph Sub-Graph (v3)

  create_session -> load_memory -> agent_answer -> auto_send -> persist

The agent_answer node delegates to the Supervisor (agents/supervisor.py)
which classifies intent and routes to specialised subagents:
  - Quick Reply       : simple email responses
  - Document Analyst  : attachment analysis
  - Research Agent    : web search for context
  - Proposal Architect: formal proposals and documents
  - PPT Strategist    : branded PowerPoint presentations

Dependencies (outlook_service) are injected at build time via closure.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from tools.file_reader import read_attachment_text


# ── State Schema ──────────────────────────────────────────────────────────────

class EmailSkillState(TypedDict):
    thread_id:          str    # Outlook conversation ID
    sender:             str    # From: header value
    subject:            str    # Subject: header value
    body:               str    # Plain-text NEW text of the latest email (quotes stripped)
    body_raw:           str    # Full raw HTML body (includes quoted thread — used for model detection)
    body_quoted:        str    # Quoted thread text from the email (context only, never decisions)
    message_id_header:  str    # Message-ID header (for In-Reply-To)
    references:         str    # References header (for threading)
    original_to:        list   # Lowercased To: email addresses (for reply-all CC)
    original_cc:        list   # Lowercased Cc: email addresses (for reply-all CC)
    session_id:         str    # MySQL session UUID (filled by create_session)
    history:            list   # [{role, content}] from Redis/MySQL
    inbox_attachments:  list   # Local file paths of attachments downloaded from the email
    attachment_status:  str    # none | downloaded | download_failed
    attachment_context: str    # Extracted text from inbox attachments (persisted in history)
    source_truncation_note: str  # Non-empty when attachment text was truncated (disclosed to user)
    answer:             str    # Final reply text from the Supervisor pipeline
    attachments:        list   # Output files created by agents (PPT, proposals)
    model_results:      list   # [{model, answer, attachments, agents_used}]
    status:             str    # Lifecycle status string
    send_ok:            bool   # True only if the reply was actually delivered
    llm_failed:         bool   # True when NO provider completed the task (quota/billing/error)
    failure_kind:       str    # "transient" when EVERY failed provider was momentarily overloaded
    retry_attempt:      int    # How many times this exact message already auto-retried a transient failure
    # Phase 2 — task-centric fields
    task_id:            str    # UUID of the resolved/created task
    task_number:        int    # Per-thread task sequence number (1, 2, 3…)
    task_label:         str    # Human label e.g. "AI Presentation"
    task_intent:        str    # "create" | "modify"
    deliverable_type:   str    # "ppt" | "proposal" | "info" | "general"


def _model_reply_body(model_name: str, answer: str,
                      task_number: int = 0, task_label: str = "") -> str:
    label = (model_name or "default").upper()
    body = answer or ""
    header = f"Model output: {label}"
    if task_number:
        task_tag = f"Task #T{task_number}"
        if task_label:
            task_tag += f" — {task_label}"
        header = f"{task_tag}\n{header}"
    return f"{header}\n\n{body}".strip()


def _detect_reply_to_model(body: str, body_raw: str, enabled_models: list) -> list:
    """
    Restrict fanout to one model only when the quoted thread contains exactly
    ONE 'Model output: LABEL' tag — meaning the user explicitly replied to
    that specific model's output.

    When the quoted text has multiple labels (combined reply) or none at all,
    run all enabled models — never silently drop a provider.
    """
    for text in (body, body_raw):
        matches = re.findall(r"Model output:\s*(\w+)", text, re.IGNORECASE)
        found = [m.lower() for m in matches if m.lower() in enabled_models]
        if len(found) == 1:
            print(f"[FANOUT] Single model quoted: '{found[0]}' — restricting fanout")
            return [found[0]]
        if len(found) > 1:
            # Combined reply was quoted — user didn't target one model; run all
            print(f"[FANOUT] Multiple models in quoted text {found} — running all providers")
            return enabled_models
    return enabled_models


# ── Attachment Failure Reply ──────────────────────────────────────────────────

def _attachment_failure_reply() -> str:
    from core.config import get_email_signature
    return (
        "Hello,\n\n"
        "Thank you for your email. It looks like an attachment was included, but I "
        "wasn't able to open a readable copy of it on my end, so I don't want to "
        "proceed without the actual content.\n\n"
        "Could you please re-send the file? If it's a supported format (PDF, Word, "
        "Excel, CSV, or PowerPoint), I'll be glad to take a look and help.\n\n"
        f"{get_email_signature()}"
    )


# ── Skill Builder ─────────────────────────────────────────────────────────────

def build_email_skill(outlook_service):
    """
    Build and compile the Email Skill sub-graph (v3 — Supervisor orchestrated).

    Args:
        outlook_service: Authenticated O365 mailbox object.

    Returns:
        A compiled LangGraph that accepts EmailSkillState as input.
    """

    # -- Node 1: Session ------------------------------------------------------
    def create_session_node(state: EmailSkillState) -> dict:
        from core.database.sqlite_db import get_or_create_session
        session_id = get_or_create_session(state["thread_id"], state["sender"])
        return {"session_id": session_id, "status": "session_ready"}

    # -- Node 2: Memory -------------------------------------------------------
    def load_memory_node(state: EmailSkillState) -> dict:
        from core.database.sqlite_db import load_session_history, save_session_history
        from core.database.sqlite_db import get_session_history

        session_id = state["session_id"]
        history    = load_session_history(session_id)
        if not history:
            history = get_session_history(session_id)
            if history:
                save_session_history(session_id, history)
        return {"history": history, "status": "memory_loaded"}

    # -- Node 3: Task Resolver ------------------------------------------------
    def resolve_task_node(state: EmailSkillState) -> dict:
        """
        Deterministically decide CREATE vs MODIFY before any LLM call.

        Resolution priority:
          0. CREATE verb in new message body    → always CREATE (overrides quoted tag)
          1. Quoted #T<n> tag in body_raw       → MODIFY that exact task
          2. MODIFY verb + exactly one open task → MODIFY that task
          3. Everything else                    → CREATE (fail-safe)

        Rule 0 is critical: when Devesh replies to a T1 thread and writes
        "Create a LinkedIn post", that is a new task even though the quoted
        HTML contains "#T1".
        """
        from core.database.sqlite_db import (
            get_task_by_tag, get_revisable_tasks_for_thread, create_task,
            get_task_for_message,
        )

        body      = state.get("body", "")
        body_raw  = state.get("body_raw", "")
        thread_id  = state["thread_id"]
        session_id = state["session_id"]
        sender     = state["sender"]
        subject    = state.get("subject", "Task")

        # A transient-provider-overload retry of this EXACT email (see
        # LLMTransientError / MAX_TRANSIENT_RETRIES): reuse whichever task the
        # first attempt minted instead of re-running the CREATE/MODIFY heuristic,
        # which would otherwise mint a second, duplicate, orphaned task every
        # retry (the heuristic has no notion of "this message already ran").
        if state.get("retry_attempt", 0) > 0:
            prior_task = get_task_for_message(state.get("message_id_header", ""))
            if prior_task:
                print(f"[TASK] Retry of the same message — reusing "
                      f"T{prior_task['task_number']} '{prior_task['task_label']}'")
                return dict(prior_task)

        create_verbs = (r"\b(create|make|draft|write|build|prepare|generate|"
                        r"produce|develop|design)\b")
        modify_verbs = (r"\b(change|fix|shorten|update|redo|revise|add|remove|"
                        r"adjust|edit|modify|improve|regenerate)\b")

        # ── 0. Explicit CREATE verb → new task, no matter what the quoted text says
        if re.search(create_verbs, body, re.IGNORECASE):
            print("[TASK] CREATE verb in new message — new task")

        else:
            # ── 1. Quoted #T<n> tag → MODIFY that task
            tag_match = re.search(r"#T(\d+)", body_raw) or re.search(r"#T(\d+)", body)
            if tag_match:
                task_number = int(tag_match.group(1))
                task = get_task_by_tag(thread_id, task_number)
                if task:
                    print(f"[TASK] MODIFY — tag #T{task_number} → '{task['task_label']}'")
                    return {
                        "task_id":     task["id"],
                        "task_number": task["task_number"],
                        "task_label":  task["task_label"],
                        "task_intent": "modify",
                        "deliverable_type": task.get("deliverable_type", "general"),
                    }
                print(f"[TASK] Tag #T{task_number} found but no matching task — CREATE")

            # ── 2. MODIFY verb + single revisable task → MODIFY that task
            #    (includes recently DELIVERED tasks, so "please revise this" after
            #    the deliverable was sent edits it instead of creating a new task)
            elif re.search(modify_verbs, body, re.IGNORECASE):
                revisable = get_revisable_tasks_for_thread(thread_id)
                if len(revisable) == 1:
                    task = revisable[0]
                    print(f"[TASK] MODIFY — verb match → '{task['task_label']}' (status={task.get('status')})")
                    return {
                        "task_id":     task["id"],
                        "task_number": task["task_number"],
                        "task_label":  task["task_label"],
                        "task_intent": "modify",
                        "deliverable_type": task.get("deliverable_type", "general"),
                    }
                if len(revisable) > 1:
                    print(f"[TASK] MODIFY verb but {len(revisable)} candidate tasks — fail-safe CREATE")

        # ── 3. Fail-safe: CREATE a new task ──────────────────────────────
        body_lower = body.lower()
        if any(k in body_lower for k in ["ppt", "presentation", "slide", "deck", "powerpoint"]):
            deliverable_type = "ppt"
        elif any(k in body_lower for k in ["proposal", "sow", "quote", "report", "document"]):
            deliverable_type = "proposal"
        elif any(k in body_lower for k in ["info", "what", "how", "tell me", "explain"]):
            deliverable_type = "info"
        else:
            deliverable_type = "general"

        task_label = (subject or "Task")[:100]
        task_id, task_number = create_task(
            thread_id, session_id, task_label, deliverable_type, sender
        )
        print(f"[TASK] CREATE — T{task_number} '{task_label}' ({deliverable_type})")
        new_task = {
            "task_id":     task_id,
            "task_number": task_number,
            "task_label":  task_label,
            "task_intent": "create",
            "deliverable_type": deliverable_type,
        }
        # Recorded so a transient-retry re-attempt of this exact message (see
        # above) reuses this task instead of minting a duplicate.
        from core.database.sqlite_db import set_task_for_message
        set_task_for_message(state.get("message_id_header", ""), new_task)
        return new_task

    # -- Node 4: Agent (Supervisor-orchestrated) ------------------------------
    def agent_answer_node(state: EmailSkillState) -> dict:
        from agents.supervisor import orchestrate
        from core.config import get_enabled_models
        from core.database.sqlite_db import (
            get_task, update_task_output, log_task_event, get_config,
        )

        task_id     = state.get("task_id", "")
        task_number = state.get("task_number", 0)
        task_label  = state.get("task_label", "")
        task_intent = state.get("task_intent", "create")
        deliverable_type = state.get("deliverable_type", "general")

        # ── Step 1: Detect reply-to-model early (cheap regex, no LLM call) ──
        enabled   = get_enabled_models() or []

        # Config override — if a specific agent model is pinned in agent_config.yaml,
        # run ONLY that model instead of fanning out across every configured provider.
        from core.config import get_agent_model
        pinned = get_agent_model()
        if pinned == "local":
            # "local" never appears in get_enabled_models() (that list is cloud-API-key
            # driven, and local deliberately stays out of Auto fan-out — see config.py).
            # So `pinned in enabled` below would always be False for local, silently
            # falling back to whatever cloud provider was enabled instead of honoring
            # the pin. Force it here.
            enabled = ["local"]
            print(f"[FANOUT] Config-pinned agent model: local (override)")
        elif pinned and pinned in enabled:
            enabled = [pinned]
            print(f"[FANOUT] Config-pinned agent model: {pinned} (override)")

        providers = _detect_reply_to_model(state["body"], state.get("body_raw", ""), enabled) if enabled else [None]
        reply_model = providers[0] if len(providers) == 1 and providers[0] in enabled else None

        # Side-effecting actions (e.g. scheduling a meeting) must run on a SINGLE
        # provider — fanning out would create duplicate calendar events/invites.
        if len(providers) > 1:
            from agents.assistant_agent import _detect_output_type, is_cancel_request, is_update_request
            _body_lower = state["body"].lower()
            if (_detect_output_type(_body_lower) == "meeting"
                    or is_cancel_request(_body_lower)
                    or is_update_request(_body_lower)):
                providers = [providers[0]]
                print(f"[FANOUT] Calendar action — single provider only: {providers}")

        # ── Step 2: Carry forward the task's live file for MODIFY requests ───
        # Tasks own their output file. On a MODIFY, pull the path from tasks.output_path
        # so the agent gets the actual current version of the document to edit.
        current_inbox_attachments = list(state.get("inbox_attachments", []))
        inbox_attachments  = list(current_inbox_attachments)
        prior_output_path: str = ""

        if task_intent == "modify" and task_id:
            task_row = get_task(task_id)
            stored_path = (task_row or {}).get("output_path") or ""
            if stored_path and os.path.isfile(stored_path):
                prior_output_path = stored_path
                if stored_path not in inbox_attachments:
                    inbox_attachments.append(stored_path)
                    print(f"[CONTEXT] Task file carried forward: {os.path.basename(stored_path)}")

        # ── Step 3: Extract attachment text (covers both current + carried) ──
        # The per-file char budget is sized to the most constrained ACTIVE provider
        # (the WRITE context is shared across the fanout). Groq's free tier is small;
        # pinning a large-context model lifts the cap. When a file IS truncated we
        # record it so the truncation is DISCLOSED to the model and the user — never
        # silent (silent truncation would break the anti-hallucination promise: the
        # model can't say a fact is "not provided" if it was cut before it saw it).
        attachment_context = ""
        persist_attachment_context = ""
        source_truncation_note = ""
        if inbox_attachments:
            from core.config import get_attachment_char_budget
            _CAP = get_attachment_char_budget(providers)
            blocks = []
            persist_blocks = []
            truncated_files = []   # (name, used_chars, total_chars)
            for path in inbox_attachments:
                try:
                    text = read_attachment_text(path)
                    if text and not text.startswith(("Error", "Access denied", "File not found", "No file")):
                        name = os.path.basename(path)
                        total = len(text)
                        if total > _CAP:
                            truncated_files.append((name, _CAP, total))
                            text = (text[:_CAP] +
                                    f"\n\n[NOTICE: this file was truncated — only the first "
                                    f"{_CAP:,} of {total:,} characters are shown below. "
                                    f"Content beyond this point was NOT analysed.]")
                            print(f"[ATTACH] Read {name}: {total} chars — TRUNCATED to {_CAP}")
                        else:
                            print(f"[ATTACH] Read {name}: {total} chars")
                        block = f"File: {name}\n{text}"
                        blocks.append(block)
                        if path in current_inbox_attachments:
                            persist_blocks.append(block)
                    elif text:
                        print(f"[ATTACH] Failed to read {os.path.basename(path)}: {text[:200]}")
                except Exception as e:
                    print(f"[ATTACH] Exception reading {os.path.basename(path)}: {e}")
            attachment_context = "\n\n---\n\n".join(blocks)
            persist_attachment_context = "\n\n---\n\n".join(persist_blocks)
            if truncated_files:
                parts = [f"{n} (analysed {u:,} of {t:,} chars)" for n, u, t in truncated_files]
                source_truncation_note = (
                    "Note: the following attachment(s) were larger than I can analyse "
                    "in one pass, so this response is based only on the portion shown: "
                    + "; ".join(parts) + ". For full coverage, send a smaller/split file"
                    + " or have the agent pinned to a large-context model."
                )
                print(f"[ATTACH] Truncation disclosed: {len(truncated_files)} file(s) over budget {_CAP}")

        def _prior_source_attachment_context(history: list) -> str:
            """Recover prior source attachment text from this thread.

            Keep the first uploaded source as the original grounding material and,
            when different, the latest uploaded source as the freshest context.
            """
            found = []
            for item in history or []:
                if not isinstance(item, dict):
                    continue
                if item.get("role") != "user":
                    continue
                content = item.get("content", "")
                marker = "[Attached file content]"
                if marker not in content:
                    continue
                prior = content.split(marker, 1)[1]
                prior = prior.split("[Email body]", 1)[0].strip()
                if prior and prior not in found:
                    found.append(prior)
            if len(found) > 2:
                found = [found[0], found[-1]]
            return "\n\n---\n\n".join(found)

        # Follow-up emails often do not include the original file again. Keep the
        # current deliverable available for edits, and also carry the original
        # thread source so revisions stay grounded on the uploaded PPT/document.
        prior_source_context = _prior_source_attachment_context(state.get("history", []))
        context_blocks = []
        if attachment_context:
            context_blocks.append(
                "CURRENT UPLOADED FILE OR TASK DELIVERABLE CONTENT:\n"
                + attachment_context
            )
        if prior_source_context and prior_source_context not in attachment_context:
            context_blocks.append(
                "ORIGINAL THREAD SOURCE ATTACHMENT CONTENT:\n"
                + prior_source_context
            )
            print("[CONTEXT] Using prior thread attachment content for follow-up request.")
        agent_attachment_context = "\n\n---\n\n".join(context_blocks)

        # Classify delivery/intake intent ONCE (LLM-based, robust to phrasing) so
        # every fanned-out model shares the same decision — no per-model LLM calls,
        # no keyword brittleness. Done BEFORE the attachment-failure exit so a request
        # that doesn't need the attachment isn't blocked by a failed download.
        from agents.assistant_agent import classify_request_intent
        _clf_model = (providers[0] if (providers and providers[0] in enabled)
                      else (enabled[0] if enabled else "groq"))
        _intent = classify_request_intent(state["body"], _clf_model)

        # Early exit if a REAL attachment failed to download AND the request actually
        # needs it: reply asking to re-send and DON'T run the LLM (no point answering
        # without the file; saves tokens). `attachment_status == download_failed` means
        # a genuine non-inline attachment failed — stray inline logos are filtered out
        # upstream (has_real_attachment). uses_attachment is fail-open (True when
        # unsure) so this keeps blocking unless the LLM is confident the file is
        # irrelevant (e.g. "schedule a meeting" with an unrelated attachment).
        if (state.get("attachment_status") == "download_failed"
                and not inbox_attachments
                and not agent_attachment_context
                and _intent.get("uses_attachment", True)):
            failure = _attachment_failure_reply()
            return {
                "answer": failure,
                "attachments": [],
                "model_results": [{"model": "supervisor", "answer": failure,
                                   "attachments": [], "agents_used": ["supervisor"]}],
                "attachment_context": persist_attachment_context,
                "status": "attachment_download_failed",
            }
        print(f"[INTENT] deliver_to_folder={_intent['deliver_to_folder']} "
              f"read_from_folder={_intent['read_from_folder']} file='{_intent['intake_filename']}' "
              f"needs_web_search={_intent.get('needs_web_search', True)} "
              f"calendar_action={_intent.get('calendar_action', 'none')} "
              f"output_format='{_intent.get('output_format', '')}' "
              f"uses_attachment={_intent.get('uses_attachment', True)} "
              f"uses_thread={_intent.get('uses_thread', True)} "
              f"save_to_rag={_intent.get('save_to_rag', False)} "
              f"uses_rag={_intent.get('uses_rag', True)} "
              f"needs_multi_kb_lookup={_intent.get('needs_multi_kb_lookup', False)} "
              f"detected_language={_intent.get('detected_language', 'en')} "
              f"content_language='{_intent.get('content_language', '')}'")

        # ── Step 4: Delegate to the Supervisor — parallel fanout per provider
        base_context = {
            "thread_id":         state["thread_id"],
            "sender":            state["sender"],
            "subject":           state["subject"],
            "body":              state["body"],
            "thread_quoted":     (state.get("body_quoted") or "")[:6000],
            "message_id_header": state["message_id_header"],
            "session_id":        state["session_id"],
            "history":           state.get("history", []),
            "inbox_attachments": inbox_attachments,
            "attachment_status": state.get("attachment_status", "none"),
            "attachment_context": agent_attachment_context,
            "source_truncation_note": source_truncation_note,
            # Phase 2 — pass task_number so file naming can embed the task tag
            "task_number":       task_number,
            "task_label":        task_label,
            "task_intent":        task_intent,
            "task_deliverable_type": deliverable_type,
            # LLM-decided delivery/intake/search intent (shared across all fanout models)
            "deliver_to_folder": _intent["deliver_to_folder"],
            "read_from_folder":  _intent["read_from_folder"],
            "intake_filename":   _intent["intake_filename"],
            "needs_web_search":  _intent.get("needs_web_search", True),
            "calendar_action":   _intent.get("calendar_action", "none"),
            # Human-like comprehension: format of deliverable + which sources the
            # request actually needs (fail-open True → never drops a needed source).
            "output_format":     _intent.get("output_format", ""),
            "uses_attachment":   _intent.get("uses_attachment", True),
            "uses_thread":       _intent.get("uses_thread", True),
            # Fail-closed: a deliberate "save to RAG" command, never inferred.
            "save_to_rag":       _intent.get("save_to_rag", False),
            # Fail-open: whether this reply should consult the RAG knowledge base.
            "uses_rag":          _intent.get("uses_rag", True),
            # Fail-closed, narrow: whether GATHER should run for a multi-part KB
            # lookup even with no web-search need (see classify_request_intent).
            "needs_multi_kb_lookup": _intent.get("needs_multi_kb_lookup", False),
            "detected_language": _intent.get("detected_language", "en"),
            "content_language":  _intent.get("content_language", ""),
        }

        print(f"[FANOUT] Running supervisor pipeline on {len(providers)} provider(s): {providers}")

        def _run_one(label):
            ctx = dict(base_context)
            try:
                res = orchestrate(ctx, force_model=label)
                return label, res, None
            except Exception as exc:
                return label, None, exc

        with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:
            results = list(pool.map(_run_one, providers))

        model_results = []
        for label, res, err in results:
            display_label = label or "default"
            if err is not None:
                # An unclassified crash — NOT known to be transient, so it never
                # qualifies for the silent auto-retry deferral below (only a
                # confirmed transient overload does); it sends today's error note.
                print(f"[FANOUT] {display_label} FAILED: {err}")
                model_results.append({
                    "model":        display_label,
                    "answer":       f"[{display_label} pipeline failed: {err}]",
                    "attachments":  [],
                    "agents_used":  ["error"],
                    "failure_kind": "",
                })
                continue
            if res.get("llm_failed"):
                # Provider outage (quota/billing/auth) — the answer is an honest
                # notification; mark the result failed so the task isn't "delivered".
                print(f"[FANOUT] {display_label} LLM UNAVAILABLE "
                      f"({res.get('failure_kind') or 'unknown'}) — task not completed.")
                model_results.append({
                    "model":        display_label,
                    "answer":       res.get("answer", ""),
                    "attachments":  [],
                    "agents_used":  ["error", "llm_unavailable"],
                    "failure_kind": res.get("failure_kind", ""),
                })
                continue
            # Capture supervisor's deliverable_type so we can refine the task label
            if res.get("deliverable_type") and task_intent == "create":
                base_context["_supervisor_deliverable_type"] = res["deliverable_type"]
            if res.get("topic") and task_intent == "create":
                base_context["_supervisor_topic"] = res["topic"]
            model_results.append({
                "model":        display_label,
                "answer":       res.get("answer", ""),
                "attachments":  res.get("attachments", []),
                "agents_used":  res.get("agents_used", ["supervisor"]),
            })

        # ── Overwrite task's live file on MODIFY (one file per task, no duplicates)
        if task_intent == "modify" and prior_output_path:
            prior_ext = os.path.splitext(prior_output_path)[1].lower()
            for r in model_results:
                new_atts = r.get("attachments", [])
                if len(new_atts) == 1:
                    new_path = new_atts[0]
                    new_ext  = os.path.splitext(new_path)[1].lower()
                    if (os.path.isfile(new_path) and os.path.isfile(prior_output_path)
                            and new_ext == prior_ext):
                        try:
                            os.replace(new_path, prior_output_path)
                            r["attachments"] = [prior_output_path]
                            print(f"[OVERWRITE] T{task_number} file updated in-place: "
                                  f"{os.path.basename(prior_output_path)}")
                        except Exception as e:
                            print(f"[OVERWRITE] Could not overwrite task file: {e}")

        # ── Log task event + update task output path ──────────────────────────
        any_success = any("error" not in r.get("agents_used", []) for r in model_results)
        # ALL configured providers failed AND every one of them was a confirmed
        # transient overload (not quota/billing/auth, not an unclassified crash)
        # — worth a silent auto-retry rather than an immediate "please resend".
        failed_results = [r for r in model_results if "error" in r.get("agents_used", [])]
        all_transient = (
            not any_success and bool(failed_results)
            and all(r.get("failure_kind") == "transient" for r in failed_results)
        )
        if task_id:
            primary_result = next(
                (r for r in model_results if "error" not in r.get("agents_used", [])),
                model_results[0] if model_results else {},
            )
            new_files = primary_result.get("attachments", [])
            live_file = new_files[0] if new_files else None
            model_used = primary_result.get("model", "")
            event_type = "change_request" if task_intent == "modify" else "created"

            try:
                log_task_event(
                    task_id=task_id,
                    event_type=event_type,
                    sender=state["sender"],
                    message=state["body"][:500],
                    model_name=model_used,
                    file_path=live_file,
                )
                if not any_success:
                    # No provider completed the task — leave it OPEN (never "delivered";
                    # the tasks.status CHECK has no 'failed') and record a 'failed'
                    # event so the audit trail shows why. Modify-intent tasks keep
                    # their existing file and status: the prior deliverable stands.
                    log_task_event(
                        task_id=task_id, event_type="failed",
                        sender=state["sender"],
                        message="No LLM provider completed this task",
                        model_name=model_used,
                    )
                    print(f"[TASK] T{task_number} NOT completed — left open; failure event logged.")
                elif live_file:
                    update_task_output(task_id, live_file, status="delivered")
                    print(f"[TASK] T{task_number} output updated: {os.path.basename(live_file)}")
                elif task_intent == "create":
                    # Non-file tasks (info/general) stay open
                    update_task_output(task_id, None, status="delivered")
            except Exception as e:
                print(f"[TASK] Event logging failed: {e}")

        # Pick the first successful result as the "primary" answer
        primary = next(
            (r for r in model_results if "error" not in r.get("agents_used", [])),
            model_results[0] if model_results else {"answer": "", "attachments": []},
        )

        return {
            "answer":             primary.get("answer", ""),
            "attachments":        primary.get("attachments", []),
            "model_results":      model_results,
            "attachment_context": persist_attachment_context,
            "source_truncation_note": source_truncation_note,
            "status":             "answer_ready",
            "llm_failed":         not any_success,
            "failure_kind":       "transient" if all_transient else "",
        }

    # -- Node 5: Auto-send (one combined reply containing all model outputs) ---
    def auto_send_node(state: EmailSkillState) -> dict:
        from core.database.sqlite_db import save_email_draft, update_draft_status
        from skills.email_skill.email_client import get_message_by_id, send_reply, get_account_address

        # Every enabled provider was momentarily overloaded (LLMTransientError),
        # not actually out of quota/billing/misconfigured, and we haven't used up
        # the auto-retry budget yet: defer silently instead of sending the "please
        # resend" notice. send_ok=False makes run_agent.py's existing retry-next-
        # poll machinery pick this exact email back up once its backoff elapses
        # (see set_retry_state) — no draft saved, nothing emailed, no side effects.
        from core.config import MAX_TRANSIENT_RETRIES
        if state.get("llm_failed") and state.get("failure_kind") == "transient":
            retry_attempt = state.get("retry_attempt", 0)
            if retry_attempt < MAX_TRANSIENT_RETRIES:
                print(f"[SEND] Deferring — every provider transiently overloaded "
                      f"(attempt {retry_attempt + 1}/{MAX_TRANSIENT_RETRIES}), "
                      "will auto-retry rather than reply now.")
                return {"status": "deferred", "send_ok": False,
                        "failure_kind": "transient"}
            print(f"[SEND] Transient-retry budget exhausted ({MAX_TRANSIENT_RETRIES} attempts) "
                  "— sending the honest 'please resend' notice now.")

        inbox_files           = state.get("inbox_attachments", [])
        inbox_attachment_path = ";".join(inbox_files) if inbox_files else None
        model_results         = state.get("model_results", [])
        task_number           = state.get("task_number", 0)
        task_label            = state.get("task_label", "")

        if not model_results:
            model_results = [{"model": "supervisor", "answer": state.get("answer", ""),
                              "attachments": state.get("attachments", []),
                              "agents_used": []}]

        # -- Reply-all CC: keep all original To/CC recipients in the loop ------
        # When Sally was CC'd on a thread, replying only to the sender cuts
        # everyone else out of the conversation.  Compute:
        #   reply_cc = (original_to + original_cc) - sender - Sally
        sally_addr = (get_account_address(outlook_service) or "").lower()
        sender_addr = (state.get("sender") or "").lower()
        original_to = state.get("original_to") or []
        original_cc = state.get("original_cc") or []
        reply_cc = []
        _seen_cc = set()
        for addr in (original_to + original_cc):
            addr_l = (addr or "").lower().strip()
            if addr_l and addr_l != sender_addr and addr_l != sally_addr and addr_l not in _seen_cc:
                reply_cc.append(addr)
                _seen_cc.add(addr_l)
        # Domain guardrail: never CC anyone outside the allowed domain(s) on a
        # reply (same internal-only rule as meeting attendees).
        if reply_cc:
            from tools.meeting_scheduler import filter_attendees_by_domain
            reply_cc, _ext_cc = filter_attendees_by_domain(reply_cc)
            if _ext_cc:
                print(f"[SEND] Dropped external CC (outside allowed domain): {', '.join(_ext_cc)}")
        if reply_cc:
            print(f"[SEND] Reply-all CC: {', '.join(reply_cc)}")

        # ── Step 1: Determine what goes in the email (from results, NOT the DB) ──
        # The email content is built purely from model_results so a failed audit
        # insert can never drop a model's output or file from the reply.
        all_files   = []   # combined attachments across all successful models
        all_agents  = []
        for result in model_results:
            if "error" in result.get("agents_used", []):
                continue
            for f in result.get("attachments", []):
                if f not in all_files:
                    all_files.append(f)
            all_agents.append(result.get("model", "supervisor").upper())

        # ── Step 1b: Save each model's draft to DB (audit only — never blocks send)
        draft_ids = {}   # model_name → draft_id
        for result in model_results:
            model_name  = result.get("model", "supervisor")
            out_files   = result.get("attachments", [])
            agents_used = ",".join(result.get("agents_used", []))
            failed      = "error" in result.get("agents_used", [])
            per_model_body = _model_reply_body(
                model_name, result.get("answer", ""),
                task_number=task_number, task_label=task_label,
            )
            try:
                draft_id = save_email_draft(
                    session_id=state["session_id"],
                    thread_id=state["thread_id"],
                    message_id=state["message_id_header"],
                    sender=state["sender"],
                    subject=state["subject"],
                    user_question=state["body"],
                    ai_draft=per_model_body,
                    model_name=model_name,
                    attachment_path=";".join(out_files) if out_files else None,
                    inbox_attachment_path=inbox_attachment_path,
                    agents_used=agents_used,
                    status="failed" if failed else "pending",
                )
                if draft_id:
                    draft_ids[model_name] = draft_id
            except Exception as e:
                print(f"[SEND] save_email_draft (audit) failed for {model_name}: {e}")

        # ── Step 2: Build one combined reply body ─────────────────────────────
        successful = [r for r in model_results if "error" not in r.get("agents_used", [])]

        if len(successful) == 1:
            # Single model — plain body, no separator needed
            r = successful[0]
            combined_body = _model_reply_body(
                r["model"], r.get("answer", ""),
                task_number=task_number, task_label=task_label,
            )
        elif len(successful) > 1:
            # Multiple models — stack sections with a clear divider
            divider = "\n\n" + ("─" * 60) + "\n\n"
            sections = []
            for r in successful:
                sections.append(_model_reply_body(
                    r["model"], r.get("answer", ""),
                    task_number=task_number, task_label=task_label,
                ))
            combined_body = divider.join(sections)
        else:
            # All models failed — use first result's error text
            r = model_results[0]
            combined_body = _model_reply_body(
                r["model"], r.get("answer", ""),
                task_number=task_number, task_label=task_label,
            )

        # Disclose attachment truncation to the user (guaranteed, not LLM-dependent)
        # so a partial-source answer is never presented as if it covered everything.
        trunc_note = (state.get("source_truncation_note") or "").strip()
        if trunc_note and successful:
            combined_body = f"{combined_body}\n\n———\n⚠️ {trunc_note}"

        # ── Step 3: Send ONE email with all outputs + all files ───────────────
        send_ok = False
        try:
            msg_obj = get_message_by_id(outlook_service, state["message_id_header"])
            if not msg_obj:
                print("[SEND ERROR] Original message not found — marking all drafts failed.")
                for mid, did in draft_ids.items():
                    update_draft_status(did, "failed")
            else:
                send_reply(msg_obj, combined_body, all_files,
                           cc=reply_cc if reply_cc else None)
                send_ok = True
                models_label = ", ".join(all_agents) if all_agents else "none"
                files_label  = f" (+{len(all_files)} file(s))" if all_files else ""
                cc_label     = f" [cc: {', '.join(reply_cc)}]" if reply_cc else ""
                print(f"[SEND] Combined reply sent to {state['sender']}"
                      f"{files_label} [models: {models_label}]{cc_label}")
                for model_name, did in draft_ids.items():
                    r = next((r for r in model_results if r.get("model") == model_name), {})
                    failed = "error" in r.get("agents_used", [])
                    # If every model failed, the error explanation itself was
                    # delivered; mark it sent so message-level dedup can work.
                    status = "failed" if failed and successful else "sent"
                    update_draft_status(did, status)
        except Exception as e:
            print(f"[SEND ERROR] {e}")
            for did in draft_ids.values():
                try:
                    update_draft_status(did, "failed")
                except Exception:
                    pass

        # Propagate the real outcome so the poller doesn't mark the inbound mail
        # handled (read + watermark advanced) when delivery actually failed.
        # `send_ok` is a dedicated field — persist_node overwrites `status`.
        return {"status": "sent" if send_ok else "send_failed", "send_ok": send_ok}

    # -- Node 6: Persist ------------------------------------------------------
    def persist_node(state: EmailSkillState) -> dict:
        from core.database.sqlite_db import log_message
        from core.database.sqlite_db import save_session_history

        # Nothing was actually sent (a deferred transient-retry, or a genuine
        # delivery failure) — don't record a fake "assistant said X" turn, and
        # don't log the user's message either, since run_agent.py will retry
        # this exact email again; logging it now would duplicate the turn in
        # history on every retry attempt. The real exchange gets logged once,
        # on whichever attempt actually completes.
        if not state.get("send_ok", True):
            return {"status": state.get("status", "not_sent")}

        session_id         = state["session_id"]
        body               = state["body"]
        attachment_context = state.get("attachment_context", "")

        # Enrich the stored user message with attachment text so future turns
        # in the same thread retain full context of what was sent.
        if attachment_context:
            user_content = f"[Attached file content]\n{attachment_context}\n\n[Email body]\n{body}"
        else:
            user_content = body

        try:
            log_message(session_id, "user",      user_content)
            log_message(session_id, "assistant", state["answer"])
        except Exception as e:
            print(f"[MySQL PERSIST ERROR] {e}")

        try:
            updated = list(state.get("history", [])) + [
                {"role": "user",      "content": user_content},
                {"role": "assistant", "content": state["answer"]},
            ]
            save_session_history(session_id, updated)
        except Exception as e:
            print(f"[Redis PERSIST ERROR] {e}")

        return {"status": "persisted"}

    # -- Graph Assembly -------------------------------------------------------
    graph = StateGraph(EmailSkillState)

    graph.add_node("create_session", create_session_node)
    graph.add_node("load_memory",    load_memory_node)
    graph.add_node("resolve_task",   resolve_task_node)
    graph.add_node("agent_answer",   agent_answer_node)
    graph.add_node("auto_send",      auto_send_node)
    graph.add_node("persist",        persist_node)

    graph.add_edge(START,            "create_session")
    graph.add_edge("create_session", "load_memory")
    graph.add_edge("load_memory",    "resolve_task")
    graph.add_edge("resolve_task",   "agent_answer")
    graph.add_edge("agent_answer",   "auto_send")
    graph.add_edge("auto_send",      "persist")
    graph.add_edge("persist",        END)

    return graph.compile()
