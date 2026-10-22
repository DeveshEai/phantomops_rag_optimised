"""
tools/file_reader.py — Attachment Reader Tools

LangChain tools for reading email attachments before processing:
  - read_pdf_attachment   : extracts text + tables from a PDF
  - read_ppt_attachment   : extracts slide text + tables from a PPTX
  - read_word_attachment  : extracts text + tables from a DOCX
  - read_excel_attachment : extracts sheet/row data from an XLSX using pandas
  - read_csv_attachment   : extracts rows + analysis from a CSV using pandas

All table blocks are followed by a [TABLE ANALYSIS] section with pre-computed
stats (min/max/avg, top-N rankings, group-by summaries) so the LLM never has
to do arithmetic on raw rows.
"""

import os

from langchain_core.tools import tool
from core.config import INBOX_ATTACHMENTS_DIR, OUTPUT_DIR

# Hard cap on returned text per file. Kept generous (and env-overridable) so the
# reader does NOT pre-truncate below the WRITE phase's provider-aware budget
# (core.config.get_attachment_char_budget) — large-context models should see the
# whole document. The downstream budget does the provider-appropriate trimming,
# and any truncation is disclosed to the model and the user (never silent).
_MAX_CHARS     = int(os.getenv("MAX_FILE_READ_CHARS", "500000"))
_MAX_EXCEL_ROWS = 500     # raw data rows shown per sheet (analysis uses full dataset)
_MIN_ROWS_FOR_ANALYSIS = 3  # skip analysis for tiny tables

# Supported inbound attachment extensions
# Only modern formats are truly supported. Legacy .ppt/.doc/.xls are excluded so the
# polite unsupported-format reply fires instead of a confusing reader error.
SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".docx", ".xlsx", ".csv"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cap_text(text: str) -> str:
    if len(text) >= _MAX_CHARS:
        return text[:_MAX_CHARS] + f"\n\n[Content truncated — file exceeded {_MAX_CHARS:,} characters]"
    return text


def _validate_file(file_path: str) -> str | None:
    if not file_path:
        return "No file path was provided."
    try:
        abs_path    = os.path.realpath(file_path)
        inbox_root  = os.path.realpath(INBOX_ATTACHMENTS_DIR)
        output_root = os.path.realpath(OUTPUT_DIR)
        in_inbox  = abs_path.startswith(inbox_root + os.sep) or abs_path == inbox_root
        in_output = abs_path.startswith(output_root + os.sep) or abs_path == output_root
        if not (in_inbox or in_output):
            return "Access denied: file is outside the allowed attachment directories."
    except Exception:
        return "Could not resolve file path."
    if not os.path.isfile(abs_path):
        return f"File not found: {file_path}"
    if os.path.getsize(abs_path) == 0:
        return f"File is empty: {file_path}"
    return None


def _format_markdown_table(rows: list) -> str:
    """Convert a list-of-lists (first row = headers) into a markdown table."""
    if not rows:
        return ""
    str_rows  = [[str(c or "").strip() for c in row] for row in rows]
    n_cols    = max(len(r) for r in str_rows)
    str_rows  = [r + [""] * (n_cols - len(r)) for r in str_rows]
    header    = "| " + " | ".join(str_rows[0]) + " |"
    separator = "| " + " | ".join(["---"] * n_cols) + " |"
    body      = "\n".join("| " + " | ".join(r) + " |" for r in str_rows[1:])
    return "\n".join([header, separator, body]) if body else "\n".join([header, separator])


