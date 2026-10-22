# PhantomOps: Sally AI Agent Documentation

**Project:** Sally (Autonomous Email Agent, Task Executor & Document Creator)
**Codename:** PhantomOps
**Version:** 4 — Native Windows (no Docker) · Outlook or Gmail (user-selectable) · SQLite + Meetings + Folder Delivery

This document is the comprehensive master reference for Sally's architecture, agent logic, tool inventory, file structure, and deployment strategy.

---

## 0. Key Capabilities — read this first

What the V4 build does and how it is wired (native Windows, Outlook):

- **Storage → SQLite only.** Everything (sessions, drafts, tasks, config, dedup claims, conversation cache) lives in one file: `{OUTPUT_DIR}/phantomops.db` (filename set by `DB_FILENAME`). No DB server, no Redis. Each deployment is isolated — its own agent + own `phantomops.db`.
- **Email provider — user-selectable Outlook or Gmail.** `email_provider` in `agent_config.yaml` (also settable via the Settings Control Center) picks `outlook` or `gmail` — one mailbox at a time, not simultaneous polling. A thin router (`skills/email_skill/email_client.py`) dispatches to the matching backend at import time, so the rest of the agent is provider-agnostic. **Unlike every other setting in this file, this one does NOT hot-reload** — the mailbox connection authenticates once when the agent starts, so switching it needs a restart. See §21.
- **Meeting scheduling.** "Schedule a meeting with `<email>` `<time>`" → an LLM extracts the details and Sally creates a real **Outlook Calendar** event + sends invites. Runs on a single provider (no fanout) to avoid duplicate invites. The extractor **never guesses a clock time** — if only a date is given (no time of day), Sally asks precisely for "the time" (not a vague "more details"), and the user's reply completes the booking directly instead of restarting the request. See §14, §15.
- **Folder delivery & intake.** "Save it to the folder / don't email it" → Sally writes the file to `DELIVERY_DIR` (defaults to `OUTPUT_DIR`) and confirms the path instead of attaching. "Read `<file>` from the folder" → reads from `INTAKE_DIR` (`OUTPUT_DIR/intake`). The deliver-vs-email and read-from-folder decisions are made by an **LLM intent classifier** (run once per email in `graph.py`), not keyword matching. See §15.
- **Downtime-safe recovery.** A persisted timestamp **watermark** (`last_email_ts` in `phantomops.db`) means mail that arrived while Sally was offline is always processed on restart — even if it was opened/marked read in the meantime. Replaces the old "fetch recent + filter unread" logic that could miss mail.
- **CC guardrail + reply-all CC.** If Sally is only **Cc'd** on a thread (not in `To:`), she stays silent unless the message actually addresses/asks the assistant — preventing reply-all spam on human-to-human threads. The "is she addressed?" decision is an LLM check (robust to phrasing), gated on To/Cc role detected from `get_account_address()`. When Sally *does* reply (to any email — CC or direct), the original To/CC recipients are **preserved on the reply** so nobody gets cut out of the thread. The write prompt also explicitly instructs the LLM to greet the **sender**, not other recipients. See §19.
- **Configuration via `agent_config.yaml`.** Agent name, **company identity** (name/short/email/web — shown in signatures, generated documents/PPTs, and the LLM's own persona; optional, defaults to today's branding so existing deployments are unaffected), active model (pin one provider — including `local` via Ollama — or `""` for Auto multi-model fanout), per-provider model variants, vision provider (cloud or `local`), sender allowlist, routing model, email provider, the other `assistant_agents` sharing the mailbox, and the meeting-domain guardrail are all set in `agent_config.yaml`. Everything hot-reloads on the **next email** except `email_provider` (restart required — see above). See §17.
- **Settings Control Center (`ui/settings_app.py`, launched via `settings.bat`).** A password-gated Streamlit page that edits `agent_config.yaml` directly — not a database, so every change actually reaches the running agent. Includes a **Company Branding** section, a **Local (Ollama)** card in Agent Model with a live-queried dropdown of actually-pulled models (`GET /api/tags`, not a free-text tag) wired into the same single-select pin group as the cloud providers, a matching local-vision-model dropdown, and live **Connect/Disconnect buttons** for the mailbox: Connect opens a browser sign-in and saves the token automatically (no separate connector script needed); Disconnect (two-step confirm) removes it to switch mailboxes. See §21.
- **Fresher web results.** Search tools accept a `time_range` and the GATHER prompt states today's date, so research returns current sources instead of training-era data.
- **Web search only when needed.** The per-email intent classifier now also decides `needs_web_search`: the GATHER (web search) phase runs only for requests needing current/external data (news, prices, benchmarks, research). Basic Q&A, opinions, ideas, drafting, and analysis of an uploaded file skip straight to WRITE — faster replies, no wasted Groq tokens or SerpAPI/Tavily credits, and no web noise in attachment-grounded reports. Fail-open: if the classifier errors, search runs as before. Same prompt update also fixed a pre-existing bug where "research X and make a pdf" was misread as folder delivery.
- **Human-like request comprehension (one classifier pass).** `classify_request_intent` now also decides three things a person judges before answering: **`output_format`** (`email|pdf|word|excel|csv|ppt` — what deliverable to produce), **`uses_attachment`** (does the request actually need the attached file?), and **`uses_thread`** (does it need the earlier conversation?). `output_format` is **LLM-primary**, with the `_detect_output_type` regex as fallback. The two source flags **gate the WRITE-phase context**: a self-contained request isn't distracted by an unrelated attachment or earlier thread chatter, while a follow-up ("his age?", "make it shorter") correctly pulls the thread in. Both source flags are **fail-open** (default `True`) so a needed source is never dropped on doubt or LLM error — they only ever *trim* a source when the LLM is confident it isn't needed. The attachment-failure early-exit now also runs *after* classification, so a failed download only blocks the reply when `uses_attachment` is true (an unrelated attachment no longer stonewalls a "schedule a meeting" email). See §2.
- **HTML email formatting.** Replies render Markdown (headings, bold, bullet/numbered lists) via a shared `html_formatter.py` used by both providers.
- **Drafting deliverables.** "Draft an email/letter" now returns the actual draft (with a `[Your name]` placeholder for third-party drafts), not a meta "strategy / key points" essay.
- **LLM Agent Routing.** Mailboxes can be shared by multiple agents (e.g. Sally and Claude). Before processing, an LLM router (`email_router.py`) reads the email to see which agent it is addressed to. Emails for other agents are filed into a matching **Outlook folder** (created on first use; `get_or_create_label` / `apply_label_and_archive`) and skipped; emails addressing no agent are skipped silently. If filing fails, the watermark does **not** advance — the email is retried next poll, never dropped. Driven by `agent_name` and `assistant_agents` in `agent_config.yaml`.
- **Thread-aware conversations.** Name-routing applies to new threads only. In an ongoing thread the agent: **observes** human-to-human messages (stores them as context, no reply), **joins** when named (reading the quoted thread for missed context), **sticks** to the thread afterwards (follow-ups and corrections need no name — an LLM addressed-check with a replied-to-the-agent's-email signal decides), and **steps back** automatically when the humans resume talking to each other. See §13.
- **Provider-outage handling (honest failure).** When an LLM provider is down for real (token/usage quota exhausted, billing problem, invalid API key), Sally no longer sends the vague "a team member will follow up" reply and silently marks the task done. A classifier (`classify_llm_failure`) recognises quota/billing/auth errors, `run_tool_loop` raises `LLMUnavailableError`, and the supervisor replies honestly: which provider failed, why, *"this task was NOT completed — please resend once resolved."* The task stays **open** with a `failed` event in `task_events`, the execution logs `status='error'`, and the console prints `[FAILED]` instead of `[Done]`. Transient per-minute rate limits are excluded (they already have a truncate-and-retry).
- **Transient provider-overload retry & auto-requeue** — a "503 UNAVAILABLE / high demand" (or 502/504/deadline-exceeded) is a momentary saturation, not an outage, so it is handled separately from the quota/billing/auth path above: **(1) in-place retry** — `invoke_with_tpm_retry` (used by WRITE and the GATHER tool loop) retries a transient overload with exponential backoff + jitter before giving up; **(2) auto-requeue** — if the retries are exhausted on **every** enabled provider for the same email, the agent (`LLMTransientError`) **defers instead of replying**: nothing is sent, the email is picked back up automatically on a later poll after a backoff window (`set_retry_state`/`get_retry_state`, stored in the `config` table — no schema change), and only after `MAX_TRANSIENT_RETRIES` (default 5, ~2/5/10/20/30 min schedule, `core/config.py`) does it give up and send the honest "please resend" notice. A retried message reuses the task it originally minted (`get_task_for_message`/`set_task_for_message`) instead of creating a duplicate, and a deferred attempt is never logged into conversation history (no fake "assistant replied" turn). A genuine quota/billing/auth outage or a normal success is unaffected — only a confirmed transient failure on *every* provider triggers the defer.
- **Reliability hardening (connection & claim hygiene).** SQLite connections in `core/database/sqlite_db.py` are now guaranteed to close via `try/finally` in every function that opens one (previously only 2 of 49 did — an exception mid-query could leak the connection indefinitely). Separately, `run_agent.py`'s cross-process message claim (`claim_message`, 10-minute TTL) is now released immediately on **any** unexpected crash during processing, not just the specific failure branches that already handled it — so a genuine bug no longer locks that email out of retrying for the full 10 minutes.
- **Native Windows delivery (no Docker).** Ships as a plain Python install driven by `install.bat` (locates Python 3.11, creates a shared `.venv`, installs deps, optionally LibreOffice + Tesseract + Ollama + the `bge-m3` embedding model via winget) and `start.bat` (checks `email_provider`, connects the matching mailbox on first run via `Outlook-Connector/connect_outlook.py` or `Gmail-Connector/connect_gmail.py`, then runs `run_agent.py`). See §18, §20, §21.
- **RAG knowledge base (optional, local) — now agentic + hybrid search.** A persistent, global vector store (ChromaDB) the agent can consult when answering. Content is added three ways: dropping a file in the **intake folder** (auto-ingested each poll), an email **"save this to RAG"** command (attachment / web-link / body / *or the agent's own prior research reply in the thread*), or a web link. Text is split by a **structure-aware chunker** that preserves likely headings, paragraphs, lists, and tables; chunks also carry citation metadata (`title`, `section_heading`, `file_name`, `page_number`, `source_date`, `language`). Retrieval is **hybrid**: dense vector search (Ollama `bge-m3` embeddings) combined with **BM25 keyword search**, fused by Reciprocal Rank Fusion — dense alone can under-rank exact/short keyword queries, which matters now that the model can issue its own precise lookup queries (below). A relevance floor (`RAG_MIN_SCORE`, default 0.5) still decides whether any KB content is injected, so an off-topic message ("hello sally") pulls nothing. `rank_bm25` is optional/lazy-imported — without it, retrieval degrades to exactly the original dense-only behavior. Embeddings run **locally via Ollama**, so KB content never leaves the machine and English/Arabic content share one store with cross-language retrieval. Beyond the always-on static lookup, the model now also has a real **`search_knowledge_base` tool** available during GATHER (when the request needs web search too) — it can call the KB multiple times with different queries for requests needing several distinct saved-info lookups, the same way it already could with web search. All optional deps are lazy-imported: a deployment that never sets up RAG is completely unaffected. See §20.
- **Local LLM option (Ollama), including routing and vision.** `agent_model: local` routes replies through a local Ollama model; `routing_model: local` does the same for the "which agent is this addressed to" decision (reusing `provider_models.local`'s model — no separate name to set); `vision_provider: local` routes image/logo auto-tagging through a local multimodal model (`provider_models.local_vision`, a separate tag since the text model isn't multimodal). All three are opt-in per config, no API key, and **never** join `""`-Auto fanout — only used when explicitly pinned, so a machine without Ollama running is unaffected. See §20.
- **Arabic (and mixed AR/EN) support.** Inbound email in Arabic is handled end-to-end: OCR reads Arabic in scanned attachments (`eng+ara`, auto-enabled only if the Tesseract Arabic pack is installed), the classifier reports the request's language (`detected_language`) so replies come back in the same language, and an explicit deliverable-language request (`content_language`, e.g. "write this essay in Arabic", "convert the attached Word file to English") is honoured across email replies, generated documents, and PPTs — including document **translation** (an English doc → an Arabic doc, and vice-versa). See §20.
- **Shared data across version upgrades.** The SQLite DB *and* the RAG vector store both live under one `DATABASE_DIR`; that folder (and `OUTPUT_DIR`) are auto-discovered in the **immediate parent** — the same convention as the shared `.venv` — so a new release dropped beside the old version folder connects to the same accumulated history and knowledge base instead of starting over. Explicit `.env` values always override. See §20.

---

## 1. Architecture Overview

Sally is an autonomous AI agent on a **LangGraph** state machine. She monitors an **Outlook** inbox (Microsoft Graph / O365) and, for each email, runs **one general-purpose agent** that reads any attachments, decides what to do, and returns the result — an email reply, a generated file (PDF / Word / Excel / CSV / PPT), a calendar invite, or a file written to a configured folder.

### Core Principle — One Agent, Many Tools (Claude-Desktop style)

Sally works like the Claude Desktop/web app: a single agent with a toolbox. The user asks for anything; the agent reasons, calls whatever tools it needs (web search, document/spreadsheet/presentation creation, file conversion), and replies. **There is no intent classification and no routing to specialist agents** — the LLM drives everything via a natural tool-calling loop.

- **Agent thinks** — reads the request + attachments, decides whether to search, create a file, convert, or just reply.
- **Tools execute** — read files, search the web, render documents, convert formats.

### Pipeline

```
Email arrives
     ↓
run_agent.py polling loop
   • watermark fetch (mail since last_email_ts, read OR unread, oldest-first)
   • sender allowlist (effective list = agent_config.yaml override else .env)
   • CC guardrail: if Sally is cc-only (not in To), LLM "addressed?" check → skip if not
   • dedup (status='sent', case-sensitive) + SQLite processing-claim
   • download supported attachments
   • unsupported format → polite rejection reply, stop
     ↓
graph.py — extract ALL attachment text (file_reader.py + _analyze_table + OCR)
   • resolve_task (CREATE vs MODIFY, mint/load T-number)
   • classify_request_intent (LLM, once): deliver_to_folder / read_from_folder / intake file
   • agent_config.yaml agent_model pin restricts the fanout set
     ↓
Task context is assembled from the current upload, the task's live deliverable
for edits, and prior source attachments from the same thread.
     ↓
For each enabled model (parallel fan-out):
   assistant_agent:
     • output_type = meeting  → schedule calendar event + invite, reply (short-circuit)
     • intake requested       → load the named file from INTAKE_DIR
     • three phases:
         Phase 1 GATHER : serpapi_search, tavily_search (time_range aware; no file tools)
         Phase 2 WRITE  : single LLM call, format-specific prompt
         Phase 3 RENDER : create_document | create_spreadsheet | generate_presentation
                        | convert_document | write_qa_responses (Q&A fill)
     • deliver_to_folder → copy file to DELIVERY_DIR, confirm path, no attachment
   Output type detected from email body keywords before Phase 1.
     ↓
auto_send — ONE combined email with each model's output + all files (Markdown→HTML)
```

> `supervisor.orchestrate()` is now a thin wrapper that simply runs `assistant_agent` for the given model. The old intent-classifier and specialist agents (proposal/ppt/quick_reply) are retired.

### Flat Tools — the reliability design

Every create-tool takes **simple flat arguments** (`title` + a markdown `content` string), never nested JSON. Groq's native tool-calling produced malformed/duplicate JSON on nested schemas; a single markdown blob is trivial for any provider to emit. The tools parse the markdown (headings, bullets, `| tables |`) into the styled file. `run_tool_loop` additionally salvages Groq's `failed_generation` pseudo tool-calls (`<function=name{...}>`) and executes them.

### Universal Capability — Read Anything, Output Anything

The user uploads **any** supported file (or none), asks Sally to do a task, and gets the result in whatever format they request:

| Format | Read (input) | Create (output) |
|---|---|---|
| PDF | ✅ pdfplumber + PyMuPDF + OCR | ✅ `create_document(format=pdf)` |
| Word (.docx) | ✅ python-docx | ✅ `create_document(format=word)` |
| Excel (.xlsx) | ✅ pandas | ✅ `create_spreadsheet(format=xlsx)` |
| CSV | ✅ pandas | ✅ `create_spreadsheet(format=csv)` |
| PowerPoint (.pptx) | ✅ python-pptx | ✅ `create_presentation` |
| Email body | — | ✅ direct reply (no file) |

If no file is attached, the agent answers from web search + its own knowledge. If the request needs no file ("research top 5 stocks", "what do you think about X?", "suggest ideas"), the answer comes back in the email body. The agent is a **general assistant** — research, advise, suggest, brainstorm, analyse, or draft.

### Phase 2 — Task-Centric Memory (Thread = Inbox, Task = Work Unit)

Each email thread holds one or more **Tasks** (T1, T2, T3…), each a unit of work with its own lifecycle: create → in_revision → delivered.

**Task resolution (`resolve_task_node`):**
1. CREATE verb in new message body → mint a new task
2. `#T<n>` tag in quoted thread → MODIFY that task
3. MODIFY verb + exactly one open task → MODIFY it
4. Fail-safe → CREATE

Task numbers are sequential **per thread** (a fresh thread starts at T1).

**Task context builder (`agent_answer_node`):**
- CREATE requests use the current uploaded source files, if any.
- MODIFY requests carry forward the task's live `output_path` as the editable draft.
- Follow-up requests also carry prior source attachments from the same thread so edits stay grounded on the original PPT/document even when the reply has no new attachment.
- Generated task deliverables are not persisted back into history as source attachments; only user-uploaded source files are stored as `[Attached file content]`. This prevents Sally from gradually treating her own generated output as source truth.
- If the user asks for a revision such as "I dislike the conclusion section, please change it", Sally edits the current task deliverable, preserves unaffected sections, validates against the original source, and sends back the updated file.

### Multi-Model Reply Fanout

With multiple provider keys configured, Sally runs the pipeline once per enabled provider in parallel and sends ONE combined email containing each model's output (divider-separated), with all generated files attached. Output files are named `<MODEL>_T<n>_<Title>_<timestamp>.<ext>` (e.g. `GEMINI_T1_Employee_Performance_Report_20260602.pdf`).

`_detect_reply_to_model` restricts fanout to one model only when exactly one model label appears in the quoted thread (user replying to a specific model).

### Technology Stack

| Layer | Technology |
|---|---|
| Agent Framework | LangChain + LangGraph |
| Architecture | One general-purpose agent + shared tools (no routing); thin `supervisor` wrapper for fanout |
| LLM Providers | Groq + Anthropic Claude, Google Gemini, OpenAI (any/all — model-agnostic; at least one key required) |
| Model selection | Auto multi-model fanout, or pin one via `agent_config.yaml` (`agent_model`) |
| Primary Model | Groq — `llama-3.3-70b-versatile` (free tier: 12k TPM cap) |
| Gemini Model | `gemini-2.5-pro` (BLOCK_NONE safety settings) |
| Web Search | SerpAPI (Google) + Tavily AI — `time_range`-aware for fresh results |
| Doc Generation | python-pptx, python-docx, openpyxl, LibreOffice (PDF on Linux), docx2pdf (Windows) |
| File Reading | pdfplumber + PyMuPDF (PDF), python-pptx (PPTX), python-docx (DOCX), pandas (XLSX/CSV) |
| OCR | Tesseract — per-page fallback for image-only PDF pages |
| Structured Analysis | pandas `_analyze_table()` — auto stats on every table in every file type |
| Storage | **SQLite only** — `{OUTPUT_DIR}/phantomops.db` (WAL). No DB server, no Redis. |
| Email | **Outlook** (Microsoft Graph / O365). A provider router (`email_client.py`) also carries a Gmail backend, but V4 is wired for Outlook. |
| Calendar | Outlook Calendar (O365) — for meeting scheduling |
| Configuration | `agent_config.yaml` (model / vision / allowlist / routing / domains). Optional Streamlit dashboard included, not auto-started. |
| Deployment | **Native Windows — `install.bat` / `start.bat`** (Python venv). LibreOffice + Tesseract installed via winget by `install.bat`. No Docker. |

---

## 2. Agents & Responsibilities

| Agent | File | Tools | Role |
|-------|------|-------|------|
| **Supervisor** | `supervisor.py` | none | Thin wrapper — runs `assistant_agent` per model, aggregates results, logs at supervisor level. No routing logic. |
| **Assistant Agent** | `assistant_agent.py` | serpapi, tavily, create_document, create_spreadsheet, generate_presentation, convert_document, read_qa_structure, write_qa_responses | General-purpose three-phase agent: GATHER → WRITE → RENDER. Handles all output types. |

> **Removed in v4:** `proposal_agent`, `ppt_agent` (agents/), `quick_reply`, and all specialist prompts (`supervisor_prompt`, `proposal_prompt`, `ppt_prompt`, `quick_reply_prompt`). Specialist-agent logic is now unified in `assistant_agent`. `document_analyst` was removed earlier — reading is a tool, not an agent.

### Three-Phase Pipeline (Assistant Agent)

| Phase | What happens |
|-------|-------------|
| **GATHER** | `run_tool_loop` with only `serpapi_search` / `tavily_search`. Collects real data; no file creation. Skipped for pure conversions and whenever the intent classifier decides `needs_web_search=false` (request answerable from attachments + general knowledge). |
| **WRITE** | Single LLM call with a format-specific system prompt (`_WRITE_PROMPTS`). Produces rich markdown. Skipped for pure conversions and Q&A fill. |
| **RENDER** | Calls the appropriate file tool (`create_document`, `create_spreadsheet`, `generate_presentation`, `convert_document`) or routes Q&A fill. |

### Output-Format Detection (LLM-primary, regex fallback)

The deliverable format is **decided by `classify_request_intent` (the per-email LLM call)** as `output_format` (`email|pdf|word|excel|csv|ppt`) — judged by what the user asks to *receive*, not by the format of any attached file ("make a deck from this pdf" → `ppt`, not `pdf`). The `_detect_output_type` regex below is the **fallback**, used only when the LLM leaves `output_format` blank or the call fails (the `meeting` type is regex/`calendar_action`-driven, not part of `output_format`).

`qa_fill` and `meeting` are **task modes**, not deliverable formats — `output_format` can't express them, so a regex-detected `qa_fill`/`meeting` always wins over whatever `output_format` the LLM returned (otherwise a Q&A-fill request could get misrouted into generating a brand-new file — see the bug-fix log, §12).

**`_detect_output_type` (fallback)** — regex scan of the lowercased email body, checked in priority order:

| Priority | Trigger keywords | Output type |
|----------|-----------------|-------------|
| 0a | schedule/set up/book/arrange + meeting/call/appointment/demo | `meeting` |
| 0b | fill/answer/complete + question/clarif/blank | `qa_fill` |
| 1 | ppt, pptx, presentation, slides, deck, pitch | `ppt` |
| 2 | csv, comma-separated | `csv` |
| 3 | create/give/send + excel/spreadsheet | `excel` |
| 4 | word, docx | `word` |
| 5 | pdf (output context, not input reference) | `pdf` |
| — | none of the above | `email` (reply) |

Before each file-type check, a shared **`_strip_input_refs()`** removes determiner+filetype phrases ("the attached excel", "from the pdf", "this presentation") so that *analysing an uploaded file* stays an email reply while *"give me an excel" / "in pdf format"* correctly generate the file.

**Folder delivery & intake are NOT keyword-detected** — `classify_request_intent()` (LLM, run once per email in `graph.py`) decides `deliver_to_folder` / `read_from_folder` / `intake_filename` by meaning, robust to phrasing. A keyword regex remains only as a fallback if the LLM call fails. If folder delivery is requested with no explicit format, the output defaults to **PDF** so a real file exists to save.

### Conversion vs Transformation

- "Convert this PPT to PDF" → `is_conversion=True` → GATHER + WRITE skipped → `convert_document` only
- "Turn this PPT into a sales pitch" → `output_type=ppt` → full GATHER → WRITE → `generate_presentation`

---

## 3. Grounding & Anti-Hallucination

The single most important content rule across all generation prompts:

> Use ONLY facts present in the attached document or in web-search results. **Never invent pricing, costs, financial figures, statistics, names, or metrics** that are not in the source. If the source lacks something the user asked for, say it wasn't provided.

For named sections or phases, Sally verifies each requested item against the available source. If a user asks for "Phase 2 and Phase 3" and the original PPT contains Phase 2 but not Phase 3, the generated output includes Phase 2 and explicitly notes that Phase 3 was not found in the provided material. For revision requests, the current task deliverable is the draft to edit, while the original uploaded source remains the grounding material.

Genuine real-time data is fetched, not guessed: the Proposal Architect's **search phase** actively calls the search tools (up to 8 iterations — e.g. one per stock) to retrieve actual current values (stock prices, market data), captures them in a research brief, and the generation step grounds strictly on that brief + the document. This gives real numbers for "top 5 stocks with prices" while preventing fabrication when the source has no such data.

---

## 4. Attachment Reading & Table Analysis

### File Readers (`tools/file_reader.py`)

| Reader | Notes |
|---|---|
| `extract_pdf_text` | PyMuPDF text + pdfplumber tables; **Tesseract OCR** fallback for image-only pages; `fitz` optional (falls back to pdfplumber text) |
| `extract_ppt_text` | Slide-by-slide; `shape.has_table` → table extraction |
| `extract_word_text` | Element-order (paragraphs + tables interleaved) |
| `extract_excel_text` | pandas — full-dataset analysis + sample (500-row display cap) |
| `extract_csv_text` | pandas — auto-encoding (utf-8/latin-1/cp1252) |

**Attachment size budget (provider-aware, never silent).** The reader cap is generous (`MAX_FILE_READ_CHARS`, default 500,000) so it doesn't pre-truncate. The WRITE phase budget is sized to the **most constrained active provider** (`get_attachment_char_budget` in `config.py`) because the context is assembled once and shared across the fanout: Groq ≈ 60k (free-tier TPM), OpenAI 400k, Claude 600k, Gemini 800k — so pinning a large-context model (e.g. `agent_model: gemini`) lifts the cap ~10× over the old hardcoded 80k. Override outright with `MAX_ATTACHMENT_CHARS`. **When a file still exceeds the budget the truncation is disclosed, not silent:** an inline `[NOTICE: truncated …]` marker is injected into the text the model reads, the WRITE prompt instructs the model to state that its analysis is partial, and `auto_send` appends a user-facing `⚠️` note to the reply ("this response is based only on the portion shown … analysed X of Y chars"). This preserves the §3 anti-hallucination contract: the model can never silently present a partial-source answer as complete.

### `_analyze_table()` — Pre-Computed Stats

Every table in every document type gets a `[TABLE ANALYSIS]` block: column summary (numeric vs text), top-N rankings per numeric column, and group-by summaries. The LLM uses verified pandas-computed numbers instead of doing arithmetic itself.

---

## 5. Output Generators (Tools)

| Tool | File | Output |
|---|---|---|
| `create_document` | `doc_creator.py` | Word/PDF from markdown. Calibri, 1.1" margins, styled tables, bullet/numbered list rendering. LibreOffice → PDF on Linux, docx2pdf on Windows. |
| `create_proposal` | `doc_creator.py` | Formal proposal variant with letterhead, recipient metadata, date. |
| `create_spreadsheet` | `excel_creator.py` | Excel or CSV from markdown tables. Multi-sheet .xlsx with navy header rows, alternating shading, auto-fit columns, frozen header. |
| `create_excel_report` | `excel_creator.py` | Multi-sheet .xlsx (direct params). |
| `create_csv_report` | `excel_creator.py` | Single flat .csv (direct params). |
| `generate_presentation` | `tools/ppt_agent.py` → `ppt_creator.py` | Fully **coded** .pptx (no template file). Navy header band + orange accent. Table slides + image slides + list slides. |
| `convert_document` | `converter_tool.py` | Pure format conversion, no LLM. |
| `read_qa_structure` | `qa_filler.py` | Reads PDF/Word/Excel, auto-detects question + response columns, returns JSON `{total_questions, q_col, a_col, questions[]}` of unanswered rows. |
| `write_qa_responses` | `qa_filler.py` | Fills answers back into the source document (preserves original, writes to `output/proposals/`). Output format: same as source, or word/pdf/excel on request. |

### Q&A Fill Flow

`read_qa_structure` → `_run_qa_hybrid` (single pass ≤ 15 questions, batches of 10 for larger docs) → `write_qa_responses`

Column detection uses keyword scoring (`_Q_KEYWORDS` / `_A_KEYWORDS`) with a positional fallback (last two non-trivial columns). Only empty response cells are filled — existing answers are preserved.

`output_type == "qa_fill"` is a **task mode**, not a deliverable format, and the *wording* regex that detects it (`_detect_output_type`) can't tell "fill in the attached questionnaire" apart from "answer these questions from your knowledge base" typed straight into the body — both say "answer"/"questions". Wording alone used to decide, and misrouted the second case into the document-form-filling flow, which correctly finds nothing to fill and dead-ends (see Bug Fixes Log). Fixed: right after `output_type` is decided, if it's `qa_fill`, the code actually **probes the attachment** with the same `read_qa_structure` check the fill flow itself uses — a genuine fillable structure keeps it as `qa_fill` (still wins over the LLM's `output_format`, e.g. "answer the questions in the attached excel using the attached pdf" isn't misrouted into generating a brand-new spreadsheet); no fillable structure found downgrades it to a normal reply, so a plain question gets answered instead of dead-ending. With **multiple attachments** (e.g. a questionnaire + a reference PDF), `_select_qa_target()` fills whichever file actually has the structure — honouring an explicit format hint ("...in the excel") first; the other file's text still flows in as answering context.

### Converter Matrix (`converter_tool.py`)

| From → | PDF | DOCX | XLSX | PPT |
|---|---|---|---|---|
| PPT/PPTX | ✅ (LibreOffice) | ✅ (slide text/tables/OCR'd images as sections) | ✅ (tables) | — |
| Word/DOCX | ✅ | — | ✅ (tables) | — |
| Excel/XLSX | ✅ | ✅ (sheets as tables) | — | — |
| PDF | — | ✅ | ✅ (tables) | — |
| CSV | — | — | ✅ | — |

Excel outputs extract tables (one sheet per table) with a text fallback if no tables are found. **Any format → PPT is never mechanical** — it's routed through the PPT Strategist (`generate_presentation`, content transformation) instead of the table-based converters above, since turning tabular/document content into slides needs restructuring, not extraction; the converter itself has no PPT-target branch at all.

### Unsupported Formats

If a user uploads a file outside the supported set, `get_unsupported_attachment_names()` detects it and Sally replies politely listing the supported formats (PDF, Word, Excel, CSV, PPT) — before any agent runs.

---

## 6. Phase 2 — Task Management (SQLite)

**`tasks`** — one row per deliverable per thread:
```sql
id (UUID), task_number, thread_id, session_id, task_label,
deliverable_type (ppt|proposal|info|general), status (open|delivered|in_revision|closed),
created_by, output_path, created_at, updated_at
UNIQUE KEY uq_thread_tasknum (thread_id, task_number)
```

**`task_events`** — audit log: `id, task_id (FK), event_type, sender, message, model_name, file_path, created_at`.

MODIFY loads the task's `output_path` for carry-forward, uses prior source attachments as grounding context, and overwrites the task file in place (`os.replace`) when the revised output has the same extension. One task owns one live deliverable; any authorized participant in the thread can request changes to that task.

---

## 7. Database Schema & Dedup (SQLite — `core/database/sqlite_db.py`)

Single file: `{OUTPUT_DIR}/phantomops.db` (WAL mode, `busy_timeout`, foreign keys on). One backend implements every function the old `mysql_db.py` + `redis_db.py` exposed, so the rest of the codebase is unchanged. Schema changes are applied idempotently via a `_MIGRATIONS` list in `init_db()` (catches "duplicate column" on re-run).

### Tables
`sessions`, `messages`, `email_drafts`, `agent_executions`, `tasks`, `task_events`, `config`, `processing_claims`, `session_cache`.

### email_drafts
```
id, session_id (FK), model_name, thread_id, message_id, sender, subject,
user_question, ai_draft, inbox_attachment_path, attachment_path, agents_used,
status (pending|sent|rejected|failed), created_at, updated_at
UNIQUE(message_id, model_name)
```

### Deduplication Logic (hardened)
- **`draft_exists_for_message`** → True if **any** draft for the message has `status='sent'`. One combined email = one delivery → skip.
- **Crash-safe resume (no duplicate sends)** — a draft is saved `pending` *before* the irreversible `send_reply`, and only flipped to `sent` *after*. A crash (or a `send_reply` that delivers but then raises) in that window would leave a `pending`/`failed` draft and, naively, cause a **resend on restart**. Instead, when a fresh poll finds **resumable** drafts (`pending`/`failed`) for a message, the loop **reconciles against the mailbox's Sent Items** (`was_reply_already_sent` — the real source of truth, scoped to messages sent at/after this inbound's timestamp so an earlier reply in the thread doesn't count): **already sent → mark the drafts `sent` and skip (no duplicate)**; **not sent → resend the SAVED draft** (`_resend_saved_drafts` — no regeneration, no replayed side effects like calendar invites/file delivery). A failed Sent-check falls back to resending the saved draft (at-least-once, never worse than before). This closes the structural duplicate-send window the `status='sent'`-as-dedup-key design otherwise leaves open.
- **`save_email_draft`** → `INSERT … ON CONFLICT(message_id, model_name) DO UPDATE` so a stale pending/failed row is updated, not skipped (previously dropped a model from the reply).
- **`claim_message`** → `INSERT OR IGNORE` cross-process claim with TTL expiry, so two Sally instances never both process the same email.
- **`auto_send_node`** → email content is built from `model_results` directly, never gated on whether the DB audit insert succeeded.
- **Case sensitivity** — SQLite text comparison is **case-sensitive by default**, so Outlook/Gmail IDs match exactly (`…GmrQ` ≠ `…Gmrq`) with no special collation needed (the old `utf8mb4_bin` / `BINARY` workarounds are gone).
- **Downtime watermark** — `config.last_email_ts` records the timestamp of the last handled email; the poll fetches everything received at/after it (read OR unread, oldest-first) so nothing is missed across restarts. Because mail delivery is **not monotonic** (a message can be stamped before the watermark yet surface only after it advanced — Exchange/Graph indexing lag, slow relays), the fetch actually starts from `watermark − EMAIL_WATERMARK_LAG_SEC` (default 120s), re-scanning a small recent window so late-arriving-but-earlier-stamped mail is still caught. The re-scanned window is deduped cheaply (`seen_ids` + `draft_exists_for_message`) before any work runs; the stored watermark stays the true high-water mark.

### Send Reliability
- `send_reply` checks the O365 `reply.send()` return value and raises on silent failure → draft marked `failed` → retried next run.
- `mark_as_read` logs warnings on failure.

---

## 8. Conversation Memory

| Layer | Storage | Purpose |
|---|---|---|
| Cache | SQLite `session_cache` | Fast history for active threads (replaces the old Redis cache) |
| Permanent | SQLite `messages` | Full history + audit |

All in one `phantomops.db`. Caching is optional at this scale (~10 users) and never participates in deduplication.

---

## 9. File Structure

```
PhantomOps (V4)/
|
|-- install.bat                 # One-time setup: Python 3.11, shared .venv, deps, (LibreOffice + Tesseract via winget)
|-- start.bat                   # Connect mailbox on first run, then run_agent.py (agent only)
|-- agent_config.yaml           # Live config: agent_name, model, vision, allowlist, routing, assistant_agents, domains
|-- README_VERSION4.txt         # Manager-facing setup steps
|
|-- run_agent.py                # Polling loop: watermark fetch, allowlist, CC guardrail, dedup, claim
|
|-- core/
|   |-- config.py               # Paths, keys, EMAIL_PROVIDER=outlook, OUTLOOK_*, DB_FILENAME, MEETING_*, DELIVERY_DIR/INTAKE_DIR
|   |-- master_graph.py         # Top-level router
|   `-- database/
|       `-- sqlite_db.py        # ALL storage (sessions, drafts, tasks, config, claims, cache) + migrations
|
|-- agents/
|   |-- __init__.py             # LLM factory, run_tool_loop, _OUTPUT_FILE_TOOLS, untrusted-content guard
|   |-- supervisor.py           # Thin wrapper — runs assistant_agent per model, logs as "SUPERVISOR"
|   |-- assistant_agent.py      # Agent: _detect_output_type, classify_request_intent, meeting flow,
|   |                           #   folder delivery/intake, GATHER → WRITE → RENDER
|   `-- prompts/assistant_prompt.py
|
|-- skills/email_skill/
|   |-- graph.py                # resolve_task, model pin, intent classify, agent_answer, auto_send, persist
|   |-- email_client.py         # PROVIDER ROUTER — dispatches to outlook_client or gmail_client per email_provider
|   |-- outlook_client.py       # O365: read/send/mark, to/cc, get_account_address, calendar, routing folders
|   |-- gmail_client.py         # Gmail API backend — same interface, used when email_provider: gmail
|   |-- email_connect.py        # Non-CLI connect/disconnect/status functions, used by the Settings UI (§21)
|   `-- html_formatter.py       # Markdown→HTML email renderer
|
|-- Outlook-Connector/          # One-time mailbox sign-in used by start.bat (connect_outlook.py + guides)
|-- Gmail-Connector/            # Same, for Gmail (connect_gmail.py + guides + ONE_TIME_ADMIN_SETUP.txt) — §21
|
|-- tools/
|   |-- web_search.py           # serpapi_search, tavily_search (time_range aware)
|   |-- rag_search.py           # search_knowledge_base tool — thin @tool wrapper over rag.search, used by GATHER (§20.2)
|   |-- file_reader.py          # All readers + _analyze_table + OCR (find_soffice / find_tesseract)
|   |-- meeting_scheduler.py    # schedule_meeting → Outlook/Google Calendar event + invites (domain-filtered)
|   |-- qa_filler.py            # read_qa_structure + write_qa_responses
|   |-- ppt_agent.py / ppt_creator.py / ppt_reviewer.py
|   |-- doc_creator.py / excel_creator.py / converter_tool.py
|   `-- asset_manager.py        # Logo/image library (vision tagging, multi-provider fallback incl. local)
|
|-- rag/                        # RAG knowledge base, split one module per pipeline stage (§20.2)
|   |-- __init__.py             # convenience re-exports of the public API below
|   |-- chunking.py             # chunk_text / chunk_text_with_metadata — structure-aware chunks + section/page metadata
|   |-- embedding.py            # get_embedder (Ollama) + available() optional-dep gate
|   |-- store.py                # Chroma lifecycle, versioned content_hash, metadata defaults, ingest / ingest_if_new, get_all_chunks (BM25 corpus)
|   |-- ingestion.py            # detect_language, fetch_url_text, scan_intake_folder (INTAKE_DIR auto-ingest)
|   `-- search.py               # retrieve — hybrid dense+BM25 search fused by RRF (score-gated) + format_context
|
|-- ui/settings_app.py          # Settings Control Center — launched via settings.bat (§21), edits agent_config.yaml
|-- print_email_provider.py     # Standalone helper start.bat queries for the active email_provider (§21)
|-- assets/  output/            # Local folders (output holds phantomops.db, deliverables, intake/, logs/)
|-- requirements.txt            # langchain-*, langgraph, O365, pandas, pdfplumber, PyMuPDF, ruamel.yaml, tzdata…
`-- md/phantomops.md
```

---

## 10. Environment Variables (`.env`)

Operational settings live in `.env`; agent behaviour (name, model, allowlist, routing,
domains) lives in `agent_config.yaml` (§17).

```
OUTPUT_DIR                          # deliverables, downloaded attachments, intake/, logs/. Auto-discovered in the
                                    #   immediate parent (shared across version folders) unless set; .env value wins
DATABASE_DIR                        # holds phantomops.db AND the RAG vector store (chroma_db/). Same auto-discovery
                                    #   as OUTPUT_DIR — shared across versions so a new release keeps prior data
ASSETS_DIR                          # templates/letterhead/logo/images; defaults to <delivery>/assets
DB_FILENAME                         # SQLite filename (e.g. phantomops.db)
MAX_FILE_READ_CHARS                 # per-file reader cap (default 500000) — keep ≥ the WRITE budget
MAX_ATTACHMENT_CHARS                # override the provider-aware WRITE attachment budget (default: sized to active provider)

# RAG knowledge base + local models (all optional — RAG is inert until set up; see §20)
OLLAMA_BASE_URL                     # local Ollama server (default http://127.0.0.1:11434)
OLLAMA_MODEL                        # chat model used when agent_model/routing_model: local (default qwen2.5:14b-instruct)
OLLAMA_VISION_MODEL                 # multimodal model used when vision_provider: local (default llava)
EMBEDDING_MODEL                     # Ollama embedding model for RAG (default bge-m3, multilingual)
CHROMA_PERSIST_DIR                  # vector store location (default DATABASE_DIR/chroma_db)
RAG_MIN_SCORE                       # relevance floor 0..1 a retrieved chunk must clear to be used (default 0.5)
RAG_INTAKE_SCAN_INTERVAL_SEC        # how often the poll loop auto-ingests new intake-folder files (default 120)

# LLM (set at least ONE — model-agnostic, falls back across configured providers)
GROQ_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY

VISION_PROVIDER, VISION_MODEL       # image/logo auto-tagging — groq|gemini|claude|openai|local (falls back across configured cloud providers; local is opt-in only, never an automatic fallback)
SERPAPI_KEY, TAVILY_API_KEY

# Email — user-selectable provider (also settable via the Settings Control Center;
# NOT hot-reloaded — the mailbox authenticates once at startup, needs a restart to switch)
EMAIL_PROVIDER=outlook                              # outlook | gmail
OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET / OUTLOOK_TENANT_ID   # used when EMAIL_PROVIDER=outlook
GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET               # used when EMAIL_PROVIDER=gmail — same plain-value
                                                    #   pattern as Outlook, NOT a credentials.json file (see §21)
EMAIL_POLL_INTERVAL                 # seconds (default 30)

# Meetings / file delivery
MEETING_TIMEZONE (default Asia/Kolkata), MEETING_DEFAULT_DURATION_MIN
DELIVERY_DIR (default OUTPUT_DIR), INTAKE_DIR (default OUTPUT_DIR/intake)

# Access
SENDER_ALLOWLIST                    # comma-separated; agent_config.yaml sender_allowlist OVERRIDES this
                                    # EMPTY = fail CLOSED (reply to nobody) unless ALLOW_ALL_SENDERS=true
ALLOW_ALL_SENDERS                   # true = reply to ANY sender when the allowlist is empty (default false)
EMAIL_WATERMARK_LAG_SEC             # lookback window (default 120s) so out-of-order/late mail isn't missed

# Settings Control Center (ui/settings_app.py, launched via settings.bat)
DASHBOARD_PASSWORD                  # default sally2026 — change it for anything beyond local testing

# Storage: SQLite only.
```

---

## 11. Key Design Decisions

| Decision | Reason |
|---|---|
| Agent = thinks, Tool = executes | Clear separation; reading/conversion are tools, not LLM calls |
| `document_analyst` removed | Reading isn't reasoning — `file_reader` already extracts text + table stats |
| Search tools in every agent | Agent decides what to search after seeing the document — accurate, fewer pre-decisions |
| Three sources per agent | Document + web search + own knowledge, combined |
| Proposal agent: search → JSON → tool | Avoids brittle native tool-calling on complex schemas (Groq emitted malformed/duplicate JSON) |
| Grounding rule everywhere | Models invented pricing not in the source — now numbers must trace to source or search |
| Search phase fetches genuine data | "Top 5 stocks with prices" → real web-fetched figures, not hallucinated |
| Output format by keyword, not intent | Supervisor mis-routed PDF requests; agent reads the body directly |
| info_request → Proposal Architect | Research answers need search + structured writing; Quick Reply is only for trivial replies |
| Coded PPT (no template file) | Self-contained design; identical output on any machine without template assets |
| `create_proposal` individual params | JSON-string param caused tool-call validation errors on Groq/Gemini |
| `use_letterhead` string-coerced | Models sent `"false"` (string); coerced to bool safely |
| Letterhead off by default | Never for reports/analyses — only when explicitly requested |
| `draft_exists` = any sent | One combined email = one delivery; prevents duplicate sends |
| `ON CONFLICT … DO UPDATE` (SQLite) | Reprocessed emails update stale rows instead of dropping a model |
| Send decoupled from audit insert | A failed DB write must never drop a model's file from the email |
| SQLite case-sensitive by default | Email IDs match exactly (`GmrQ` ≠ `Gmrq`); no special collation needed |
| SQLite-only (no MySQL/Redis) | One file per agent; zero install; fits per-user isolation at ~10-user scale |
| LLM intent for folder delivery | Keyword lists kept missing phrasings ("output Dir", "mail"); LLM judges meaning |
| Watermark for downtime recovery | Unread-status fetch missed mail opened during downtime; timestamp catches all |
| Watermark lookback (`−lag`) | Delivery isn't monotonic; fetching from `watermark−120s` catches late/out-of-order mail a `≥watermark` filter would skip. Dedup makes the re-scan free |
| Allowlist fails CLOSED | An empty allowlist now denies everyone (was: allow all). Open mode is an explicit `allow_all_senders` opt-in — an autonomous mail/calendar agent must not default to replying to the whole internet |
| CC guardrail = LLM addressed-check | Cc-only mail replies only when truly asked; no reply-all spam on human threads |
| `send_reply` return checked | O365 silent send failures now caught and retried |
| PyMuPDF added to requirements | Was missing — PDF reading silently broken in the packaged build |
| Tesseract OCR fallback | Scanned/image PPT-exported PDFs now readable |
| Unsupported-format rejection | Polite reply listing supported formats instead of silent failure |
| Gemini 2.5 Pro supervisor | Far better at structured JSON classification than Llama |
| Model+task in filenames | `GROQ_T1_…`, `GEMINI_T1_…` — clear provenance per output |
| Provider-aware attachment budget | Old fixed 80k truncated big docs silently and was tuned to Groq's free tier. Budget now scales to the active provider (Gemini 800k, Claude 600k…), so pinning a large-context model stops truncating real documents |
| Truncation is disclosed, never silent | A partial-source answer must not look complete (breaks anti-hallucination). Truncation is flagged inline to the model, in the prompt, and as a `⚠️` note on the reply |

---

## 12. Bug Fixes Log

| Bug | File | Severity | Fix |
|-----|------|----------|-----|
| `alt_id` fallback in `_fill_excel` wrote answers to wrong rows | `tools/qa_filler.py` | **Critical** | Removed the `alt_id = str(row_num - 1)` fallback. The primary `row_id = str(row_num - 2)` correctly maps openpyxl row numbers to pandas 0-based indices; the fallback was one row ahead and could fill a blank/separator row with a neighbouring question's answer. |
| Supervisor logged every email as agent "ASSISTANT" | `agents/supervisor.py` | Major | Changed `agent_name="ASSISTANT"` → `"SUPERVISOR"` in `log_agent_execution`. Previously both `assistant_agent` and `supervisor` wrote "ASSISTANT" rows, making it impossible to distinguish the two in `agent_executions`. |
| `"create_presentation"` phantom entry in `_OUTPUT_FILE_TOOLS` | `agents/__init__.py` | Minor | Removed the duplicate/wrong name. The tool is `generate_presentation`; `create_presentation` never existed. Dead set entry that could silently discard a file path if a tool with that name was ever added. |
| Dead `if False` branch in `_extract_from_word` | `tools/qa_filler.py` | Minor | Removed `q_col = headers[first["_row"] - 1] if False else ""`. The `if False` made the line a no-op (`q_col` was always `""`); the correct column names are derived on the lines immediately after. |
| `q_idx` assigned but unused in `_fill_excel` | `tools/qa_filler.py` | Trivial | Changed `q_idx, a_idx = ...` to `_, a_idx = ...`. |
| CC reply dropped all other recipients (no reply-all) | `graph.py`, `gmail_client.py`, `outlook_client.py` | **Major** | `send_reply` only sent To: the sender. When Sally was CC'd on "To: Siddhi, CC: Sally", her reply went only to the sender — Siddhi was cut out. Fix: `auto_send_node` now computes `reply_cc = (original_to + original_cc) - sender - Sally` and passes it as a `cc` parameter to `send_reply`. Both Gmail and Outlook `send_reply` accept the new `cc` kwarg; `original_to`/`original_cc` flow from `get_message_detail` through `run_agent.py` → `EmailSkillState` → `auto_send_node`. |
| CC reply greeted the wrong person (To-recipient instead of sender) | `agents/assistant_agent.py` | **Major** | On a CC'd thread (To: Siddhi, CC: Sally, From: Siddhant), Sally said "Hi Siddhi" instead of "Hi Siddhant". The LLM confused the To-recipient with the requester. Fix: added an explicit `REPLY TO (greet this person): {sender}` line to the write context, and the email write prompt now states: "Open with a greeting to the SENDER … NEVER greet someone other than the sender." |
| Hypothetical questions triggered unwanted email drafts | `agents/assistant_agent.py` | Major | A question like "what would Claude say about trees?" triggered the `DRAFTING` rule because the LLM misinterpreted "what would Claude say" as a command to draft a message. Fix: Added an explicit guard rule stating that "DRAFTING only applies when the user explicitly asks YOU to draft... Questions like 'what would X say about Y' are NOT drafting requests." |
| On Outlook, agent routing silently DROPPED emails for other agents | `outlook_client.py`, `email_client.py` | **Critical** | The routing feature shipped with no-op stubs for Outlook (`get_or_create_label` returned `""`, `apply_label_and_archive` did nothing), but the loop still advanced the watermark — every email addressed to another agent was lost. Fix: real Outlook implementations (find/create a mail folder by name, mark read + `msg.move()` into it), stubs deleted, imports wired in `email_client.py`. |
| Routing filing failure still advanced the watermark (email lost) | `run_agent.py`, `gmail_client.py` | **Critical** | If the Gmail Labels API failed transiently, `label_id=""` meant the email was neither labelled nor marked read, yet `_handled()` advanced the watermark past it — permanently skipped, unread, for every agent. Fix: the routing block wraps filing in try/except; on failure it sets `batch_ok=False` and discards the seen-id so the email is retried next poll. `apply_label_and_archive` now re-raises instead of swallowing errors. |
| Provider quota/billing outage reported as task success | `agents/__init__.py`, `assistant_agent.py`, `supervisor.py`, `graph.py`, `run_agent.py` | **Major** | When the LLM provider ran out of tokens/credits (or the API key was invalid), the error was swallowed, the sender got the vague "a team member will follow up" reply, the task was marked `delivered`, and the console printed `[Done]`. Fix: `classify_llm_failure()` detects quota/billing/auth errors; `run_tool_loop` and the WRITE phase raise `LLMUnavailableError`; the supervisor sends an honest notification naming the provider and reason ("task NOT completed — check billing/usage, resend when resolved"); the task stays open with a `failed` task-event; execution logs `status='error'`; console prints `[FAILED]`. Per-minute rate limits are excluded (transient, already retried). |
| Router crashed on block-list LLM responses, degrading to string match | `agents/email_router.py` | Minor | `resp.content.strip()` raised `AttributeError` when a thinking model returned content blocks (a list, not a string), silently degrading every routing decision to the naive substring fallback. Fix: route the response through the shared `extract_text_content()` helper. |
| Outlook-client replies leaked the whole quoted thread into `body` | `gmail_client.py` | **Major** | `_strip_quoted` only knew Gmail markers (`On … wrote:` / `>` lines). Replies sent from Outlook desktop/mobile (`________` rule, `-----Original Message-----`, or a bare `From:`+`Sent:/To:/Subject:` block) kept the entire quoted conversation in the body — polluting output-type detection, intent classification, and the LLM context. Found in production: a follow-up about RAG triggered the meeting flow because the QUOTED previous reply contained "behind schedule, … not meeting its objectives". Fix: `_strip_quoted` now breaks on all three Outlook quote styles (verified against the real stored email). |
| Meeting flow hijacked by idioms ("behind schedule", "meeting its objectives", "the plan") | `agents/assistant_agent.py` | Major | `_detect_output_type` matched `schedule…meeting` etc. on idiomatic noun/verb usage, so a report request like "why the project is behind schedule and not meeting its objectives" short-circuited into calendar scheduling. Fix: idiom phrases are neutralised before the regex, and a determiner before the trigger word ("the plan", "a fix", "our schedule") no longer counts as a scheduling verb. Real requests ("schedule a meeting with X", "set up a call") verified unchanged. |
| PDF conversion silently failed on native Windows (no Docker) | `converter_tool.py`, `doc_creator.py`, `qa_filler.py` | Major | All three called the Linux command name `libreoffice`, which does not exist on Windows (binary is `soffice.exe`, not on PATH) — so PPT→PDF and the Word→PDF fallback produced no PDF off-Docker. Fix: shared `find_soffice()` resolves the binary on PATH or in the default Windows install dirs and is used everywhere LibreOffice is invoked. |
| Groq per-minute (TPM) rate limit reported to the user as a "billing problem" | `agents/__init__.py` | **Major** | A single large request (13337 tokens > Groq free-tier 12k TPM) raised a transient `rate_limit_exceeded` error — but `classify_llm_failure` matched the word "billing" inside the upgrade URL Groq appends (`.../settings/billing`) and aborted the task with a false "billing problem" email. Fix: per-minute/RPM rate limits (and "reduce your message size"/"request too large") are now detected FIRST and classified transient (None); only daily (TPD) limits and real payment/quota phrases count as outages. `rate_limit_exceeded` removed from the quota patterns. |
| Large prompts hit Groq's 12k TPM cap (root cause: uncapped tool output) | `tools/web_search.py`, `agents/assistant_agent.py`, `agents/__init__.py` | Major | The real driver was **uncapped web-search output** — `tavily_search` (`search_depth="advanced"`) returned 1-2k chars of `content` per result × 5 results, and the agent searches up to 8 times, accumulating into the GATHER context and the research brief fed to WRITE → a single 13k-token call over the cap. **Root-cause fix:** cap each search result to 700 chars and each search call to 3500 chars (`_cap` in `web_search.py`), and cap the research brief injected into the WRITE prompt to 8000 chars. **Safety net:** `invoke_with_tpm_retry()` trims the longest message ~40% and retries (≤3×) on a TPM error in the WRITE phase. Strategic option for consistently large workloads remains pinning Gemini or upgrading Groq to Dev tier. |
| Outlook polling crashed every cycle: `'QueryBuilder' object has no attribute 'on_attribute'` | `skills/email_skill/outlook_client.py` | **Critical** | `list_unread_messages`/`list_messages_since` used the OLD O365 chained-query API (`new_query().on_attribute(x).equals(y)` / `.order_by(...)`). The installed O365 (>=2.0.33) ships a rewritten `QueryBuilder` where filters are one-shot (`qb.equals("isRead", False)`, `qb.greater_equal("receivedDateTime", dt)`) and ordering is a separate `CompositeFilter` combined with `&` (`qb.orderby(("receivedDateTime", True))`). Every poll failed with no messages ever fetched. Fix: rewritten to the new API; verified via `as_params()` against the OData query that Graph expects. |
| Outlook "Mailbox address: (unknown)" — CC guardrail always off | `skills/email_skill/outlook_client.py` | Minor | `get_account_address` called `account.get_current_user()`, which doesn't exist on the installed O365 `Account` class (it's `get_current_user_data()`, returning `None` for non-interactive auth flows). The call always raised, so Sally's own address was never resolved and the CC guardrail stayed permanently disabled. Fix: use `get_current_user_data()` and handle a `None` result. |
| Outlook sign-in silently reused the browser's logged-in account (no mailbox picker) | `skills/email_skill/setup_outlook_oauth.py`, V4 `Outlook-Connector/connect_outlook.py` | Minor | The O365 consent URL is built by MSAL's `initiate_auth_code_flow(...)` with no `prompt`, so Microsoft reused the existing browser session and auto-connected whatever account was signed in — you could never choose a different mailbox. Fix: a `_force_account_picker()` helper injects `prompt=select_account` into the consent URL before the browser opens (and in the console-paste flow), so the account chooser ("Use another account") appears every time. Also fixed the legacy `scopes=` kwarg → `requested_scopes=` (the former is ignored on O365 ≥2.1 and the flow fails with "Must provide at least one scope"). |
| Meeting invite left out the requester ("me") | `agents/assistant_agent.py` | Major | "Create a meeting for me and Devesh" only kept email addresses appearing literally in the body, so the sender ("me") was dropped and the invite went to Devesh alone (organised by the agent mailbox). Fix: the sender's address is passed into `_run_meeting_flow`, the prompt maps first-person words to it, and a deterministic backstop inserts the sender when they self-refer; third-party scheduling (no first-person) still excludes them. |
| Stray inline logo treated as a failed attachment → blocked unrelated requests | `skills/email_skill/outlook_client.py`, `run_agent.py`, `graph.py` | **Major** | Outlook's Graph `has_attachments` counts **inline images** (e.g. a signature logo), so a plain "set up a bi-weekly meeting…" email looked like a failed file upload → the graph replied "re-send the file" (before classification) and never scheduled. Fix: `has_real_attachment()` checks for a **non-inline** attachment; `attachment_status` is only `download_failed` when a REAL file failed. Then the "re-send, don't run the LLM" early-exit is correct/unconditional (saves tokens on a genuinely unreadable file) and a stray logo no longer triggers it. Gmail already excluded inline parts. |
| Domain guardrail bypassed: reply-all auto-CC'd external recipients (Outlook) | `skills/email_skill/outlook_client.py` | **Critical** | `send_reply` used O365 `msg.reply()` which **defaults to `to_all=True`** → Graph's reply-all endpoint **auto-populates the reply with every original To/Cc recipient (incl. external/out-of-domain) before our code runs**. The reply-CC domain filter only cleaned the list *we* add, so externals slipped through and got the report. Fix: `msg.reply(to_all=False)` — reply to the **sender only**, then add **only** the already-domain-filtered `cc` list. Verified live: `to_all=True` auto-CC'd 5 recipients; `to_all=False` → To=sender, Cc=empty (we control it). Gmail was already safe (builds To/Cc manually). |
| Thread-aware attachment retrieval (feature) + Graph 400 on conversationId+orderby | `skills/email_skill/outlook_client.py`, `gmail_client.py`, `run_agent.py` | Feature | When an email **references a file** ("the attached file") but none is attached to *this* message, the agent looks back through the **same thread** (Outlook `conversationId` / Gmail `threadId`) for the most recent attachment it received and reuses it. Only reaches messages the agent was on (CC'd throughout) — it cannot pull a file from a message it never received (carry-forward or intake folder needed there). Caught live: Graph rejects `$filter conversationId` **combined with** `$orderby` ("restriction or sort order too complex") → filter only, sort client-side. Verified live (pulled a real .xlsx from an earlier thread message). |
| Reply-All scheduled a meeting from the QUOTED thread (Outlook) | `skills/email_skill/outlook_client.py` | **Critical** | O365 `get_body_text()` returns the FULL body *including* the quoted reply chain (the code wrongly assumed it stripped quotes, `body_quoted=""`). On a Reply-All the entire prior conversation landed in `body`, so old text ("…setting up a meeting…book a call…") triggered a false **schedule** even though the new ask was "read the attached file / costing". Fix: run `_split_quoted()` on the Outlook body too — the **new text** is the query (drives classification), the **quoted thread** goes to `body_quoted` as background context only. Verified on the real emails: whole-body→`schedule` (bug) vs new-query→`none` (fixed). |
| Outlook meeting search returned a Graph 400 (cancel feature broken) | `tools/meeting_scheduler.py` | **Critical** | `find_meetings` filtered Outlook events with `$filter=start/dateTime ge <datetimeoffset>`, but Graph stores `start/dateTime` as a **string** → "A binary operator with incompatible types was detected (Edm.String vs Edm.DateTimeOffset)". Every calendar search failed, so the entire cancel-meeting flow was non-functional on Outlook. Fix: use Graph's **calendarView** via O365 `get_events(include_recurring=True, start_recurring=…, end_recurring=…)` (also expands recurring events); subject filtered client-side. **Caught by a live create→find→cancel round-trip against the real mailbox** (unit tests passed but couldn't exercise Graph). |
| Agent confused the current request with the attached file / dragged in unrelated thread context | `agents/assistant_agent.py`, `skills/email_skill/graph.py` | Major | The WRITE phase always fed in *every* available source — the attachment and the whole quoted thread/history — even when the request didn't refer to them, so an answer could be skewed by an unrelated attached file or by old conversation. Like a person, the agent should first decide *which* sources a request actually needs. Fix: `classify_request_intent` now returns `output_format` (LLM-primary deliverable format), `uses_attachment`, and `uses_thread`; the WRITE assembly gates the attachment context on `uses_attachment` and the thread/history context on `uses_thread`. Both flags are **fail-open** (default `True`) so a needed source is never dropped on doubt or LLM error — they only trim when the LLM is confident. Classification also moved *above* the attachment-failure early-exit so a failed download only blocks when `uses_attachment` is true. Live-verified: "his age?" → `uses_thread=True`; "make a ppt from this pdf" → `ppt`+`uses_attachment`; "explain ML" → `email`, no sources. |
| New `output_format` classifier field bypassed Q&A fill / filled the wrong attachment | `agents/assistant_agent.py` | Major | The `output_format` addition above (`excel`/`email`/…) is decided by the SAME classifier call as `qa_fill`'s trigger, and it took priority — so "answer the questions in the attached excel using the attached pdf" got routed to `output_format='excel'` (generate a NEW spreadsheet) instead of the Q&A-fill flow. Separately, when multiple files were attached, the fill flow always used `inbox_attachments[0]` — filling the reference PDF instead of the actual questionnaire if it happened to be attached first. Fix: `qa_fill`/`meeting` are task modes, not deliverable formats, so regex-detected `qa_fill`/`meeting` now wins over the LLM `output_format`; new `_select_qa_target()` picks whichever attachment actually has a question/answer structure (honouring an explicit format hint first) instead of always the first file. Verified: `qa_fill` wins over `output_format='excel'`; selector picks the questionnaire even when the reference file is attached first. |
| Meeting requests with no time of day silently booked at 10:00 | `agents/assistant_agent.py` | **Major** | The meeting-extraction prompt explicitly told the LLM "if no time of day is given use 10:00:00" — so "schedule a meeting with X tomorrow about Y" (no time stated) booked a real calendar invite at a guessed 10am instead of asking for the actual time; the missing-time validation never fired because the field was never actually empty. Fix: the extractor no longer guesses — a stated date with no clock time leaves `start_datetime` blank and lists `"time"` in `missing` (distinct from `"date"`), so Sally asks precisely for what's absent. The partial request is stashed (`set_pending_action(..., "schedule", {needs_info, original_body, sender})`) so the user's reply (e.g. "3pm") completes the SAME booking instead of being read as a new, detail-less request. Live-verified: "...tomorrow about Q3" → "I just need the time" + pending stashed; "...tomorrow at 3pm" → passes validation and proceeds to book. |
| Transient provider overload (Gemini 503 "high demand") reported as a completed task | `agents/__init__.py`, `agents/assistant_agent.py`, `agents/supervisor.py` | **Major** | A `503 UNAVAILABLE` / "high demand" error is NOT a quota/billing/auth outage, so `classify_llm_failure` returned nothing for it — the WRITE phase silently swallowed the error, produced no content, and fell through to the generic "a team member will follow up" reply, sent to the customer as if the task had succeeded (task marked delivered). Fix: `invoke_with_tpm_retry` now also retries a transient overload with exponential backoff + jitter (genuine outages still bypass all retry and re-raise immediately); if the retries are exhausted, WRITE raises a new `LLMTransientError` instead of falling through, and the supervisor handles it exactly like a real outage (`llm_failed=True`, task stays open) — with an honest reply that doesn't wrongly suggest a billing problem. Reproduced the exact reported case end-to-end: retries with backoff, then raises `LLMTransientError` correctly. |
| All-providers-transient-down still sent an apology on every single retry | `skills/email_skill/graph.py`, `run_agent.py`, `core/database/sqlite_db.py` | Major | Once the fix above existed, EVERY transient failure (even a 5-second blip) still immediately sent the "please resend" notice — no better than the original bug from the customer's perspective, and it would re-send that notice on every subsequent retry too. Fix (auto-requeue): when every enabled provider's failure is confirmed transient, `auto_send_node` **defers instead of sending** — `send_ok=False` reuses the poll loop's existing retry-next-poll machinery; a backoff ledger (`set_retry_state`/`get_retry_state`, stored in the `config` table) makes the poll loop wait out a schedule (2/5/10/20/30 min) before retrying the SAME email, giving up and sending the honest notice only after `MAX_TRANSIENT_RETRIES` (5). Two related gaps found and closed while building this: a retried message would have minted a duplicate orphaned task each cycle (fixed via `get_task_for_message`/`set_task_for_message` reuse), and `persist_node` would have logged a fake "assistant replied" turn for every deferred attempt that was never actually sent (fixed by skipping persistence when `send_ok` is false). Verified end-to-end via the real compiled graph: first failure defers with nothing sent; a same-message retry reuses the same task; budget-exhausted sends the honest notice; a genuine quota outage and a normal success are both unaffected; zero history entries logged across deferred attempts. |
| SQLite connections leaked on exception paths | `core/database/sqlite_db.py` | Major | Only 2 of 49 functions guaranteed `conn.close()` via `try/finally` — the other 34 closed the connection only on the success path, so any exception between opening it and the explicit `close()` (a bad write, an unexpected value) left it open indefinitely, accumulating silently over the agent's long uptime. Fix: every function now wraps its query work in `try/finally`; where a function could fail while *acquiring* the connection itself, that stays a separate preceding `try/except` (an unconditional `try/finally` around acquisition would raise `NameError` closing a variable that was never assigned). No query, table, or return-value logic changed. Leak-detection test: forced a mid-query exception across all 3 original code shapes (no try at all, try/except with close inside try, early-return-before-close) — connection now closes in every case (previously would have leaked in all 4 tested). |
| Cross-process message claim not released on unexpected crashes | `run_agent.py` | Major | `claim_message()` takes a 10-minute claim on an email before processing starts; several specific failure branches already released it, but the top-level catch-all `except Exception` (for anything not already anticipated) did not — a genuine crash left the claim locked for its full TTL even though the same process could safely retry immediately. Fix: `claimed_message_id` is set right after a successful claim and checked in the catch-all handler to release it there too. Verified: simulated an unanticipated exception after a successful claim — the message could be re-claimed immediately afterward instead of waiting out the 10-minute lock. |

| Duplicate send possible after a crash between send and the `sent` DB write | `run_agent.py`, `core/database/sqlite_db.py`, `outlook_client.py`, `gmail_client.py`, `email_client.py` | **Major** | Dedup keyed on `status='sent'`, but the row is marked `sent` *after* the irreversible `send_reply`. A crash (or a `send_reply` that delivers then raises) in that window left a `pending`/`failed` draft, so the next poll regenerated and **resent** — a duplicate email plus replayed side effects (calendar invite, files). Fix: on a fresh poll, **resumable** drafts (`get_resumable_drafts_for_message`, status `pending`/`failed`) trigger a **reconciliation against Sent Items** (`was_reply_already_sent`, scoped to this inbound's timestamp): already sent → `mark_message_drafts_sent` + skip (no duplicate); not sent → `_resend_saved_drafts` (saved draft, no regeneration/side-effects). Sent-check error → resend saved (at-least-once, never worse). Also closes the pre-existing case where `send_reply` delivered but raised on the response. |
| RAG collection name collided with a stale pre-existing store | `core/config.py` | **Major** | The new RAG code reused the collection name `rag_docs` — the exact name left behind on disk by this codebase's *original* (since-deleted) RAG system, built with a different embedding model/dimension (768-d vs bge-m3's 1024-d). On any machine with that leftover `chroma_db/`, the first real ingest hit Chroma's "embedding dimension mismatch" and silently disabled RAG (caught by graceful degradation → looked like RAG never worked). Fix: collection renamed to `phantomops_kb` (env-overridable), sidestepping the collision without touching old data. |
| RAG leaked irrelevant KB content into off-topic replies ("hello sally" → gen-AI slides) | `tools/rag_store.py`, `core/config.py` | **Major** | Chroma always returns the *nearest* chunks however far, so a bare greeting still pulled the top-K of whatever was in the global KB (e.g. gen-AI slides saved in a different thread) at a weak score, and the WRITE prompt then told the model to "ground your answer" in them → a greeting got a ramble about slides the sender never mentioned. Fix: a relevance floor (`RAG_MIN_SCORE`, default 0.5) drops weak matches before injection — measured with bge-m3, irrelevant greetings top out ~0.43 while genuine matches (incl. cross-language EN↔AR) score ~0.65+, so 0.5 sits cleanly in the gap. Verified: greetings inject nothing; real and cross-language questions still retrieve. |
| Arabic essay/PPT requested in an English email came out in English | `agents/assistant_agent.py`, `tools/ppt_agent.py` | **Major** | The reply-language logic keyed only on `detected_language` (the language the *email* was written in). "Please write me an essay **in Arabic** about India" is an English request for Arabic *content* — `detected_language='en'`, so nothing told the writer to produce Arabic, and the model wrote an English essay *about* writing in Arabic. Fix: a new `content_language` classifier field captures an explicit deliverable-language request separately from the request's own language, takes priority in the WRITE prompt, and is threaded into `tools/ppt_agent.py` (which has its own generation prompt) so slide content also switches language. Live-verified both directions and formats. |
| "Convert the attached Word file to Arabic" produced an untranslated file | `agents/assistant_agent.py` | **Major** | A format word next to "convert" ("convert the **word** file to arabic") tripped the `is_conversion` regex → routed to the mechanical format converter, which **skips the WRITE phase entirely**, so the `content_language` translation instruction never ran and the file came back untranslated (or failed). Fix: an explicit target language means it's a *translation*, not a format conversion — force the transformation path and return the translated document in the **source file's own format** (Word→Word, PDF→PDF). Checked before the PDF→spreadsheet special case. Verified: English `.docx` → genuine Arabic `.docx`; mechanical "word → pdf" (no language) unaffected. |
| MODIFY follow-up lost the deliverable's file type / language | `agents/assistant_agent.py` | **Major** | On a follow-up like "expand the time-management section" or "add a slide after slide 3", the classifier (seeing only the new message) defaulted `output_format` to `email` and could drop the carried-forward file — so a Word/PDF/Excel task silently degraded to a plain email reply that ignored the actual document, and an Arabic deck reverted to English on an edit that didn't restate "in Arabic". Fix: for a MODIFY task the carried-forward prior file is authoritative — `uses_attachment`/`uses_thread` are forced true, `output_type` is set from the prior file's own extension (PPT had a narrower fix already; generalised to word/pdf/excel/csv), and the prior deliverable's language is kept when the follow-up doesn't restate one. Verified across a create→modify→convert PPT thread and a Word-report edit. |
| Arabic replies signed off with `[اسمك]` ("[your name]") instead of the agent name | `agents/assistant_agent.py` | Minor | Switching the reply to Arabic pulled the model toward generic Arabic letter templates that end in a placeholder, overriding the earlier "sign as {name}" instruction. Fix: the signature requirement is restated *alongside* the language-switch instruction (using the configured `get_agent_name()`, not hardcoded) — translate only the closing phrase, never replace the signer's name with a placeholder. |
| `agent_model: local` silently ignored — replies kept running on a cloud provider | `skills/email_skill/graph.py` | **Critical** | The pin-override check was `if pinned and pinned in enabled:`, but `enabled` (`get_enabled_models()`) only ever lists cloud providers with an API key — `local` is deliberately never in it (it's opt-in-only, not part of `""`-Auto fanout). So `"local" in enabled` was always `False`, the override never fired, and the agent kept replying on whatever cloud provider was configured, silently. Fix: `pinned == "local"` is special-cased to force the fanout set to `["local"]` directly, bypassing the cloud-key check. Verified across local-pin-with-cloud-keys, local-pin-no-cloud-keys, cloud-pin, and Auto — no regression to existing fanout behaviour. |
| `xlsx → docx`, `pptx → docx`, and any format `→ PPT` conversions were unsupported | `tools/converter_tool.py`, `agents/assistant_agent.py` | Major | The converter had `docx→xlsx` and `pptx→xlsx` (table extraction) but never the reverse pairs, and had **zero** branch for any `→ ppt/pptx/powerpoint` target at all — "convert this Excel/Word/PDF to PowerPoint" always failed with "Unsupported conversion". Fix: added `_excel_to_word()` and `_pptx_to_word()` (sheets/slide text+tables+OCR'd images become sections/tables in a Word doc); any→PPT is rerouted *before* reaching the mechanical converter into the PPT Strategist content-transformation path instead (turning content into slides needs restructuring, not extraction — see §5). |
| Groq vision auto-tagging silently broken (dead model + reasoning-model truncation) | `tools/asset_manager.py` | **Major** | The hardcoded default vision model (`meta-llama/llama-4-scout-17b-16e-instruct`) no longer exists on Groq's API at all (confirmed against Groq's live model list) — image/logo auto-tagging failed on every call, with no other cloud provider key configured to fall back to. Switching to `qwen/qwen3.6-27b` alone wasn't enough: it's a reasoning model that emits a `<think>...</think>` preamble before answering, and the hardcoded `max_tokens=300` truncated the response (`finish_reason=length`) before the JSON ever completed, so the regex silently found no match and returned `None` — no exception, no log. Fix: new default model + `max_tokens=800`. Verified via the real Groq API: 300 tokens truncates mid-reasoning, 800 completes and parses. |
| `qa_fill` misrouted a plain "answer these questions" email into document form-filling | `agents/assistant_agent.py` | **Critical** | `_detect_output_type`'s `qa_fill` regex is wording-only ("answer"/"fill" near "question") and can't tell "there's a form attached with blanks" from "I typed my own questions, just answer them from your knowledge base" — both trip the same words. A sender who typed 3 questions in the body while referencing an earlier-attached *reference* PDF (not a form) got routed into the document-extraction flow, which correctly found nothing fillable and dead-ended. **Two more bugs stacked on top, found while fixing this:** (1) Phase 3 (RENDER) had no `qa_fill` case at all, so it fell through to a catch-all (`answer = written_content`) that **silently wiped out** whatever sensible early-exit message `qa_fill`'s own Phase 2 logic had already built — replacing it with nothing, which then produced the generic "unable to complete, team member will follow up" apology instead of an actual answer *or* a useful error. (2) A separate "MODIFY task + carried-forward attachment → regenerate that file type" heuristic then re-hijacked even a correctly-downgraded plain question back into a document-regeneration attempt — a plain follow-up like "what's the uptime SLA?" in a MODIFY-tagged thread with an attached PDF triggered a live web search and generated an unrelated PDF instead of just answering inline, even after the `qa_fill` fix, because the heuristic keyed only on `task_intent=="modify" + attachment present`, not on whether the request was actually an edit. Fix: `qa_fill` now probes the actual attachment structure before committing to the flow (§5); RENDER gets an explicit no-op case for `qa_fill` (Phase 2 already fully handled it); the MODIFY-continuity override now also requires an actual edit-intent verb (add/change/update/expand/etc.) before firing, and skips entirely when `qa_fill` just downgraded to email. Verified: original bug report, the classic fillable-questionnaire case (incl. under a MODIFY task), and a plain follow-up question in a MODIFY thread with an attachment — all correct, no regressions. |
| RAG replies opened with "the knowledge base doesn't have enough information" — then answered correctly from it anyway | `agents/assistant_agent.py` | Major | The KB context header told the model the material "may be partial ... say so if it doesn't fully cover the question", which it read as a standing instruction to hedge, regardless of whether the retrieved content actually answered the question. Retrieved chunks already clear the `RAG_MIN_SCORE` relevance gate (§20), so they're relevant by construction — the caveat should be the exception, not the default posture. Fix: reworded to state KB facts plainly and reserve the caveat for content that genuinely doesn't address part of the question. Verified: Arabic and English questions covered by the KB now answer directly with no disclaimer; a question genuinely not in the KB (a different topic) still correctly says so before falling back to general knowledge. |
| Settings UI edits had zero effect on the running agent | `ui/settings_app.py` | **Critical** | Every control (agent name, model pin, per-provider model, vision provider, allowlist) wrote to a SQLite `config` table via `get_config`/`set_config` — a leftover from an earlier architecture. The agent has read `agent_config.yaml` exclusively for all of this since Phase 2 (§17); nothing in the pipeline has consulted that DB table in a long time, so every "saved" change silently did nothing. The per-provider model dropdown was worse — it wrote a key (`f"{pid}_model"`) that no code, past or present, ever read at all. Fix: new `set_agent_config_value()` (`core/config.py`) writes directly to `agent_config.yaml` via `ruamel.yaml`'s round-trip loader instead of plain `yaml.safe_load`/`dump` — critical because this file carries extensive hand-written documentation comments that a naive load+dump would silently delete on the first UI save. Found and fixed a real `ruamel` quirk along the way: a comment trailing a list (e.g. the note above `allow_all_senders`, right after `sender_allowlist`'s items) is anchored to the *list's own last-item index*, not the parent mapping — replacing the list wholesale orphans it regardless of in-place vs. by-reference assignment; fixed by explicitly capturing and re-anchoring it to the new last index after every edit. Every control in the UI now rewired to this function. |
| Settings UI crashed on launch (`SyntaxError`) — passed local testing, failed in production | `ui/settings_app.py` | **Critical** | A status-badge f-string used backslash-escaped quotes inside its `{}` expression part — legal only under PEP 701 (Python 3.12+). The shared production venv (`C:\<delivery>\.venv`) runs 3.11.9, where this is a hard `SyntaxError` that prevents the script from parsing at all, not a style nit. It shipped because verification ran against a newer Python on the dev machine's PATH that silently tolerates the relaxed grammar — `ast.parse()`/AppTest passed there without ever exercising the actual production interpreter. A second, unrelated issue surfaced by the same real run: `ruamel.yaml` (added to `requirements.txt` for the fix above) was never installed into that same pre-existing shared venv, since it predates the dependency being added — a fresh `install.bat` run picks it up automatically, but an already-existing `.venv` needs a manual catch-up `pip install`. Fix: rewrote the f-string to build the badge HTML as a plain string first (no escaping needed); installed the missing dependency. **Lesson institutionalised:** verify UI/dependency changes against the actual shared venv's interpreter (`.venv\Scripts\python.exe`), never whatever Python happens to be default in the dev shell. |
| A source-file mention could be misread as the requested output format | `agents/assistant_agent.py` | **Major** | `_detect_output_type`'s creation-verb regex scanned the raw email body (deliberately, to keep "make the PPT" from being stripped as an input reference) — but this meant "Give me a summary of the attached pdf in **word format**" could latch onto "the attached pdf" as the target instead of "word", since it was the nearest format word to the trigger verb "give". Confirmed both the regex fallback and (separately) that the LLM-primary path already handled this correctly, so the bug only surfaced when the LLM classifier call itself failed and the regex safety net took over. Fix: strip only unambiguous input-file mentions (`attached`/`uploaded`/`provided` + filetype) before the creation-verb scan, leaving weaker `the`/`this` determiners alone so `"create the PPT"` still works. Verified across 11 cases (the reported bug, input-only mentions, and the original `"make the PPT"` regression this scan exists to prevent) — no regressions. |
| PPT image slides could be built around an irrelevant or filename-guessed image | `tools/asset_manager.py`, `tools/ppt_creator.py` | **Major** | Three compounding issues, found via a live test run with real (>4MB) images: (1) `_tag_image()` silently fell back to filename words when vision tagging failed for any reason (no provider, API error, or — the common case — the image exceeding a 4MB size limit) and that guess was surfaced as a verified "available theme" in the PPT-generation prompt exactly like a real vision tag. (2) `search_category()` fell back to an arbitrary asset when nothing scored above zero, so a genuinely unmatched query still got *some* random image instead of none. (3) `ppt_creator.py`'s layout dispatch let a resolved `image_path` force an `image_left`/`image_right` layout even on a slide the model explicitly set to `"auto"`. Fix: filename-derived tags are marked `tag_source: "filename"` and excluded from the theme summary (and retried on the next `scan_all()` instead of stuck forever); `get_best_image()` now returns `None` (no image, not a random one) when nothing genuinely matches; a resolved image only renders on a slide the model actually requested one for. Verified on a real deck: images with real vision tags matched correctly, images stuck on filename tags correctly showed no picture instead of a wrong one. |
| An explicit slide-count request could silently grow past what was asked | `tools/ppt_creator.py`, `tools/ppt_agent.py` | **Major** | `_normalize_slides()` splits any slide with more than 4 bullets across multiple slides for readability — but it ran unconditionally, even when the sender explicitly asked for an exact `slide_count`. The LLM already honored the count in the JSON it returned; a "1 slide" request with enough information to need 5-6 bullets on that one slide (more information, not an extra slide) was silently split into 2, changing the total without telling anyone. Fix: `generate_presentation()` now passes `slide_count` through the deck JSON, and `_normalize_slides()` skips the bullet-count split whenever it's set, trusting the LLM's own slide boundaries. Verified: a 1-slide request with 6 bullets now renders as exactly 1 content slide (4 total incl. cover/divider/closing) instead of 2 (5 total). |
| BM25 gave an off-topic query a false-positive match in a small KB | `rag/search.py` | Major | Found while testing the new hybrid retrieval (below): a common stopword ("are", from "Refunds **are** processed...") that happened to appear in only one document of a small test KB got an inflated IDF score from BM25's math (rare-in-corpus reads as "important," even though it's semantically empty) — an off-topic greeting containing "are" got a nonzero match purely from that one word. Larger corpora self-correct (a stopword appears almost everywhere, driving its IDF toward zero); a small KB can't rely on that. Fix: stopwords (English + Arabic) are filtered out before BM25 tokenizing. Verified: the same off-topic query that previously false-matched now correctly returns nothing. |

## 13. Shared Mailbox & Agent Routing (LLM)

Sally supports running in a **shared mailbox** monitored by multiple AI agents (e.g. Sally and Claude). 

Instead of hardcoded regex matches, an **LLM-based router** (`agents/email_router.py`) intercepts each email inside the `run_agent.py` polling loop (after the allowlist, before the CC guardrail). 
It asks an LLM: *"Which assistant (if any) is the sender talking to?"*

**Logic Flow:**
1. **Our Agent** (`agent_name` in config) → email proceeds through the pipeline normally.
2. **Another Agent** (listed in `assistant_agents` in config — legacy `known_agents` still accepted) → email is filed under that agent's name and skipped by Sally. The message is marked read and **moved into an Outlook mail folder** of the same name (e.g. `claude`), created on first use.
3. **No name mentioned** → email is skipped silently.

The routing model can be changed live via the `routing_model` setting in `agent_config.yaml` (defaults to `groq`). The prompt dynamically builds a name→role mapping so the LLM explicitly understands the difference between the primary agent (Sally) and other agents, preventing ambiguity.

### Thread-aware conversations (observe → join → stick → step back)

Name-routing applies to **new threads only**. Within a conversation the agent behaves like a human assistant on the thread:

1. **Observe** — every thread message the agent receives but doesn't act on (humans talking to each other; the router's "none" branch; the CC guardrail's "not addressed" branch) is **stored into the thread's session history** (`_observe_message` in `run_agent.py`). Nothing is replied; context is kept.
2. **Join when named** — a new thread engages the agent via the LLM router (name required). The **quoted thread** in the email (`body_quoted`, split out by `_split_quoted`) is passed to the WRITE phase as fenced untrusted background, so the agent also knows discussion that happened before it was added.
3. **Stick** — once the agent has replied in a thread (`thread_has_sent_reply(thread_id)`), follow-ups need no name. Each message passes `is_addressed_in_thread()` (LLM): a task/question/correction aimed at the assistant → engage; the check receives a reply-structure hint (`replying_to_agent` — did the sender hit reply on the agent's own email, detected from the quoted block's author).
4. **Step back** — human-to-human messages inside a sticky thread fail the addressed-check and are observed silently. The bias on ambiguity is silence (same as the CC guardrail): a wrongly-silent agent costs one explicit re-ask; a wrongly-loud one spams a human conversation.
5. **Hand off mid-thread** — if a not-addressed message NAMES another known agent (free word-boundary scan first, then the LLM router confirms it is being addressed, not just mentioned — "Claude, take this one" vs "Claude's draft was good"), the email is filed under that agent's label/folder and **NOT stored in our context**: a handoff is fully the other agent's. Filing failures retry next poll, same as new-thread routing.

The WRITE phase now actually consumes conversation history (last 6 turns — previously loaded but unused) plus the quoted-thread block, so "summarize what we discussed" works from the agent's own observations even when the agent stayed silent throughout.

**Failure safety:** label/folder lookups are cached in memory per session. If the label/folder cannot be created or the move fails (API error), both clients **raise** instead of swallowing the error, and the polling loop stops the watermark (`batch_ok=False`) so the email is retried on the next poll — a routed email is never silently lost. The same applies on Outlook, which previously had no-op stubs that dropped routed mail.

## 14. Use Cases

| Use Case | Status | Flow |
|---|---|---|
| RFP response | ✅ | Read RFP → search EAI capabilities → Word/PDF |
| Research report | ✅ | Search genuine data → PDF/Word/Excel/email |
| Excel/CSV data analysis | ✅ | `_analyze_table()` stats → report in any format |
| PPT from scratch | ✅ | Search → coded deck |
| PPT transformation | ✅ | Read old deck → restructure → new deck |
| File conversion | ✅ | `converter_tool` (PPT/Word/Excel↔PDF/Excel) |
| Q&A document fill | ✅ | `read_qa_structure` → hybrid batch answer → `write_qa_responses` |
| General Q&A / email reply | ✅ | GATHER + WRITE → email body |
| Draft an email/letter | ✅ | Returns the actual draft (`[Your name]` placeholder), not a strategy |
| Multi-document (SOW + RFP) | ✅ | All attachments read into one context |
| Schedule a meeting | ✅ | LLM extracts details → calendar event + invite (§14) |
| Deliver file to a folder | ✅ | "save to folder / don't email" → write to `DELIVERY_DIR`, confirm path (§15) |
| Read input from a folder | ✅ | "read `<file>` from the folder" → load from `INTAKE_DIR` (§15) |
| Risk & MSA analysis | 🔄 Phase 2 | add contract_tool |
| Bid pricing intelligence | 🔄 Phase 2 | add pricing_tool |

---

## 15. Meeting Scheduling (`tools/meeting_scheduler.py`)

**Calendar action is LLM-classified.** Whether an email is a meeting **schedule / cancel / update** (or `none`) is decided by the per-email `classify_request_intent` LLM call (a `calendar_action` field), NOT by keyword regex — so natural phrasings ("put together a call", "bump the standup to 5", "call it off", "draft an email proposing a meeting" → correctly `none`) route correctly without pattern whack-a-mole. The regex helpers (`is_cancel_request`/`is_update_request`/`_detect_output_type`) remain only as a **fallback** if that LLM call fails. The run-loop routes on `calendar_action`. **Scheduling books directly** (no confirmation step — the misread-intent risk is handled by the LLM classification + quoted-thread stripping, and the domain guardrail keeps invitees internal); **cancel and update are still confirmation-gated** (higher consequence: they act on existing meetings).

When the action is a meeting schedule, the agent short-circuits the 3-phase pipeline:
1. **Extract** — an LLM call (given today's date + `MEETING_TIMEZONE`) returns JSON: `subject`, `attendees[]` (emails that appear literally in the mail — never invented), `start_datetime`, `duration_minutes`, `description`, `add_video`, and `missing[]`. The extractor **never guesses a clock time**: if the email states a date but no time of day, `start_datetime` is left blank and `"time"` is listed in `missing` (distinct from `"date"`) — the old prompt instruction to default to `10:00:00` is gone, since that silently booked the wrong time whenever the sender only said "tomorrow" or "Monday".
2. **Validate** — if an attendee email or the date/time is missing, Sally replies asking **precisely** for what's absent ("I just need the time" vs "the date" vs "the date and time"), and stashes the partial request (`set_pending_action(thread_id, "schedule", {needs_info, original_body, sender})`) — no event created yet. The user's next reply (e.g. "3pm") is recognised as supplying the missing detail (not a yes/no confirmation) and re-runs extraction on the original request **plus** the reply, completing the booking directly rather than being treated as an unrelated new request.
3. **Schedule** — `schedule_meeting()` creates a real event and sends invites on whichever provider is active (§21):
   - **Outlook** → O365 `Account.schedule()` event with attendees; needs `Calendars.ReadWrite` granted.
   - **Gmail** → Google Calendar event via the same OAuth token (`calendar.events` scope, requested alongside the Gmail scopes at connect time).
4. **Confirm** — replies with the scheduled time and join link.

The event is organised by **the agent's own mailbox**, so the requester is added as an attendee whenever they refer to themselves ("me"/"I"/"us") — otherwise they'd be left out of their own meeting. First-person references resolve to the sender's address (prompt + deterministic backstop); pure third-party scheduling ("a meeting between A and B") excludes the sender.

**Domain guardrail (internal-only).** The agent only emails/invites people inside `meeting_attendee_domains` (`agent_config.yaml`; ships as `eaisystems.com`; empty = no restriction). Two enforcement points share the same domain list (`filter_attendees_by_domain` / `get_meeting_allowed_domains()`):
- **Meeting attendees** — never invites an outside address to a schedule/update; external addresses are dropped and reported ("I left out x@gmail.com…"); if *all* are external it refuses and asks for an internal one. Enforced in the agent flow **and** in `schedule_meeting`/`update_meeting` (hard backstop).
- **Reply CC** — on a reply-all, any external address on the original To/Cc is **dropped from the reply's CC** (internal recipients kept), so the agent never emails outside the org. The sender themselves is still replied to (gated separately by the sender allowlist).

**Updating a meeting.** "reschedule the sync to 5pm", "rename it to …", "add sid@x to the call", "remove John from the standup", "make it 45 min" → `update_meeting()` changes the existing event in place (whole series for recurring): title, start/duration, add/remove attendees, agenda, and online-meeting toggle. Confirmation-gated like schedule/cancel; verified live on Outlook (rename + move + duration + attendee in one update). Single-occurrence edits of a series are not yet supported (whole-series only).

**Recurring meetings.** "every Monday at 4pm", "daily for 5 days", "every 2 weeks on Mon & Wed" → the extractor returns a `recurrence` object (`freq` daily/weekly/monthly, `interval`, `days`, `count`, `until`) which `schedule_meeting` translates per provider: Google gets an iCalendar `RRULE`, Outlook gets the O365 `Recurrence` (`set_daily`/`set_weekly`/`set_monthly` + range). The confirmation message states the cadence ("repeating **weekly on Monday**"). **Cancelling a recurring meeting removes the whole series** — `find_meetings` returns the **series master** id for occurrences (Outlook `seriesMasterId` / Google `recurringEventId`) and dedupes, so a weekly meeting shows as one candidate and cancelling it deletes every occurrence (labelled "recurring series" in the prompt). Verified live on Outlook (create → expands to N occurrences → cancel series → all gone). Cancelling a *single* occurrence of a series is not yet supported (whole-series only).

**Confirmation gating.** **Scheduling is one-step** — the agent creates the event and sends invites immediately (no "reply yes" step). The original misread-intent risk is covered upstream by the LLM `calendar_action` classification and quoted-thread stripping, and the domain guardrail keeps invitees internal, so the extra confirmation was redundant. **Cancel and update remain confirmation-gated** (they act on existing meetings, higher consequence): they stash the action via `set_pending_action(thread_id, "cancel"|"update", …)` and only execute on the follow-up "yes"/number. The pending store is shared (`get_pending_action` → `{kind, payload}`), pending actions expire after 30 min, and a non-confirmation reply drops the stale pending. (`_run_schedule_confirm_flow` is retained to complete any schedule confirmation that was in flight from an older build.)

Meetings run on a **single provider** (the graph forces `providers=[providers[0]]`) so fanout can't create duplicate invites.

### Cancelling a meeting (search → confirm → cancel)

Cancellation can target **any meeting in the mailbox**, so it is deliberately a **two-step, confirmation-gated** action — Sally never cancels in one shot:

1. **Detect** — `is_cancel_request()` (regex) catches "cancel/call off/scrap the meeting/call/sync…" without colliding with scheduling. Runs *before* the scheduling branch and regardless of `output_type`.
2. **Search** — an LLM extracts a subject keyword + date window; `find_meetings()` queries the calendar (Outlook `get_events` with a `start` range / Google `events().list`) and returns candidates (`event_id`, subject, start, attendees, `is_organizer`).
3. **Confirm** — the candidates are stashed via `set_pending_cancel(thread_id, …)` (config table, 30-min TTL) and Sally **replies asking the user to confirm** — "reply **yes**" for one match, or "reply with the **number**" for several. Nothing is cancelled yet.
4. **Execute** — the follow-up in the same thread routes (regardless of keywords) to `_run_cancel_confirm_flow()`, which interprets yes/no/number/none, then `cancel_meeting(event_id)`: `cancel_event()` if the mailbox **organises** it (attendees notified) or `decline_event()` if it's only an **attendee**. The pending entry is cleared.

Safety: a non-confirmation message (longer than ~8 words and no yes/no/number) **drops the stale pending** instead of hijacking the new request; pending entries expire after 30 minutes if never confirmed.

---

## 16. Folder Delivery & Intake

Preconfigured, never taken from the email body (the email only chooses *"the configured folder,"* never an arbitrary path).

- **`DELIVERY_DIR`** (default `OUTPUT_DIR`) — when the LLM intent says `deliver_to_folder`, the generated file(s) are copied here and the reply **confirms the path instead of attaching** (no email file). Same-file guard avoids re-copying a file already in the folder. With no explicit format, defaults to PDF.
- **`INTAKE_DIR`** (default `OUTPUT_DIR/intake`) — when intent says `read_from_folder`, the named file (or the newest, for "latest") is read **sandboxed to this folder** (basename only, no `..` traversal) and fed into the pipeline like an attachment.
- Both folders are created at startup and logged. Point them at a network/shared path via `.env`. (Keep `phantomops.db` on **local disk** — SQLite over a network share can corrupt; `DELIVERY_DIR` may be a share, the DB folder should not.)
- Decision is **LLM-driven** (`classify_request_intent`), not keyword matching — see §2.

---

## 17. Configuration (`agent_config.yaml`)

V4 is configured by editing `agent_config.yaml` in the delivery folder — directly, or via
the **Settings Control Center** (`settings.bat`, §21), which writes to this same file.
Changes are re-read and applied on the **next email** — no restart — with ONE exception
(`email_provider`, below). Precedence: `agent_config.yaml` → `.env` → built-in default.

- **`agent_name`** — used in signatures, greetings, and the CC "was I addressed?" check.
- **`company`** — `name`/`short`/`email`/`web`, the company identity shown in email signatures, generated documents/PPTs (cover, closing, footer, and the logo badge's two-tone text), and the LLM's own persona in its write prompts. Optional — each field independently falls back to the built-in default if unset, so an existing deployment that never sets this block is unaffected. Read via `core/config.py`'s `get_company_info()` (same per-field fallback pattern as `get_agent_name()`); `get_email_signature()` and `brand_text()` apply it to the (previously hardcoded) signature blocks and static LLM prompt constants respectively — see the Bug Fixes Log era note in §21.6.
- **`email_provider`** — `outlook` or `gmail`. **Does NOT hot-reload** — the mailbox connection authenticates once when the agent starts, so a change here only takes effect after restarting. See §21.
- **`agent_model`** — pin one provider (`groq` / `gemini` / `claude` / `openai` / `local`) or `""` for **Auto** multi-model fanout. The pin restricts the graph's fanout set. `local` (Ollama) is never part of Auto — opt-in only, must be pinned explicitly.
- **`provider_models`** — the exact model variant used when each provider runs, including `local` (text model tag) and `local_vision` (a *separate* multimodal tag — the text model isn't multimodal).
- **`vision_provider`** — for image/logo auto-tagging: `groq` / `gemini` / `claude` / `openai` / `local`. Same opt-in-only rule as `agent_model` for `local`.
- **`sender_allowlist`** — full emails and/or whole domains (prefix `@`); **overrides** `SENDER_ALLOWLIST` in `.env`. **Empty = fail CLOSED (reply to nobody).** To reply to every sender, leave it empty and set **`allow_all_senders: true`** (or `ALLOW_ALL_SENDERS=true` in `.env`) — an explicit, logged choice, never the silent default.
- **`routing_model`** — provider used to decide which agent an email is addressed to, including `local` (reuses `provider_models.local` — no separate model name to set).
- **`assistant_agents`** — other AI assistants sharing the mailbox (their mail is filed to an Outlook folder and skipped).
- **`meeting_attendee_domains`** — the internal-only guardrail (defaults to `eaisystems.com`; never turns off).

**Writes preserve the file's comments.** `set_agent_config_value()` (used by the Settings
UI) round-trips the file through `ruamel.yaml` instead of plain `PyYAML` — a naive
`safe_load`/`dump` would silently delete every hand-written comment in this file on the
first UI save.

---

## 18. Deployment — Native Windows (no Docker)

V4 ships as a plain Python install driven by two batch scripts. No Docker, no image,
no compose — the agent runs directly with `python run_agent.py`.

- **`install.bat`** (one-time) — locates Python 3.11 (installs it via `winget` if missing), creates a **shared `.venv`** (placed in the parent folder so several versions beside it reuse one environment; override via `PHANTOMOPS_VENV`), `pip install -r requirements.txt`, and optionally installs **LibreOffice** (PDF output) and **Tesseract OCR** via `winget`. Needs internet at install time. Next-steps output covers both mailbox providers.
- **`start.bat`** — queries the active `email_provider` (via `print_email_provider.py`, a tiny standalone script — `cmd.exe`'s `for /f` backtick parser is unreliable with an inline `python -c "...;...(...)"` one-liner containing multiple embedded quotes, so the query lives in its own file instead), then on first run opens the matching sign-in (`Outlook-Connector/connect_outlook.py` or `Gmail-Connector/connect_gmail.py`, both force an account picker) to create the mailbox token; then runs `python run_agent.py`. `ASSETS_DIR`/`OUTPUT_DIR` default to folders inside the delivery; `phantomops.db` lives in `output/` and console logs are mirrored to `output/logs/agent-<date>.log`.
- **`settings.bat`** — launches the Settings Control Center (§21) at `http://localhost:8502`, auto-opening the browser. Independent of `start.bat`; safe to run at the same time.
- **Provider — user-selectable, one at a time.** `EMAIL_PROVIDER` (`.env`) / `email_provider` (`agent_config.yaml`, takes precedence) picks `outlook` or `gmail`. The `OUTLOOK_*` or `GMAIL_CLIENT_ID`/`_SECRET` values come from `.env`; `outlook_token.txt` / `gmail_token.json` grant mailbox access and are generated on the host via the matching connector. See §21.
- **Provider abstraction (internal).** `email_client.py` routes to a backend at import time and the same interface (`list_messages_since`, `get_message_detail`, `download_attachments`, `send_reply`, `mark_as_read`, `get_account_address`, `get_or_create_label` / `apply_label_and_archive`) is implemented for both Outlook and Gmail.
- **PDF without Docker.** The binary on Windows is `soffice.exe`, not the Linux `libreoffice`. `tools/converter_tool.find_soffice()` resolves it on PATH or in the default Windows install locations (used by `converter_tool`, `doc_creator`, `qa_filler`), and `find_tesseract()` does the same for OCR — so PDF conversion and scanned-PDF reading work natively.

> **Confidential:** the delivery folder / zip contains real API keys (`.env`) and
> `outlook_token.txt` / `gmail_token.json` (live mailbox access). Treat it as a secret.

---

## 19. CC Guardrail (reply only when addressed) + Reply-All CC

So that CC'ing Sally into a thread doesn't turn her into a reply-all spammer, the polling loop (`run_agent.py`) decides **whether to respond at all** before running the pipeline:

1. **Resolve her own address once at startup** — `get_account_address()` (O365 `get_current_user_data()`). Logged as `CC guardrail: on/off`. If it can't be resolved, the guardrail disables itself (no behaviour change).
2. **Role check per email** — `get_message_detail()` now returns `to` + `cc` (lowercased email lists). Sally is **cc-only** if her address is in `cc` and **not** in `to`.
3. **The gate:**
   - In **To:** (or neither) → respond as normal (no LLM call).
   - **Cc-only** → `should_respond_when_cc()` runs an LLM check (groq, robust to phrasing) — *"is the latest message asking the assistant to do/answer something?"* If **no** → mark read, advance the watermark, skip (logs `[CC] Observing only`). If **yes** → proceed (logs `[CC] CC'd but addressed`).

**addressed = false** for: human-to-human chatter, requests aimed at a human, or naming Sally without a task (greetings, "Thanks Sally!", acknowledgements, "Sally" as a project name, "no action needed" / "you don't need to reply"). **addressed = true** only for a real request/question to the assistant.

**Fail-safes:** unknown address → guardrail off; LLM error → name-mention fallback (`"sally"`/`@sally` in body). Verified across 28 cases (14 core + 4 role + 10 edge), 10/10 after tightening for greeting/thank-you/no-action false-positives.

### Reply-All CC (keep the thread intact)

When Sally replies to **any** email, the original `To:` and `Cc:` recipients are **preserved on her reply** so nobody gets cut out of the conversation thread. This is critical when Sally is CC'd: if Siddhant emails Siddhi and CC's Sally, Sally's reply should go back to both — not silently cut Siddhi out.

**Implementation** (`auto_send_node` in `graph.py`):
1. `original_to` and `original_cc` are passed from `get_message_detail()` through `run_agent.py` → `EmailSkillState`.
2. `reply_cc = (original_to + original_cc) - sender - Sally` (deduplicated, lowercased comparison).
3. `send_reply(msg, body, files, cc=reply_cc)` — Outlook `send_reply` accepts the `cc` kwarg (replies to the sender only via `to_all=False`, then adds the domain-filtered CC).
4. If a direct email has no other recipients, `reply_cc` is empty and `cc=None` is passed (no change in behaviour).

Logged as `[SEND] Reply-all CC: addr1, addr2` when CC recipients are added.

### Sender-Greeting Prompt Fix

The email write prompt explicitly instructs the LLM to greet the **sender** (the person in `EMAIL FROM:`), not other people on the thread. This prevents a bug where Sally says "Hi Siddhi" (the To-recipient) when the actual requester is Siddhant (the sender). The write context includes a `REPLY TO (greet this person): {sender}` line to reinforce sender identity.

---

## 20. RAG Knowledge Base, Local Models & Arabic Support

Three related, **fully optional** subsystems added on top of the base agent. All optional dependencies (`chromadb`, `langchain-ollama`, `trafilatura`) are **lazy-imported** — a deployment that never sets any of this up behaves exactly as before, with an honest log line if a RAG feature is invoked without the runtime present.

### 20.1 Local LLM option (Ollama)

`build_agent_llm()` (`agents/__init__.py`) gained a `"local"` branch using `ChatOllama` against `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`). Set `agent_model: local` in `agent_config.yaml` (and `provider_models.local` to a pulled model tag) to route replies through a local model instead of a cloud provider — same pin-a-provider mechanism as any other, opt-in, no API key. Cloud providers remain the default; `local` is **not** part of `""`-Auto fanout (it depends on a running Ollama server).

### 20.2 RAG knowledge base (`rag/` package)

A single **global**, persistent vector store — one shared reference library the agent can draw on across all threads and senders (like a company glossary/policy store), not per-conversation memory.

Split into one module per pipeline stage, each importable on its own:

| Module | Responsibility |
|---|---|
| `rag/chunking.py` | `chunk_text` / `chunk_text_with_metadata` — structure-aware splitter that keeps likely headings, paragraphs, lists, and table rows together, detects PDF `--- Page N ---` and PPT `Slide N:` markers as location metadata, and uses bounded overlap fallback for oversized units. Pure text transform, no optional deps. |
| `rag/embedding.py` | `get_embedder()` (Ollama) + `available()` — the optional-dependency gate (`chromadb` + `langchain-ollama`) every other module checks first. |
| `rag/store.py` | ChromaDB collection lifecycle (`_get_collection`), `content_hash`, metadata cleanup/defaulting, `ingest`/`ingest_if_new` (content-hash dedup against the SQLite ledger, salted by `CHUNKING_STRATEGY_VERSION` so chunking/metadata upgrades trigger one clean rebuild; stable per-source IDs with delete-before-upsert so an edited source updates in place); `get_all_chunks` — a thin read accessor exposing the full corpus (documents + metadata) `rag/search.py`'s BM25 branch builds its lexical index from. No new storage: BM25 reads exactly what `ingest()` already persisted in Chroma. |
| `rag/ingestion.py` | `detect_language`, `fetch_url_text` (web link → clean text via `trafilatura`), `scan_intake_folder`, `_extract_text_for_ingest` — where KB text comes from. |
| `rag/search.py` | `retrieve` — **hybrid** dense (vector) + BM25 (lexical) search fused by Reciprocal Rank Fusion, score-gated — + `format_context` (renders hits for the WRITE prompt). See below. |

`rag/__init__.py` re-exports the public functions above for convenience, but call sites (`agents/assistant_agent.py`, `run_agent.py`) import from the specific submodule they use (e.g. `from rag.search import retrieve`) so it's clear which pipeline stage is in play.

- **Store:** ChromaDB, local persistent, collection `phantomops_kb` (deliberately **not** `rag_docs` — see the Bug Fixes Log), at `CHROMA_PERSIST_DIR` (defaults to `DATABASE_DIR/chroma_db`).
- **Embeddings:** local via Ollama, `EMBEDDING_MODEL` (`bge-m3`, multilingual) — so KB content never leaves the machine, and English/Arabic content share one store with **cross-language retrieval** (an English question can surface an Arabic chunk and vice-versa).
- **Chunking strategy:** `CHUNKING_STRATEGY_VERSION = "structure-aware-v2"`. The public `chunk_text()` still returns plain chunk strings for compatibility; `chunk_text_with_metadata()` is used by `rag.store.ingest()` to attach per-chunk metadata. Chunks preserve likely section headings as `Section: ...`, keep paragraphs/lists/tables together when possible, and split only oversized units with `CHUNK_OVERLAP` fallback. Bumping the strategy version intentionally changes `content_hash`, so already-saved unchanged sources re-ingest once and pick up improved chunk boundaries/metadata.
- **Per-chunk metadata:** `source_type`, `source_ref`, `title`, `section_heading`, `file_name`, `page_number`/`page_end` when extractable, `source_date` when known, `language`, and `ingested_at` — carried in Chroma itself so `search.retrieve()` surfaces them (and `format_context` tags each chunk with citation/freshness details) without a second DB lookup.
- **Audit/dedup ledger:** a `rag_sources` SQLite table (`source_type`, `source_ref`, `content_hash`, `chunk_count`, `language`, `ingested_at`, in `core/database/sqlite_db.py` alongside every other table — see §7) records every ingested source; `has_rag_source`/`record_rag_source`, called from `rag/store.py`.
- **Hybrid retrieval (dense + BM25, fused by RRF):** dense embeddings capture semantic similarity well but can under-rank exact term/keyword matches — a real gap once the model started issuing its own short, precise queries via the `search_knowledge_base` tool (below) rather than one long natural-language sentence. `rag/search.py`'s `retrieve()` now also runs a BM25 lexical search (`rank_bm25`, optional/lazy-imported — falls through to exactly the original dense-only behavior, same score scale, if it isn't installed) over the same corpus (`rag/store.py`'s `get_all_chunks()`), and fuses the two ranked lists via **Reciprocal Rank Fusion** rather than blending raw scores (cosine similarity and BM25 scores are on incomparable scales; RRF fuses by rank position instead, no per-query normalization needed). The relevance floor still applies per-branch before fusion — dense keeps the `RAG_MIN_SCORE` cosine floor, BM25's floor is "score > 0" (genuine term overlap) — so a chunk can surface via a real keyword match even if it didn't clear the dense floor on its own ("BM25 rescuing" a precise short query). Tokenization is a lightweight regex word-split with a small hardcoded EN+AR stopword list (needed after testing surfaced a real false-positive: a common word appearing in only one document of a small KB got an inflated BM25 score purely from low corpus-wide frequency — see the Bug Fixes Log).

**Ingestion — three paths:**
1. **Intake folder auto-ingest.** Each poll cycle (throttled by `RAG_INTAKE_SCAN_INTERVAL_SEC`, default 120s) `run_agent.py` calls `rag.ingestion.scan_intake_folder()`, which scans `INTAKE_DIR`; any new/changed file (dedup by content hash) is parsed via `tools/file_reader.py` (pdf/pptx/docx/xlsx/csv + plain `.txt`) and ingested. Intake chunks carry `file_name`, title from the file stem, detected `language`, and file modified date as `source_date`. Runs *alongside* the existing on-demand "read this file" behaviour.
2. **Email "save this to RAG" command.** A `save_to_rag` classifier flag (fail-closed — a deliberate command, never inferred) short-circuits before GATHER/WRITE, same shape as the calendar flows. `_run_rag_ingest_flow` saves, in order: an **attachment** (plus the email body when it carries a real note, each under its own `source_ref`; attachment chunks carry filename/title/source date when available); a **web link** in the body (fetched fresh via `rag.ingestion.fetch_url_text`, plus any body note); the agent's **most recent reply in the thread** when the message refers back to it ("save that research to RAG" → saves the findings, greeting/signature stripped); otherwise the body text.
3. **Web link.** As above — a link in a "save to RAG" body is fetched via `rag.ingestion.fetch_url_text` and ingested.

**Retrieval at reply time — two layers, the classifier still decides scope:**

1. **Static pre-fetch (`uses_rag` + relevance floor), unchanged.** The classifier's `uses_rag` flag (fail-open, defaults true) decides whether to look; before WRITE, `rag.search.retrieve(body)` runs a single, non-agentic lookup (no LLM call) on the raw request text and returns hit text plus metadata (`title`, `section_heading`, `file_name`, `page_number`, `source_date`, `language`, `ingested_at`, score). `format_context()` renders citation-like labels such as `[Source: Policy, file policy.pdf, section Policy > Limits, page 2, source date 2026-08-01, language en, saved 2026-08-07]`, then the hits are merged into the WRITE prompt as an untrusted `KNOWLEDGE BASE CONTENT` block. Crucially, `retrieve()` **drops any chunk scoring below `RAG_MIN_SCORE`** (default 0.5) — Chroma always returns *something*, so without this an off-topic message ("hello sally") would still pull the nearest (irrelevant) chunk and the model would ramble about it. This is the zero-extra-LLM-call baseline that guarantees grounding for the common single-topic case regardless of what happens next.

2. **Agentic lookup — the `search_knowledge_base` tool (`tools/rag_search.py`), additive on top.** A single one-shot lookup on the whole request can't handle a task genuinely needing several *distinct* KB lookups (e.g. "find our policy on X, then check it against policy Y") — one embedding of the whole request may not surface both. `search_knowledge_base` is a thin `@tool` wrapper around `retrieve()`/`format_context()`, added to GATHER's tool list (`agents/assistant_agent.py`) alongside `serpapi_search`/`tavily_search` whenever `uses_rag` is true — same pattern, so the model can call it repeatedly with different queries in one reasoning loop, mixing KB lookups and web searches freely. **The classifier still gates scope, not the model**: the tool is only ever offered when GATHER runs at all, and GATHER's trigger stays `needs_web_search` (deliberately *not* broadened to include `uses_rag`, which is fail-open/true for almost every request — doing so would have undone the "skip GATHER when unnecessary" cost optimization already described in §0). So a pure KB-only request with no web-search need still gets only the static pre-fetch, exactly as before; the agentic capability activates for the subset of requests that were already going to run GATHER for web-search reasons. Live-verified: a "compare policy A and policy B, and tell me the current year" request made the model call `search_knowledge_base` twice (once per distinct policy) plus one web search, in a single GATHER iteration, producing one correctly-cited answer.

### 20.3 Arabic (and mixed AR/EN) support

- **OCR:** `_ocr_page`/`_ocr_image_bytes` (`tools/file_reader.py`) run Tesseract with `lang=` from `_ocr_lang()`, which returns `eng+ara` **only if the Arabic traineddata is actually installed** (else `eng` — an older install with no Arabic pack keeps working, never errors on a missing language). Plain-text Arabic (email body, native-text PDF/DOCX/XLSX) already round-trips as Unicode with no change; OCR was the only lossy path.
- **`detected_language`** (classifier): the language of the *request itself* (`en`/`ar`/`mixed`) — drives the reply language, so Arabic in → Arabic out. LLM-primary with a script-count fallback if the classifier call fails. The Arabic signature is restated alongside the language switch so it doesn't degrade to a `[اسمك]` placeholder.
- **`content_language`** (classifier): an *explicit* request for the deliverable's language ("write this essay **in Arabic**", "convert the attached file **to English**), separate from and higher-priority than `detected_language` — a request written in English can still ask for Arabic content. Honoured in email replies, generated documents (via the WRITE prompt), and PPTs (threaded into `tools/ppt_agent.py`'s own generation prompt). Also drives **document translation**: "convert the attached Word file to Arabic" is routed as a content transformation (not a mechanical format convert), returning the translated document in the source file's own format.
- **Cross-language KB:** because embeddings are `bge-m3`, a saved Arabic document is retrievable from an English question and vice-versa — no separate handling.

### 20.4 Shared data location (`DATABASE_DIR` / `OUTPUT_DIR` / `ASSETS_DIR`)

Both the SQLite DB and the Chroma store live under one `DATABASE_DIR`; `OUTPUT_DIR` holds deliverables/attachments/logs. When not set in `.env`, each is **auto-discovered in the immediate parent folder** (`_find_shared_dir`) — the same convention as the shared `.venv` in `install.bat`: sibling `version_N` delivery folders reuse one `database`/`output`, so a new release connects to the same accumulated history and knowledge base instead of starting empty. The search is deliberately **one level only** (a generic name like `output` could otherwise collide with an unrelated folder further up the drive). An explicit `.env` value always overrides auto-discovery; a genuinely first-time machine creates its own empty folder.

`ASSETS_DIR` (templates/letterhead/logo/images, `core/config.py`) follows the same *pattern* but is **not** auto-discovered the way `DATABASE_DIR`/`OUTPUT_DIR` are — it only takes effect when explicitly set in `.env` (defaults to `<delivery>/assets` otherwise). Pointing multiple deployments' `ASSETS_DIR` at the same physical folder shares one asset library (and its `index.json` tag/theme cache) across them — an image or template added once becomes usable everywhere, no manual copying between deployments needed. Since `index.json` lives inside `ASSETS_DIR` itself, sharing the folder shares the cache too, not just the raw files.

### 20.5 Installer additions (`install.bat`)

Beyond the base steps, `install.bat` now: pre-creates the shared `database` folder (+ `chroma_db` inside it) via the same parent-discovery logic; installs **Ollama** via winget with real error reporting (admin-rights/winget-prompt guidance on failure) and pulls **`bge-m3`** with a 3-attempt retry (a freshly-installed Ollama service takes a moment to start); and downloads the **Arabic Tesseract traineddata** (`ara.traineddata`) into the tessdata folder (best-effort — OCR stays English-only if it can't). A chat model for `agent_model: local` is **not** auto-pulled (the operator picks one). All of this is optional/best-effort — the base agent runs without any of it.

---

## 21. Mailbox Connectors (Outlook & Gmail) & the Settings Control Center

### 21.1 Email provider selection

`email_provider` (`agent_config.yaml`, also settable via the Settings Control Center)
picks `outlook` or `gmail` — **one mailbox at a time**, not simultaneous polling. Unlike
every other setting in this file, it does **not** hot-reload: `get_email_provider()`
(`core/config.py`) is read once, at process start, by both `email_client.py`'s
provider-dispatch (module import time) and `run_agent.py`'s startup credential check —
switching providers needs a restart, and the UI/warning text says so explicitly.

### 21.2 Gmail credentials — plain values, not a `credentials.json` file

Gmail's setup mirrors Outlook's exactly: `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` are
plain values pasted into `.env`, same as `OUTLOOK_CLIENT_ID`/`_SECRET`/`_TENANT_ID` — not
a JSON file downloaded from Google Cloud Console and placed in the project root (the
original design; replaced). Google's OAuth library supports building the sign-in flow
directly from a `client_config` dict (`InstalledAppFlow.from_client_config(...)`) exactly
as well as from a file — this isn't a workaround, it's the same underlying flow minus the
file-download step Google Cloud Console offers but doesn't require.

**One-time setup, not per-mailbox.** Exactly like Outlook's Azure app, ONE Google Cloud
OAuth client is registered once, ever, for the whole product — not once per tester. Once
`GMAIL_CLIENT_ID`/`_SECRET` are filled into a delivery's `.env`, every individual tester's
experience becomes "run `start.bat`, browser opens, log in with the agent mailbox, done"
— identical to Outlook's, and identical to how "Connect Gmail" works in any product that
already did this registration step (e.g. Claude's own Gmail connector: the end user never
sees a client ID because the vendor already created one).

This split is enforced in the docs themselves, not just by convention:
- **`Gmail-Connector/instructions.txt` / `MANAGER_GUIDE.txt`** (tester-facing) assume the
  two values are already filled in — zero mention of Google Cloud Console, mirroring how
  Outlook's guide never mentions Azure app registration at all.
- **`Gmail-Connector/ONE_TIME_ADMIN_SETUP.txt`** (new, admin-only) has the actual Google
  Cloud Console steps (enable Gmail API + Calendar API, configure the OAuth consent
  screen, create a Desktop-app OAuth client), explicitly framed as a do-once-ever product
  task. Also covers the one genuinely *recurring* admin task: each new agent mailbox must
  be added under "Test users" on the consent screen before it can sign in, or Google
  blocks it with "Access blocked: this app's request is invalid".
- `connect_gmail.py`'s missing-credentials error is short and points a tester at "ask
  whoever provided this package" and an admin at `ONE_TIME_ADMIN_SETUP.txt`, instead of
  dumping the full Google Cloud walkthrough at everyone every time.

### 21.3 Gmail-Connector (mirrors Outlook-Connector)

A `Gmail-Connector/` folder (v4-only, alongside the existing `Outlook-Connector/`) with
the same shape: `connect_gmail.py`, `run.bat`, `signout.bat`, `instructions.txt`,
`MANAGER_GUIDE.txt`. `connect_gmail.py` reads `GMAIL_CLIENT_ID`/`_SECRET` from `.env`,
opens the Google sign-in page automatically (`InstalledAppFlow.run_local_server(port=0)`
— genuinely one-click, no copy-pasting a URL), and saves `gmail_token.json` next to
`start.bat`. `signout.bat` deletes the token (keeps the Client ID/Secret — no need to
redo the Google Cloud setup) and offers to reconnect immediately.

`start.bat` is provider-aware: it queries the active provider via `print_email_provider.py`
(a standalone script, not an inline `python -c` one-liner — `cmd.exe`'s `for /f` backtick
parser is unreliable with multiple embedded quotes/semicolons in a single quoted argument,
confirmed by direct testing: it silently captured **zero** output from the inline form),
then launches whichever connector matches if no token exists yet.

### 21.4 Settings Control Center — Connect / Disconnect

The Settings UI (`ui/settings_app.py`, launched via `settings.bat`) shows a live status
badge ("✅ Connected" / "⚠️ Not connected") for whichever provider is selected in the
dropdown — not necessarily the currently *active* one, so a mailbox can be connected
ahead of switching to it — plus:

- **Connect button** — runs the same OAuth flow as the standalone connector scripts, but
  in-process: `skills/email_skill/email_connect.py` extracts the core logic
  (`connect_outlook`/`connect_gmail`) into functions that return `(success, message)`
  instead of printing to a console and calling `input()`, callable directly from a
  Streamlit button click. Blocking — the browser opens and the button shows a spinner
  until sign-in completes. Disabled once already connected.
- **Disconnect button** — two-step confirm (a real, if reversible, action shouldn't fire
  on one accidental click), then deletes the token file via `disconnect()`. Disabled when
  nothing's connected.
- **What "disconnect" actually does to an already-running agent: nothing, immediately.**
  `run_agent.py` calls `get_email_service()` exactly **once**, before the polling loop
  starts, and reuses that same service object for every operation for the rest of the
  process's life — it never re-reads the token file again. Deleting the file doesn't
  reach into a running process's memory, so a disconnect while the agent is mid-request
  (or at any point while it keeps running) has **zero effect until the next restart** —
  that's when `get_email_service()` runs again, finds no token, and fails to start until
  reconnected. (Both providers' underlying libraries — O365's `Account`, Google's
  `Credentials` — also auto-refresh access tokens transparently in memory without
  re-reading the file, so this holds for the token's full natural lifetime, not just
  until the next expiry.)

### 21.5 Settings Control Center — from dead to functional

Before this, every control in `ui/settings_app.py` wrote to a SQLite `config` table
(`get_config`/`set_config`) that nothing in the pipeline had read since the config
mechanism moved to `agent_config.yaml` — every "saved" setting silently had zero effect
(Bug Fixes Log, §12). `set_agent_config_value()` (`core/config.py`) now writes directly
to `agent_config.yaml`, preserving its comments via `ruamel.yaml`'s round-trip loader
(§17) — every control (agent name, model pin, per-provider model, vision provider,
allowlist, email provider, mailbox connect/disconnect) is wired to it. V4's copy carries
its own "PhantomOps" branding text in the UI chrome (page titles, headers, help copy);
the configurable `agent_name` field itself (and its example placeholder) is left alone in
both copies — it's a legitimate per-deployment choice, not product branding.

### 21.6 Company Branding & Local (Ollama) model selectors

**Company Branding section** (placed directly under Agent Name, same pattern): four
text inputs (name/short/email/web) writing to `agent_config.yaml`'s `company:` block
(§17) via `set_agent_config_value("company.name", ...)` etc. Company identity was
previously hardcoded as literal `"EAI Systems Pvt. Ltd."` / `"www.eaisystems.com"` /
`"info@eaisystems.com"` strings across **10 files** — 9 duplicated email/document
signature blocks (now consolidated into one shared `core/config.py` helper,
`get_email_signature()`), the static LLM prompt constants in `agents/assistant_agent.py`
(`_WRITE_PROMPTS`, `_QA_ANSWER_PROMPT`) and `tools/ppt_agent.py`'s `_PPT_SYSTEM_PROMPT`
(re-branded via `brand_text()` at the point each prompt is fetched for use, not by
rewriting the constants themselves — keeps them hot-reloadable and readable), and
`tools/ppt_creator.py`'s cover/closing/footer text and the logo badge's two-tone text
split. All still default to the original EAI values when the `company:` block is unset,
so an existing deployment is unaffected.

**Local (Ollama) model selectors.** The Agent Model section previously had no card for
`agent_model: local` at all (only the four cloud providers), even though
`agent_config.yaml` and `core/config.py`'s `build_agent_llm()` fully supported it; Vision
Provider listed `"local"` as an option but had no way to pick *which* pulled model to use
for it. Rather than a free-text model-tag input, both now use a **live-queried dropdown**
of models actually pulled on the local Ollama server: `get_ollama_models()`
(`core/config.py`) calls `GET {OLLAMA_BASE_URL}/api/tags`, returns `[]` on any failure
(Ollama not running, network error) — never raises, same optional-dependency philosophy
as the rest of the local-model support. The Local (Ollama) card in Agent Model is wired
into the same single-select pin group as the cloud provider toggles; a matching dropdown
for `provider_models.local_vision` appears under Vision Provider only when `"local"` is
selected there. `routing_model` stays YAML-only for now (no dashboard control) — a
narrower, deliberately deferred scope.
