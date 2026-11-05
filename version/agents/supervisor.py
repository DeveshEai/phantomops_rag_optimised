"""
agents/supervisor.py — Supervisor

Thin wrapper that runs the single general-purpose assistant_agent for each
enabled model in the fan-out. No intent classification, no routing.
"""

import time
from agents import log_agent_execution, LLMUnavailableError, LLMTransientError


_KIND_EXPLANATIONS = {
    "quota":   "has reached its token / usage quota",
    "billing": "has a billing problem (credits exhausted or payment required)",
    "auth":    "rejected the configured API key",
}


def _llm_unavailable_reply(provider: str, kind: str) -> str:
    """Honest notification sent when the LLM provider itself is down."""
    from core.config import get_email_signature
    why = _KIND_EXPLANATIONS.get(kind, "is currently unavailable")
    extra = " and the API key configuration" if kind == "auth" else ""
    return (
        f"I could not complete this task: my AI provider ({provider}) {why}.\n\n"
        f"Please check the provider's billing / usage dashboard{extra}.\n\n"
        "This task was NOT completed — please resend it once the issue is resolved.\n\n"
        f"{get_email_signature()}"
    )


def _llm_transient_reply(provider: str) -> str:
    """Honest notification for a momentary provider overload (503/"high
    demand") — distinct from _llm_unavailable_reply: there's no billing/quota
    problem to check, it's simply saturated and usually clears within minutes.
    """
    from core.config import get_email_signature
    return (
        f"I could not complete this task: my AI provider ({provider}) is "
        "temporarily overloaded (high demand on their side, not a billing or "
        "quota issue).\n\n"
        "This task was NOT completed — please resend it in a few minutes.\n\n"
        f"{get_email_signature()}"
    )


def _is_multi_agent_request(context: dict) -> bool:
    """
    Return True only for requests that genuinely require the multi-agent
    orchestrator (Arabic pipeline only). Calendar actions (schedule/cancel/
    update) are fully handled inside assistant_agent.run() via the LLM-powered
    _run_meeting_flow() -- routing them to the orchestrator bypasses that and
    uses the broken regex-only scheduling_agent.execute_scheduling() instead.
    Simple Word / Excel / PPT / general / calendar requests all go straight
    to the high-quality assistant_agent.run() pipeline.
    """
    text = (context.get("body", "") + " " + context.get("subject", "")).lower()
    lang = context.get("content_language", "") or context.get("detected_language", "")

    needs_arabic = (
        "arabic" in text
        or "\u0628\u0627\u0644\u0639\u0631\u0628\u064a\u0629" in text
        or lang == "ar"
    )
    # SCH-004 FIX: needs_meeting removed -- calendar/scheduling requests are
    # handled by assistant_agent._run_meeting_flow() (LLM extractor + domain
    # guardrail + pending-action confirmation gate). Routing them to the
    # orchestrator skips all of that and uses the old regex-only parser which
    # cannot parse natural date/time formats like "10 November 2026" or
    # "11:00 AM - 12:00 PM (IST)".
    return needs_arabic
def orchestrate(context: dict, force_model: str | None = None) -> dict:

    """Run the assistant agent for one email.

    Args:
        context:     All email fields + session info.
        force_model: Provider override for multi-model fan-out.

    Returns:
        Dict with keys: answer (str), attachments (list[str]), agents_used (list[str]).
    """
    start = time.time()

    if force_model:
        context["force_model"] = force_model

    llm_failed = False
    failure_kind = ""
    agents_used = ["orchestrator"]
    try:
        if _is_multi_agent_request(context):
            # Genuinely multi-agent: Arabic pipeline, meeting scheduling, etc.
            from core.orchestrator.orchestrator import CentralOrchestrator
            task_id = context.get("task_id") or context.get("session_id") or "task_default"
            orch = CentralOrchestrator()
            result = orch.coordinate_request(context, parent_task_id=task_id)
            if result.get("agents_used"):
                agents_used = result["agents_used"]
        else:
            # Simple Word / Excel / PPT / general request:
            # Use the original full LLM GATHER→WRITE→RENDER pipeline for
            # high-quality, topic-specific document generation.
            from agents import assistant_agent
            result = assistant_agent.run(context)
            agents_used = ["assistant"]

    except LLMUnavailableError as e:
        print(f"[SUPERVISOR] LLM provider unavailable ({e.provider}/{e.kind}): {e}")
        llm_failed = True
        failure_kind = e.kind
        result = {"answer": _llm_unavailable_reply(e.provider, e.kind), "files": []}
    except LLMTransientError as e:
        print(f"[SUPERVISOR] LLM provider transiently overloaded ({e.provider}): {e}")
        llm_failed = True
        failure_kind = "transient"
        result = {"answer": _llm_transient_reply(e.provider), "files": []}
    except Exception as e:
        print(f"[SUPERVISOR] Multi-agent orchestrator encountered exception: {e} — falling back to assistant agent.")
        try:
            from agents import assistant_agent
            result = assistant_agent.run(context)
            agents_used = ["assistant"]
        except Exception as fallback_exc:
            print(f"[SUPERVISOR] Assistant agent fallback failed: {fallback_exc}")
            from core.config import get_email_signature
            result = {
                "answer": (
                    "Thank you for reaching out. I was unable to complete your request "
                    "automatically. A team member will get back to you shortly.\n\n"
                    f"{get_email_signature()}"
                ),
                "files": [],
            }

    duration_ms  = int((time.time() - start) * 1000)
    answer       = result.get("answer", "")
    attachments  = result.get("files", [])

    print(f"[SUPERVISOR] Done in {duration_ms}ms — files: {len(attachments)}")
    log_agent_execution(
        session_id=context.get("session_id", ""),
        message_id=context.get("message_id_header", ""),
        agent_name="SUPERVISOR",
        agent_input=context.get("body", "")[:200],
        agent_output=(f"LLM unavailable ({failure_kind}) — task NOT completed"
                      if llm_failed else f"files={len(attachments)}"),
        duration_ms=duration_ms,
        status="error" if llm_failed else "success",  # DB CHECK: success|error|skipped
    )
    return {
        "answer": answer,
        "attachments": attachments,
        "agents_used": agents_used,
        "llm_failed": llm_failed,
        "failure_kind": failure_kind,
    }
