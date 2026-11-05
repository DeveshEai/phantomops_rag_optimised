# agents/prompts/ — Prompt templates for each Sally subagent.
#
# Each output-format's WRITE-phase prompt lives in its own file so it can be
# tuned independently without touching assistant_agent.py.  Import the
# unified WRITE_PROMPTS dict from here for backwards-compatible usage.

from .assistant_prompt import ASSISTANT_SYSTEM_PROMPT  # noqa: F401 — general agent sys prompt
from .pdf_prompt   import PDF_WRITE_PROMPT             # noqa: F401
from .word_prompt  import WORD_WRITE_PROMPT            # noqa: F401
from .excel_prompt import EXCEL_WRITE_PROMPT           # noqa: F401
from .csv_prompt   import CSV_WRITE_PROMPT             # noqa: F401
from .ppt_prompt   import PPT_WRITE_PROMPT             # noqa: F401
from .email_prompt import EMAIL_WRITE_PROMPT           # noqa: F401

# Convenience mapping used by assistant_agent.py (drop-in replacement for
# the old _WRITE_PROMPTS dict that lived inline in that file).
WRITE_PROMPTS: dict[str, str] = {
    "pdf":   PDF_WRITE_PROMPT,
    "word":  WORD_WRITE_PROMPT,
    "excel": EXCEL_WRITE_PROMPT,
    "csv":   CSV_WRITE_PROMPT,
    "ppt":   PPT_WRITE_PROMPT,
    "email": EMAIL_WRITE_PROMPT,
}
