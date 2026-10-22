"""
ui/dashboard.py -- PhantomOps Visual Approval Dashboard (v3)

Review, edit, and approve AI-generated email drafts before they are sent.
Each email shows the Supervisor pipeline result with an expandable
agent trace panel showing which subagents contributed.

v3 Changes:
  - Single draft per email (Supervisor pipeline) instead of multi-model tabs
  - Agent trace panel showing subagent execution details
  - Backward-compatible: legacy multi-model drafts still show as tabs
"""

import sys
import os
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.database.sqlite_db import (
    get_pending_drafts,
    update_draft_status,
    get_draft_by_id,
    reject_other_model_drafts,
    get_agent_executions,
)
from skills.email_skill.outlook_client import get_outlook_service, get_message_by_id, format_reply_html


# ── Agent display labels ──────────────────────────────────────────────────────

_AGENT_LABELS = {
    "supervisor":        ("🧠", "Supervisor"),
    "document_analyst":  ("📄", "Document Analyst"),
    "research_agent":    ("🔍", "Research & Intelligence"),
    "proposal_architect":("📝", "Proposal Architect"),
    "ppt_strategist":    ("🎨", "PPT Strategist"),
    "quick_reply":       ("⚡", "Quick Reply"),
    "SUPERVISOR":        ("🧠", "Supervisor"),
    "DOC_ANALYST":       ("📄", "Document Analyst"),
    "RESEARCH":          ("🔍", "Research & Intelligence"),
    "PROPOSAL":          ("📝", "Proposal Architect"),
    "PPT_STRATEGIST":    ("🎨", "PPT Strategist"),
    "QUICK_REPLY":       ("⚡", "Quick Reply"),
}

# Display labels are derived from the live model IDs in core.config so the
# dashboard never drifts from the actual model in use.
from core.config import GROQ_MODEL, CLAUDE_MODEL, GEMINI_MODEL, OPENAI_MODEL

_MODEL_LABELS = {
    "groq":       (f"Groq ({GROQ_MODEL})",       "🟠"),
    "claude":     (f"Claude ({CLAUDE_MODEL})",   "🟣"),
    "gemini":     (f"Gemini ({GEMINI_MODEL})",   "🔵"),
    "openai":     (f"OpenAI ({OPENAI_MODEL})",   "🟢"),
    "supervisor": ("PhantomOps v3 Pipeline",     "🧠"),
}


def _split_paths(value) -> list[str]:
    return [p.strip() for p in (value or "").split(";") if p.strip()]


def _file_label(path: str) -> str:
    if not os.path.isfile(path):
        return f"{os.path.basename(path)} (missing)"
    size_kb = max(1, round(os.path.getsize(path) / 1024))
    return f"{os.path.basename(path)} ({size_kb:,} KB)"


def _send_reply_with_attachments(msg, body: str, attachments: list[str]) -> None:
    reply = msg.reply()
    reply.body = format_reply_html(body)
    for path in attachments:
        if os.path.isfile(path):
            reply.attachments.add(path)
            print(f"[Email] Attached: {path}")
        else:
            print(f"[Email] Attachment missing, skipping: {path}")
    reply.send()


# ── Cached resources ──────────────────────────────────────────────────────────

@st.cache_resource
def get_outlook():
    try:
        return get_outlook_service()
    except Exception as e:
        st.error(f"Outlook auth failed: {e}")
        return None


# ── Action handlers ───────────────────────────────────────────────────────────

def handle_approve(draft_id: int, message_id: str, text: str):
    outlook = get_outlook()
    if not outlook:
        return
    draft = get_draft_by_id(draft_id)
    if not draft:
        st.error("Draft not found.")
        return
    try:
        msg_obj = get_message_by_id(outlook, draft["message_id"])
        if msg_obj:
            attachments = _split_paths(draft.get("attachment_path"))
            _send_reply_with_attachments(msg_obj, text, attachments)
            update_draft_status(draft_id, "sent", text)
            # Auto-reject all other pending drafts for the same email
            reject_other_model_drafts(message_id, draft_id)
            st.success("Email sent!")
            st.rerun()
        else:
            st.error("Original message not found in Outlook.")
    except Exception as e:
        st.error(f"Failed to send: {e}")


def handle_reject(draft_id: int):
    update_draft_status(draft_id, "rejected")
    st.info("Draft rejected.")
    st.rerun()


def handle_regenerate(draft_id: int, question: str):
    """Regenerate by running the full Supervisor pipeline."""
    with st.spinner("Regenerating via PhantomOps pipeline..."):
        try:
            from agents.supervisor import orchestrate

            draft = get_draft_by_id(draft_id)
            inbox_attachments = _split_paths(draft.get("inbox_attachment_path") if draft else "")

            context = {
                "body": question,
                "subject": draft.get("subject", ""),
                "sender": draft.get("sender", ""),
                "session_id": draft.get("session_id", ""),
                "message_id_header": draft.get("message_id", ""),
                "history": [],
                "inbox_attachments": inbox_attachments,
                "attachment_status": "downloaded" if inbox_attachments else "none",
                "attachment_context": "",
            }

            # Re-read attachments for context
            if inbox_attachments:
                from tools.file_reader import read_attachment_text
                blocks = []
                for path in inbox_attachments:
                    try:
                        text = read_attachment_text(path)
                        if text:
                            blocks.append(f"File: {os.path.basename(path)}\n{text[:50000]}")
                    except Exception:
                        pass
                context["attachment_context"] = "\n\n---\n\n".join(blocks)

            result = orchestrate(context)
            new_text = result.get("answer", "")

            update_draft_status(draft_id, "pending", new_text)
            st.success("Draft regenerated!")
            st.rerun()
        except Exception as e:
            st.error(f"Regeneration failed: {e}")


