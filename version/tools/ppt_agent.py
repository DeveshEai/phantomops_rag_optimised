"""
tools/ppt_agent.py — Dedicated PPT-generation subagent

The email agent calls generate_presentation with just a topic + context string.
This module handles the full cycle internally:
  1. Focused LLM call to plan slide content as JSON
  2. create_presentation -> .pptx file
  3. review_presentation -> structural check
  4. One fix round if issues are found

Keeping all PPT-specific prompting and quality rules here lets the email
agent's system prompt stay lean.
"""

import os
import json
import re

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage


_PPT_SYSTEM_PROMPT = (
    "You are a senior business consultant and presentation writer for EAI Systems Pvt. Ltd.\n"
    "Read the supplied content and produce a complete, client-ready presentation as JSON.\n\n"

    "GROUNDING — THE MOST IMPORTANT RULE:\n"
    "Use ONLY facts present in the supplied source material or research findings. "
    "NEVER invent pricing, costs, financial figures, percentages, statistics, dates, "
    "client names, or metrics that are not explicitly in the source. If the source does "
    "not contain numbers, write accurate qualitative content instead — do NOT fabricate "
    "data to look impressive. A presentation with invented pricing is a serious failure.\n\n"

    "SLIDE CONTENT RULES:\n"
    "1. Generate the exact number of slides requested in the topic/context. If no specific number is requested, generate exactly 8 to 9 slides.\n"
    "2. Bullet slides: 4 to 6 bullet points per slide.\n"
    "3. Every bullet should be a clear, full sentence that conveys real information.\n"
    "   BAD:  'AI Integration'\n"
    "   GOOD: 'The proposed solution automates the document intake workflow described in the source'\n"
    "4. Include specific numbers, percentages, names, or dates ONLY when they appear in the "
    "source material or research. If the source has no such data, do not add any.\n"
    "5. The second-to-last slide must be 'Next Steps' with concrete action items.\n"
    "   A closing 'Thank You' slide is added automatically — do not include it.\n"
    "6. Every slide must be substantive and faithful to the source — informative, not padded.\n\n"

    "SLIDE LAYOUT — set a 'layout' field on each slide:\n"
    "  'list'         — full-width detailed bullet list; USE THIS for most content slides\n"
    "  'auto'         — card grid; use sparingly (only for 3-4 short comparison points)\n"
    "  'image_left'   — image left, detailed bullets right; use for 3-4 slides\n"
    "  'image_right'  — detailed bullets left, image right; use for 3-4 slides\n"
    "Rules:\n"
    "  - Use 'list' for at LEAST 6 slides — it holds the most content and looks professional.\n"
    "  - Use 'image_left' or 'image_right' for 3-4 slides to break visual monotony.\n"
    "  - NEVER use the same layout more than 3 times in a row.\n"
    "  - Avoid 'auto' (card grid) unless the content is genuinely 3-4 short parallel points.\n\n"

    "TABLE SLIDES — REQUIRED when source data has numbers, rankings, or comparisons:\n"
    "  Add a 'table' key (no 'bullets' key). Renders a professional styled data table.\n"
    "  MANDATORY for: performance data, rankings, branch comparisons, financial figures, timelines.\n"
    "  Include 2 to 4 table slides per deck whenever the data supports it.\n"
    "  Format:\n"
    '    {"heading": "Slide Title", "table": {"headers": ["Col A", "Col B", "Col C"], "rows": [["r1c1","r1c2","r1c3"]]}}\n'
    "  Rules: max 6 columns, max 12 rows, no 'bullets' on the same slide.\n\n"

    "IMAGE SLIDES — add visual depth to the deck:\n"
    "  Use 'image_left' or 'image_right' + 'image_query' for context-rich slides.\n"
    "  image_query: 2-4 descriptive keywords: 'team meeting', 'data analytics dashboard',\n"
    "  'ai automation', 'office collaboration', 'financial growth chart', 'global business'.\n"
    "  The system matches the closest image from the company library.\n\n"

    "OUTPUT FORMAT — return ONLY a valid JSON object, nothing else:\n"
    "- Use double quotes only.\n"
    "- No backslashes inside string values.\n"
    "- No markdown fences, no explanation text.\n\n"

    "{\n"
    '  "title": "Presentation Title",\n'
    '  "subtitle": "One-line client value proposition",\n'
    '  "template": "sales_blue",\n'
    '  "slides": [\n'
    '    {\n'
    '      "heading": "The Business Challenge",\n'
    '      "layout": "list",\n'
    '      "bullets": [\n'
    '        "Manual processing of 500+ monthly invoices consumes 3 FTE and introduces a 15% error rate",\n'
    '        "Siloed legacy systems prevent real-time visibility into order status and financial exposure",\n'
    '        "Compliance audits require 2 weeks of manual data gathering, delaying quarterly close by 5 days",\n'
    '        "Customer onboarding cycle averages 22 days due to manual document verification steps"\n'
    '      ]\n'
    '    },\n'
    '    {\n'
    '      "heading": "Our AI Automation Platform",\n'
    '      "layout": "image_left",\n'
    '      "image_query": "automation robot",\n'
    '      "bullets": [\n'
    '        "Intelligent document processing handles PDFs, emails, and forms with 98% extraction accuracy",\n'
    '        "End-to-end workflow automation reduces manual touchpoints by 80% within the first quarter",\n'
    '        "Real-time dashboards give managers instant visibility into process health and bottlenecks"\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    "}"
)


