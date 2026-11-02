# 📝 PhantomOps Prompts Directory

All system prompts, write prompts, and instruction templates are centralized here. You no longer need to search through large Python files to customize prompts.

## Files & Usage

| File | What It Contains | How to Customize |
|---|---|---|
| [`system_personas.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/system_personas.py) | **Sally Persona & Security Guards**<br>Base persona guidelines and untrusted attachment guards (`TOOL_LOOP_INJECTION_GUARD`). | Edit here to change general tone, personality, or safety boundaries. |
| [`write_prompts.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/write_prompts.py) | **Phase 2 Document Authoring Prompts**<br>Templates for `pdf`, `word`, `excel`, `csv`, `ppt`, and `email` deliverables. | Adjust length requirements, section count, table instructions, and formatting for generated files. |
| [`research_prompts.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/research_prompts.py) | **Phase 1 GATHER Loop Prompts**<br>Instructions guiding the agent to gather real, current data via Tavily, SerpAPI, and web browsing. | Modify recency instructions, search query formats, or link-opening limits. |
| [`qa_prompts.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/qa_prompts.py) | **RFP & Questionnaire Prompts**<br>Instructions for batch answering questions extracted from forms and documents. | Edit answering style, grounding strictness, and fallback phrases. |
| [`arabic_prompts.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/arabic_prompts.py) | **Arabic & RTL Prompts**<br>Instructions safeguarding against English fallback, ensuring authentic Arabic deliverables, and RTL formatting. | Adjust tone, regional dialect registers, and signature translation rules. |
| [`meeting_prompts.py`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/prompts/meeting_prompts.py) | **Calendar Scheduling Prompts**<br>JSON extraction instructions for meeting scheduling, dates, attendees, recurrence, and cancellations. | Modify time zone handling or default meeting duration. |

## Quick Import Example

```python
from prompts import WRITE_PROMPTS, get_search_prompt, QA_ANSWER_PROMPT
```

