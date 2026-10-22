"""
tools/qa_filler.py — Q&A Document Filler

Two tools that work together:
  read_qa_structure   -> extract Q&A table from any file format
  write_qa_responses  -> fill the answers back into the document

Supports: PDF, Word, Excel (read) -> Word or Excel (write)
Auto-detects question/response columns regardless of header name.
"""

import os
import json
import re
from datetime import datetime
from langchain_core.tools import tool
from core.config import PROPOSALS_OUTPUT_DIR

# ── Keywords for column detection ─────────────────────────────────────────────

_Q_KEYWORDS  = {"question", "query", "clarification", "requirement", "description",
                "concern", "issue", "bidder", "item", "scope", "remark"}
_A_KEYWORDS  = {"response", "answer", "reply", "dtm", "our", "solution",
                "comment", "clarif", "official"}


def _score_header(header: str, keywords: set) -> int:
    h = header.lower()
    return sum(1 for kw in keywords if kw in h)


def _detect_qa_columns(headers: list) -> tuple[int | None, int | None]:
    """
    Return (question_col_idx, response_col_idx).
    Uses keyword scoring; falls back to positional heuristics.
    """
    q_scores = [_score_header(h, _Q_KEYWORDS) for h in headers]
    a_scores = [_score_header(h, _A_KEYWORDS) for h in headers]

    q_idx = q_scores.index(max(q_scores)) if max(q_scores) > 0 else None
    a_idx = a_scores.index(max(a_scores)) if max(a_scores) > 0 else None

    # Fallback: last two non-trivial columns -> question is second-to-last, answer is last
    if q_idx is None or a_idx is None or q_idx == a_idx:
        n = len(headers)
        if n >= 2:
            q_idx = n - 2
            a_idx = n - 1

    return q_idx, a_idx


def _extract_from_excel(file_path: str) -> dict:
    import pandas as pd
    xl = pd.ExcelFile(file_path, engine="openpyxl")
    sheet_name = xl.sheet_names[0]
    df = xl.parse(sheet_name).fillna("")

    headers = list(df.columns)
    q_idx, a_idx = _detect_qa_columns(headers)

    questions = []
    for i, row in df.iterrows():
        q_text = str(row.iloc[q_idx]).strip() if q_idx is not None else ""
        a_text = str(row.iloc[a_idx]).strip() if a_idx is not None else ""
        if q_text and not a_text:
            # Collect context cols (everything except Q and A cols)
            ctx = {h: str(row[h]).strip() for j, h in enumerate(headers)
                   if j != q_idx and j != a_idx and str(row[h]).strip()}
            questions.append({"id": i, "context": ctx, "question": q_text})

    return {
        "source_format": "excel",
        "source_path":   file_path,
        "sheet":         sheet_name,
        "headers":       headers,
        "q_col":         headers[q_idx] if q_idx is not None else "",
        "a_col":         headers[a_idx] if a_idx is not None else "",
        "total_questions": len(questions),
        "questions": questions,
    }


def _extract_from_word(file_path: str) -> dict:
    from docx import Document
    doc = Document(file_path)
    all_questions = []

    for tbl_idx, table in enumerate(doc.tables):
        if not table.rows:
            continue
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        q_idx, a_idx = _detect_qa_columns(headers)
        if q_idx is None or a_idx is None:
            continue
        for r_idx, row in enumerate(table.rows[1:], start=1):
            cells = [cell.text.strip() for cell in row.cells]
            if q_idx >= len(cells) or a_idx >= len(cells):
                continue
            q_text = cells[q_idx]
            a_text = cells[a_idx]
            if q_text and not a_text:
                ctx = {headers[j]: cells[j] for j in range(len(cells))
                       if j != q_idx and j != a_idx and j < len(headers) and cells[j]}
                all_questions.append({
                    "id":       f"t{tbl_idx}_r{r_idx}",
                    "context":  ctx,
                    "question": q_text,
                    "_tbl":     tbl_idx,
                    "_row":     r_idx,
                    "_a_idx":   a_idx,
                })

    # Pick first table that had Q&A structure
    q_col = a_col = ""
    if all_questions:
        first = all_questions[0]
        tbl = doc.tables[first["_tbl"]]
        headers = [c.text.strip() for c in tbl.rows[0].cells]
        q_col_idx, a_col_idx = _detect_qa_columns(
            [c.text.strip() for c in tbl.rows[0].cells])
        hdr = [c.text.strip() for c in tbl.rows[0].cells]
        q_col = hdr[q_col_idx] if q_col_idx is not None else "Question"
        a_col = hdr[a_col_idx] if a_col_idx is not None else "Response"

    return {
        "source_format":   "word",
        "source_path":     file_path,
        "q_col":           q_col,
        "a_col":           a_col,
        "total_questions": len(all_questions),
        "questions":       all_questions,
    }