def _call_llm(prompt: str, content: str, model_label: str, temperature: float = 0.6) -> str:
    """Call the selected LLM provider and return raw text.

    Slide content is creative writing, so generation runs warm by default.
    The fix round passes a lower temperature to follow structural
    corrections more closely.
    """
    from agents import build_agent_llm, extract_text_content

    llm = build_agent_llm(model_label, temperature=temperature)
    response = llm.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content=content[:50_000]),
    ])
    return extract_text_content(response)


def _extract_deck(raw: str) -> dict:
    """Parse JSON from LLM output, fixing invalid escape sequences."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    candidate = match.group()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', candidate)
        return json.loads(cleaned)


@tool
def generate_presentation(
    topic: str,
    context: str,
    template: str = "sales_blue",
    model_label: str = "groq",
    task_number: int = 0,
    content_language: str = "",
    slide_count: int = 0,
    use_existing_template: bool = True,
) -> str:
    """
    Generate a complete branded PowerPoint presentation from a topic and context.

    Use this for ALL PowerPoint or slide deck requests — it handles content
    planning, slide creation, layout review, and one fix round automatically.

    Args:
        topic:    Short description of what the presentation covers.
        context:  Full email body and any attachment text to draw content from.
        template: Slide theme — one of:
                    sales_blue   (default — external / client-facing)
                    sales_orange (technical or solution overview)
                    marketing    (campaigns, product launches)
                    internal     (reports, team updates)
        content_language: "ar"/"en" if the sender explicitly requested (or a
                    MODIFY follow-up should keep) a specific language for the
                    deck — e.g. "write this in Arabic" or "translate this to
                    English". Empty ("") means no explicit requirement; the
                    slides follow whatever language `context` is already in.
                    This is separate from `context`'s own language because
                    this module has its own content-generation prompt,
                    independent of the caller's — that requirement doesn't
                    reach here unless passed explicitly. Verified this was a
                    real gap: a caller-side "write in Arabic" instruction had
                    no effect on the actual slide JSON generated here.
        slide_count: explicit number of slides the sender asked for (e.g.
                    "make it 6 slides"). 0 means no explicit count was given —
                    falls back to _PPT_SYSTEM_PROMPT's own default (8-9).

    Returns:
        Absolute path to the created .pptx file, or an error message string.
    """
    from tools.ppt_creator import create_presentation
    from tools.ppt_reviewer import review_presentation
    from tools.asset_manager import scan_all, get_asset_summary, get_template_summary

    # Index any new assets added since the last run
    scan_all(verbose=True)
    from core.config import brand_text
    prompt_with_assets = brand_text(
        _PPT_SYSTEM_PROMPT
        + f"\n\nTEMPLATE LIBRARY:\n{get_template_summary()}\n"
        + f"\nIMAGE LIBRARY (use these themes for image_query):\n{get_asset_summary()}\n"
    )
    if content_language == "ar":
        prompt_with_assets += (
            "\n\nLANGUAGE (explicit requirement — highest priority): write EVERY string in the "
            "output JSON (title, subtitle, headings, bullets, table headers/rows) in Arabic — "
            "the full slide content itself, not just a translated title. This applies even if the "
            "source material below is in English; translate/write it fresh in Arabic.\n"
        )
    elif content_language == "en":
        prompt_with_assets += (
            "\n\nLANGUAGE (explicit requirement — highest priority): write EVERY string in the "
            "output JSON (title, subtitle, headings, bullets, table headers/rows) in English — "
            "the full slide content itself. This applies even if the source material below is in "
            "Arabic or another language; translate it fully into English, do not leave any "
            "non-English text in the output.\n"
        )
    if slide_count:
        prompt_with_assets += (
            f"\n\nSLIDE COUNT (explicit requirement — overrides SLIDE CONTENT RULES rule 1 "
            f"above): generate EXACTLY {slide_count} content slides — not the 8-9 default, not "
            f"an approximation. Count carefully; this is a hard requirement, not a suggestion.\n"
        )

    content_input = f"Topic: {topic}\n\nSource material:\n{context}"

    # ── Marp High-Impact Presentation Engine (Primary) ────────────────────────
    try:
        from tools.marp_creator import compile_marp_to_pptx, MARP_SYSTEM_PROMPT
        marp_prompt = brand_text(MARP_SYSTEM_PROMPT)
        marp_prompt += (
            "\n\nCRITICAL MANDATE: NEVER output template placeholders like '[Insert #]', '$[Insert]', "
            "'[Insert %]', '[Insert Forecast]', '[Date]', '[App X]', or '[Company Name]'. Write complete, finished, "
            "authoritative executive prose and concrete strategic deliverables."
        )
        if content_language == "ar":
            marp_prompt += "\n\nLANGUAGE (explicit requirement): Write all slide content, headings, and cards in Arabic."
        elif content_language == "en":
            marp_prompt += "\n\nLANGUAGE (explicit requirement): Write all slide content, headings, and cards in English."
        if slide_count:
            marp_prompt += f"\n\nSLIDE COUNT (explicit requirement): Generate EXACTLY {slide_count} slides."

        print(f"[PPT AGENT] Generating high-quality presentation using Marp Engine...")
        marp_md = _call_llm(marp_prompt, content_input, model_label=model_label, temperature=0.5)
        pptx_path = compile_marp_to_pptx(marp_md, title=topic, task_number=task_number, model_label=model_label)
        if pptx_path and os.path.isfile(pptx_path):
            print(f"[PPT AGENT] Marp presentation successfully built: {pptx_path}")
            return pptx_path
    except Exception as me:
        print(f"[PPT AGENT] Marp generation failed ({me}) — falling back to standard PPT generator")

    # ── Round 1: generate slide JSON → build PPTX → review ────────────────────
    try:
        raw1 = _call_llm(prompt_with_assets, content_input, model_label=model_label, temperature=0.6)
        deck1 = _extract_deck(raw1)
    except Exception as e:
        return f"PPT content generation failed: {e}"

    deck1["template"]     = template
    deck1["use_existing_template"] = use_existing_template
    deck1["model_label"]  = model_label or ""
    if task_number:
        deck1["task_number"] = task_number
    if slide_count:
        # Tells create_presentation's slide splitter to keep exactly the
        # slides the LLM returned instead of auto-splitting a bullet-heavy
        # one — an explicit slide count must not silently grow because one
        # slide had more information to fit. See the Bug Fixes Log.
        deck1["slide_count"] = slide_count
    pptx_path = create_presentation.invoke({"slides_json": json.dumps(deck1)})
    print(f"[PPT AGENT] Round 1 path: {pptx_path}")

    if not isinstance(pptx_path, str) or not pptx_path.strip().endswith(".pptx"):
        return f"PPT creation failed: {pptx_path}"

    review = review_presentation.invoke({"pptx_path": pptx_path})
    print(f"[PPT AGENT] Review: {review[:120]}")

    if review == "LGTM":
        return pptx_path

    # ── Round 2: fix layout issues and rebuild once ────────────────────────────
    fix_prompt = (
        f"{prompt_with_assets}\n\n"
        "ADDITIONAL REQUIREMENT — the previous version had these structural issues. "
        "Fix each one in the JSON you produce:\n"
        f"{review}"
    )
    fix_input = f"{content_input}\n\nIssues to fix:\n{review}"

    try:
        raw2 = _call_llm(fix_prompt, fix_input, model_label=model_label, temperature=0.4)
        deck2 = _extract_deck(raw2)
        deck2["template"]    = template
        deck2["use_existing_template"] = use_existing_template
        deck2["model_label"] = model_label or ""
        if task_number:
            deck2["task_number"] = task_number
        if slide_count:
            deck2["slide_count"] = slide_count
        pptx_path2 = create_presentation.invoke({"slides_json": json.dumps(deck2)})
        print(f"[PPT AGENT] Round 2 path: {pptx_path2}")
        if isinstance(pptx_path2, str) and pptx_path2.strip().endswith(".pptx"):
            return pptx_path2
    except Exception as e:
        print(f"[PPT AGENT] Fix round failed: {e}")

    return pptx_path  # return round-1 result if fix fails
