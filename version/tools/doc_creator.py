"""
tools/doc_creator.py — Proposal / Letter Creator

Creates a Word (.docx) or PDF proposal using the company letterhead as the
base document. The LLM selects India or UK letterhead based on context.

If "pdf" is requested, docx2pdf converts the Word file (requires MS Word
on the machine). Falls back to .docx with a warning if conversion fails.
"""

import os
import re
import json
from datetime import datetime
from langchain_core.tools import tool
from core.config import PROPOSALS_OUTPUT_DIR

# Dark navy used for the document title and section headings
_HEADING_COLOR = (0x1F, 0x33, 0x55)
_BODY_FONT     = "Calibri"
_BODY_SIZE_PT  = 11


def _setup_document(doc):
    """Apply professional page margins, font, and paragraph defaults."""
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml.ns import qn

    # Page margins — 1.1" left/right, 1" top/bottom
    section = doc.sections[0]
    section.left_margin   = Inches(1.1)
    section.right_margin  = Inches(1.1)
    section.top_margin    = Inches(1.0)
    section.bottom_margin = Inches(1.0)

    # Default body font
    style = doc.styles["Normal"]
    style.font.name = _BODY_FONT
    style.font.size = Pt(_BODY_SIZE_PT)
    style.font.color.rgb = RGBColor(0x23, 0x26, 0x2F)

    # Default paragraph spacing — small gap after, 1.15 line spacing
    pf = style.paragraph_format
    pf.space_after  = Pt(6)
    pf.space_before = Pt(0)
    try:
        from docx.shared import RGBColor as _C
        from docx.oxml import OxmlElement
        rPr = style.element.get_or_add_rPr()
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), _BODY_FONT)
        rFonts.set(qn("w:hAnsi"), _BODY_FONT)
        rPr.insert(0, rFonts)
    except Exception:
        pass


def _add_word_table(doc, headers: list, rows: list):
    """Add a styled Word table with a navy header row and alternating row shading."""
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    n_cols = len(headers)
    n_rows = len(rows) + 1

    tbl = doc.add_table(rows=n_rows, cols=n_cols)
    tbl.style = "Table Grid"

    def _set_cell_bg(cell, hex_color: str):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), hex_color)
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:val"), "clear")
        tcPr.append(shd)

    # Header row — navy background, white bold text
    for col_idx, hdr in enumerate(headers):
        cell = tbl.cell(0, col_idx)
        cell.text = str(hdr)
        _set_cell_bg(cell, "1F3355")
        para = cell.paragraphs[0]
        for run in para.runs:
            run.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            run.font.size = Pt(10)

    # Data rows — alternating white / light blue
    for row_idx, row_data in enumerate(rows, 1):
        bg = "E5F1FC" if row_idx % 2 == 0 else "FFFFFF"
        for col_idx in range(n_cols):
            val = str(row_data[col_idx]) if col_idx < len(row_data) else ""
            cell = tbl.cell(row_idx, col_idx)
            cell.text = val
            _set_cell_bg(cell, bg)
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(10)


def _add_content_block(doc, content: str):
    """
    Render a content string into Word paragraphs intelligently:
    - Lines starting with '- ' or '• ' → bullet list items
    - Lines starting with '1. ', '2. ' etc. → numbered list items
    - Everything else → plain paragraph
    Blank lines between blocks are used to separate groups.
    """
    from docx.shared import Pt, Inches
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    _bullet_re   = re.compile(r"^[-•\*]\s+")
    _numbered_re = re.compile(r"^\d+[\.\)]\s+")

    def _add_bullet(doc, text: str, numbered: bool = False):
        """Add a styled bullet or numbered paragraph."""
        para = doc.add_paragraph()
        pf   = para.paragraph_format
        pf.left_indent   = Inches(0.35)
        pf.first_line_indent = Inches(-0.2)
        pf.space_before  = Pt(2)
        pf.space_after   = Pt(2)
        run = para.add_run(("• " if not numbered else "") + text)
        run.font.name = _BODY_FONT
        run.font.size = Pt(_BODY_SIZE_PT)
        return para

    for block in re.split(r"\n\s*\n", content):
        block = block.strip()
        if not block:
            continue

        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        # Detect if all non-empty lines are bullets or numbered
        all_bullets  = all(_bullet_re.match(l.strip()) for l in lines)
        all_numbered = all(_numbered_re.match(l.strip()) for l in lines)

        if all_bullets:
            for line in lines:
                text = _bullet_re.sub("", line.strip())
                _add_bullet(doc, text, numbered=False)
        elif all_numbered:
            for i, line in enumerate(lines, 1):
                text = _numbered_re.sub("", line.strip())
                _add_bullet(doc, f"{i}. {text}", numbered=True)
        else:
            # Mixed block — render line by line, detecting individual bullet lines
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if _bullet_re.match(line):
                    _add_bullet(doc, _bullet_re.sub("", line), numbered=False)
                elif _numbered_re.match(line):
                    _add_bullet(doc, line, numbered=True)
                else:
                    doc.add_paragraph(line)


