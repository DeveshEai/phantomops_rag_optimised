"""
tools/engines/csv_engine.py — Render engine for CSV files.

Phase 3 (RENDER) calls render() with the LLM-written markdown content and a
context dict.  It invokes tools/excel_creator.py → create_spreadsheet() and
returns (files, answer) so assistant_agent.py's RENDER block stays thin.

Customise this file to:
  - Change CSV delimiter or encoding options applied by excel_creator
  - Add BOM for Excel-safe Unicode CSVs
  - Override the covering-email text for CSV attachments
"""

import os


def render(written_content: str, ctx: dict) -> tuple[list[str], str]:
    """
    Render written_content as a CSV file.

    Args:
        written_content: Single markdown table produced by the WRITE phase.
        ctx: Runtime context dict passed through from assistant_agent.run().
             Useful keys: title, subject, model_label, task_number, agent_name.

    Returns:
        (files, answer) — files is a list with the .csv path on success
        (empty if creation failed); answer is always "" so the caller builds
        the covering email from the file list.
    """
    from tools.excel_creator import create_spreadsheet

    title = ctx.get("title") or ctx.get("subject") or "Data"
    agent_name = ctx.get("agent_name", "ASSISTANT")
    files: list[str] = []

    result = create_spreadsheet.invoke({
        "title":   title,
        "content": written_content,
        "format":  "csv",
    })

    if isinstance(result, str) and os.path.isfile(result):
        files.append(result)
        print(f"[{agent_name}][csv_engine] Rendered CSV: {os.path.basename(result)}")
    else:
        print(f"[{agent_name}][csv_engine] CSV generation did not produce a file: {result}")

    return files, ""
