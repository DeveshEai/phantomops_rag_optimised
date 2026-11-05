"""
tools/engines/excel_engine.py — Render engine for Excel (.xlsx) workbooks.

Phase 3 (RENDER) calls render() with the LLM-written markdown content and a
context dict.  It invokes tools/excel_creator.py → create_spreadsheet() and
returns (files, answer) so assistant_agent.py's RENDER block stays thin.

Customise this file to:
  - Change sheet-level formatting / colour themes applied by excel_creator
  - Add pivot tables or charts post-creation
  - Override the covering-email text for Excel attachments
"""

import os


def render(written_content: str, ctx: dict) -> tuple[list[str], str]:
    """
    Render written_content as an Excel .xlsx workbook.

    Args:
        written_content: Markdown string (## Sheet Name + tables) produced by the WRITE phase.
        ctx: Runtime context dict passed through from assistant_agent.run().
             Useful keys: title, subject, model_label, task_number, agent_name.

    Returns:
        (files, answer) — files is a list with the .xlsx path on success
        (empty if creation failed); answer is always "" so the caller builds
        the covering email from the file list.
    """
    from tools.excel_creator import create_spreadsheet

    title = ctx.get("title") or ctx.get("subject") or "Spreadsheet"
    agent_name = ctx.get("agent_name", "ASSISTANT")
    files: list[str] = []

    result = create_spreadsheet.invoke({
        "title":   title,
        "content": written_content,
        "format":  "excel",
    })

    if isinstance(result, str) and os.path.isfile(result):
        files.append(result)
        print(f"[{agent_name}][excel_engine] Rendered EXCEL: {os.path.basename(result)}")
    else:
        print(f"[{agent_name}][excel_engine] EXCEL generation did not produce a file: {result}")

    return files, ""
