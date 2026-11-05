# tools/engines/ — Per-format render engines for assistant_agent.py Phase 3 (RENDER).
#
# Each engine module exposes a single render(written_content, context) function
# that takes the LLM-written markdown and produces the final file (or returns
# the answer string for email replies).
#
# Engines:
#   word_engine   — .docx via tools/doc_creator.py   (output_type == "word")
#   pdf_engine    — .pdf  via tools/doc_creator.py   (output_type == "pdf")
#   excel_engine  — .xlsx via tools/excel_creator.py (output_type == "excel")
#   csv_engine    — .csv  via tools/excel_creator.py (output_type == "csv")
#   ppt_engine    — .pptx via tools/ppt_agent.py     (output_type == "ppt")
#
# To add support for a new output type:
#   1. Create tools/engines/<type>_engine.py with a render() function.
#   2. Import and register it in RENDER_ENGINES below.
#   3. Add the new type to classify_request_intent's output_format enum if needed.

from .word_engine  import render as render_word   # noqa: F401
from .pdf_engine   import render as render_pdf    # noqa: F401
from .excel_engine import render as render_excel  # noqa: F401
from .csv_engine   import render as render_csv    # noqa: F401
from .ppt_engine   import render as render_ppt    # noqa: F401

# Dispatch table: output_type → render function.
# Each render(written_content, ctx) returns (files: list[str], answer: str).
RENDER_ENGINES: dict = {
    "word":  render_word,
    "pdf":   render_pdf,
    "excel": render_excel,
    "csv":   render_csv,
    "ppt":   render_ppt,
}