# ── Group helpers ─────────────────────────────────────────────────────────────

def _group_drafts(drafts: list) -> list[list]:
    """
    Group draft rows by message_id. Within each group, keep only the latest
    draft per model (highest id wins) so duplicate runs don't produce
    duplicate entries.
    """
    email_models: dict[str, dict[str, dict]] = {}
    order: list[str] = []

    for d in drafts:
        mid = d["message_id"]
        model = d.get("model_name", "supervisor")
        if mid not in email_models:
            email_models[mid] = {}
            order.append(mid)
        existing = email_models[mid].get(model)
        if existing is None or d["id"] > existing["id"]:
            email_models[mid][model] = d

    groups = []
    for mid in order:
        model_map = email_models[mid]
        groups.append(list(model_map.values()))
    return groups


# ── Agent Trace Panel ─────────────────────────────────────────────────────────

def _render_agent_trace(draft: dict):
    """Show an expandable panel with subagent execution details."""
    message_id = draft.get("message_id", "")
    agents_used = draft.get("agents_used", "")

    if not agents_used and not message_id:
        return

    with st.expander("🔍 Agent Pipeline Trace", expanded=False):
        # Show agents used (from draft record)
        if agents_used:
            agent_list = [a.strip() for a in agents_used.split(",") if a.strip()]
            cols = st.columns(len(agent_list))
            for i, agent in enumerate(agent_list):
                icon, label = _AGENT_LABELS.get(agent, ("🔧", agent))
                with cols[i]:
                    st.metric(label=label, value=icon)

        # Show detailed execution log (from agent_executions table)
        if message_id:
            try:
                executions = get_agent_executions(message_id)
                if executions:
                    st.caption("Execution details:")
                    for ex in executions:
                        icon, label = _AGENT_LABELS.get(
                            ex["agent_name"], ("🔧", ex["agent_name"])
                        )
                        status_icon = "✅" if ex["status"] == "success" else "❌"
                        duration = f"{ex['duration_ms']}ms" if ex.get("duration_ms") else "—"

                        st.markdown(
                            f"**{icon} {label}** — {status_icon} {duration}"
                        )
                        if ex.get("agent_output"):
                            st.text(ex["agent_output"][:300])
            except Exception:
                pass  # agent_executions table may not exist yet


# ── Draft renderer ────────────────────────────────────────────────────────────

def _render_draft(draft: dict):
    """Render a single draft with edit controls and agent trace."""
    draft_id = draft["id"]
    message_id = draft["message_id"]

    # Agent trace panel
    _render_agent_trace(draft)

    # Output files
    out_files = _split_paths(draft.get("attachment_path"))
    if out_files:
        st.markdown("**Output files:**")
        for path in out_files:
            st.caption(f"- {_file_label(path)}")
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    st.download_button(
                        "Download",
                        data=f,
                        file_name=os.path.basename(path),
                        key=f"dl_{draft_id}_{os.path.basename(path)}",
                        use_container_width=True,
                    )
    else:
        st.caption("No output file attached.")

    # Editable draft text
    edited_text = st.text_area(
        "Edit draft before approving",
        value=draft["ai_draft"],
        height=220,
        key=f"text_{draft_id}",
    )

    # Action buttons
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("Approve & Send", key=f"send_{draft_id}", type="primary",
                      use_container_width=True):
            handle_approve(draft_id, message_id, edited_text)
    with col_b:
        if st.button("Regenerate", key=f"regen_{draft_id}",
                      use_container_width=True):
            handle_regenerate(draft_id, draft["user_question"])
    with col_c:
        if st.button("Reject", key=f"reject_{draft_id}",
                      use_container_width=True):
            handle_reject(draft_id)


# ── Main App ──────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="PhantomOps Approvals", page_icon="📧", layout="wide")
    st.title("PhantomOps Approval Dashboard")
    st.markdown("Review AI-generated email drafts and approve the best reply.")

    get_outlook()

    drafts = get_pending_drafts()
    if not drafts:
        st.success("All caught up! No pending drafts.")
        return

    groups = _group_drafts(drafts)
    st.subheader(f"Pending Emails: {len(groups)}  |  Total Drafts: {len(drafts)}")
    st.divider()

    for group in groups:
        first = group[0]

        # ── Email header ──────────────────────────────────────────────────────
        hcol1, hcol2 = st.columns([3, 6])
        with hcol1:
            st.markdown(f"**From:** `{first['sender']}`")
            st.markdown(f"**Subject:** {first['subject'][:80]}")
            st.caption(f"Received: {first['created_at'].strftime('%Y-%m-%d %H:%M')}")
            in_files = _split_paths(first.get("inbox_attachment_path"))
            if in_files:
                st.markdown("**Input attachments:**")
                for p in in_files:
                    st.caption(f"- {os.path.basename(p)}")
        with hcol2:
            st.info(first["user_question"])

        # ── Draft display ─────────────────────────────────────────────────────
        if len(group) == 1:
            # v3 style: single draft, no tabs needed
            _render_draft(group[0])
        else:
            # Legacy v2 or multi-draft: show as tabs
            tab_labels = []
            for d in group:
                m = d.get("model_name", "supervisor")
                name, icon = _MODEL_LABELS.get(m, (m, "❓"))
                tab_labels.append(f"{icon} {name}")

            tabs = st.tabs(tab_labels)
            for tab, draft in zip(tabs, group):
                with tab:
                    _render_draft(draft)

        st.divider()


if __name__ == "__main__":
    main()
