"""
tools/converter_tool.py — File Format Converter

Pure format conversion — no LLM, no reasoning.
Uses LibreOffice (Linux/Docker) or docx2pdf (Windows).

Supported conversions:
  .pptx -> .pdf
  .docx -> .pdf
  .xlsx -> .pdf
  .pdf  -> .docx  (basic, via LibreOffice)
  .pdf  -> .xlsx  (table extraction)
  .docx -> .xlsx  (table extraction)
  .pptx -> .xlsx  (table extraction)
  .xlsx -> .docx  (sheets as tables)
  .pptx -> .docx  (slide text/tables as sections)
  .csv  -> .xlsx
"""

import os
import platform
import shutil
import subprocess
from datetime import datetime
from langchain_core.tools import tool
from core.config import OUTPUT_DIR


_SOFFICE_CACHE = None


def find_soffice() -> str | None:
    """Locate the LibreOffice binary across platforms (cached).

    Looks for 'libreoffice' or 'soffice' on PATH (Linux/Docker, or a Windows
    install that added itself to PATH), then the default Windows install
    locations where the binary is 'soffice.exe' rather than 'libreoffice'.
    Returns the executable path, or None if LibreOffice is not installed.
    """
    global _SOFFICE_CACHE
    if _SOFFICE_CACHE is not None:
        return _SOFFICE_CACHE or None
    for name in ("libreoffice", "soffice"):
        found = shutil.which(name)
        if found:
            _SOFFICE_CACHE = found
            return found
    if platform.system() == "Windows":
        for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
            if os.path.isfile(c):
                _SOFFICE_CACHE = c
                return c
    _SOFFICE_CACHE = ""
    return None


def _lo_convert(input_path: str, target_fmt: str, out_dir: str) -> str | None:
    """Run LibreOffice headless conversion. Returns output path or None on failure."""
    soffice = find_soffice()
    if not soffice:
        print("[CONVERTER] LibreOffice not found — install it or set it on PATH.")
        return None
    try:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", target_fmt,
             "--outdir", out_dir, input_path],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[CONVERTER] LibreOffice error: {result.stderr.decode()[:300]}")
            return None
        # LibreOffice replaces the extension of the original filename
        base = os.path.splitext(os.path.basename(input_path))[0]
        out_path = os.path.join(out_dir, f"{base}.{target_fmt}")
        return out_path if os.path.isfile(out_path) else None
    except Exception as e:
        print(f"[CONVERTER] LibreOffice failed: {e}")
        return None