def _analyze_table(rows_or_df) -> str:
    """
    Pre-compute statistics from any table so the LLM gets verified numbers
    instead of having to mentally rank or sum raw rows.

    Accepts:
      - list-of-lists  (first row = column headers)
      - pandas DataFrame (already parsed)

    Returns a formatted [TABLE ANALYSIS] block, or "" if the table is too
    small or contains no numeric columns worth analysing.
    """
    try:
        import pandas as pd

        # ── Build DataFrame ───────────────────────────────────────────────────
        if isinstance(rows_or_df, pd.DataFrame):
            df = rows_or_df.copy()
        else:
            rows = [r for r in rows_or_df if r]
            if len(rows) < _MIN_ROWS_FOR_ANALYSIS + 1:  # +1 for header row
                return ""
            headers = [str(c).strip() for c in rows[0]]
            df = pd.DataFrame(
                [[str(c).strip() if c is not None else "" for c in row]
                 for row in rows[1:]],
                columns=headers,
            )

        df.dropna(how="all", inplace=True)
        if len(df) < _MIN_ROWS_FOR_ANALYSIS:
            return ""

        # ── Coerce numeric columns (strip currency/comma formatting) ──────────
        for col in df.columns:
            cleaned = (
                df[col].astype(str)
                .str.replace(r"[₹$€£,\s%]", "", regex=True)
                .str.strip()
            )
            coerced = pd.to_numeric(cleaned, errors="coerce")
            # Accept column as numeric only if >50% of values converted
            if coerced.notna().sum() / max(len(df), 1) > 0.5:
                df[col] = coerced

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        text_cols    = [c for c in df.columns if c not in numeric_cols]

        if not numeric_cols:
            return ""  # nothing to analyse

        # ── Number formatter ──────────────────────────────────────────────────
        def _fmt(v):
            try:
                if pd.isna(v):
                    return ""
                if isinstance(v, float) and v == int(v):
                    return f"{int(v):,}"
                return f"{v:,.2f}"
            except Exception:
                return str(v)

        lines = []

        # ── Column summary ────────────────────────────────────────────────────
        lines.append("COLUMN SUMMARY:")
        for col in df.columns:
            if col in numeric_cols:
                lines.append(
                    f"  {col}: numeric — "
                    f"min {_fmt(df[col].min())} | "
                    f"max {_fmt(df[col].max())} | "
                    f"avg {_fmt(df[col].mean())}"
                )
            else:
                unique_vals = df[col].dropna().unique()
                samples = ", ".join(str(v) for v in unique_vals[:5])
                more    = "…" if len(unique_vals) > 5 else ""
                lines.append(
                    f"  {col}: text — {len(unique_vals)} unique values "
                    f"({samples}{more})"
                )

        # ── Top-10 ranking by each numeric column (max 2 columns) ────────────
        name_col = next(
            (c for c in text_cols if any(
                k in c.lower() for k in
                ["name", "employee", "person", "agent", "staff", "rep", "title"]
            )),
            text_cols[0] if text_cols else None,
        )

        for num_col in numeric_cols[:2]:
            top_n   = min(10, len(df))
            top_df  = df.nlargest(top_n, num_col)
            # Show: name col + up to 2 extra text cols + the ranked column
            show = []
            if name_col:
                show.append(name_col)
            for c in text_cols:
                if c != name_col and len(show) < 3:
                    show.append(c)
            show.append(num_col)
            show = [c for c in show if c in df.columns]

            lines.append(f"\nTOP {top_n} BY {num_col}:")
            table_rows = [show] + [
                [_fmt(row[c]) if c in numeric_cols else str(row[c])
                 for c in show]
                for _, row in top_df[show].iterrows()
            ]
            lines.append(_format_markdown_table(table_rows))

        # ── Group-by: first categorical column × numeric columns ─────────────
        if text_cols:
            grp_col = text_cols[0]
            grp_headers = [grp_col, "Count"]
            for nc in numeric_cols[:3]:
                grp_headers += [f"Avg {nc}", f"Max {nc}"]

            grp_rows = []
            for val, group in df.groupby(grp_col, sort=True):
                row = [str(val), str(len(group))]
                for nc in numeric_cols[:3]:
                    row += [_fmt(group[nc].mean()), _fmt(group[nc].max())]
                grp_rows.append(row)

            if grp_rows:
                lines.append(f"\nBY {grp_col.upper()}:")
                lines.append(_format_markdown_table([grp_headers] + grp_rows))

        return "[TABLE ANALYSIS]\n" + "\n".join(lines) + "\n[/TABLE ANALYSIS]"

    except Exception as e:
        return f"[TABLE ANALYSIS — failed: {e}]"


# ── PDF ───────────────────────────────────────────────────────────────────────

_TESSERACT_CACHE = None


def find_tesseract():
    """Locate the Tesseract executable even when it isn't on PATH. Checks PATH
    first, then the standard Windows install locations (UB-Mannheim system and
    per-user installs). Mirrors find_soffice() for LibreOffice. Cached."""
    global _TESSERACT_CACHE
    if _TESSERACT_CACHE is not None:
        return _TESSERACT_CACHE or None
    import shutil, platform
    # 1) explicit override (set TESSERACT_CMD in .env to any install location)
    override = os.environ.get("TESSERACT_CMD", "").strip().strip('"')
    if override and os.path.isfile(override):
        _TESSERACT_CACHE = override
        return override
    # 2) on PATH
    found = shutil.which("tesseract")
    if found:
        _TESSERACT_CACHE = found
        return found
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                         "Tesseract-OCR", "tesseract.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                _TESSERACT_CACHE = c
                return c
    _TESSERACT_CACHE = ""
    return None


