"""
agents/email_router.py — LLM-based email routing classifier

Determines which AI agent (if any) an inbound email is addressed to.
Used by run_agent.py to route emails to the correct agent or label them
in Gmail for another agent.

The LLM provider is configurable via `routing_model` in agent_config.yaml
and can be switched live between groq, gemini, claude, and openai.
"""

import json as _json
from langchain_core.messages import SystemMessage, HumanMessage


def classify_email_recipient(
    body: str,
    subject: str,
    agent_name: str,
    known_agents: list[str],
    model_label: str,
) -> str:
    """Use an LLM to determine which agent an email is addressed to.

    Args:
        body:          The email body text (latest message, quoted text stripped).
        subject:       The email subject line.
        agent_name:    This agent's own name (from agent_config.yaml).
        known_agents:  List of other agent names sharing this mailbox.
        model_label:   LLM provider to use (groq/gemini/claude/openai).

    Returns:
        The exact agent name the email is addressed to, or "none" if no
        agent is being addressed. Always lowercased.
    """
    # Build a clear name → role mapping for the prompt
    agents_block = f"  - \"{agent_name}\" — the primary AI assistant on this mailbox\n"
    for other in known_agents:
        agents_block += f"  - \"{other}\" — another AI assistant on this mailbox\n"

    # Illustrative second-agent name for the example rules — use a REAL configured
    # name so the examples track whatever known_agents is set to (no hardcoded
    # "Claude"). Falls back to a neutral phrase if no other agents are registered.
    eo   = known_agents[0] if known_agents else "the other assistant"
    eo_l = eo.lower()

    # Only warn about the assistant-name vs LLM-model-name clash when a registered
    # name actually collides with a provider (e.g. an assistant literally named
    # "claude" or "gemini") — otherwise the note is irrelevant noise.
    _model_names = {"claude", "gemini", "groq", "openai", "gpt"}
    _clash = next((n for n in [agent_name, *known_agents] if n.lower() in _model_names), None)
    name_note = (
        f"IMPORTANT: These are assistant NAMES, not LLM model names — \"{_clash}\" here is "
        f"the name of an assistant, not a reference to the {_clash} model.\n\n"
        if _clash else
        "IMPORTANT: These are assistant NAMES — match only on the names listed above.\n\n"
    )

    system_prompt = (
        "You are a mail router. A shared mailbox is monitored by several AI assistants. "
        "Each assistant has a unique name. Your ONLY job is to decide which assistant "
        "(if any) the sender is talking to.\n\n"
        "Registered assistants:\n"
        f"{agents_block}\n"
        + name_note +
        "Decision rules (apply in order):\n"
        "1. If the email greets or addresses an assistant by name "
        f"(e.g. 'Hi {agent_name}', 'Hey {eo}', '@{eo_l}', 'Dear {agent_name}', '{eo}, can you…'), "
        "return that assistant's name.\n"
        "2. Accept minor typos or short forms of a registered name.\n"
        "3. Addressing means talking TO the assistant. Talking ABOUT an assistant in the "
        f"third person ('{eo}'s draft was good', 'we can ask {agent_name} later', '{eo} already "
        "handled it') is NOT addressing — that alone does not count.\n"
        "4. If no registered assistant is being ADDRESSED — even if the email contains a "
        "task or request — return \"none\".\n\n"
        'Reply with ONLY valid JSON: {"addressed_to": "<name>"}\n'
        "Return the EXACT registered name (case-insensitive is fine), "
        'or "none" if no assistant is addressed.'
    )

    all_names = [agent_name] + known_agents
    user_msg = f"Subject: {subject}\n\nBody:\n{body[:3000]}"

    try:
        from agents import build_agent_llm, extract_text_content
        llm = build_agent_llm(model_label, temperature=0)
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
        text = extract_text_content(resp).strip()  # handles thinking-model block lists too

        # Extract JSON from possible markdown wrapping
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = _json.loads(text)
        addressed = str(result.get("addressed_to", "none")).strip().lower()

        # Validate against known names
        known_lower = {n.lower(): n for n in all_names}
        if addressed in known_lower:
            print(f"[Router] LLM classified email as addressed to: {addressed}")
            return addressed

        print(f"[Router] LLM returned '{addressed}' — treating as 'none'")
        return "none"

    except Exception as e:
        print(f"[Router] LLM routing failed ({e}) — falling back to string match")
        return _fallback_string_match(body, subject, agent_name, known_agents)


def _fallback_string_match(
    body: str, subject: str, agent_name: str, known_agents: list[str]
) -> str:
    """Simple string-match fallback if the LLM call fails."""
    text = (body + " " + subject).lower()

    # Check our own name first
    if agent_name.lower() in text:
        return agent_name.lower()

    # Check other agent names
    for name in known_agents:
        if name.lower() in text:
            return name.lower()

    return "none"