def _extract_from_pdf(file_path: str) -> dict:
    import pdfplumber
    all_questions = []
    q_col = a_col = ""

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            for tbl in (page.extract_tables() or []):
                if not tbl or len(tbl) < 2:
                    continue
                headers = [str(c or "").strip() for c in tbl[0]]
                q_idx, a_idx = _detect_qa_columns(headers)
                if q_idx is None or a_idx is None:
                    continue
                if not q_col:
                    q_col = headers[q_idx]
                    a_col = headers[a_idx]
                for r_idx, row in enumerate(tbl[1:], start=1):
                    q_text = str(row[q_idx] or "").strip() if q_idx < len(row) else ""
                    a_text = str(row[a_idx] or "").strip() if a_idx < len(row) else ""
                    if q_text and not a_text:
                        ctx = {headers[j]: str(row[j] or "").strip()
                               for j in range(len(row))
                               if j != q_idx and j != a_idx
                               and j < len(headers) and str(row[j] or "").strip()}
                        all_questions.append({
                            "id":       f"p{page_num}_r{r_idx}",
                            "context":  ctx,
                            "question": q_text,
                        })

    return {
        "source_format":   "pdf",
        "source_path":     file_path,
        "q_col":           q_col or "Question",
        "a_col":           a_col or "Response",
        "total_questions": len(all_questions),
        "questions":       all_questions,
    }


# ── LangChain Tools ───────────────────────────────────────────────────────────

@tool
def read_qa_structure(file_path: str) -> str:
    """
    Read a document (PDF, Word, Excel) and extract its Q&A structure.
    Automatically detects which column contains questions and which is the response column,
    regardless of header names.

    Returns a JSON string with:
      total_questions: how many unanswered questions were found
      q_col: name of the question column
      a_col: name of the response/answer column
      questions: list of {id, context, question} — the unanswered items

    Use this BEFORE answering — it tells you what needs to be filled in.

    Args:
        file_path: Absolute path to the PDF, Word, or Excel file.
    """
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls"):
            result = _extract_from_excel(file_path)
        elif ext in (".docx", ".doc"):
            result = _extract_from_word(file_path)
        elif ext == ".pdf":
            result = _extract_from_pdf(file_path)
        else:
            return json.dumps({"error": f"Unsupported format: {ext}"})

        print(f"[QA] Extracted {result['total_questions']} unanswered questions from {os.path.basename(file_path)}")
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def write_qa_responses(
    file_path: str,
    responses_json: str,
    output_format: str = "same",
) -> str:
    """
    Fill answers back into a Q&A document and save the result.

    Args:
        file_path:      Path to the original source file (from read_qa_structure).
        responses_json: JSON list of {id, answer} pairs matching the IDs from read_qa_structure.
                        Example: [{"id": "t0_r1", "answer": "Yes, participation is allowed."}]
        output_format:  "same" (default, preserves original format), "word", "pdf", or "excel".

    Returns the absolute path to the completed output file.
    """
    try:
        responses = json.loads(responses_json) if isinstance(responses_json, str) else responses_json
        answer_map = {str(r["id"]): str(r.get("answer", "")) for r in responses}
        ext = os.path.splitext(file_path)[1].lower()

        os.makedirs(PROPOSALS_OUTPUT_DIR, exist_ok=True)
        ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name  = os.path.splitext(os.path.basename(file_path))[0]
        fmt        = output_format.lower().strip() if output_format != "same" else ext.lstrip(".")

        if ext in (".xlsx", ".xls") or fmt in ("excel", "xlsx"):
            return _fill_excel(file_path, answer_map, ts, safe_name)
        elif ext in (".docx", ".doc") or fmt in ("word", "docx"):
            return _fill_word(file_path, answer_map, ts, safe_name)
        elif ext == ".pdf":
            # PDF is read-only — output as Word (or convert to PDF after)
            return _fill_pdf_as_word(file_path, answer_map, ts, safe_name, to_pdf=(fmt == "pdf"))
        else:
            return f"Unsupported source format: {ext}"
    except Exception as e:
        return f"Error filling Q&A responses: {e}"