_OCR_LANG_CACHE = None


def _ocr_lang() -> str:
    """Tesseract language string for OCR — includes Arabic ('ara') only if that
    language pack is actually installed alongside the 'eng' one Tesseract ships
    with by default. A machine that hasn't installed the Arabic traineddata
    (older installs, or install.bat run before this feature existed) keeps
    getting English-only OCR exactly as before, instead of every OCR call
    failing outright because pytesseract can't load a language pack that
    isn't there. Checked once per process, then cached."""
    global _OCR_LANG_CACHE
    if _OCR_LANG_CACHE is not None:
        return _OCR_LANG_CACHE
    try:
        import pytesseract
        _tess = find_tesseract()
        if _tess:
            pytesseract.pytesseract.tesseract_cmd = _tess
        available = pytesseract.get_languages(config="")
        _OCR_LANG_CACHE = "eng+ara" if "ara" in available else "eng"
    except Exception:
        _OCR_LANG_CACHE = "eng"
    return _OCR_LANG_CACHE


def _ocr_page(fitz_page) -> str:
    """Render a single PDF page as an image and run Tesseract OCR on it."""
    try:
        import fitz as _fitz
        import pytesseract
        from PIL import Image
        import io

        # Point pytesseract at the installed binary if it isn't on PATH.
        _tess = find_tesseract()
        if _tess:
            pytesseract.pytesseract.tesseract_cmd = _tess

        mat = _fitz.Matrix(2.0, 2.0)  # 2x resolution for better accuracy
        pix = fitz_page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, lang=_ocr_lang(), config="--psm 3")
        return text.strip()
    except Exception as e:
        print(f"[OCR] Page OCR failed: {e}")
        return ""


def _ocr_image_bytes(blob: bytes, min_chars: int = 12) -> str:
    """Run Tesseract OCR on a raw image blob (embedded in a PPTX/DOCX).

    Returns the recognised text, or "" if it's shorter than `min_chars` — that
    threshold drops decorative logos/icons that OCR to a few junk characters,
    while keeping real text baked into screenshots/diagrams. Small images are
    upscaled for better accuracy (mirrors the 2x render used for PDF pages)."""
    try:
        import pytesseract
        from PIL import Image
        import io

        _tess = find_tesseract()
        if _tess:
            pytesseract.pytesseract.tesseract_cmd = _tess

        img = Image.open(io.BytesIO(blob))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Upscale small images so Tesseract has enough pixels to work with.
        if max(img.size) < 1000:
            img = img.resize((img.size[0] * 2, img.size[1] * 2))
        text = (pytesseract.image_to_string(img, lang=_ocr_lang(), config="--psm 3") or "").strip()
        return text if len(text) >= min_chars else ""
    except Exception as e:
        print(f"[OCR] Image OCR failed: {e}")
        return ""


def _iter_pptx_pictures(shapes):
    """Yield image blobs from PICTURE shapes in a slide, recursing into groups.
    Skips tiny decorative images (logos/icons) by a minimum display-size guard."""
    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except Exception:
        return
    for shape in shapes:
        try:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from _iter_pptx_pictures(shape.shapes)
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                # ~0.8in x 0.4in minimum (EMU: 914400/inch) — skip bullet icons/logos.
                if (getattr(shape, "width", 0) or 0) >= 730000 and (getattr(shape, "height", 0) or 0) >= 365000:
                    yield shape.image.blob
        except Exception:
            continue


def _iter_docx_image_blobs(doc, limit: int = 20):
    """Yield image blobs embedded in a python-docx Document (via its image
    relationships). Capped to `limit` to bound OCR time."""
    n = 0
    try:
        rels = doc.part.rels.values()
    except Exception:
        return
    for rel in rels:
        try:
            if "image" not in rel.reltype:
                continue
            yield rel.target_part.blob
            n += 1
            if n >= limit:
                return
        except Exception:
            continue


def ocr_images_text(blobs, cap: int) -> str:
    """OCR a sequence of image blobs and join the non-empty results, up to `cap`
    images. Shared by the readers and converters so every path treats embedded
    image text the same way."""
    chunks = []
    for blob in blobs:
        t = _ocr_image_bytes(blob)
        if t:
            chunks.append(t)
        if len(chunks) >= cap:
            break
    return "\n".join(chunks)


