"""
version/prompts/ — Centralized Prompt Registry for PhantomOps
Provides direct, easy access to all system prompts, write prompts, research briefs,
QA answering prompts, Arabic localization instructions, and meeting extractors.
"""

from .system_personas import TOOL_LOOP_INJECTION_GUARD, SALLY_PERSONA_BASE, ASSISTANT_SYSTEM_PROMPT
from .write_prompts import WRITE_PROMPTS
from .research_prompts import get_search_prompt
from .qa_prompts import QA_ANSWER_PROMPT, QA_SINGLE_THRESHOLD, QA_BATCH_SIZE
from .arabic_prompts import get_arabic_prompt_guidance
from .meeting_prompts import get_meeting_extract_prompt, get_meeting_cancel_prompt

__all__ = [
    "TOOL_LOOP_INJECTION_GUARD",
    "SALLY_PERSONA_BASE",
    "ASSISTANT_SYSTEM_PROMPT",
    "WRITE_PROMPTS",
    "get_search_prompt",
    "QA_ANSWER_PROMPT",
    "QA_SINGLE_THRESHOLD",
    "QA_BATCH_SIZE",
    "get_arabic_prompt_guidance",
    "get_meeting_extract_prompt",
    "get_meeting_cancel_prompt",
]
