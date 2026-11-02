"""
agents/prompts/assistant_prompt.py — Sally's general-purpose agent prompt
Backward-compatible wrapper importing from version/prompts/system_personas.py
"""

try:
    from prompts.system_personas import ASSISTANT_SYSTEM_PROMPT
except ImportError:
    try:
        from version.prompts.system_personas import ASSISTANT_SYSTEM_PROMPT
    except ImportError:
        ASSISTANT_SYSTEM_PROMPT = (
            "You are Sally, a capable, professional AI assistant at EAI Systems Pvt. Ltd.\n"
            "You handle inbound emails. Do whatever the email asks: answer questions, research "
            "topics, analyse attached files, suggest ideas, give recommendations, brainstorm, "
            "compare options, draft content, or produce a document/spreadsheet/presentation."
        )

__all__ = ["ASSISTANT_SYSTEM_PROMPT"]