def extract_pdf_text(file_path: str) -> str:
    """
    Extract text and tables from a PDF.
    Primary: pdfplumber (tables) + PyMuPDF (text).
    Fallback: Tesseract OCR for any page that yields no extractable text.
    """
    validation_error = _validate_file(file_path)
    if validation_error:
        return validation_error
    try:
        import pdfplumber
    except Exception as e:
        return f"Error reading PDF: pdfplumber not available ({e})"

    # PyMuPDF (fitz) is optional — used for fast text + OCR rendering.
    # If unavailable, fall back to pdfplumber's own text extraction.
    try:
        import fitz
        _has_fitz = True
    except Exception:
        fitz = None
        _has_fitz = False
        print("[PDF] PyMuPDF (fitz) unavailable — using pdfplumber text extraction only.")

    try:
        pages_text = []
        fitz_doc   = fitz.open(file_path) if _has_fitz else None

        with pdfplumber.open(file_path) as pdf:
            for i, plumber_page in enumerate(pdf.pages, 1):
                parts = []
                fitz_page = fitz_doc[i - 1] if (_has_fitz and i - 1 < len(fitz_doc)) else None

                # Plain text — PyMuPDF if available, else pdfplumber
                if fitz_page is not None:
                    text = fitz_page.get_text().strip()
                else:
                    text = (plumber_page.extract_text() or "").strip()
                if text:
                    parts.append(text)

                # Tables via pdfplumber
                for tbl in (plumber_page.extract_tables() or []):
                    if tbl:
                        parts.append(f"[TABLE]\n{_format_markdown_table(tbl)}\n[/TABLE]")
                        analysis = _analyze_table(tbl)
                        if analysis:
                            parts.append(analysis)

                if parts:
                    pages_text.append(f"--- Page {i} ---\n" + "\n\n".join(parts))
                elif fitz_page is not None:
                    # No extractable text — OCR (needs fitz to rasterise)
                    print(f"[OCR] Page {i} has no extractable text — running OCR...")
                    ocr_text = _ocr_page(fitz_page)
                    if ocr_text:
                        pages_text.append(f"--- Page {i} (OCR) ---\n{ocr_text}")

        if fitz_doc is not None:
            fitz_doc.close()

        full_text = "\n\n".join(pages_text).strip()
        if not full_text:
            return "The PDF appears to be empty or image-only with no readable content even after OCR."
        return _cap_text(full_text)

    except Exception as e:
        return f"Error reading PDF: {e}"


# ── PPTX ──────────────────────────────────────────────────────────────────────

def extract_ppt_text(file_path: str) -> str:
    """Extract text and tables from a PPTX, slide by slide."""
    validation_error = _validate_file(file_path)
    if validation_error:
        return validation_error
    if os.path.splitext(file_path)[1].lower() == ".ppt":
        return "Legacy .ppt files are not supported. Please provide a .pptx file."
    try:
        from pptx import Presentation
        prs         = Presentation(file_path)
        slides_text = []

        for i, slide in enumerate(prs.slides, 1):
            parts = []
            for shape in slide.shapes:
                if shape.has_table:
                    rows = [
                        [cell.text.strip() for cell in row.cells]
                        for row in shape.table.rows
                    ]
                    if rows:
                        parts.append(f"[TABLE]\n{_format_markdown_table(rows)}\n[/TABLE]")
                        analysis = _analyze_table(rows)
                        if analysis:
                            parts.append(analysis)
                elif shape.has_text_frame:
                    text = shape.text.strip()
                    if text:
                        parts.append(text)

            # OCR images on the slide (text baked into screenshots / image-only slides).
            ocr_text = ocr_images_text(_iter_pptx_pictures(slide.shapes), cap=10)
            if ocr_text:
                parts.append("[Image OCR]\n" + ocr_text)

            if parts:
                slides_text.append(f"Slide {i}:\n" + "\n".join(parts))

        if not slides_text:
            return "No readable text found in the presentation."
        return _cap_text("\n\n".join(slides_text))
    except Exception as e:
        return f"Error reading PPTX: {e}"


# ── DOCX ──────────────────────────────────────────────────────────────────────

