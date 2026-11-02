"""
version/engines/llm/ — Multi-Provider LLM Inference & Resilience Engine
Handles model provider instantiation (Groq, OpenAI, Claude, Gemini, Ollama),
exponential backoff, context trimming, and tool execution loops.
"""

from core.config import (
    get_enabled_models,
    get_agent_model,
    get_provider_model,
    get_agent_name,
    brand_text,
)
from agents import (
    build_agent_llm,
    invoke_with_tpm_retry,
    extract_text_content,
    run_tool_loop,
    classify_llm_failure,
)

__all__ = [
    "build_agent_llm",
    "invoke_with_tpm_retry",
    "extract_text_content",
    "run_tool_loop",
    "classify_llm_failure",
    "get_enabled_models",
    "get_agent_model",
    "get_provider_model",
    "get_agent_name",
    "brand_text",
]
