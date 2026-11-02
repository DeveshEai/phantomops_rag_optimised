# ⚙️ PhantomOps Engines Directory

All functional engines in PhantomOps are cleanly partitioned into modular packages below. You can inspect, modify, or extend each engine independently.

## Available Engines

| Engine Directory | Package / Tools Wrapped | What It Does |
|---|---|---|
| [`presentation/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/presentation/) | `tools.marp_creator`<br>`tools.ppt_rota`<br>`tools.ppt_reviewer`<br>`tools.image_provider` | **PowerPoint & Presentation Engine**<br>• Marp CLI compilation to native `.pptx`<br>• 10-theme dynamic design rotation (Theme + Layout)<br>• Pexels API real photo fetching and image caching<br>• Automated layout & contrast visual QA review |
| [`documents/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/documents/) | `tools.doc_creator`<br>`tools.converter_tool` | **Word & PDF Document Engine**<br>• Formatted `.docx` executive reports and proposals<br>• LibreOffice / ReportLab direct `.pdf` rendering<br>• Cross-format document conversion |
| [`spreadsheets/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/spreadsheets/) | `tools.excel_creator` | **Spreadsheet & Calculation Engine**<br>• Multi-tab `.xlsx` workbooks styled with openpyxl<br>• Dynamic calculated formulas and summary sheets<br>• Single-table `.csv` export |
| [`rag/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/rag/) | `rag.search`<br>`rag.ingestion`<br>`rag.store`<br>`rag.chunking`<br>`tools.rag_search` | **Optimised Hybrid RAG Engine**<br>• ChromaDB dense vector search + BM25 keyword search<br>• Reciprocal Rank Fusion (RRF, $k=60$)<br>• Structure-aware chunking and local Ollama (`bge-m3`) embeddings |
| [`research/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/research/) | `tools.web_search` | **Live Web Research & Browsing Engine**<br>• Tavily & Google SerpAPI live external research<br>• In-page browsing suite (`open` and `find_in_page`)<br>• Context caching and fallback synthesis safeguards |
| [`extraction/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/extraction/) | `tools.file_reader`<br>`tools.qa_filler` | **Document Ingestion & OCR Engine**<br>• Multi-format extraction (PDF, DOCX, XLSX, PPTX, CSV)<br>• Tesseract OCR integration for scans (`eng+ara`)<br>• Automated RFP & questionnaire structure extraction |
| [`llm/`](file:///c:/Users/USER/Downloads/phantomops_rag_optimised/version/engines/llm/) | `core.config`<br>`agents.__init__` | **Inference & Execution Engine**<br>• Multi-model provider setup (Groq, OpenAI, Claude, Gemini, Ollama)<br>• Exponential backoff with jitter and context trimming<br>• Core tool loop runner and failure classification |

## Quick Import Examples

```python
# Access an individual engine:
from engines.presentation import compile_marp_to_pptx, get_next_ppt_bundle
from engines.documents import create_document
from engines.spreadsheets import create_spreadsheet
from engines.rag import search_knowledge_base
from engines.research import tavily_search, open_url

# Or access through the unified root package:
import engines
engines.presentation.create_presentation(...)
engines.documents.create_document(...)
engines.rag.search_knowledge_base(...)
```