def extract_word_text(file_path: str) -> str:
    """Extract text and tables from a DOCX in document order."""
    validation_error = _validate_file(file_path)
    if validation_error:
        return validation_error
    if os.path.splitext(file_path)[1].lower() == ".doc":
        return "Legacy .doc files are not supported. Please provide a .docx file."
    try:
        from docx import Document
        from docx.oxml.ns import qn

        doc   = Document(file_path)
        parts = []

        for element in doc.element.body:
            tag = element.tag.split("}")[-1]

            if tag == "p":
                text = "".join(
                    node.text or "" for node in element.iter(qn("w:t"))
                ).strip()
                if text:
                    parts.append(text)

            elif tag == "tbl":
                rows = []
                for tr in element.findall(f".//{qn('w:tr')}"):
                    cells = [
                        "".join(node.text or "" for node in tc.iter(qn("w:t"))).strip()
                        for tc in tr.findall(f".//{qn('w:tc')}")
                    ]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    parts.append(f"[TABLE]\n{_format_markdown_table(rows)}\n[/TABLE]")
                    analysis = _analyze_table(rows)
                    if analysis:
                        parts.append(analysis)

        # OCR embedded images (text baked into pictures/screenshots). The
        # min-length filter in _ocr_image_bytes drops header/footer logos.
        ocr_text = ocr_images_text(_iter_docx_image_blobs(doc), cap=20)
        if ocr_text:
            parts.append("[Image OCR]\n" + ocr_text)

        if not parts:
            return "The Word document appears to be empty or contains no extractable text."
        return _cap_text("\n\n".join(parts))
    except Exception as e:
        return f"Error reading Word document: {e}"


# ── Excel ─────────────────────────────────────────────────────────────────────

def extract_excel_text(file_path: str) -> str:
    """Extract structured table data + pre-computed analysis from an Excel (.xlsx)."""
    validation_error = _validate_file(file_path)
    if validation_error:
        return validation_error
    if os.path.splitext(file_path)[1].lower() == ".xls":
        return "Legacy .xls files are not supported. Please provide a .xlsx file."
    try:
        import pandas as pd
        xl          = pd.ExcelFile(file_path, engine="openpyxl")
        sheets_text = []

        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name)
            df.dropna(how="all", inplace=True)
            df.dropna(axis=1, how="all", inplace=True)
            if df.empty:
                continue

            total_rows = len(df)
            truncated  = total_rows > _MAX_EXCEL_ROWS

            # Analysis runs on the FULL dataset (before row cap)
            analysis = _analyze_table(df)

            # Raw sample for additional context
            sample_df = df.head(_MAX_EXCEL_ROWS) if truncated else df
            sample    = sample_df.to_string(index=False, na_rep="")

            header = (
                f"Sheet: {sheet_name} ({total_rows} rows — "
                f"analysis on full data, sample shows first {_MAX_EXCEL_ROWS})"
                if truncated else
                f"Sheet: {sheet_name} ({total_rows} rows)"
            )

            block = header
            if analysis:
                block += f"\n\n{analysis}"
            block += f"\n\nDATA SAMPLE:\n{sample}"
            if truncated:
                block += f"\n[Showing first {_MAX_EXCEL_ROWS} of {total_rows} rows]"

            sheets_text.append(block)

        # OCR images embedded in the workbook (text pasted as a picture). Rare,
        # but covered for completeness; logos are dropped by the min-length filter.
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path)
            blobs = []
            for ws in wb.worksheets:
                for img in (getattr(ws, "_images", None) or []):
                    try:
                        data = img._data() if callable(getattr(img, "_data", None)) else None
                        if data is None:
                            ref = getattr(img, "ref", None)
                            if hasattr(ref, "read"):
                                ref.seek(0)
                                data = ref.read()
                        if data:
                            blobs.append(data)
                    except Exception:
                        continue
            ocr_text = ocr_images_text(blobs, cap=20)
            if ocr_text:
                sheets_text.append("[Image OCR]\n" + ocr_text)
        except Exception as e:
            print(f"[OCR] Excel image OCR failed: {e}")

        if not sheets_text:
            return "The Excel file appears to be empty or contains no readable data."
        return _cap_text("\n\n".join(sheets_text))
    except Exception as e:
        return f"Error reading Excel file: {e}"


# ── CSV ──────────────────────────────────────────────────────────────────────

