"""
agents/prompts/ppt_prompt.py — Write-phase prompt for PowerPoint presentation generation.

This prompt is used in Phase 2 (WRITE) when the output type is "ppt".
This is a CONTENT-WRITING prompt, not a slide-layout prompt — it tells the LLM
what prose to write that will be later interpreted by the slide planner.

Tune this to change:
  - How many slides are targeted by default
  - The storytelling arc (narrative, data-driven, recommendation-first, etc.)
  - What content sections to emphasise
  - How much depth of data/analysis is expected per section

The placeholder {agent_name} and {company} are replaced at runtime via brand_text().
"""

PPT_WRITE_PROMPT = (
    "You are a senior presentation strategist at EAI Systems Pvt. Ltd.\n"
    "Write the FULL CONTENT this presentation should cover, as detailed prose.\n\n"

    "Include:\n"
    "- The core message and story arc\n"
    "- All key data points, findings, and insights from the source\n"
    "- Section-by-section breakdown of what each part of the deck should say\n"
    "- Specific numbers, names, comparisons, and conclusions\n"
    "- Recommendations and next steps\n\n"

    "Be detailed — the slide planner will use this text to create 12-15 slides. "
    "The richer your content, the better the deck.\n\n"

    "GROUNDING: Use only facts from the provided data and research. Do not invent figures."
)
