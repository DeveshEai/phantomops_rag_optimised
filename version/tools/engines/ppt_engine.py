"""
tools/engines/ppt_engine.py — Render engine for PowerPoint (.pptx) presentations.

Phase 3 (RENDER) calls render() with the LLM-written content brief and a context
dict.  It invokes tools/ppt_agent.py → generate_presentation() and returns
(files, answer) so assistant_agent.py's RENDER block stays thin.

Customise this file to:
  - Change the default template selection logic (which branded template to use)
  - Add per-topic or per-department template overrides
  - Adjust the slide-count or content-language forwarding
  - Add post-processing (speaker notes export, thumbnail generation, etc.)
  - Override the covering-email text for PPT attachments
"""

import os
import re


# ── Template-selection helper ─────────────────────────────────────────────────
# Reads the body of the request to pick a named company template.
# Available templates (assets/templates/):
#   sales_blue   — Template 1: client-facing sales (blue)      [DEFAULT]
#   sales_orange — Template 2: sales + tech solutions (orange)
#   marketing    — Template 3: marketing and events (dark)
#   internal     — Template 4: internal / technical (light)
#
# Add new regex branches here to support additional templates or per-client
# branding without changing any other file.

def _pick_template(body_lower: str) -> str:
    """Return the template key that best matches the request wording."""
    if re.search(r"sales.*orange|orange.*template", body_lower):
        return "sales_orange"
    if re.search(r"marketing|dark.*template", body_lower):
        return "marketing"
    if re.search(r"internal|technical|light.*template", body_lower):
        return "internal"
    return "sales_blue"   # default


def render(written_content: str, ctx: dict) -> tuple[list[str], str]:
    """
    Render written_content as a branded PowerPoint .pptx file.

    Args:
        written_content: Content brief (prose) produced by the WRITE phase.
        ctx: Runtime context dict passed through from assistant_agent.run().
             Required keys: title, body_lower, model_label, task_number,
             content_language, slide_count, agent_name.

    Returns:
        (files, answer) — files is a list with the .pptx path on success
        (empty if creation failed); answer is always "" so the caller builds
        the covering email from the file list.
    """
    from tools.ppt_agent import generate_presentation

    title          = ctx.get("title") or ctx.get("subject") or "Presentation"
    body_lower     = ctx.get("body_lower", "")
    model_label    = ctx.get("model_label", "")
    task_number    = ctx.get("task_number", 0)
    content_lang   = ctx.get("content_language", "")
    slide_count    = ctx.get("slide_count", 0)
    agent_name     = ctx.get("agent_name", "ASSISTANT")
    template       = _pick_template(body_lower)

    files: list[str] = []

    result = generate_presentation.invoke({
        "topic":            title,
        "context":          written_content,
        "template":         template,
        "model_label":      model_label,
        "task_number":      task_number,
        "content_language": content_lang,
        "slide_count":      slide_count,
    })

    if isinstance(result, str) and os.path.isfile(result):
        files.append(result)
        print(f"[{agent_name}][ppt_engine] Rendered PPT: {os.path.basename(result)}")
    else:
        print(f"[{agent_name}][ppt_engine] PPT generation did not produce a file: {result}")

    return files, ""