def _fill_excel(file_path: str, answer_map: dict, ts: str, safe_name: str) -> str:
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = load_workbook(file_path)
    ws = wb.active

    headers = [cell.value or "" for cell in ws[1]]
    _, a_idx = _detect_qa_columns([str(h) for h in headers])
    if a_idx is None:
        return "Error: could not detect response column in Excel."

    filled = 0
    for row_num, row in enumerate(ws.iter_rows(min_row=2), start=2):
        row_id = str(row_num - 2)       # matches the 'id' field from _extract_from_excel (0-based df index)
        answer = answer_map.get(row_id)
        if answer and a_idx < len(row):
            cell = row[a_idx]
            if not cell.value:           # only fill empty cells
                cell.value     = answer
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                filled += 1

    out = os.path.join(PROPOSALS_OUTPUT_DIR, f"{safe_name}_filled_{ts}.xlsx")
    wb.save(out)
    print(f"[QA] Filled {filled} responses -> {out}")
    return out


def _fill_word(file_path: str, answer_map: dict, ts: str, safe_name: str) -> str:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document(file_path)
    filled = 0

    for tbl_idx, table in enumerate(doc.tables):
        if not table.rows:
            continue
        headers = [c.text.strip() for c in table.rows[0].cells]
        q_idx, a_idx = _detect_qa_columns(headers)
        if a_idx is None:
            continue
        for r_idx, row in enumerate(table.rows[1:], start=1):
            row_id = f"t{tbl_idx}_r{r_idx}"
            answer = answer_map.get(row_id)
            if answer and a_idx < len(row.cells):
                cell = row.cells[a_idx]
                if not cell.text.strip():    # only fill empty cells
                    para = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
                    para.clear()
                    run = para.add_run(answer)
                    run.font.size = Pt(10)
                    run.font.name = "Calibri"
                    filled += 1

    out = os.path.join(PROPOSALS_OUTPUT_DIR, f"{safe_name}_filled_{ts}.docx")
    doc.save(out)
    print(f"[QA] Filled {filled} responses -> {out}")
    return out


def _fill_pdf_as_word(file_path: str, answer_map: dict, ts: str, safe_name: str, to_pdf: bool = False) -> str:
    """PDF is read-only — reconstruct as a Word doc with answers filled in."""
    import pdfplumber
    from docx import Document
    from docx.shared import Pt, RGBColor
    _NAVY = RGBColor(0x1F, 0x33, 0x55)

    doc_out = Document()
    filled  = 0

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            for tbl in (page.extract_tables() or []):
                if not tbl or len(tbl) < 2:
                    continue
                headers = [str(c or "").strip() for c in tbl[0]]
                q_idx, a_idx = _detect_qa_columns(headers)

                table_obj = doc_out.add_table(rows=len(tbl), cols=len(headers))
                table_obj.style = "Table Grid"

                # Header row
                for ci, h in enumerate(headers):
                    cell = table_obj.rows[0].cells[ci]
                    cell.text = h
                    para = cell.paragraphs[0]
                    if para.runs:
                        para.runs[0].bold = True
                        para.runs[0].font.color.rgb = _NAVY

                # Data rows
                for ri, row in enumerate(tbl[1:], start=1):
                    row_id = f"p{page_num}_r{ri}"
                    for ci in range(min(len(row), len(headers))):
                        cell = table_obj.rows[ri].cells[ci]
                        if ci == a_idx and answer_map.get(row_id):
                            cell.text = answer_map[row_id]
                            filled += 1
                        else:
                            cell.text = str(row[ci] or "").strip()

            doc_out.add_paragraph()

    docx_out = os.path.join(PROPOSALS_OUTPUT_DIR, f"{safe_name}_filled_{ts}.docx")
    doc_out.save(docx_out)

    if to_pdf:
        import platform, subprocess
        pdf_out = docx_out.replace(".docx", ".pdf")
        if platform.system() != "Linux":
            try:
                from docx2pdf import convert
                convert(docx_out, pdf_out)
                os.remove(docx_out)
                print(f"[QA] Filled {filled} responses -> {pdf_out}")
                return pdf_out
            except Exception:
                pass
        try:
            from tools.converter_tool import find_soffice
            soffice = find_soffice()
            if soffice:
                result = subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf",
                     "--outdir", os.path.dirname(docx_out), docx_out],
                    capture_output=True, timeout=120,
                )
                if result.returncode == 0 and os.path.isfile(pdf_out):
                    os.remove(docx_out)
                    print(f"[QA] Filled {filled} responses -> {pdf_out}")
                    return pdf_out
        except Exception:
            pass

    print(f"[QA] Filled {filled} responses -> {docx_out}")
    return docx_out