def convert_file(input_path: str, output_format: str) -> str:
    """
    Convert a file to the requested format. Returns the output file path.
    Raises ValueError / RuntimeError on failure.
    """
    if not os.path.isfile(input_path):
        raise ValueError(f"Input file not found: {input_path}")

    src_ext  = os.path.splitext(input_path)[1].lower().lstrip(".")
    tgt_fmt  = output_format.lower().strip().lstrip(".")

    if src_ext == tgt_fmt:
        return input_path  # nothing to do

    # Build output directory — timestamped subfolder inside output/
    out_dir = os.path.join(OUTPUT_DIR, "converted")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[CONVERTER] {src_ext} -> {tgt_fmt}: {os.path.basename(input_path)}")

    # ── .docx -> .pdf ─────────────────────────────────────────────────────────
    if src_ext in ("docx", "doc") and tgt_fmt == "pdf":
        try:
            from docx2pdf import convert as d2p
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_path = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.pdf")
            d2p(input_path, pdf_path)
            if os.path.isfile(pdf_path):
                print(f"[CONVERTER] Saved (docx2pdf) -> {pdf_path}")
                return pdf_path
        except Exception:
            pass
        result = _lo_convert(input_path, "pdf", out_dir)
        if result:
            print(f"[CONVERTER] Saved (LibreOffice) -> {result}")
            return result
        raise RuntimeError("PDF conversion failed — neither docx2pdf nor LibreOffice succeeded.")

    # ── .pptx / .xlsx / .csv -> .pdf ──────────────────────────────────────────
    if tgt_fmt == "pdf":
        result = _lo_convert(input_path, "pdf", out_dir)
        if result:
            print(f"[CONVERTER] Saved (LibreOffice) -> {result}")
            return result
        raise RuntimeError(f"Cannot convert {src_ext} to PDF — LibreOffice failed.")

    # ── .pdf -> .docx ─────────────────────────────────────────────────────────
    if src_ext == "pdf" and tgt_fmt in ("docx", "word"):
        # LibreOffice converts a TEXT pdf well (keeps layout) but only embeds page
        # images for a SCANNED pdf — giving a Word doc with no selectable text. So:
        # read the pdf first (OCR-capable); if it's image-only, build the docx from
        # the OCR'd text instead, so the words are actually usable.
        from tools.file_reader import extract_pdf_text
        pdf_text = extract_pdf_text(input_path) or ""
        is_scanned = "(OCR)" in pdf_text   # extract_pdf_text tags OCR'd pages
        if not is_scanned:
            result = _lo_convert(input_path, "docx", out_dir)
            if result:
                print(f"[CONVERTER] Saved (LibreOffice) -> {result}")
                return result
        if pdf_text and not pdf_text.startswith(("Error", "The PDF appears")):
            from docx import Document
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.docx")
            d = Document()
            for line in pdf_text.split("\n"):
                d.add_paragraph(line)
            d.save(out)
            print(f"[CONVERTER] Saved (PDF->DOCX via {'OCR text' if is_scanned else 'text'}) -> {out}")
            return out
        # Last resort: whatever LibreOffice can produce.
        result = _lo_convert(input_path, "docx", out_dir)
        if result:
            print(f"[CONVERTER] Saved (LibreOffice) -> {result}")
            return result
        raise RuntimeError("PDF -> DOCX conversion failed.")

    # ── .pdf -> .xlsx ─────────────────────────────────────────────────────────
    if src_ext == "pdf" and tgt_fmt in ("xlsx", "excel"):
        return _pdf_to_excel(input_path, out_dir)

    # ── .docx -> .xlsx ────────────────────────────────────────────────────────
    if src_ext in ("docx", "doc") and tgt_fmt in ("xlsx", "excel"):
        return _docx_to_excel(input_path, out_dir)

    # ── .xlsx -> .docx ────────────────────────────────────────────────────────
    if src_ext in ("xlsx", "xls") and tgt_fmt in ("docx", "word"):
        return _excel_to_word(input_path, out_dir)

    # ── .pptx -> .xlsx ────────────────────────────────────────────────────────
    if src_ext in ("pptx", "ppt") and tgt_fmt in ("xlsx", "excel"):
        return _pptx_to_excel(input_path, out_dir)

    # ── .pptx -> .docx ────────────────────────────────────────────────────────
    if src_ext in ("pptx", "ppt") and tgt_fmt in ("docx", "word"):
        return _pptx_to_word(input_path, out_dir)

    # ── .csv -> .xlsx ─────────────────────────────────────────────────────────
    if src_ext == "csv" and tgt_fmt in ("xlsx", "excel"):
        import pandas as pd
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        out  = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.xlsx")
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                pd.read_csv(input_path, encoding=enc).to_excel(out, index=False)
                print(f"[CONVERTER] Saved (CSV->Excel) -> {out}")
                return out
            except UnicodeDecodeError:
                continue
        raise RuntimeError("CSV -> Excel failed — could not decode the CSV file.")

    raise ValueError(f"Unsupported conversion: {src_ext} -> {tgt_fmt}")


