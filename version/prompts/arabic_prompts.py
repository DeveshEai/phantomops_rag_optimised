"""
version/prompts/arabic_prompts.py — Arabic Language & RTL Adaptation Prompts
"""

def get_arabic_prompt_guidance(
    agent_name: str,
    content_lang: str = "",
    detected_lang: str = "en"
) -> str:
    """
    Returns explicit prompt instructions ensuring Arabic responses are generated correctly,
    safeguarding against the common failure modes of generic placeholders or English responses.
    """
    sig_reminder = (
        f' Still sign off as "{agent_name}" exactly as given above (keep the name '
        f'and company name in Latin script, unchanged) — translate only the '
        f'closing phrase itself (e.g. \'Best regards\' -> a natural Arabic closing '
        f"like 'مع أطيب التحيات'), never replace the signer's name with a "
        f"placeholder."
    )

    if content_lang == "ar":
        return (
            "\n\nCONTENT LANGUAGE (explicit user request — highest priority, "
            "overrides any other language default): the user asked for the "
            "DELIVERABLE ITSELF to be written in Arabic. Write the ENTIRE "
            "document/essay/answer in Arabic — the actual full content, not a "
            "description, summary, or discussion of it in English. This applies "
            "regardless of what language the request or any source material is in."
            + sig_reminder
        )
    elif content_lang == "en":
        return (
            "\n\nCONTENT LANGUAGE (explicit user request — highest priority): "
            "the user asked for the deliverable itself to be written in English, "
            "even if the request or source material is in another language. Write "
            "the entire document/essay/answer in English."
        )
    elif detected_lang == "ar":
        return (
            "\n\nLANGUAGE: The sender's request is in Arabic. Write your entire "
            "reply in Arabic (match their dialect/register where reasonable), "
            "even if source documents or thread history are in English."
            + sig_reminder
        )
    elif detected_lang == "mixed":
        return (
            "\n\nLANGUAGE: The sender's request mixes Arabic and English. Reply "
            "in whichever language best matches the main substance of their "
            "request — default to Arabic if genuinely unclear which dominates."
            + sig_reminder
        )
    return ""

