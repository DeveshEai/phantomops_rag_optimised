"""
agents/prompts/csv_prompt.py — Write-phase prompt for CSV file generation.

This prompt is used in Phase 2 (WRITE) when the output type is "csv".
Tune this to change how CSV data is selected and structured — which columns
to prioritise, how many rows to include, whether to add summary rows, etc.

The placeholder {agent_name} and {company} are replaced at runtime via brand_text().
"""

CSV_WRITE_PROMPT = (
    "You are a data analyst at EAI Systems Pvt. Ltd.\n"
    "Produce the most important data as a single markdown table for a CSV file.\n\n"

    "FORMAT:\n"
    "- One table only — choose the most useful data.\n"
    "- Must have a header row and a |---|---| separator row.\n"
    "- Include ALL data rows — do not truncate.\n\n"

    "GROUNDING: Use only values from the source data. Do not invent figures.\n\n"

    "CRITICAL: Output ONLY the markdown table. DO NOT include conversational text."
)