def _new_excel_path(input_path: str, out_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.xlsx")


def _docx_to_excel(input_path: str, out_dir: str) -> str:
    """Extract all tables from a Word doc into an Excel workbook (one sheet per table)."""
    from docx import Document
    from openpyxl import Workbook

    doc = Document(input_path)
    wb  = Workbook()
    wb.remove(wb.active)
    count = 0

    for t_idx, table in enumerate(doc.tables, 1):
        count += 1
        ws = wb.create_sheet(title=f"Table_{t_idx}"[:31])
        for row in table.rows:
            ws.append([cell.text.strip() for cell in row.cells])

    if count == 0:
        ws = wb.create_sheet(title="Text")
        for para in doc.paragraphs:
            if para.text.strip():
                ws.append([para.text.strip()])

    # OCR any embedded images (text baked into pictures) into their own sheet.
    try:
        from tools.file_reader import _iter_docx_image_blobs, ocr_images_text
        ocr_text = ocr_images_text(_iter_docx_image_blobs(doc), cap=20)
        if ocr_text:
            ws = wb.create_sheet(title="Image_OCR")
            for line in ocr_text.split("\n"):
                ws.append([line])
    except Exception as e:
        print(f"[CONVERTER] Word->Excel image OCR skipped ({e}).")

    out = _new_excel_path(input_path, out_dir)
    wb.save(out)
    print(f"[CONVERTER] Saved (Word->Excel, {count} table(s)) -> {out}")
    return out


def _excel_to_word(input_path: str, out_dir: str) -> str:
    """Turn each non-empty sheet of an Excel workbook into a table in a Word doc."""
    from openpyxl import load_workbook
    from docx import Document

    wb  = load_workbook(input_path, data_only=True)
    doc = Document()
    sheet_count = 0

    for sheet in wb.worksheets:
        rows = [row for row in sheet.iter_rows(values_only=True)
                if any(c is not None and str(c).strip() for c in row)]
        if not rows:
            continue
        sheet_count += 1
        doc.add_heading(sheet.title, level=2)
        n_cols = max(len(row) for row in rows)
        table = doc.add_table(rows=len(rows), cols=n_cols)
        table.style = "Light Grid Accent 1"
        for r_idx, row in enumerate(rows):
            for c_idx in range(n_cols):
                val = row[c_idx] if c_idx < len(row) else None
                table.cell(r_idx, c_idx).text = "" if val is None else str(val)
        doc.add_paragraph()

    if sheet_count == 0:
        doc.add_paragraph("(No data found in the workbook.)")

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.docx")
    doc.save(out)
    print(f"[CONVERTER] Saved (Excel->Word, {sheet_count} sheet(s)) -> {out}")
    return out


def _pptx_to_excel(input_path: str, out_dir: str) -> str:
    """Extract all tables from a PPT into an Excel workbook (one sheet per table)."""
    from pptx import Presentation
    from openpyxl import Workbook

    prs = Presentation(input_path)
    wb  = Workbook()
    wb.remove(wb.active)
    count = 0

    for slide_num, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if shape.has_table:
                count += 1
                ws = wb.create_sheet(title=f"S{slide_num}_T{count}"[:31])
                for row in shape.table.rows:
                    ws.append([cell.text.strip() for cell in row.cells])

    if count == 0:
        ws = wb.create_sheet(title="Text")
        for slide_num, slide in enumerate(prs.slides, 1):
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text.strip():
                    ws.append([f"Slide {slide_num}", shape.text.strip()])

    # OCR any images on the slides (screenshots / image-only slides) into a sheet.
    try:
        from tools.file_reader import _iter_pptx_pictures, ocr_images_text
        ocr_parts = []
        for slide_num, slide in enumerate(prs.slides, 1):
            t = ocr_images_text(_iter_pptx_pictures(slide.shapes), cap=10)
            if t:
                ocr_parts.append(f"Slide {slide_num}:\n{t}")
        if ocr_parts:
            ws = wb.create_sheet(title="Image_OCR")
            for line in "\n".join(ocr_parts).split("\n"):
                ws.append([line])
    except Exception as e:
        print(f"[CONVERTER] PPT->Excel image OCR skipped ({e}).")

    out = _new_excel_path(input_path, out_dir)
    wb.save(out)
    print(f"[CONVERTER] Saved (PPT->Excel, {count} table(s)) -> {out}")
    return out


def _pptx_to_word(input_path: str, out_dir: str) -> str:
    """Extract each slide's text/tables into a Word doc, one section per slide."""
    from pptx import Presentation
    from docx import Document

    prs = Presentation(input_path)
    doc = Document()

    for slide_num, slide in enumerate(prs.slides, 1):
        doc.add_heading(f"Slide {slide_num}", level=2)
        wrote_something = False
        for shape in slide.shapes:
            if shape.has_table:
                wrote_something = True
                rows = list(shape.table.rows)
                table = doc.add_table(rows=len(rows), cols=len(rows[0].cells) if rows else 0)
                table.style = "Light Grid Accent 1"
                for r_idx, row in enumerate(rows):
                    for c_idx, cell in enumerate(row.cells):
                        table.cell(r_idx, c_idx).text = cell.text.strip()
                doc.add_paragraph()
            elif shape.has_text_frame and shape.text.strip():
                wrote_something = True
                doc.add_paragraph(shape.text.strip())
        if not wrote_something:
            doc.add_paragraph("(No text content on this slide.)")

    # OCR any images (screenshots / image-only slides) into their own section.
    try:
        from tools.file_reader import _iter_pptx_pictures, ocr_images_text
        for slide_num, slide in enumerate(prs.slides, 1):
            t = ocr_images_text(_iter_pptx_pictures(slide.shapes), cap=10)
            if t:
                doc.add_heading(f"Slide {slide_num} — Image Text (OCR)", level=3)
                doc.add_paragraph(t)
    except Exception as e:
        print(f"[CONVERTER] PPT->Word image OCR skipped ({e}).")

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.docx")
    doc.save(out)
    print(f"[CONVERTER] Saved (PPT->Word) -> {out}")
    return out


def _pdf_to_excel(input_path: str, out_dir: str) -> str:
    """Extract all tables from a PDF into a single Excel workbook (one sheet per table)."""
    import pdfplumber
    from openpyxl import Workbook

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}_{ts}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)   # drop default empty sheet
    table_count = 0

    with pdfplumber.open(input_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            for tbl_idx, table in enumerate(page.extract_tables() or [], 1):
                if not table:
                    continue
                table_count += 1
                ws = wb.create_sheet(title=f"P{page_num}_T{tbl_idx}"[:31])
                for row in table:
                    ws.append([("" if c is None else str(c)) for c in row])

    if table_count == 0:
        # No tables found — dump page text into a single sheet so the file isn't empty.
        # Use the OCR-capable reader (file_reader) so a scanned/image-only PDF — which
        # pdfplumber's extract_text() returns nothing for — still yields content.
        ws = wb.create_sheet(title="Text")
        text = ""
        try:
            from tools.file_reader import extract_pdf_text
            text = extract_pdf_text(input_path) or ""
        except Exception as e:
            print(f"[CONVERTER] OCR-capable text extraction failed ({e}) — falling back to pdfplumber text.")
        if not text.strip():
            with pdfplumber.open(input_path) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        for line in text.split("\n"):
            ws.append([line])

    wb.save(out)
    print(f"[CONVERTER] Saved (PDF->Excel, {table_count} table(s)) -> {out}")
    return out


@tool
def convert_document(file_path: str, output_format: str) -> str:
    """
    Convert a file to a different format. No LLM, no content changes — pure format conversion.

    Use ONLY for direct format conversions like:
      PPT/PPTX -> PDF
      Word/DOCX -> PDF
      Excel/XLSX -> PDF
      PDF -> DOCX
      Excel/XLSX -> DOCX (sheets become tables)
      DOCX -> Excel/XLSX (tables extracted)

    Do NOT use this for transformations (e.g. "make this a sales pitch") —
    those require the PPT Strategist agent.

    Args:
        file_path:     Absolute path to the input file.
        output_format: Target format — "pdf", "docx", or "word".

    Returns:
        Absolute path to the converted file, or an error message.
    """
    try:
        return convert_file(file_path, output_format)
    except Exception as e:
        return f"Conversion failed: {e}"