def extract_csv_text(file_path: str) -> str:
    """Extract structured data + pre-computed analysis from a CSV file."""
    validation_error = _validate_file(file_path)
    if validation_error:
        return validation_error
    try:
        import pandas as pd

        # Try common encodings — CSV files from Windows are often latin-1
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except (UnicodeDecodeError, Exception):
                continue
        else:
            return "Error reading CSV: could not decode file with utf-8, latin-1, or cp1252."

        df.dropna(how="all", inplace=True)
        df.dropna(axis=1, how="all", inplace=True)
        if df.empty:
            return "The CSV file appears to be empty or contains no readable data."

        total_rows = len(df)
        truncated  = total_rows > _MAX_EXCEL_ROWS

        analysis   = _analyze_table(df)
        sample_df  = df.head(_MAX_EXCEL_ROWS) if truncated else df
        sample     = sample_df.to_string(index=False, na_rep="")

        header = (
            f"CSV file ({total_rows} rows — "
            f"analysis on full data, sample shows first {_MAX_EXCEL_ROWS})"
            if truncated else
            f"CSV file ({total_rows} rows)"
        )

        block = header
        if analysis:
            block += f"\n\n{analysis}"
        block += f"\n\nDATA SAMPLE:\n{sample}"
        if truncated:
            block += f"\n[Showing first {_MAX_EXCEL_ROWS} of {total_rows} rows]"

        return _cap_text(block)
    except Exception as e:
        return f"Error reading CSV: {e}"


# ── Dispatcher ────────────────────────────────────────────────────────────────

def read_attachment_text(file_path: str) -> str:
    """Read any supported attachment by extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_pdf_text(file_path)
    if ext in {".pptx", ".ppt"}:
        return extract_ppt_text(file_path)
    if ext in {".docx", ".doc"}:
        return extract_word_text(file_path)
    if ext in {".xlsx", ".xls"}:
        return extract_excel_text(file_path)
    if ext == ".csv":
        return extract_csv_text(file_path)
    return f"Unsupported attachment type: {ext or '(none)'}"


# ── LangChain Tools ───────────────────────────────────────────────────────────

@tool
def read_pdf_attachment(file_path: str) -> str:
    """
    Extract text and structured tables from a PDF file.
    Tables include a pre-computed analysis (rankings, group-by summaries, stats).
    Use when the email has a PDF attachment that needs to be read before
    creating a proposal, summary, or output document.

    Args:
        file_path: Absolute path to the PDF file on disk.

    Returns:
        Extracted text, tables, and analysis (capped at 80 000 characters).
    """
    return extract_pdf_text(file_path)


@tool
def read_ppt_attachment(file_path: str) -> str:
    """
    Extract text and structured tables from a PowerPoint (.pptx) file, slide by slide.
    Tables include a pre-computed analysis (rankings, group-by summaries, stats).
    Use when the email has a PPTX attachment that needs to be read before
    re-creating it as a branded deck or generating a summary.

    Args:
        file_path: Absolute path to the PPTX file on disk.

    Returns:
        Extracted slide-by-slide text, tables, and analysis (capped at 80 000 characters).
    """
    return extract_ppt_text(file_path)


@tool
def read_word_attachment(file_path: str) -> str:
    """
    Extract text and structured tables from a Word (.docx) file in document order.
    Tables include a pre-computed analysis (rankings, group-by summaries, stats).
    Use when the email has a Word attachment that needs to be read before
    converting it to a PPT, PDF, or another document.

    Args:
        file_path: Absolute path to the .docx file on disk.

    Returns:
        Extracted text, tables, and analysis (capped at 80 000 characters).
    """
    return extract_word_text(file_path)


@tool
def read_excel_attachment(file_path: str) -> str:
    """
    Extract structured data and pre-computed analysis from an Excel (.xlsx) file.
    Each sheet includes: column stats, top-N rankings, group-by summaries,
    and a raw data sample. Analysis runs on the full dataset before any row cap.
    Use when the email has an Excel attachment for data analysis or reporting.

    Args:
        file_path: Absolute path to the .xlsx file on disk.

    Returns:
        Full analysis + data sample (capped at 80 000 characters).
    """
    return extract_excel_text(file_path)


@tool
def read_csv_attachment(file_path: str) -> str:
    """
    Extract structured data and pre-computed analysis from a CSV file.
    Includes: column stats, top-N rankings, group-by summaries, and a data sample.
    Handles utf-8, latin-1, and cp1252 encodings automatically.
    Use when the email has a CSV attachment for data analysis or reporting.

    Args:
        file_path: Absolute path to the .csv file on disk.

    Returns:
        Full analysis + data sample (capped at 80 000 characters).
    """
    return extract_csv_text(file_path)
