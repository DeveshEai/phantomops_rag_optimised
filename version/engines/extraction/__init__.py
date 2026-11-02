"""
version/engines/extraction/ — Multi-Format Document Ingestion & OCR Engine
Extracts text and tabular content from PDF, Word, PowerPoint, Excel, and CSV files,
with automated Tesseract OCR for scanned / image-based pages.
"""

from tools.file_reader import (
    read_attachment_text,
    extract_pdf_text,
    extract_ppt_text,
    extract_word_text,
    extract_excel_text,
    extract_csv_text,
    find_tesseract,
)
from tools.qa_filler import (
    read_qa_structure,
    write_qa_responses,
)

__all__ = [
    "read_attachment_text",
    "extract_pdf_text",
    "extract_ppt_text",
    "extract_word_text",
    "extract_excel_text",
    "extract_csv_text",
    "find_tesseract",
    "read_qa_structure",
    "write_qa_responses",
]