def _strip_inline_md(text: str) -> str:
    """Remove inline markdown we don't render (**bold**, *italic*, `code`)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def _render_markdown(doc, md: str):
    """
    Render a markdown string into the Word document:
      # / ## / ###   → headings
      | a | b |      → tables (separator row --- skipped)
      - / * / 1.     → bullet / numbered lists
      blank-line blocks → paragraphs
    """
    lines      = (md or "").replace("\r\n", "\n").split("\n")
    buffer     = []
    tbl_buffer = []
    heading_re = re.compile(r"^(#{1,4})\s+(.*)$")

    def _flush_buffer():
        if buffer:
            _add_content_block(doc, "\n".join(buffer))
            buffer.clear()

    def _flush_table():
        if not tbl_buffer:
            return
        rows = []
        for ln in tbl_buffer:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all((c and set(c) <= set("-: ")) for c in cells):
                continue  # markdown separator row
            rows.append([_strip_inline_md(c) for c in cells])
        tbl_buffer.clear()
        if rows:
            _add_word_table(doc, rows[0], rows[1:])

    for raw in lines:
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            _flush_buffer()
            tbl_buffer.append(line)
            continue
        _flush_table()
        h = heading_re.match(line.strip())
        if h:
            _flush_buffer()
            size = {1: 16, 2: 13, 3: 12}.get(len(h.group(1)), 12)
            _add_heading(doc, _strip_inline_md(h.group(2).strip()), size_pt=size)
        else:
            buffer.append(_strip_inline_md(line))

    _flush_table()
    _flush_buffer()


def _add_heading(doc, text: str, size_pt: int):
    """Add a professionally spaced heading — no blank-line spacers needed."""
    from docx.shared import Pt, RGBColor
    para = doc.add_paragraph()
    pf   = para.paragraph_format
    # Space above section headings; less space above the main title
    pf.space_before = Pt(16 if size_pt < 18 else 0)
    pf.space_after  = Pt(4)
    pf.keep_with_next = True   # heading stays with its first content paragraph
    run = para.add_run(text)
    run.bold           = True
    run.font.name      = _BODY_FONT
    run.font.size      = Pt(size_pt)
    run.font.color.rgb = RGBColor(*_HEADING_COLOR)
    return para


@tool
def create_proposal(
    title: str,
    sections: list,
    recipient: str = "",
    use_letterhead: str = "false",
    region: str = "india",
    format: str = "pdf",
    model_label: str = "",
    task_number: int = 0,
) -> str:
    """
    Create a formal proposal, report, or letter as a PDF or Word document.

    Parameters:
      title          : Document title.
      sections       : List of section objects. Each section is a dict with:
                         'heading'  — section heading (str)
                         'content'  — body text (str)
                         'table'    — optional {'headers': [...], 'rows': [[...]]}
      recipient      : Client or company name (optional).
      use_letterhead : MUST be false unless the email explicitly requests letterhead.
                       Never use letterhead for reports, analyses, or data documents.
      region         : 'india' (default) or 'uk' — only matters when use_letterhead is true.
      format         : 'pdf' (default) or 'word'.

    Returns the absolute file path of the saved document, or an error message.
    """
    try:
        from docx import Document

        title          = (title or "Document").strip()
        recipient      = recipient or ""
        region         = (region or "india").lower().strip()
        fmt            = (format or "pdf").lower().strip()
        sections       = sections or []
        use_letterhead = str(use_letterhead).strip().lower() in ("true", "1", "yes")

        if use_letterhead:
            from tools.asset_manager import scan_all, get_best_letterhead
            scan_all(verbose=False)
            lh_path = get_best_letterhead(region)
            if lh_path and os.path.isfile(lh_path):
                doc = Document(lh_path)
                print(f"[DOC] Letterhead applied ({region}).")
            else:
                doc = Document()
                print(f"[DOC] Letterhead not available for region '{region}' — using blank document.")
        else:
            doc = Document()
            print(f"[DOC] Plain document (no letterhead).")

        _setup_document(doc)

        from docx.shared import Pt, RGBColor
        _MUTED = RGBColor(0x62, 0x69, 0x76)

        def _meta_para(text: str, bold: bool = False, size: float = 10):
            p = doc.add_paragraph()
            p.paragraph_format.space_after  = Pt(2)
            p.paragraph_format.space_before = Pt(0)
            r = p.add_run(text)
            r.font.name  = _BODY_FONT
            r.font.size  = Pt(size)
            r.font.bold  = bold
            r.font.color.rgb = _MUTED
            return p

        # Date + recipient header
        _meta_para(datetime.now().strftime("%d %B %Y"))
        if recipient:
            _meta_para(f"To: {recipient}", bold=True)

        # Document title
        _add_heading(doc, title, size_pt=18)

        # Thin divider line under title
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn as _qn
        def _add_divider(doc):
            p = doc.add_paragraph()
            p.paragraph_format.space_after  = Pt(12)
            p.paragraph_format.space_before = Pt(2)
            pPr = p._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(_qn("w:val"), "single")
            bottom.set(_qn("w:sz"), "6")
            bottom.set(_qn("w:space"), "1")
            bottom.set(_qn("w:color"), "1F3355")
            pBdr.append(bottom)
            pPr.append(pBdr)
        _add_divider(doc)

        # Body sections
        for section in sections:
            heading    = section.get("heading", "").strip()
            content    = section.get("content", "").strip()
            table_data = section.get("table")

            if heading:
                _add_heading(doc, heading, size_pt=13)
            if content:
                _add_content_block(doc, content)
            if table_data and isinstance(table_data, dict):
                tbl_headers = table_data.get("headers", [])
                tbl_rows    = table_data.get("rows", [])
                if tbl_headers:
                    _add_word_table(doc, tbl_headers, tbl_rows)

        # Closing — spaced properly
        closing = doc.add_paragraph()
        closing.paragraph_format.space_before = Pt(24)
        closing.paragraph_format.space_after  = Pt(2)
        cr = closing.add_run("Yours sincerely,")
        cr.font.name = _BODY_FONT
        cr.font.size = Pt(_BODY_SIZE_PT)

        sig = doc.add_paragraph()
        sig.paragraph_format.space_before = Pt(28)
        sig.paragraph_format.space_after  = Pt(2)
        from core.config import get_agent_name, get_company_info
        _company = get_company_info()
        sr = sig.add_run(f"{get_agent_name()} | {_company['name']}")
        sr.font.name = _BODY_FONT
        sr.font.size = Pt(_BODY_SIZE_PT)
        sr.bold      = True
        sr.font.color.rgb = RGBColor(*_HEADING_COLOR)

        web = doc.add_paragraph()
        web.paragraph_format.space_before = Pt(0)
        wr = web.add_run(f"{_company['web']}  |  {_company['email']}")
        wr.font.name  = _BODY_FONT
        wr.font.size  = Pt(10)
        wr.font.color.rgb = _MUTED

        # Save
        os.makedirs(PROPOSALS_OUTPUT_DIR, exist_ok=True)
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title   = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:50].strip()
        model_prefix = f"{model_label.upper()}_" if model_label else ""
        task_tag     = f"T{task_number}_" if task_number else ""
        docx_path    = os.path.join(PROPOSALS_OUTPUT_DIR, f"{model_prefix}{task_tag}{safe_title}_{timestamp}.docx")
        doc.save(docx_path)

        if fmt == "word":
            print(f"[DOC] Saved Word -> {docx_path}")
            return docx_path

        # PDF conversion — docx2pdf on Windows, LibreOffice on Linux
        pdf_path = docx_path.replace(".docx", ".pdf")
        import platform
        _is_linux = platform.system() == "Linux"

        if not _is_linux:
            try:
                from docx2pdf import convert
                convert(docx_path, pdf_path)
                os.remove(docx_path)
                print(f"[DOC] Saved PDF (docx2pdf) -> {pdf_path}")
                return pdf_path
            except Exception as e:
                print(f"[DOC] docx2pdf failed: {e}")

        # LibreOffice (Linux/Docker, or Windows without MS Word)
        try:
            import subprocess
            from tools.converter_tool import find_soffice
            soffice = find_soffice()
            if soffice:
                result = subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf",
                     "--outdir", os.path.dirname(docx_path), docx_path],
                    capture_output=True, timeout=120,
                )
                if result.returncode == 0 and os.path.isfile(pdf_path):
                    os.remove(docx_path)
                    print(f"[DOC] Saved PDF (LibreOffice) -> {pdf_path}")
                    return pdf_path
        except Exception:
            pass

        print(f"[DOC] PDF conversion unavailable — returning .docx instead.")
        return docx_path

    except Exception as e:
        return f"Error creating proposal: {e}"


def _docx_to_pdf(docx_path: str) -> str:
    """Convert a .docx to .pdf (docx2pdf on Windows, LibreOffice on Linux). Returns the
    pdf path on success, else the original docx path."""
    import platform
    pdf_path  = docx_path.replace(".docx", ".pdf")
    if platform.system() != "Linux":
        try:
            from docx2pdf import convert
            convert(docx_path, pdf_path)
            os.remove(docx_path)
            print(f"[DOC] Saved PDF (docx2pdf) -> {pdf_path}")
            return pdf_path
        except Exception as e:
            print(f"[DOC] docx2pdf failed: {e}")
    try:
        import subprocess
        from tools.converter_tool import find_soffice
        soffice = find_soffice()
        if soffice:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf",
                 "--outdir", os.path.dirname(docx_path), docx_path],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0 and os.path.isfile(pdf_path):
                os.remove(docx_path)
                print(f"[DOC] Saved PDF (LibreOffice) -> {pdf_path}")
                return pdf_path
    except Exception:
        pass
    print(f"[DOC] PDF conversion unavailable — returning .docx instead.")
    return docx_path


@tool
def create_document(title: str, content: str, format: str = "pdf") -> str:
    """
    Create a Word or PDF document from markdown content.

    Write the body in plain markdown — the tool renders it into a clean, branded document:
      # Heading 1, ## Heading 2, ### Heading 3
      - bullet points
      1. numbered lists
      | Col A | Col B |  markdown tables (with a |---|---| separator row)
      blank lines separate paragraphs

    Use this for reports, proposals, letters, analyses, summaries — any prose document.

    Args:
        title:   Document title (becomes the heading and filename).
        content: The document body as markdown.
        format:  "pdf" (default) or "word".

    Returns the absolute path to the created file, or an error message.
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor

        title = (title or "Document").strip()
        fmt   = (format or "pdf").lower().strip()

        doc = Document()
        _setup_document(doc)

        # Title + divider
        _add_heading(doc, title, size_pt=18)
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn as _qn
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(_qn("w:val"), "single"); bottom.set(_qn("w:sz"), "6")
        bottom.set(_qn("w:space"), "1");     bottom.set(_qn("w:color"), "1F3355")
        pBdr.append(bottom); pPr.append(pBdr)

        # Body
        _render_markdown(doc, content)

        # Signature
        sig = doc.add_paragraph()
        sig.paragraph_format.space_before = Pt(24)
        from core.config import get_email_signature
        r = sig.add_run(get_email_signature())
        r.font.name = _BODY_FONT
        r.font.size = Pt(10)
        r.font.color.rgb = RGBColor(0x62, 0x69, 0x76)

        os.makedirs(PROPOSALS_OUTPUT_DIR, exist_ok=True)
        ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:50].strip()
        docx_path  = os.path.join(PROPOSALS_OUTPUT_DIR, f"{safe_title}_{ts}.docx")
        doc.save(docx_path)

        if fmt in ("word", "docx"):
            print(f"[DOC] Saved Word -> {docx_path}")
            return docx_path
        return _docx_to_pdf(docx_path)

    except Exception as e:
        return f"Error creating document: {e}"
