"""
tools/engines/pdf_engine.py — Render engine for PDF documents.

Phase 3 (RENDER) calls render() with the LLM-written markdown content and a
context dict.  It invokes tools/doc_creator.py → create_document() and
returns (files, answer) so assistant_agent.py's RENDER block stays thin.

Customise this file to:
  - Change PDF styling / page size / margins applied by doc_creator
  - Add headers, footers, or watermarks
  - Insert a cover page or table of contents
  - Override the covering-email text for PDF attachments
"""

import os


def render(written_content: str, ctx: dict) -> tuple[list[str], str]:
    """
    Render written_content as a PDF file.

    Args:
        written_content: Markdown string produced by the WRITE phase.
        ctx: Runtime context dict passed through from assistant_agent.run().
             Useful keys: title, subject, model_label, task_number, agent_name.

    Returns:
        (files, answer) — files is a list with the .pdf path on success
        (empty if creation failed); answer is always "" so the caller builds
        the covering email from the file list.
    """
    from tools.doc_creator import create_document

    title = ctx.get("title") or ctx.get("subject") or "Document"
    agent_name = ctx.get("agent_name", "ASSISTANT")
    page_count = ctx.get("page_count", 0)
    files: list[str] = []

    result = create_document.invoke({
        "title":      title,
        "content":    written_content,
        "format":     "pdf",
        "page_count": page_count,
    })

    if isinstance(result, str) and os.path.isfile(result):
        files.append(result)
        print(f"[{agent_name}][pdf_engine] Rendered PDF: {os.path.basename(result)}")
    else:
        print(f"[{agent_name}][pdf_engine] PDF generation did not produce a file: {result}")

    return files, ""
