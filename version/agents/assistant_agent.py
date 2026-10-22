"""
agents/assistant_agent.py — Sally's general-purpose agent (three-phase)

Phase 1 — GATHER  : search the web + read attachments (no file creation)
Phase 2 — WRITE   : dedicated LLM call focused entirely on writing rich content
Phase 3 — RENDER  : pass the written content to the file tool for conversion

The writing step is completely separate from the rendering step.
The LLM has full context and can focus on quality — no tool-call management overhead.
"""

import os
import re
import time
from agents import (
    run_tool_loop, build_agent_llm, extract_text_content,
    log_agent_execution, wrap_untrusted, TOOL_LOOP_INJECTION_GUARD,
    LLMUnavailableError, LLMTransientError, classify_llm_failure,
    invoke_with_tpm_retry, is_transient_provider_error,
)


# ── Output format detection ───────────────────────────────────────────────────

# Determiner + file-type words that mark an INPUT reference to an uploaded file
# ("the attached ppt", "this presentation") vs an OUTPUT request ("make a ppt",
# "in pdf format"). Stripping these stops "analyse the attached excel" from being
# misread as "create an excel".
_INPUT_REF_RE = re.compile(
    r"\b(the|this|that|these|those|attached|uploaded|your|provided)\s+"
    r"(pdf|pptx?|presentation|slides?|deck|docx?|word|document|excel|spreadsheet|xlsx|csv|file|attachment)\b"
)


def _strip_input_refs(text: str) -> str:
    """Remove 'the/this/attached <filetype>' references before output detection."""
    return _INPUT_REF_RE.sub(" ", text)


# Unambiguous INPUT-file mentions only — "attached/uploaded/provided <filetype>"
# can ONLY refer to a file that came WITH the email, never the requested output
# (unlike a bare "the pdf", which the create-verb scan below still needs to see
# so "create the PPT" keeps working). Stripping just these before that scan
# stops a source-file mention from being mistaken for the target format, e.g.
# "give me a summary of the attached pdf in word format" was matching "give ...
# the attached PDF" and returning 'pdf' instead of the actual target, 'word'.
_STRONG_INPUT_REF_RE = re.compile(
    r"\b(attached|uploaded|provided)\s+"
    r"(pdf|pptx?|presentation|slides?|deck|docx?|word|document|excel|spreadsheet|xlsx|csv|file|attachment)\b"
)


def _strip_strong_input_refs(text: str) -> str:
    return _STRONG_INPUT_REF_RE.sub(" ", text)


_CONV_FMTS = r"pdf|word|docx|xlsx|excel|csv|pptx|ppt|powerpoint"


def _detect_conversion_target(body_lower: str, src_ext: str = "") -> str:
    """Pick the TARGET format for a 'convert X to Y' request.

    The destination is the format named AFTER 'to'/'into' — NOT just the first
    format word, which is usually the SOURCE: "convert pdf to excel" must yield
    'excel', not 'pdf' (picking 'pdf' made the converter a no-op since source ==
    target). Falls back to the last format word that isn't the source, then 'pdf'.
    """
    m = re.search(rf"\b(?:in)?to\s+(?:an?\s+|the\s+)?({_CONV_FMTS})\b", body_lower)
    if m:
        return m.group(1)
    # No "to <fmt>" — exclude the source format and take the last remaining one.
    src = (src_ext or "").lower().lstrip(".")
    src_aliases = {src}
    if src == "docx": src_aliases.add("word")
    if src == "xlsx": src_aliases.add("excel")
    if src == "pptx": src_aliases.update({"ppt", "powerpoint"})
    cands = [c for c in re.findall(rf"\b({_CONV_FMTS})\b", body_lower) if c not in src_aliases]
    return cands[-1] if cands else "pdf"


def _detect_output_type(body_lower: str) -> str:
    """
    Detect the OUTPUT format the user wants created/returned.
    Defaults to email reply if no explicit output format is mentioned.

    Key distinction:
      OUTPUT: "create a excel file", "send me a pdf", "in word format"
      INPUT:  "from the pdf", "in the excel", "the attached pdf" → NOT an output request
    """
    # 0a. Meeting scheduling — a distinct action (create a calendar event + invite).
    # First neutralise idioms where the keywords are NOT a scheduling request:
    # "behind/on/ahead of schedule" (noun) and "meeting its objectives" (verb) —
    # e.g. "project is behind schedule, ... not meeting its objectives" must not
    # trigger the meeting flow.
    _mt = re.sub(
        r"\b(behind|on|ahead of)\s+schedule\b|"
        r"\bmeeting\s+(its|our|their|your|his|her|the)\s+\w+",
        " ", body_lower,
    )
    # A determiner before the trigger word makes it a NOUN ("the plan", "a fix",
    # "our schedule") — only verb usage ("plan a call", "fix a meeting") counts.
    if re.search(
        r"(?<!\bthe )(?<!\ba )(?<!\ban )(?<!\bour )(?<!\bmy )(?<!\byour )(?<!\bthis )(?<!\bthat )"
        r"\b(schedule|set ?up|set-up|arrange|organi[sz]e|book|fix|plan)\b.{0,30}"
        r"\b(meeting|call|appointment|sync|catch[- ]?up|discussion|interview|demo)\b",
        _mt,
    ):
        return "meeting"

    # 0b. Q&A fill — high priority (an ATTACHED document/form has blank Q&A
    # fields needing answers). Wording alone ("answer the questions") can't
    # reliably tell this apart from a plain RAG-answered email — the sender
    # might type questions directly in the body while ALSO attaching a real
    # fillable questionnaire, which is still a legitimate qa_fill. So this
    # regex intentionally stays wording-only and broad; the real disambiguation
    # (does an attachment actually HAVE a fillable Q&A structure?) happens
    # later, right before Phase 2, once inbox_attachments can be inspected —
    # see the qa_fill-vs-plain-email downgrade in run().
    if re.search(
        r"(fill|answer|respond|complete|provide response).{0,30}"
        r"(question|clarif|query|response|blank|column)|"
        r"(question|clarif|query).{0,30}(fill|answer|respond|complete)|"
        r"fill in.{0,20}(response|answer|blank)",
        body_lower,
    ):
        return "qa_fill"

    # Creation verbs override input-reference stripping (weak "the/this <fmt>"
    # is left in place). Without this, requests like "make the PPT" or "create
    # the Excel" can lose "the PPT/Excel" and silently fall back to a plain
    # email reply. Strong input mentions ("the ATTACHED pdf") are stripped
    # first regardless — those can only ever be the source file, never the
    # requested output — so a trigger verb earlier in the sentence doesn't
    # latch onto a source-file mention instead of the real target format
    # named later (e.g. "give me a summary of the attached pdf in word format").
    create_fmt = re.search(
        r"\b(create|make|generate|build|prepare|produce|design|draft|develop|export|save|send|give)\b"
        r".{0,45}?\b(the\s+|a\s+|an\s+)?"
        r"(pptx?|powerpoint|presentation|slides?|deck|pdf|word|docx?|excel|spreadsheet|xlsx|csv)\b",
        _strip_strong_input_refs(body_lower),
    )
    if create_fmt:
        fmt_word = create_fmt.group(3)
        if re.search(r"pptx?|powerpoint|presentation|slides?|deck", fmt_word):
            return "ppt"
        if re.search(r"excel|spreadsheet|xlsx", fmt_word):
            return "excel"
        if re.search(r"csv", fmt_word):
            return "csv"
        if re.search(r"word|docx?", fmt_word):
            return "word"
        if re.search(r"pdf", fmt_word):
            return "pdf"

    # File-type OUTPUT detection runs on text with INPUT references stripped, so
    # "summarise the attached excel" stays an email reply, while "give me an excel"
    # / "report in pdf format" are correctly detected as create-this-file requests.
    fmt = _strip_input_refs(body_lower)

    # 1. Presentation
    if re.search(r"\bppt\b|\bpptx\b|\bpresentation\b|\bslides?\b|\bdeck\b|\bpitch\b", fmt):
        return "ppt"

    # 2. CSV (before excel — more specific)
    if re.search(r"\bcsv\b|\binto csv\b|\bas csv\b|comma[- ]separated", fmt):
        return "csv"

    # 3. Excel / spreadsheet (creation verb + format, or explicit output phrase)
    if re.search(
        r"\b(give|send|create|make|generate|export|save|produce)\b.{0,30}\b(excel|spreadsheet)\b|"
        r"\bin excel\b|\bas excel\b|\binto excel\b|excel format|excel report|excel output|excel file\b",
        fmt,
    ):
        return "excel"

    # 4. Word
    if re.search(r"\bword\b|\bdocx\b|word file|word format|word document", fmt):
        return "word"

    # 5. PDF
    if re.search(r"\bpdf\b", fmt):
        return "pdf"

    # 6. No explicit output format → email reply
    return "email"


_SLIDE_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_SLIDE_COUNT_RE = re.compile(
    r"\b(\d{1,2})\s*[-\s]?\s*slides?\b|"
    r"\bslides?\s*[:\-]?\s*(\d{1,2})\b|"
    rf"\b({'|'.join(_SLIDE_COUNT_WORDS)})\s*[-\s]?\s*slides?\b",
    re.IGNORECASE,
)


def _extract_slide_count(body_lower: str) -> int:
    """Explicit slide-count request ('make it 6 slides', '10-slide deck',
    'five slides') — 0 if none was mentioned, meaning fall back to the
    planner's own default. Extracted from the ORIGINAL request text, not the
    WRITE phase's expanded prose, since a structural instruction like this is
    easy to lose/reword across the WRITE → slide-planner rewrite chain."""
    m = _SLIDE_COUNT_RE.search(body_lower)
    if not m:
        return 0
    digits = m.group(1) or m.group(2)
    if digits:
        n = int(digits)
    else:
        n = _SLIDE_COUNT_WORDS.get((m.group(3) or "").lower(), 0)
    return n if 1 <= n <= 40 else 0


# ── File delivery / intake (preconfigured folders) ────────────────────────────

# "save it to the folder/dir", "write to the network location", "don't send in mail"
_FOLDER_DELIVERY_RE = re.compile(
    r"(save|store|write|put|place|drop|deliver|export)\b.{0,30}\b"
    r"(folder|directory|dir|network|shared|server|disk|drive|location|phantomops)\b"
    r"|\b(do ?n'?t|do not|no need to|without|instead of|rather than)\b.{0,18}\b(e-?mail|mail)\b"
    r"|\bnetwork (location|folder|drive|share)\b",
    re.IGNORECASE,
)
# "read/process the file <name> from the (shared/network/intake) folder"
_INTAKE_RE = re.compile(
    r"\b(read|process|use|open|analyse|analyze|fetch|pick ?up|grab)\b.{0,45}"
    r"\b(from|in)\b.{0,20}\b(folder|directory|network|shared|intake|input|drive|location|phantomops)\b",
    re.IGNORECASE,
)
_FILENAME_RE = re.compile(r"\b([A-Za-z0-9_\-()]+\.(?:pdf|docx?|xlsx?|csv|pptx?))\b", re.IGNORECASE)


def _wants_folder_delivery(body_lower: str) -> bool:
    return bool(_FOLDER_DELIVERY_RE.search(body_lower))


def _intake_request(body: str):
    """Return a filename, '__latest__', or None for a 'read file from folder' request."""
    if not _INTAKE_RE.search(body):
        return None
    m = _FILENAME_RE.search(body)
    if m:
        return m.group(1).strip()
    return "__latest__"   # named no file → newest in the intake folder


def _resolve_intake_file(name: str):
    """Safely resolve a file within INTAKE_DIR (basename only — no traversal)."""
    from core.config import INTAKE_DIR
    if not os.path.isdir(INTAKE_DIR):
        return None
    files = [os.path.join(INTAKE_DIR, f) for f in os.listdir(INTAKE_DIR)
             if os.path.isfile(os.path.join(INTAKE_DIR, f))]
    if not files:
        return None
    if name == "__latest__":
        return max(files, key=os.path.getmtime)
    safe = os.path.basename(name)
    for f in files:
        if os.path.basename(f).lower() == safe.lower():
            return f
    return None


def _deliver_to_folder(files: list):
    """Copy generated files into DELIVERY_DIR; return (confirmation_reply, [])."""
    import shutil
    from core.config import DELIVERY_DIR
    os.makedirs(DELIVERY_DIR, exist_ok=True)
    delivered = []
    for f in files:
        if not os.path.isfile(f):
            continue
        base = os.path.basename(f)
        dest = os.path.join(DELIVERY_DIR, base)
        if os.path.abspath(f) == os.path.abspath(dest):
            delivered.append(base)           # already in the delivery folder
            continue
        root, ext = os.path.splitext(base)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(DELIVERY_DIR, f"{root}_{n}{ext}"); n += 1
        shutil.copy2(f, dest)
        delivered.append(os.path.basename(dest))
    names = ", ".join(delivered) if delivered else "the file(s)"
    answer = (f"Hello,\n\nDone — I've saved {names} to the configured output folder:\n"
              f"{DELIVERY_DIR}\n\nThe file(s) are available there instead of being attached to "
              f"this email. Let me know if you'd like anything changed.\n\n{_sig()}")
    return answer, []


def should_respond_when_cc(body: str, model_label: str) -> bool:
    """Sally is only CC'd on this email (not a direct recipient). Decide (LLM,
    robust to phrasing) whether the message is actually addressing/asking the
    assistant, vs humans talking to each other. Used as a 'reply-all' guardrail."""
    import json as _json
    from langchain_core.messages import SystemMessage, HumanMessage
    from core.config import get_agent_name
    name = get_agent_name()
    sys = (
        f"An AI assistant named {name} is CC'd (NOT a direct recipient) on this email. "
        "Decide whether the latest message is asking the assistant to DO or ANSWER something.\n"
        'Reply with ONLY JSON: {"addressed": true|false}\n'
        "addressed = true ONLY if there is an actual request, question, or task for the assistant "
        f"(e.g. '{name}, draft…', 'can the assistant pull…', '@{name} please…').\n"
        f"addressed = false if it is human-to-human discussion, OR if {name} is only mentioned "
        f"without a task — including: a greeting or thank-you ('Thanks {name}!'), an acknowledgement, "
        f"merely naming/referencing them, '{name}' used as a project/person's name, or a message telling "
        f"them NOT to act ('no need for {name} to…', '{name} you don't need to reply', 'no action needed'). "
        "A request aimed at a human (e.g. 'Devesh, can you…') is also false."
    )
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        r = llm.invoke([SystemMessage(content=sys), HumanMessage(content=body[:1500])])
        return bool(_json.loads(_extract_json_block(extract_text_content(r))).get("addressed"))
    except Exception as e:
        print(f"[CC] addressed-check failed ({e}) — falling back to name check")
        bl = (body or "").lower()
        nl = name.lower()
        return nl in bl or f"@{nl}" in bl   # minimal fallback: only if named


def is_addressed_in_thread(body: str, model_label: str, replying_to_agent: bool = False) -> bool:
    """The agent has ALREADY replied in this thread ("sticky" thread). Decide whether
    the LATEST message is directed at the assistant — a task, question, correction, or
    follow-up (no name needed) — or whether it's humans talking to each other, in which
    case the agent observes silently.

    replying_to_agent: True when the sender hit reply on the agent's OWN email
    (detected from the quoted block) — a strong signal the message is for the agent.
    """
    import json as _json
    from langchain_core.messages import SystemMessage, HumanMessage
    from core.config import get_agent_name
    name = get_agent_name()
    hint = (
        "The sender hit reply on the ASSISTANT'S OWN previous email — that usually "
        "means they are talking to the assistant (e.g. a correction or follow-up)."
        if replying_to_agent else
        "The sender replied to another PERSON'S message in the thread, so it may be "
        "humans talking to each other."
    )
    sys = (
        f"An AI assistant named {name} is part of an ongoing email thread (it has already "
        "replied in this thread). Decide whether the LATEST message is directed at the "
        f"assistant. {hint}\n"
        'Reply with ONLY JSON: {"addressed": true|false}\n'
        f"addressed = true if the message names the assistant with a request ('{name}, "
        "please…'), OR is a task, question, correction, or follow-up aimed at the "
        "assistant even WITHOUT its name (e.g. 'actually I meant…', 'make it "
        "monthly instead', 'can you also add…' in reply to the assistant's work).\n"
        "addressed = false when people are talking to EACH OTHER: discussion between "
        "humans, a request aimed at a named person ('Devesh, can you…'), acknowledgements "
        "('thanks', 'will do', 'sounds good'), or chatter that asks the assistant nothing."
    )
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        r = llm.invoke([SystemMessage(content=sys), HumanMessage(content=body[:1500])])
        return bool(_json.loads(_extract_json_block(extract_text_content(r))).get("addressed"))
    except Exception as e:
        print(f"[Thread] addressed-check failed ({e}) — falling back")
        if replying_to_agent:
            return True   # replying to the agent's own email → engage
        bl = (body or "").lower()
        nl = name.lower()
        return nl in bl or f"@{nl}" in bl


_ARABIC_CHAR_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿ]")
_LATIN_CHAR_RE  = re.compile(r"[A-Za-z]")


def _detect_language_fallback(body: str) -> str:
    """Script-based language guess used only when the LLM classifier call
    fails (classify_request_intent's exception fallback) — the LLM path reads
    actual meaning and is preferred; this is a safety net, not the primary
    mechanism. Counts Arabic-script vs Latin-script characters in the body."""
    text = body or ""
    ar = len(_ARABIC_CHAR_RE.findall(text))
    en = len(_LATIN_CHAR_RE.findall(text))
    if ar == 0:
        return "en"
    if en == 0:
        return "ar"
    # Both scripts present in meaningful amounts → mixed; a handful of stray
    # Latin characters (names, "RAG", punctuation) shouldn't flip a mostly-
    # Arabic email to "mixed", and vice versa.
    minority_ratio = min(ar, en) / max(ar, en)
    return "mixed" if minority_ratio > 0.15 else ("ar" if ar > en else "en")


def classify_request_intent(body: str, model_label: str) -> dict:
    """LLM-based intent detection (robust to phrasing — NOT keyword matching):
    whether to deliver the output to a folder vs email it, and whether to read an
    input file already sitting in a folder. Falls back to a minimal keyword check
    only if the LLM call fails."""
    import json as _json
    from langchain_core.messages import SystemMessage, HumanMessage
    sys = (
        "Read the email request and decide a few things. Reply with ONLY a JSON object, no prose:\n"
        '{"deliver_to_folder": true|false, "read_from_folder": true|false, "intake_filename": "", '
        '"needs_web_search": true|false, "calendar_action": "none"|"schedule"|"cancel"|"update", '
        '"output_format": "email"|"pdf"|"word"|"excel"|"csv"|"ppt", '
        '"uses_attachment": true|false, "uses_thread": true|false, "save_to_rag": true|false, '
        '"uses_rag": true|false, "needs_multi_kb_lookup": true|false, '
        '"detected_language": "en"|"ar"|"mixed", '
        '"content_language": ""|"en"|"ar"}\n\n'
        "calendar_action: what the sender wants done with a CALENDAR MEETING/event —\n"
        '  "schedule": create/set up/arrange a new meeting, call, or appointment (incl. recurring);\n'
        '  "cancel": cancel / call off / delete an existing meeting;\n'
        '  "update": change an EXISTING meeting — reschedule/move the time, change duration, rename '
        "it, edit the agenda, or add/remove attendees (e.g. 'add sid@x to the sync', 'remove John "
        "from the standup', 'push the call to 5pm');\n"
        '  "none": anything else (a reply, a document, a question — including merely TALKING ABOUT a '
        "meeting, e.g. 'draft an email proposing a meeting' or 'what time was the meeting?').\n"
        "Distinguish carefully: 'remove John from the meeting' is \"update\" (attendee change), "
        "but 'remove/cancel the meeting' is \"cancel\". Drafting or asking ABOUT a meeting is \"none\".\n\n"
        "deliver_to_folder: true if the user wants the produced file SAVED / written / stored to a "
        "folder, directory, drive, shared or network location, server, or disk — or says NOT to "
        "email/mail/send it. false if they just want a normal reply or the file emailed/attached. "
        "Merely asking to CREATE/make a file (a pdf, report, ppt, excel) is NOT folder delivery — "
        "the file is emailed by default unless a folder/location is mentioned. "
        "Examples: 'research X and make a pdf' → false (no folder mentioned); "
        "'create a report and save it to the folder' → true.\n"
        "read_from_folder: true ONLY if the user explicitly points to a file sitting in a folder/"
        "directory/drive/shared location (e.g. 'read report.pdf from the shared folder'). It is "
        "FALSE when the user refers to 'the attached file', 'the above file', or an email attachment "
        "— those are uploaded with the email, not in a folder.\n"
        "intake_filename: the exact file name to read if (and only if) read_from_folder is true, else \"\".\n"
        "needs_web_search: true ONLY if answering requires CURRENT or EXTERNAL information from the "
        "internet — news, prices, stocks, market/industry data, competitor info, benchmarks, "
        "rankings, anything 'latest/current/today', or researching a company/topic beyond what the "
        "email and its attachments contain. false when the request can be fully answered from the "
        "attached/referenced documents plus general knowledge — summarising or analysing an uploaded "
        "file, opinions, suggestions/ideas/brainstorming, drafting text, explaining a concept, or a "
        "conversational reply. Explaining a concept, term, or technology ('what is X', 'explain Y', "
        "'how does Z work') is general knowledge → false, even for tech/AI topics — only 'latest "
        "news/developments about X' needs search. When a request mixes both (e.g. 'report on the "
        "attached file AND compare with current market data'), use true.\n"
        "output_format: what form the DELIVERABLE should take —\n"
        '  "ppt": a PowerPoint / slide deck / presentation;\n'
        '  "word": a Word / .docx document, letter, or formatted write-up as a file;\n'
        '  "excel": an Excel / .xlsx spreadsheet or workbook;\n'
        '  "csv": a CSV / comma-separated data file;\n'
        '  "pdf": a PDF document/report;\n'
        '  "email": a normal email reply with the answer in the message body (the DEFAULT when '
        "no file format is requested — questions, summaries, drafts, explanations).\n"
        "Judge by what the user asks to RECEIVE, not by the format of any attached file. "
        "'make a deck from this', 'turn it into slides' → ppt; 'put it in a spreadsheet' → excel; "
        "'export as csv' → csv; 'write it up as a doc' → word; 'generate a report' (no format) → pdf; "
        "'summarise this', 'what do you think', 'reply to them' → email.\n"
        "uses_attachment: true if answering this request requires reading a FILE attached to / "
        "uploaded with the email (e.g. 'summarise the attached report', 'make a ppt from this pdf', "
        "'review the document'). false if the request stands on its own and does not point to an "
        "attached file (a general question, a brand-new draft, a meeting request). When unsure, true.\n"
        "uses_thread: true if answering requires the EARLIER conversation/thread — a follow-up that "
        "refers back to something already said (pronouns or references like 'his age', 'make it "
        "shorter', 'what about next year', 'as we discussed', 'the report you sent'), or asks to "
        "summarise/continue the prior discussion. false if the request is fully self-contained and "
        "makes sense on its own without any previous message. When unsure, true.\n"
        "save_to_rag: true ONLY if the sender explicitly asks to SAVE/STORE/REMEMBER/ADD content "
        "into the knowledge base / RAG / memory for future reference — e.g. 'save this into RAG', "
        "'add this to the knowledge base', 'remember this glossary', 'store the content from this "
        "link'. false for every normal request (a question, a document to create, a reply) — this "
        "is a distinct, deliberate save COMMAND, not something to infer from context. When unsure, false.\n"
        "uses_rag: true for almost every request. The knowledge base may hold policies, glossaries, "
        "terminology, or reference material the sender doesn't know was ever saved — so a question "
        "that SOUNDS generic or like public knowledge can still be asking about THIS organization's "
        "own specific version of it, and you cannot tell just from the phrasing. Treat any question, "
        "information request, or ambiguous ask as true by default, even ones that read as ordinary "
        "factual questions (e.g. 'what is the maximum daily withdrawal limit', 'what's our refund "
        "policy', 'how many vacation days do I get' → true — these could easily be answered by a "
        "saved internal document, not general knowledge). Use false ONLY for requests that are "
        "self-evidently NOT about looking anything up — scheduling/cancelling a meeting, a save_to_rag "
        "command itself, a pure file-format conversion, or a reply that only continues content already "
        "fully present in this email/attachment/thread. When genuinely unsure, true — checking an "
        "empty or irrelevant knowledge base costs nothing.\n"
        "needs_multi_kb_lookup: true ONLY when the request needs the assistant to cross-reference "
        "or compare MULTIPLE DISTINCT pieces of saved/internal information — e.g. 'compare our "
        "refund policy with our shipping policy', 'find our policy on X and check it against Y', "
        "'look up both the vacation policy and the sick leave policy'. false for a request about a "
        "SINGLE topic, even when uses_rag is true — this is the vast majority of requests. This is a "
        "narrow, deliberate flag: do not set it true just because uses_rag is true; only when the "
        "request explicitly names or implies two or more separate things to look up. A request that "
        "sets this true should also have uses_rag true (a multi-lookup request is inherently "
        "RAG-relevant).\n"
        "detected_language: the language of the sender's actual REQUEST/ASK in this email — "
        '"ar" if it\'s written in Arabic (any dialect), "en" if English, "mixed" if the request '
        "itself genuinely mixes both within the same message. Judge the request/question text, "
        "not any quoted thread history or attachment content in a different language. When unsure, \"en\".\n"
        "content_language: ONLY set when the sender EXPLICITLY specifies what language the "
        "DELIVERABLE ITSELF (the essay/report/document/answer being produced) should be written "
        "in — e.g. 'write me an essay in Arabic about X', 'reply in French', 'give the summary in "
        "Spanish'. This is DIFFERENT from detected_language (the language of the request), and "
        "takes priority over it for the deliverable's content when both are present — a request "
        "written in English asking for 'an essay in Arabic' has detected_language=\"en\" but "
        'content_language="ar" (the covering email can stay in whatever language it was written '
        "in, but the essay itself must be in the explicitly requested language). Leave "
        '"" (empty) when no explicit target language is stated for the content — the default '
        "is then to match detected_language, same as before this field existed.\n"
        "Judge by MEANING, not by specific words. If genuinely unclear, use false "
        "(for needs_web_search: if unclear whether external data is needed, use true; "
        "for uses_attachment, uses_thread, and uses_rag: if unclear, use true; "
        "for save_to_rag and needs_multi_kb_lookup: if unclear, false)."
    )
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        r = llm.invoke([SystemMessage(content=sys), HumanMessage(content=body[:1500])])
        d = _json.loads(_extract_json_block(extract_text_content(r)))
        ca = str(d.get("calendar_action") or "none").strip().lower()
        if ca not in ("none", "schedule", "cancel", "update"):
            ca = "none"
        of = str(d.get("output_format") or "").strip().lower()
        if of not in ("email", "pdf", "word", "excel", "csv", "ppt"):
            of = ""  # leave blank → run() falls back to regex output detection
        dl = str(d.get("detected_language") or "en").strip().lower()
        if dl not in ("en", "ar", "mixed"):
            dl = "en"
        cl = str(d.get("content_language") or "").strip().lower()
        if cl not in ("en", "ar"):
            cl = ""
        return {
            "deliver_to_folder": bool(d.get("deliver_to_folder")),
            "read_from_folder":  bool(d.get("read_from_folder")),
            "intake_filename":   str(d.get("intake_filename") or "").strip(),
            "needs_web_search":  bool(d.get("needs_web_search", True)),
            "calendar_action":   ca,
            "output_format":     of,
            # Fail-open source flags: default True so we never DROP a needed source.
            "uses_attachment":   bool(d.get("uses_attachment", True)),
            "uses_thread":       bool(d.get("uses_thread", True)),
            # Fail-CLOSED: this is a deliberate save command, not a context source —
            # an ambiguous read should never silently write into the KB.
            "save_to_rag":       bool(d.get("save_to_rag", False)),
            "uses_rag":          bool(d.get("uses_rag", True)),
            # Fail-CLOSED, narrow and deliberate — like save_to_rag, defaulting this
            # true would trigger GATHER on far more requests than intended.
            "needs_multi_kb_lookup": bool(d.get("needs_multi_kb_lookup", False)),
            "detected_language": dl,
            "content_language": cl,
        }
    except Exception as e:
        print(f"[INTENT] LLM classify failed ({e}) — using keyword fallback")
        nm = _intake_request(body)
        bl = body.lower()
        # Regex fallback for the calendar action (LLM-primary, keyword-safety-net).
        if is_update_request(bl):     ca = "update"
        elif is_cancel_request(bl):   ca = "cancel"
        elif _detect_output_type(bl) == "meeting": ca = "schedule"
        else:                         ca = "none"
        return {
            "deliver_to_folder": _wants_folder_delivery(body.lower()),
            "read_from_folder":  bool(nm),
            "intake_filename":   "" if nm in (None, "__latest__") else nm,
            # Fail-open: if the classifier is down, keep today's always-search behavior
            "needs_web_search":  True,
            "calendar_action":   ca,
            # Blank → run() uses regex _detect_output_type; sources fail-open to True.
            "output_format":     "",
            "uses_attachment":   True,
            "uses_thread":       True,
            # Regex safety net so a total LLM outage doesn't disable the command
            # entirely; still fail-closed if no explicit save phrasing is found.
            "save_to_rag":       bool(re.search(
                r"\b(save|store|add|remember)\b.{0,25}\b(rag|knowledge base|kb|memory)\b",
                bl,
            )),
            "uses_rag":          True,
            # No regex heuristic attempted for this narrow flag — if the classifier
            # itself is down, this enhancement simply doesn't activate, falling back
            # to exactly the pre-existing behavior (static pre-fetch only), not a
            # new gap.
            "needs_multi_kb_lookup": False,
            "detected_language": _detect_language_fallback(body),
            # No reliable keyword signal for an explicit content-language
            # override — leave unset (falls back to detected_language) rather
            # than guess.
            "content_language":  "",
        }


# ── Meeting scheduling flow ───────────────────────────────────────────────────

def _sig() -> str:
    """Email signature with the configurable agent name + company identity
    (both dashboard-overridable)."""
    from core.config import get_email_signature
    return get_email_signature()


def _extract_json_block(text: str) -> str:
    """Pull the first {...} JSON object out of an LLM response (strips fences/prose)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    return m.group(0) if m else (text or "")


# ── RAG ingest flow ("save this to RAG") ──────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

# A "save that/the research to RAG" that points BACK at content the agent
# produced earlier in the thread, rather than providing new content inline.
_SAVE_BACKREF_RE = re.compile(
    r"\b(that|this|it|those|the|your|the above|the previous)\s+"
    r"(research|info(?:rmation)?|findings?|answer|reply|response|results?|data|"
    r"content|report|summary|details?|output)\b|"
    r"\b(what you (found|sent|said|wrote|gave|researched)|you (found|sent|researched)|"
    r"the (above|previous|earlier|last)|earlier|previously)\b",
    re.IGNORECASE,
)


def _last_assistant_message(history) -> str:
    """The agent's most recent reply in this thread (the thing a follow-up like
    'save that research' refers to). Empty if the thread has no prior agent turn."""
    for item in reversed(history or []):
        if isinstance(item, dict) and item.get("role") == "assistant":
            content = (item.get("content") or "").strip()
            if content:
                return content
    return ""


def _strip_reply_boilerplate(text: str) -> str:
    """Drop the greeting line and signature block from a prior agent reply so
    only the substantive content (the research/answer) gets saved to the KB,
    not 'Hi X,' / 'Best regards, Sally | ...'."""
    t = (text or "").strip()
    for marker in ("\nBest regards", "\nمع أطيب التحيات", "\nمع الشكر", "\nمع التحية", "\nمع تحيات"):
        idx = t.find(marker)
        if idx != -1:
            t = t[:idx].strip()
            break
    lines = t.split("\n", 1)
    if len(lines) == 2 and re.match(r"^\s*(hi|hello|hey|dear|مرحبا|أهلا)\b.{0,40}$", lines[0], re.IGNORECASE):
        t = lines[1].strip()
    return t


def _run_rag_ingest_flow(body: str, inbox_attachments: list, attachment_context: str,
                          message_id: str = "", language: str = None,
                          history: list = None) -> str:
    """Handle a "save this to RAG" command (save_to_rag task mode — see
    classify_request_intent). Never generates a document; always short-circuits
    before GATHER/WRITE/RENDER, same shape as the calendar flows in run().

    What gets saved:
      - An email attachment, if one was uploaded — attachment_context already
        holds its extracted text (from the same read used elsewhere in run()),
        so it's reused here rather than re-parsed. The email BODY is saved
        alongside it (its own source_ref, so it can't collide with or
        overwrite the attachment's chunks on re-ingest) whenever the body has
        real content beyond just the save command itself — e.g. "save the
        attached policy, and note clause 3 was updated to 6000 SAR" saves
        BOTH the document and that sentence, not just one or the other.
      - Otherwise, a web link in the body, if present — fetched fresh via
        fetch_url_text.
      - Otherwise, if the message REFERS BACK to something the agent produced
        earlier in this thread ("save that research to RAG"), the agent's most
        recent reply from `history` (e.g. after researching a topic, save the
        findings) — its greeting/signature stripped so only the substance is
        stored.
      - Otherwise, the email body text alone.
    """
    from rag.store import ingest_if_new
    from rag.ingestion import fetch_url_text
    from rag.embedding import available as _rag_available

    if not _rag_available():
        return (f"Hello,\n\nThe knowledge base isn't set up on this installation yet "
                f"(needs the local RAG components installed and running) — I couldn't save "
                f"this. Please let your administrator know.\n\n{_sig()}")

    if inbox_attachments and attachment_context:
        label = ", ".join(os.path.basename(p) for p in inbox_attachments)
        attachment_meta = {
            "file_name": label,
            "title": os.path.splitext(label)[0] if len(inbox_attachments) == 1 else label,
        }
        if len(inbox_attachments) == 1:
            try:
                from datetime import datetime, timezone
                attachment_meta["source_date"] = datetime.fromtimestamp(
                    os.path.getmtime(inbox_attachments[0]), timezone.utc
                ).date().isoformat()
            except Exception:
                pass
        n_att = ingest_if_new(attachment_context, source_type="email",
                              source_ref=message_id or label, language=language,
                              metadata=attachment_meta)

        # Also save the body text itself, as long as it's more than just the
        # bare save-command sentence (a short "please save this" alone isn't
        # worth a standalone KB chunk). Distinct source_ref so its chunk IDs
        # never collide with — or get wiped by — the attachment's own upsert.
        n_body = 0
        body_text = (body or "").strip()
        if len(body_text) > 60:
            n_body = ingest_if_new(body_text, source_type="email",
                                   source_ref=(message_id or label) + "#body",
                                   language=language,
                                   metadata={"title": "Email note"})

        if n_att or n_body:
            parts = []
            if n_att:
                parts.append(f"the content of {label} ({n_att} chunk{'s' if n_att != 1 else ''})")
            if n_body:
                parts.append(f"the note in your email ({n_body} chunk{'s' if n_body != 1 else ''})")
            return (f"Hello,\n\nI've saved {' and '.join(parts)} into the knowledge base. "
                    f"I'll use it to help answer future questions.\n\n{_sig()}")
        return f"Hello,\n\nThat content is already in the knowledge base — nothing new to save.\n\n{_sig()}"

    urls = _URL_RE.findall(body or "")
    if urls:
        url = urls[0]
        text = fetch_url_text(url)
        if not text:
            return (f"Hello,\n\nI wasn't able to fetch or read {url} — please check the link "
                    f"is accessible and try again.\n\n{_sig()}")
        n_link = ingest_if_new(text, source_type="web", source_ref=url, language=language)

        # Same as the attachment case: also save the body's own content (minus
        # the link itself, so we're not just re-saving the URL string as noise)
        # when there's more to it than the save command + the link.
        n_body = 0
        body_text = _URL_RE.sub("", body or "").strip()
        if len(body_text) > 60:
            n_body = ingest_if_new(body_text, source_type="email",
                                   source_ref=(message_id or url) + "#body",
                                   language=language,
                                   metadata={"title": "Email note"})

        if n_link or n_body:
            parts = []
            if n_link:
                parts.append(f"the content from {url} ({n_link} chunk{'s' if n_link != 1 else ''})")
            if n_body:
                parts.append(f"the note in your email ({n_body} chunk{'s' if n_body != 1 else ''})")
            return (f"Hello,\n\nI've saved {' and '.join(parts)} into the knowledge base.\n\n{_sig()}")
        return f"Hello,\n\nThat page is already in the knowledge base — nothing new to save.\n\n{_sig()}"

    text = (body or "").strip()

    # No attachment, no link. If this is a follow-up pointing back at content the
    # agent produced earlier in the thread — a bare "save that to RAG" or an
    # explicit "save the research/findings/answer" — save the agent's most recent
    # reply instead of the (useless) command text. A short body with a prior agent
    # turn present is treated as a back-reference; a long body carries its own
    # content and is saved as-is (below).
    prior = _last_assistant_message(history)
    if prior and (len(text) <= 80 or _SAVE_BACKREF_RE.search(text)):
        research = _strip_reply_boilerplate(prior)
        if research:
            n = ingest_if_new(research, source_type="email",
                              source_ref=(message_id or "thread") + "#prior_answer",
                              language=language,
                              metadata={"title": "Prior agent response"})
            if n:
                return (f"Hello,\n\nI've saved my earlier response from this thread into the "
                        f"knowledge base ({n} chunk{'s' if n != 1 else ''}). I'll use it to help "
                        f"answer future questions.\n\n{_sig()}")
            return f"Hello,\n\nThat content is already in the knowledge base — nothing new to save.\n\n{_sig()}"

    if not text:
        return f"Hello,\n\nI didn't find any content in this message to save.\n\n{_sig()}"
    n = ingest_if_new(text, source_type="email", source_ref=message_id or text[:80],
                      language=language, metadata={"title": "Email note"})
    if n:
        return f"Hello,\n\nI've saved this into the knowledge base ({n} chunk{'s' if n != 1 else ''}).\n\n{_sig()}"
    return f"Hello,\n\nThat content is already in the knowledge base — nothing new to save.\n\n{_sig()}"


def _run_meeting_flow(body: str, model_label: str, agent_name: str, sender: str = "",
                      thread_id: str = "") -> str:
    """Extract meeting details and PROPOSE the meeting (confirmation guardrail):
    stash the details and ask the user to confirm; the event is only created once
    they reply yes (in _run_schedule_confirm_flow). Returns the reply text — a
    confirmation request, a request for missing info, or an error note. This way a
    misread "schedule" intent never emails attendees a real invite by mistake.

    `sender` is the requester's email. The event is organised by the agent's own
    mailbox, so the requester ("me"/"I"/"us") must be added as an ATTENDEE or they
    are left out of their own meeting — first-person references resolve to it.
    """
    import json as _json
    from datetime import date
    from langchain_core.messages import SystemMessage, HumanMessage
    from core.config import MEETING_TIMEZONE, MEETING_DEFAULT_DURATION_MIN
    from tools.meeting_scheduler import schedule_meeting

    sender = (sender or "").strip()
    today_str = date.today().strftime("%A, %d %B %Y")
    sender_line = (
        f'The person who sent this request ("me", "I", "myself", "us", "our team") is '
        f'{sender} — include this address in "attendees" whenever they refer to themselves.\n'
        if sender and "@" in sender else
        'If the sender refers to themselves ("me"/"I") but no sender address is known, '
        'add "requester" to "missing".\n'
    )
    sys_prompt = (
        "You extract meeting details from an email so an assistant can create a "
        f"calendar invite. TODAY IS {today_str}. All times are in the {MEETING_TIMEZONE} timezone.\n\n"
        + sender_line +
        "Return ONLY a JSON object (no prose, no code fences) with these keys:\n"
        '  "subject": short meeting title (string),\n'
        '  "attendees": [email addresses — those that appear literally in the email PLUS '
        "the sender's address above when they refer to themselves; never invent unknown ones],\n"
        '  "start_datetime": local time "YYYY-MM-DDTHH:MM:SS" — resolve relative dates '
        "(tomorrow, next Monday) from today's date. NEVER guess the clock time: if the "
        'email states no time of day, set "start_datetime" to "" and list "time" in "missing",\n'
        f'  "duration_minutes": integer (default {MEETING_DEFAULT_DURATION_MIN} if unspecified),\n'
        '  "description": one-line agenda or empty string,\n'
        '  "add_video": true if they ask for an online/video/Meet/Teams call else false,\n'
        '  "recurrence": for a REPEATING meeting an object '
        '{"freq":"daily"|"weekly"|"monthly","interval":<int>,'
        '"days":[lowercase weekday names for weekly, e.g. "monday"],'
        '"count":<int or null>,"until":"YYYY-MM-DD" or null}; '
        'use null for a one-time meeting. Examples: "every Monday"→'
        '{"freq":"weekly","interval":1,"days":["monday"],"count":null,"until":null}; '
        '"daily for 5 days"→{"freq":"daily","interval":1,"days":[],"count":5,"until":null}. '
        'For weekly recurrence, start_datetime is the FIRST occurrence on/after today.\n'
        '  "missing": list any REQUIRED field you could not determine: "attendees" if no '
        'email address is present, "date" if no date/day is given, "time" if no clock time '
        'is given (do NOT guess a time). List both "date" and "time" if neither is given.\n'
    )
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        resp = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=body)])
        data = _json.loads(_extract_json_block(extract_text_content(resp)))
    except Exception as e:
        print(f"[{agent_name}] Meeting extraction failed: {e}")
        return ("Hello,\n\nI'd be happy to set up that meeting, but I couldn't quite make out the "
                "details. Could you confirm the attendee's email address, the date and time, and "
                f"the topic?\n\n{_sig()}")

    missing   = set(data.get("missing") or [])
    attendees = [a.strip() for a in (data.get("attendees") or []) if isinstance(a, str) and "@" in a]

    # Backstop: if the requester refers to themselves but the model omitted their
    # address, add it — otherwise they're left out of their own meeting (the event
    # is organised by the agent's mailbox, not the sender's).
    if sender and "@" in sender and re.search(r"\b(me|my|myself|i|we|us|our)\b", body, re.IGNORECASE):
        if sender.lower() not in {a.lower() for a in attendees}:
            attendees.insert(0, sender)

    # Guardrail: drop attendees outside the allowed domain(s); tell the user.
    from tools.meeting_scheduler import filter_attendees_by_domain
    attendees, _ext = filter_attendees_by_domain(attendees)
    if not attendees and _ext:
        return ("Hello,\n\nI can only invite people inside the organisation, and the address(es) "
                f"you gave are external: {', '.join(_ext)}. Please share an internal attendee's "
                f"email and I'll set it up.\n\n{_sig()}")

    if not attendees:
        missing.add("attendees")
    if not (data.get("start_datetime") or "").strip():
        missing.add("start_datetime")

    if missing:
        want_date = "date" in missing
        want_time = "time" in missing
        # A bare empty start_datetime with no finer hint means we lack both.
        if "start_datetime" in missing and not want_date and not want_time:
            want_date = want_time = True
        asks = []
        if "attendees" in missing:
            asks.append("the email address of the person to invite")
        if want_date and want_time:
            asks.append("the date and time")
        elif want_date:
            asks.append("the date")
        elif want_time:
            asks.append("the time")
        ask_str = " and ".join(asks) if asks else "a few more details"
        # Remember what we have so the user's next reply (e.g. "3pm") completes the
        # booking instead of being read as a brand-new, detail-less request.
        from core.database.sqlite_db import set_pending_action
        set_pending_action(thread_id, "schedule",
                           {"needs_info": True, "original_body": body, "sender": sender})
        return (f"Hello,\n\nI can set up that meeting — I just need {ask_str}. Once you share "
                f"that, I'll send out the calendar invite.\n\n{_sig()}")

    # Create the event directly (no confirmation step). The original misread-intent
    # risk is handled upstream by LLM action-classification + quoted-thread
    # stripping; the domain guardrail keeps invitees internal.
    from tools.meeting_scheduler import _normalize_recurrence, schedule_meeting
    rec = _normalize_recurrence(data.get("recurrence"))
    result = schedule_meeting(
        subject=data.get("subject") or "Meeting",
        attendees=attendees,
        start_iso=data.get("start_datetime"),
        duration_min=data.get("duration_minutes") or MEETING_DEFAULT_DURATION_MIN,
        description=data.get("description") or "",
        add_video=bool(data.get("add_video")),
        recurrence=rec,
    )
    if not result.get("ok"):
        print(f"[{agent_name}] Scheduling failed: {result.get('error')}")
        return ("Hello,\n\nI tried to schedule the meeting but hit a problem creating the calendar "
                f"event. A team member will follow up to set it up.\n\n{_sig()}")

    who       = ", ".join(result["attendees"])
    link_line = f"\nJoin link: {result['link']}\n" if result.get("link") else ""
    repeats   = result.get("recurrence", "one-time")
    when_line = (f"for **{result['start']}** (until {result['end']})" if repeats == "one-time"
                 else f"starting **{result['start']}**, repeating **{repeats}**")
    ext_note  = (f"\n\n(I left out {', '.join(_ext)} — only internal attendees can be invited.)"
                 if _ext else "")
    return (f"Hello,\n\nDone — I've scheduled **{result['subject']}** {when_line} "
            f"and sent a calendar invitation to {who}.{link_line}{ext_note}\n"
            f"Let me know if you'd like to change the time or add anyone.\n\n{_sig()}")


def _run_schedule_confirm_flow(body: str, model_label: str, agent_name: str,
                               thread_id: str, payload: dict) -> str:
    """Interpret the user's yes/no and only NOW create the calendar event + invite
    (the confirmation guardrail for scheduling)."""
    from tools.meeting_scheduler import schedule_meeting
    from core.database.sqlite_db import clear_pending_action

    # "I'm missing the date/time" follow-up: the user is supplying the detail we
    # asked for, NOT confirming yes/no. Re-run extraction on the original request
    # plus this reply and book directly (no 10am guess, no extra confirmation).
    if payload.get("needs_info"):
        clear_pending_action(thread_id)
        combined = ((payload.get("original_body") or "")
                    + "\n\nAdditional details from the requester: " + (body or "")).strip()
        return _run_meeting_flow(combined, model_label, agent_name,
                                 payload.get("sender", ""), thread_id)

    text = (body or "").strip().lower()
    negative = re.search(r"\b(no|nope|don'?t|do not|skip|cancel that|never ?mind|stop|leave it)\b", text)
    affirmative = re.search(r"\b(yes|yep|yeah|confirm|go ?ahead|do it|sure|ok|okay|proceed|send it|schedule it)\b", text)

    if negative and not affirmative:
        clear_pending_action(thread_id)
        return ("Hello,\n\nOkay — I won't schedule that meeting. Let me know if you'd like to "
                f"set it up later.\n\n{_sig()}")
    if not affirmative:
        return (f"Hello,\n\nShould I go ahead and schedule **{payload.get('subject')}** for "
                f"**{payload.get('display_start')}** with {', '.join(payload.get('attendees', []))}? "
                f"Reply **yes** or **no**.\n\n{_sig()}")

    result = schedule_meeting(
        subject=payload.get("subject") or "Meeting",
        attendees=payload.get("attendees") or [],
        start_iso=payload.get("start_iso"),
        duration_min=payload.get("duration_min") or 30,
        description=payload.get("description") or "",
        add_video=bool(payload.get("add_video")),
        recurrence=payload.get("recurrence"),
    )
    clear_pending_action(thread_id)
    if not result.get("ok"):
        print(f"[{agent_name}] Scheduling failed: {result.get('error')}")
        return ("Hello,\n\nI tried to schedule the meeting but hit a problem creating the calendar "
                f"event. A team member will follow up to set it up.\n\n{_sig()}")

    who       = ", ".join(result["attendees"])
    link_line = f"\nJoin link: {result['link']}\n" if result.get("link") else ""
    repeats   = result.get("recurrence", "one-time")
    when_line = (f"for **{result['start']}** (until {result['end']})" if repeats == "one-time"
                 else f"starting **{result['start']}**, repeating **{repeats}**")
    return (f"Hello,\n\nDone — I've scheduled **{result['subject']}** {when_line} "
            f"and sent a calendar invitation to {who}.{link_line}\n"
            f"Let me know if you'd like to change the time or add anyone.\n\n{_sig()}")


# ── Meeting cancellation flow (search → confirm → cancel) ─────────────────────
# Cancelling can hit any meeting in the mailbox, so it is NEVER done in one step:
# the request lists matching meetings and asks the user to confirm; the
# confirmation (next message in the thread) actually cancels. Candidates are
# stashed via set_pending_action so the wrong meeting is never cancelled.

_CANCEL_RE = re.compile(
    r"\b(cancel|call(?:ing|ed)?\s+off|calling\s+off|scrap|delete|remove|drop)\b"
    r"[^.\n]{0,40}\b(meeting|call|appointment|sync|invite|invitation|event|catch[- ]?up|"
    r"demo|interview|discussion)\b"
    r"|\b(meeting|call|appointment|sync|invite|event)\b[^.\n]{0,30}\b(cancel|call(?:ed)?\s+off)\b",
    re.IGNORECASE,
)


def is_cancel_request(body_lower: str) -> bool:
    """True if the message asks to cancel/call off a meeting (not schedule one)."""
    text = body_lower or ""
    # "remove John from the meeting" changes attendees; it must not be treated
    # like "remove/cancel the meeting" itself.
    if is_update_request(text):
        return False
    return bool(_CANCEL_RE.search(text))


def _fmt_candidate(c: dict, idx: int = None) -> str:
    who = ", ".join(c.get("attendees") or []) or "no attendees listed"
    role = "you organise this" if c.get("is_organizer") else "you're an attendee"
    head = f"{idx}. " if idx else ""
    rec = " — **recurring series** (cancelling removes all occurrences)" if c.get("is_recurring") else ""
    return f"{head}**{c.get('subject')}** — {c.get('start')} (with {who}; {role}){rec}"


def _run_cancel_flow(body: str, model_label: str, agent_name: str,
                     thread_id: str) -> str:
    """Find candidate meetings to cancel and ask the user to confirm. Stores the
    candidates for the confirmation step; does NOT cancel anything here."""
    import json as _json
    from datetime import date
    from langchain_core.messages import SystemMessage, HumanMessage
    from core.config import MEETING_TIMEZONE
    from tools.meeting_scheduler import find_meetings
    from core.database.sqlite_db import set_pending_action

    today_str = date.today().strftime("%A, %d %B %Y")
    sys_prompt = (
        "Extract which meeting the sender wants to CANCEL. TODAY IS "
        f"{today_str} ({MEETING_TIMEZONE}). Return ONLY JSON with:\n"
        '  "subject": keywords from the meeting title/topic, or "" if none given,\n'
        '  "date_from": "YYYY-MM-DDTHH:MM:SS" earliest plausible time, or "" ,\n'
        '  "date_to": "YYYY-MM-DDTHH:MM:SS" latest plausible time, or "".\n'
        'Resolve relative dates (tomorrow, next Monday) from today. For a single '
        'day use 00:00:00 to 23:59:59 of that day.'
    )
    subject_q, date_from, date_to = "", "", ""
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        resp = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=body)])
        d = _json.loads(_extract_json_block(extract_text_content(resp)))
        subject_q = (d.get("subject") or "").strip()
        date_from = (d.get("date_from") or "").strip()
        date_to   = (d.get("date_to") or "").strip()
    except Exception as e:
        print(f"[{agent_name}] Cancel extraction failed (using open window): {e}")

    matches = find_meetings(subject_q, date_from, date_to)
    if matches and isinstance(matches[0], dict) and matches[0].get("error"):
        print(f"[{agent_name}] Calendar search failed: {matches[0]['error']}")
        return ("Hello,\n\nI tried to look up that meeting but couldn't reach the calendar "
                f"just now. Please try again shortly.\n\n{_sig()}")

    if not matches:
        return ("Hello,\n\nI couldn't find a matching meeting on the calendar. Could you give "
                "me the meeting title and its date so I can find the right one?\n\n"
                f"{_sig()}")

    # Cap to a sensible number to confirm against.
    matches = matches[:8]
    set_pending_action(thread_id, "cancel", matches)

    if len(matches) == 1:
        return (f"Hello,\n\nI found this meeting:\n\n{_fmt_candidate(matches[0])}\n\n"
                "Reply **yes** to confirm and I'll cancel it (attendees are notified). "
                "Reply **no** to leave it as is.\n\n" + _sig())

    listing = "\n".join(_fmt_candidate(c, i + 1) for i, c in enumerate(matches))
    return (f"Hello,\n\nI found a few meetings that could match:\n\n{listing}\n\n"
            "Reply with the **number** of the one to cancel (or **none** to leave them). "
            "I'll cancel it once you confirm.\n\n" + _sig())


def _run_cancel_confirm_flow(body: str, model_label: str, agent_name: str,
                             thread_id: str, pending: list) -> str:
    """Interpret the user's confirmation/selection against stored candidates and
    cancel the chosen meeting (or clear the pending request)."""
    from tools.meeting_scheduler import cancel_meeting
    from core.database.sqlite_db import clear_pending_action as clear_pending_cancel

    text = (body or "").strip().lower()

    negative = re.search(r"\b(no|don'?t|do not|leave it|never ?mind|cancel that|stop|none|keep it)\b", text)
    affirmative = re.search(r"\b(yes|yep|yeah|confirm|go ahead|please do|do it|sure|ok|okay|proceed|cancel it)\b", text)

    chosen = None
    if len(pending) == 1:
        if negative and not affirmative:
            clear_pending_cancel(thread_id)
            return ("Hello,\n\nUnderstood — I've left that meeting on the calendar. Let me know "
                    f"if you need anything else.\n\n{_sig()}")
        if affirmative:
            chosen = pending[0]
    else:
        m = re.search(r"\b(\d{1,2})\b", text)
        if negative and not m:
            clear_pending_cancel(thread_id)
            return ("Hello,\n\nNo problem — I've left those meetings as they are.\n\n" + _sig())
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(pending):
                chosen = pending[idx]

    if chosen is None:
        # Couldn't tell which / whether to cancel — re-ask, keep pending alive.
        if len(pending) == 1:
            return (f"Hello,\n\nJust to confirm — should I cancel "
                    f"{_fmt_candidate(pending[0])}? Reply **yes** or **no**.\n\n{_sig()}")
        listing = "\n".join(_fmt_candidate(c, i + 1) for i, c in enumerate(pending))
        return (f"Hello,\n\nWhich one should I cancel? Reply with its number:\n\n{listing}\n\n"
                + _sig())

    res = cancel_meeting(chosen.get("event_id", ""))
    clear_pending_cancel(thread_id)
    if not res.get("ok"):
        print(f"[{agent_name}] Cancel failed: {res.get('error')}")
        return ("Hello,\n\nI found the meeting but hit a problem cancelling it on the calendar. "
                f"A team member will follow up.\n\n{_sig()}")

    verb = "cancelled and notified the attendees" if res.get("action") == "cancelled" \
        else "declined your seat on"
    return (f"Hello,\n\nDone — I've {verb} **{chosen.get('subject')}** "
            f"({chosen.get('start')}).\n\n{_sig()}")


# ── Meeting update flow (search → confirm → apply) ────────────────────────────
# Changing an existing meeting (time, title, attendees, agenda, video) emails the
# update to attendees, so like schedule/cancel it is confirmation-gated. For a
# recurring meeting the change applies to the whole series.

_MTG = r"(meeting|call|appointment|sync|stand[- ]?up|huddle|catch[- ]?up|invite|event)"
_UPDATE_RE = re.compile(
    # reschedule / rename / edit the meeting itself ([^\n] so emails with dots match)
    r"\b(reschedule|re-schedule|move|push|shift|postpone|rename|update|edit|change|extend|shorten|lengthen)\b"
    r"[^\n]{0,30}\b(" + _MTG[1:-1] + r"|time|date|title|subject|agenda|duration|length)\b"
    # add an attendee TO a meeting
    r"|\b(add|invite|include)\b[^\n]{0,30}\bto\b[^\n]{0,20}\b" + _MTG + r"\b"
    # remove an attendee FROM a meeting
    r"|\b(remove|drop|take off|uninvite|kick)\b[^\n]{0,30}\bfrom\b[^\n]{0,20}\b" + _MTG + r"\b"
    # change the LENGTH ("make the meeting 60 minutes") — needs a duration token so
    # it doesn't collide with "make a meeting" (a create request)
    r"|\b(make|set|change|extend|shorten)\b[^\n]{0,20}\b(" + _MTG[1:-1] + r"|it)\b"
    r"[^\n]{0,12}\b\d{1,3}\s*(min|mins|minute|minutes|hour|hours|hr|hrs)\b",
    re.IGNORECASE,
)


def is_update_request(body_lower: str) -> bool:
    """True if the message asks to CHANGE an existing meeting (not create/cancel)."""
    return bool(_UPDATE_RE.search(body_lower or ""))


def _describe_changes(ch: dict) -> str:
    bits = []
    if ch.get("new_subject"):       bits.append(f"rename to \"{ch['new_subject']}\"")
    if ch.get("new_start"):         bits.append(f"move to {ch['new_start'].replace('T', ' ')}")
    if ch.get("new_duration_min"):  bits.append(f"set length to {ch['new_duration_min']} min")
    if ch.get("add_attendees"):     bits.append("add " + ", ".join(ch["add_attendees"]))
    if ch.get("remove_attendees"):  bits.append("remove " + ", ".join(ch["remove_attendees"]))
    if ch.get("new_description"):   bits.append("update the agenda")
    if ch.get("add_video") is True:  bits.append("add an online meeting link")
    if ch.get("add_video") is False: bits.append("remove the online meeting link")
    return "; ".join(bits) if bits else "no recognisable change"


def _run_update_flow(body: str, model_label: str, agent_name: str, thread_id: str) -> str:
    """Find the meeting to change and the requested changes, then ask to confirm.
    Stores a pending 'update'; applies nothing here."""
    import json as _json
    from datetime import date
    from langchain_core.messages import SystemMessage, HumanMessage
    from core.config import MEETING_TIMEZONE
    from tools.meeting_scheduler import find_meetings
    from core.database.sqlite_db import set_pending_action

    today_str = date.today().strftime("%A, %d %B %Y")
    sys_prompt = (
        "The sender wants to CHANGE an existing meeting. TODAY IS "
        f"{today_str} ({MEETING_TIMEZONE}). Return ONLY JSON:\n"
        '  "subject_query": keywords identifying which meeting, or "",\n'
        '  "date_from": "YYYY-MM-DDTHH:MM:SS" earliest plausible time of the EXISTING meeting or "",\n'
        '  "date_to": "YYYY-MM-DDTHH:MM:SS" latest plausible time or "",\n'
        '  "changes": {\n'
        '     "new_subject": new title or null,\n'
        '     "new_start": new "YYYY-MM-DDTHH:MM:SS" (resolve relative dates) or null,\n'
        '     "new_duration_min": integer or null,\n'
        '     "add_attendees": [email addresses to add — literal only],\n'
        '     "remove_attendees": [email addresses to remove — literal only],\n'
        '     "new_description": new agenda text or null,\n'
        '     "add_video": true/false to add/remove an online link, else null\n'
        "  }\n"
        "Only fill change fields the sender actually asked to change; leave the rest null/empty."
    )
    sq, df, dt_, changes = "", "", "", {}
    try:
        llm = build_agent_llm(model_label, temperature=0.0)
        resp = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=body)])
        d = _json.loads(_extract_json_block(extract_text_content(resp)))
        sq = (d.get("subject_query") or "").strip()
        df = (d.get("date_from") or "").strip()
        dt_ = (d.get("date_to") or "").strip()
        changes = d.get("changes") or {}
    except Exception as e:
        print(f"[{agent_name}] Update extraction failed: {e}")
        return ("Hello,\n\nI can update that meeting — could you tell me which meeting and what to "
                f"change (new time, title, or who to add/remove)?\n\n{_sig()}")

    # Clean attendee lists to real emails only.
    changes["add_attendees"] = [a.strip() for a in (changes.get("add_attendees") or []) if isinstance(a, str) and "@" in a]
    changes["remove_attendees"] = [a.strip() for a in (changes.get("remove_attendees") or []) if isinstance(a, str) and "@" in a]
    # Guardrail: only internal-domain attendees may be added.
    from tools.meeting_scheduler import filter_attendees_by_domain
    changes["add_attendees"], _ext_add = filter_attendees_by_domain(changes["add_attendees"])
    if _describe_changes(changes) == "no recognisable change":
        if _ext_add:
            return ("Hello,\n\nI can only invite people inside the organisation, and "
                    f"{', '.join(_ext_add)} are external — so there's nothing I can change. "
                    f"Share an internal address and I'll add them.\n\n{_sig()}")
        return ("Hello,\n\nI can update that meeting — what should I change? (new time, length, "
                f"title, agenda, or who to add/remove)\n\n{_sig()}")

    matches = find_meetings(sq, df, dt_)
    if matches and isinstance(matches[0], dict) and matches[0].get("error"):
        print(f"[{agent_name}] Calendar search failed: {matches[0]['error']}")
        return ("Hello,\n\nI tried to look up that meeting but couldn't reach the calendar just now. "
                f"Please try again shortly.\n\n{_sig()}")
    if not matches:
        return ("Hello,\n\nI couldn't find that meeting on the calendar. Could you give me its title "
                f"and date so I can find the right one?\n\n{_sig()}")

    matches = matches[:8]
    summary = _describe_changes(changes)
    if len(matches) == 1:
        c = matches[0]
        set_pending_action(thread_id, "update", {"event_id": c["event_id"], "subject": c["subject"],
                                                 "changes": changes})
        return (f"Hello,\n\nI'll update {_fmt_candidate(c)}:\n  • {summary}\n\n"
                "Reply **yes** to confirm and I'll send the update, or **no** to skip.\n\n" + _sig())

    # Multiple matches — store all + the changes, ask which one.
    set_pending_action(thread_id, "update", {"candidates": matches, "changes": changes})
    listing = "\n".join(_fmt_candidate(c, i + 1) for i, c in enumerate(matches))
    return (f"Hello,\n\nI'll apply this change — {summary} — but which meeting?\n\n{listing}\n\n"
            "Reply with the **number** (or **none** to skip).\n\n" + _sig())


def _run_update_confirm_flow(body: str, model_label: str, agent_name: str,
                             thread_id: str, payload: dict) -> str:
    """Interpret yes/no/number and apply the meeting change on confirmation."""
    from tools.meeting_scheduler import update_meeting
    from core.database.sqlite_db import clear_pending_action

    text = (body or "").strip().lower()
    negative = re.search(r"\b(no|nope|don'?t|do not|skip|never ?mind|stop|leave it|none|keep it)\b", text)
    affirmative = re.search(r"\b(yes|yep|yeah|confirm|go ?ahead|do it|sure|ok|okay|proceed|send it|update it)\b", text)
    changes = payload.get("changes") or {}

    target = None  # (event_id, subject)
    if "candidates" in payload:
        cands = payload["candidates"]
        m = re.search(r"\b(\d{1,2})\b", text)
        if negative and not m:
            clear_pending_action(thread_id)
            return ("Hello,\n\nNo problem — I've left those meetings unchanged.\n\n" + _sig())
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < len(cands):
                target = (cands[i]["event_id"], cands[i].get("subject"))
        if target is None:
            listing = "\n".join(_fmt_candidate(c, i + 1) for i, c in enumerate(cands))
            return (f"Hello,\n\nWhich meeting should I update? Reply with its number:\n\n{listing}\n\n"
                    + _sig())
    else:
        if negative and not affirmative:
            clear_pending_action(thread_id)
            return ("Hello,\n\nUnderstood — I've left that meeting unchanged.\n\n" + _sig())
        if not affirmative:
            return (f"Hello,\n\nJust to confirm — should I update **{payload.get('subject')}** "
                    f"({_describe_changes(changes)})? Reply **yes** or **no**.\n\n{_sig()}")
        target = (payload.get("event_id"), payload.get("subject"))

    res = update_meeting(target[0], changes)
    clear_pending_action(thread_id)
    if not res.get("ok"):
        print(f"[{agent_name}] Update failed: {res.get('error')}")
        return ("Hello,\n\nI found the meeting but hit a problem updating it on the calendar. "
                f"A team member will follow up.\n\n{_sig()}")
    applied = ", ".join(res.get("applied") or []) or "the requested changes"
    return (f"Hello,\n\nDone — I've updated **{res.get('subject') or target[1]}** ({applied}) "
            f"and notified the attendees.\n\n{_sig()}")


# ── Write prompts per output type ─────────────────────────────────────────────

_WRITE_PROMPTS = {
    "pdf": (
        "You are a senior analyst and professional writer at EAI Systems Pvt. Ltd.\n"
        "Write a COMPREHENSIVE, DETAILED document in markdown based on all the information provided.\n\n"
        "QUALITY REQUIREMENTS:\n"
        "- Every section must have 3-6 paragraphs of substantive analysis — not just bullet summaries.\n"
        "- Include reasoning: WHY something is true, not just WHAT is true.\n"
        "- Include observations, implications, and analytical insights.\n"
        "- Use specific facts, figures, and names from the source material.\n"
        "- Bullet points for lists; numbered steps for processes.\n"
        "- TABLES (important): present ANY tabular, ranked, or comparative data as a Markdown "
        "table — `| Col | Col |` with a `|---|---|` separator row. This includes rankings "
        "(e.g. top/bottom performers), scores, metrics, comparisons, schedules, and line items. "
        "Pull the relevant figures from the source into the table; never render such data as "
        "prose or plain bullets when a table fits.\n"
        "- Structure: # for title (one), ## for main sections, ### for subsections.\n"
        "- A thorough document should have 8-12 sections. Do not truncate.\n\n"
        "GROUNDING: Use only facts from the provided data and research. "
        "Do not invent numbers, prices, or figures not in the source. "
        "If something is unknown, say so explicitly rather than guessing.\n\n"
        "Begin the document with a clear '# Title' line.\n"
        "CRITICAL: Output ONLY the document content itself. DO NOT include any conversational text, "
        "greetings, email sign-offs, or messages like 'Here is your report' or 'Best regards'. Just the document."
    ),
    "word": (
        "You are a senior analyst and professional writer at EAI Systems Pvt. Ltd.\n"
        "Write a COMPREHENSIVE, DETAILED Word document in markdown based on all the information provided.\n\n"
        "QUALITY REQUIREMENTS:\n"
        "- Every section must have 3-6 paragraphs of substantive analysis — not just bullet summaries.\n"
        "- Include reasoning: WHY something is true, not just WHAT is true.\n"
        "- Include observations, implications, and analytical insights.\n"
        "- Use specific facts, figures, and names from the source material.\n"
        "- Bullet points for lists; numbered steps for processes.\n"
        "- TABLES (important): present ANY tabular, ranked, or comparative data as a Markdown "
        "table — `| Col | Col |` with a `|---|---|` separator row. This includes rankings "
        "(e.g. top/bottom performers), scores, metrics, comparisons, schedules, and line items. "
        "Pull the relevant figures from the source into the table; never render such data as "
        "prose or plain bullets when a table fits.\n"
        "- Structure: # for title, ## for main sections, ### for subsections.\n"
        "- A thorough document should have 8-12 sections. Do not truncate.\n\n"
        "GROUNDING: Use only facts from the provided data and research. "
        "Do not invent numbers, prices, or figures not in the source.\n\n"
        "Begin with a clear '# Title' line.\n"
        "CRITICAL: Output ONLY the document content itself. DO NOT include any conversational text, "
        "greetings, email sign-offs, or messages like 'Here is your report' or 'Best regards'. Just the document."
    ),
    "excel": (
        "You are a data analyst at EAI Systems Pvt. Ltd.\n"
        "Produce the data as markdown tables for an Excel workbook.\n\n"
        "FORMAT:\n"
        "- Use '## Sheet Name' before each table — this becomes the sheet name.\n"
        "- Always include a '## Summary' sheet first with key metrics.\n"
        "- Add 2-5 more sheets for rankings, breakdowns, detailed data.\n"
        "- Every table must have a header row and a |---|---| separator row.\n"
        "- Include ALL rows from the data — do not summarise or truncate.\n\n"
        "ANALYSIS: Before each table, add 1-2 sentences of key insight from that data.\n\n"
        "GROUNDING: Use only values from the source data and research. "
        "Do not invent figures not in the source.\n\n"
        "CRITICAL: Output ONLY the markdown tables and analysis. DO NOT include any conversational "
        "text, greetings, or email sign-offs."
    ),
    "csv": (
        "You are a data analyst at EAI Systems Pvt. Ltd.\n"
        "Produce the most important data as a single markdown table for a CSV file.\n\n"
        "FORMAT:\n"
        "- One table only — choose the most useful data.\n"
        "- Must have a header row and a |---|---| separator row.\n"
        "- Include ALL data rows — do not truncate.\n\n"
        "GROUNDING: Use only values from the source data. Do not invent figures.\n\n"
        "CRITICAL: Output ONLY the markdown table. DO NOT include conversational text."
    ),
    "ppt": (
        "You are a senior presentation strategist at EAI Systems Pvt. Ltd.\n"
        "Write the FULL CONTENT this presentation should cover, as detailed prose.\n\n"
        "Include:\n"
        "- The core message and story arc\n"
        "- All key data points, findings, and insights from the source\n"
        "- Section-by-section breakdown of what each part of the deck should say\n"
        "- Specific numbers, names, comparisons, and conclusions\n"
        "- Recommendations and next steps\n\n"
        "Be detailed — the slide planner will use this text to create 12-15 slides. "
        "The richer your content, the better the deck.\n\n"
        "GROUNDING: Use only facts from the provided data and research. Do not invent figures."
    ),
    "email": (
        "You are Sally, a capable general-purpose assistant at EAI Systems Pvt. Ltd.\n"
        "Produce the deliverable the user asked for, in clean Markdown (rendered to polished "
        "HTML before sending). DO THE TASK DIRECTLY — never describe how you WOULD do it, and "
        "never output a 'strategy', a 'key points to include' list, or an 'example structure'. "
        "Just produce the finished result.\n\n"
        "DRAFTING (highest priority): If the user explicitly asks YOU (Sally) to draft / write / "
        "compose / prepare an email, message, letter, reply, or post FOR THEM TO SEND, output "
        "THE ACTUAL PIECE, ready to use — the finished text itself, not advice about writing it.\n"
        "- DRAFTING only applies when the user is asking Sally to produce a piece of writing they "
        "will send themselves. Questions like 'what would X say about Y?', 'how would X describe Z?', "
        "'give me an idea about X', or any hypothetical/informational question are NOT drafting "
        "requests — answer them directly as a conversational reply, with NO draft template.\n"
        "- If it IS a draft addressed to a THIRD PARTY (e.g. a client), write it in the user's voice "
        "with its own subject line and closing, and sign it with a neutral placeholder like '[Your name]' "
        "— do NOT sign the draft as Sally. A one-line lead-in ('Here's a draft you can send:') is "
        "fine, and you may delimit the draft with a line of dashes.\n\n"
        "FORMATTING (Markdown, only when it genuinely helps):\n"
        "- Open with a one-line greeting to the SENDER (the person shown in 'EMAIL FROM:'), then "
        "lead straight into the answer or the deliverable. NEVER greet someone other than the sender "
        "— other people in the To/CC line are NOT the requester.\n"
        "- Headings '## ', bullets '- ', ordered '1.', and '**bold**' are for research/structured "
        "answers. A drafted email or message is usually plain prose — don't force headings onto it.\n"
        "- Keep paragraphs short; separate blocks with a blank line.\n\n"
        "QUALITY:\n"
        "- For research/data questions: be thorough and specific, cite figures and dates.\n"
        "- For advice/suggestions: give concrete recommendations.\n"
        "- Match depth to the request: simple question → concise; complex analysis → detailed.\n\n"
        "GROUNDING: Use facts from the attached document and web search. "
        "Do not invent figures not in the source.\n\n"
        "End YOUR covering message (not an embedded draft) with this exact signature:\n"
        "Best regards,\nSally | EAI Systems Pvt. Ltd.\nwww.eaisystems.com\n\n"
        "Write ONLY the email body (Markdown). Do not output JSON or code fences."
    ),
}


# ── Q&A fill constants ───────────────────────────────────────────────────────

_QA_SINGLE_THRESHOLD = 15   # ≤ this many questions → single-pass
_QA_BATCH_SIZE       = 10   # batch size when > threshold

_QA_ANSWER_PROMPT = (
    "You are a professional document analyst and response writer at EAI Systems Pvt. Ltd.\n"
    "You are given a list of questions extracted from a document. Answer EACH question "
    "clearly and professionally.\n\n"
    "CRITICAL RULES:\n"
    "- Answer EVERY question provided — do not skip any.\n"
    "- Use ONLY facts from the attached document context and web-search results.\n"
    "- Do NOT invent facts, prices, dates, or figures not in the source.\n"
    "- If a question cannot be answered from the available information, say: "
    "  'Based on the available information, this could not be determined.'\n"
    "- Keep answers concise but complete — typically 1-4 sentences per question.\n"
    "- Maintain professional tone appropriate for the document type.\n\n"
    "OUTPUT FORMAT — return a JSON array ONLY, no other text:\n"
    '[{"id": "<question_id>", "answer": "<your answer here>"}]\n\n'
    "Match every 'id' value exactly from the input."
)


def _run_qa_batch(questions: list, context_message: str, llm) -> dict:
    """Answer a batch of questions, return {id: answer} dict."""
    import json as _json
    from langchain_core.messages import SystemMessage, HumanMessage

    q_text = _json.dumps(
        [{"id": q["id"], "context": q.get("context", {}), "question": q["question"]}
         for q in questions],
        ensure_ascii=False, indent=2,
    )
    msg = f"{context_message}\n\nQUESTIONS TO ANSWER:\n{q_text}"

    from core.config import brand_text
    response = llm.invoke([
        SystemMessage(content=brand_text(_QA_ANSWER_PROMPT)),
        HumanMessage(content=msg),
    ])
    raw = extract_text_content(response)

    # Extract JSON array from response
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return {}
    try:
        answers = _json.loads(m.group())
        return {str(a["id"]): str(a.get("answer", "")) for a in answers}
    except Exception:
        return {}


def _run_qa_hybrid(questions: list, context_message: str, model_label: str) -> dict:
    """
    Hybrid batch strategy:
    ≤ _QA_SINGLE_THRESHOLD questions → single LLM call
    >  _QA_SINGLE_THRESHOLD questions → batches of _QA_BATCH_SIZE
    """
    import json as _json
    llm = build_agent_llm(model_label, temperature=0.3)
    all_answers = {}

    if len(questions) <= _QA_SINGLE_THRESHOLD:
        print(f"[QA] Single-pass: {len(questions)} questions")
        all_answers = _run_qa_batch(questions, context_message, llm)
    else:
        total_batches = (len(questions) + _QA_BATCH_SIZE - 1) // _QA_BATCH_SIZE
        print(f"[QA] Batch mode: {len(questions)} questions → {total_batches} batches of {_QA_BATCH_SIZE}")
        for i in range(0, len(questions), _QA_BATCH_SIZE):
            batch = questions[i: i + _QA_BATCH_SIZE]
            batch_num = i // _QA_BATCH_SIZE + 1
            print(f"[QA] Answering batch {batch_num}/{total_batches} ({len(batch)} questions)")
            batch_answers = _run_qa_batch(batch, context_message, llm)
            all_answers.update(batch_answers)

    print(f"[QA] Answered {len(all_answers)}/{len(questions)} questions")
    return all_answers


# ── Title extraction ──────────────────────────────────────────────────────────

def _extract_title(content: str, fallback: str = "Report") -> str:
    """Pull the first # heading from markdown as the document title."""
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else fallback


# ── Provenance renaming ───────────────────────────────────────────────────────

def _rename_with_provenance(path: str, model_label: str, task_number: int) -> str:
    try:
        if not (isinstance(path, str) and os.path.isfile(path)):
            return path
        d, fname = os.path.split(path)
        prefix = ""
        if model_label:
            prefix += f"{model_label.upper()}_"
        if task_number:
            prefix += f"T{task_number}_"
        if not prefix or fname.startswith(prefix):
            return path
        new_path = os.path.join(d, prefix + fname)
        os.replace(path, new_path)
        return new_path
    except Exception:
        return path


def _select_qa_target(paths: list[str], body_lower: str):
    """Pick which attachment is the questionnaire to FILL when several are sent.

    A reference PDF/Word/Excel attached for context usually has no detectable
    question/answer structure, while the actual questionnaire does. We try files
    in order — honouring an explicit format hint ("...in the excel") first — and
    return the first one that has real questions. Returns ``(file_path, qa_data)``;
    ``qa_data`` is ``None`` only when nothing was readable at all.
    """
    from tools.qa_filler import read_qa_structure
    import json as _json

    ext_hint = None
    if re.search(r"\bexcel\b|\bxlsx\b", body_lower):     ext_hint = ".xlsx"
    elif re.search(r"\bword\b|\bdocx\b", body_lower):    ext_hint = ".docx"
    elif re.search(r"\bcsv\b", body_lower):              ext_hint = ".csv"
    elif re.search(r"\bpdf\b", body_lower):              ext_hint = ".pdf"

    # Files matching the requested format come first; order is otherwise preserved.
    ordered = sorted(paths, key=lambda p: 0 if (ext_hint and p.lower().endswith(ext_hint)) else 1)

    fallback = None  # first readable-but-no-questions file (e.g. already filled)
    for p in ordered:
        try:
            d = _json.loads(read_qa_structure.invoke({"file_path": p}))
        except Exception:
            continue
        if d.get("error"):
            continue
        if d.get("total_questions", 0) > 0:
            return p, d
        if fallback is None:
            fallback = (p, d)
    return fallback


# ── Main agent ────────────────────────────────────────────────────────────────

def run(context: dict) -> dict:
    from core.config import PROPOSAL_AGENT_MODEL
    from langchain_core.messages import SystemMessage, HumanMessage
    from tools.web_search import serpapi_search, tavily_search
    from tools.doc_creator import create_document
    from tools.excel_creator import create_spreadsheet
    from tools.converter_tool import convert_document
    from tools.ppt_agent import generate_presentation

    agent_name  = "ASSISTANT"
    start       = time.time()

    body               = context.get("body", "")
    subject            = context.get("subject", "")
    sender             = context.get("sender", "")
    history            = context.get("history", [])
    thread_quoted      = (context.get("thread_quoted") or "").strip()
    attachment_context = context.get("attachment_context", "")
    source_truncation_note = (context.get("source_truncation_note") or "").strip()
    inbox_attachments  = context.get("inbox_attachments", []) or []
    task_number        = context.get("task_number", 0)
    task_intent        = str(context.get("task_intent") or "").strip().lower()
    task_deliverable_type = str(context.get("task_deliverable_type") or "").strip().lower()
    # Dynamic model: agent_config.yaml can pin a provider; fanout passes force_model.
    from core.config import get_agent_model
    _config_model = get_agent_model()
    model_label  = context.get("force_model") or _config_model or PROPOSAL_AGENT_MODEL
    _source = "fanout" if context.get("force_model") else ("agent_config.yaml" if _config_model else ".env default")
    print(f"[{agent_name}] Model: {model_label.upper()} (source: {_source})")
    body_lower         = body.lower()

    # ── Intent (LLM-decided, robust to phrasing) — classified once in the graph
    # and passed in; classify here only if run() is called standalone.
    if "deliver_to_folder" in context:
        _deliver     = bool(context.get("deliver_to_folder"))
        _read_folder = bool(context.get("read_from_folder"))
        _intake_name = context.get("intake_filename") or ("__latest__" if _read_folder else "")
        _needs_search = bool(context.get("needs_web_search", True))
        _calendar_action = context.get("calendar_action", "none")
        _llm_output_fmt = str(context.get("output_format") or "").strip().lower()
        # Fail-open: missing flags default True so a needed source is never dropped.
        _uses_attachment = bool(context.get("uses_attachment", True))
        _uses_thread     = bool(context.get("uses_thread", True))
        # Fail-closed: a deliberate save command, never inferred on ambiguity.
        _save_to_rag     = bool(context.get("save_to_rag", False))
        _uses_rag        = bool(context.get("uses_rag", True))
        _needs_multi_kb_lookup = bool(context.get("needs_multi_kb_lookup", False))
        _detected_lang   = str(context.get("detected_language") or "en").strip().lower()
        _content_lang    = str(context.get("content_language") or "").strip().lower()
    else:
        _intent      = classify_request_intent(body, model_label)
        _deliver     = _intent["deliver_to_folder"]
        _read_folder = _intent["read_from_folder"]
        _intake_name = _intent["intake_filename"] or ("__latest__" if _read_folder else "")
        _needs_search = _intent.get("needs_web_search", True)
        _calendar_action = _intent.get("calendar_action", "none")
        _llm_output_fmt = str(_intent.get("output_format") or "").strip().lower()
        _uses_attachment = bool(_intent.get("uses_attachment", True))
        _uses_thread     = bool(_intent.get("uses_thread", True))
        _save_to_rag     = bool(_intent.get("save_to_rag", False))
        _uses_rag        = bool(_intent.get("uses_rag", True))
        _needs_multi_kb_lookup = bool(_intent.get("needs_multi_kb_lookup", False))
        _detected_lang   = str(_intent.get("detected_language") or "en").strip().lower()
        _content_lang    = str(_intent.get("content_language") or "").strip().lower()

    # A MODIFY task means the caller (graph.py) already carried the prior
    # deliverable forward as an attachment specifically so this edit can see
    # it — that's authoritative, not a guess, so it must not be second-guessed
    # by the classifier's uses_attachment/uses_thread judgment (which only saw
    # the new message's own text and can easily decide a short edit request
    # like "expand the section about X" doesn't reference anything). Verified
    # this was a real, reproducible bug: without this override, a MODIFY
    # follow-up on a Word/PDF/Excel/CSV deliverable silently drops the prior
    # file and thread, and invents an unrelated, disconnected reply instead of
    # editing the actual document.
    if task_intent == "modify":
        _uses_attachment = True
        _uses_thread = True
        # Same authoritative-context principle applied to language: if this
        # edit doesn't restate "in Arabic" (classify_request_intent has no
        # memory of the thread — it only reads the new message), fall back to
        # the carried-forward prior deliverable's OWN language so an edit pass
        # doesn't silently flip an Arabic document back to English. Verified
        # this was real: "add a slide after slide 3" (no language mentioned)
        # regenerated an Arabic presentation entirely in English.
        if not _content_lang and attachment_context:
            _prior_lang = _detect_language_fallback(attachment_context)
            if _prior_lang in ("ar", "mixed"):
                _content_lang = "ar"
                print(f"[{agent_name}] MODIFY with no language restated → "
                      f"keeping prior deliverable's language (Arabic)")

    # ── Intake: pull a requested file from the preconfigured intake folder ────
    # An EMAIL ATTACHMENT always takes priority — "the above attached file" is an
    # attachment, not an intake-folder file, so never divert to the folder when
    # one was uploaded (the classifier can over-trigger read_from_folder).
    if _read_folder and _intake_name and not inbox_attachments:
        from tools.file_reader import read_attachment_text
        _ipath = _resolve_intake_file(_intake_name)
        if _ipath:
            _itext = read_attachment_text(_ipath)
            if _itext and not _itext.startswith(("Error", "Access denied", "File not found", "No file")):
                _block = f"File: {os.path.basename(_ipath)}\n{_itext}"
                attachment_context = (attachment_context + "\n\n---\n\n" + _block) if attachment_context else _block
                inbox_attachments = inbox_attachments + [_ipath]
                print(f"[{agent_name}] [INTAKE] Loaded {os.path.basename(_ipath)} from intake folder")
        else:
            from core.config import INTAKE_DIR
            return {"answer": (f"Hello,\n\nI couldn't find that file in the intake folder "
                               f"({INTAKE_DIR}). Please check the file name and that it has been "
                               f"placed there.\n\n{_sig()}"), "files": []}

    # ── "Save this to RAG" command — a deliberate save action, not a document
    # request, so it short-circuits here (same shape as the calendar flows
    # below) before any GATHER/WRITE/RENDER work happens.
    if _save_to_rag:
        try:
            _rag_answer = _run_rag_ingest_flow(
                body, inbox_attachments, attachment_context,
                message_id=context.get("message_id_header", ""),
                language=_detected_lang,
                history=history,
            )
        except Exception as e:
            # Deps installed but the embedding backend (Ollama) is down/unreachable,
            # or the vector store failed mid-write. Reply honestly instead of letting
            # this surface as a generic "something went wrong" apology.
            print(f"[{agent_name}] RAG save failed: {e}")
            _rag_answer = (f"Hello,\n\nI couldn't save this into the knowledge base — the "
                           f"local knowledge-base service doesn't seem to be running right "
                           f"now. Please let your administrator know, and I can try again "
                           f"once it's back.\n\n{_sig()}")
        log_agent_execution(
            session_id=context.get("session_id", ""),
            message_id=context.get("message_id_header", ""),
            agent_name=agent_name,
            agent_input=body[:500],
            agent_output=_rag_answer[:500],
            duration_ms=int((time.time() - start) * 1000),
        )
        return {"answer": _rag_answer, "files": []}

    # ── Phase 0: Detect what format the user wants ────────────────────────────
    # `qa_fill` and `meeting` are TASK MODES, not output formats — the LLM
    # `output_format` field can't express them (it only knows email/pdf/word/…),
    # so regex detection wins for those. Otherwise the LLM-decided format is
    # primary (it read the whole request); regex is the safety net when the LLM
    # left it blank or the call failed.
    _regex_output = _detect_output_type(body_lower)
    if _regex_output in ("qa_fill", "meeting"):
        output_type = _regex_output
    elif _llm_output_fmt in ("email", "pdf", "word", "excel", "csv", "ppt"):
        output_type = _llm_output_fmt
    else:
        output_type = _regex_output

    # qa_fill above is wording-only ("answer/fill the questions...") — it can't
    # tell apart "there's a real questionnaire attached with blanks to fill"
    # from "I typed my own questions in the body, just answer them" (now that
    # RAG exists, the second case is common and should get a normal, RAG-
    # grounded reply). The real test is whether an attachment ACTUALLY has a
    # fillable Q&A structure — checked here with inbox_attachments available,
    # via the same read_qa_structure probe Phase 2 would use. Real bug this
    # fixes: a sender who typed 3 questions in the body while referencing an
    # earlier-attached research PDF (not a form) got dead-ended in qa_fill's
    # "no unanswered questions found" branch instead of just being answered.
    _qa_probe = None
    _qa_fill_downgraded = False
    if output_type == "qa_fill":
        _qa_probe = _select_qa_target(inbox_attachments, body_lower) if inbox_attachments else (None, None)
        _qa_probe_data = _qa_probe[1]
        if _qa_probe_data is None or _qa_probe_data.get("error") or _qa_probe_data.get("total_questions", 0) == 0:
            output_type = (_llm_output_fmt if _llm_output_fmt in
                           ("email", "pdf", "word", "excel", "csv", "ppt") else "email")
            _qa_probe = None
            _qa_fill_downgraded = True
            print(f"[{agent_name}] qa_fill requested but no fillable Q&A structure found — "
                  f"answering as {output_type} instead")


    is_conversion = bool(re.search(
        r"convert\b.{0,40}(pdf|word|docx|excel|xlsx|csv)|"
        r"(pdf|word|docx|excel|xlsx|csv).{0,20}to.{0,20}(pdf|word|docx|excel|xlsx|csv)",
        body_lower,
    ))
    # PDF → Excel/CSV is NOT a mechanical conversion. pdfplumber's table extraction
    # (no OCR) finds clean grid tables only, so a normal/scanned PDF yields a BLANK
    # spreadsheet. Route it through the transformation path instead: the agent reads
    # the PDF text (with OCR fallback) and the LLM structures "the information" into
    # a real, populated spreadsheet, grounded on the document.
    # "convert/translate <attached doc> to Arabic/English" is a TRANSLATION
    # (content transformation), NOT a mechanical format conversion — even though
    # a nearby format word ("convert the WORD file to arabic") trips the
    # is_conversion regex above. A mechanical convert skips the WRITE phase
    # entirely, so the content_language instruction never runs and the file
    # comes back untranslated (the reported bug). An explicit target language
    # (_content_lang, reliably classified) is the signal: route through WRITE
    # and return the translated document in the SOURCE file's own format (Word
    # stays Word, PDF stays PDF) so the deliverable is usable. Checked FIRST so
    # it wins over the PDF→spreadsheet special case below.
    if is_conversion and _content_lang and inbox_attachments:
        _src_ext = os.path.splitext(inbox_attachments[0])[1].lstrip(".").lower()
        _ext_to_out = {"docx": "word", "doc": "word", "pdf": "pdf",
                       "xlsx": "excel", "xls": "excel", "csv": "csv",
                       "pptx": "ppt", "ppt": "ppt"}
        if _src_ext in _ext_to_out:
            is_conversion = False
            output_type = _ext_to_out[_src_ext]
            print(f"[{agent_name}] Translate-to-{_content_lang} of attached {_src_ext} → "
                  f"content transformation into {output_type} (not a mechanical format convert)")

    if is_conversion and inbox_attachments:
        _src_ext = os.path.splitext(inbox_attachments[0])[1].lstrip(".").lower()
        _tgt = _detect_conversion_target(body_lower, _src_ext)
        if _src_ext == "pdf" and _tgt in ("excel", "xlsx", "csv"):
            is_conversion = False
            output_type = "csv" if _tgt == "csv" else "excel"
            print(f"[{agent_name}] PDF→{_tgt}: using content transformation "
                  f"(reads PDF text incl. OCR, structures into a spreadsheet) — not mechanical extraction")
        # Any→PPT is NOT a mechanical conversion either: turning tabular/document
        # content into slides requires restructuring/summarization, which only the
        # PPT Strategist (generate_presentation) can do. The mechanical converter
        # (tools/converter_tool.py) has zero support for any->pptx target — every
        # branch there is pdf/docx/xlsx/csv only — so without this it fails with
        # "Unsupported conversion: <ext> -> ppt".
        elif _tgt in ("ppt", "pptx", "powerpoint"):
            is_conversion = False
            output_type = "ppt"
            print(f"[{agent_name}] {_src_ext}→PPT: using content transformation "
                  f"(PPT Strategist restructures content into slides) — not mechanical conversion")
    # Task deliverable continuity on a MODIFY: a follow-up like "expand the
    # section about X" or "add a slide after slide 3" often doesn't repeat the
    # file format, so output_type would otherwise default to "email" and the
    # edit would silently never touch the actual document. task_deliverable_type
    # is too coarse to fix this alone ("proposal" covers both word AND pdf) —
    # the carried-forward prior file's own extension (see Step 2 in graph.py's
    # agent_answer_node, which puts task.output_path into inbox_attachments for
    # MODIFY) tells us exactly what's being edited. Verified this was a real,
    # reproducible bug for word/pdf/excel/csv tasks (PPT alone had a narrower
    # fix below already).
    # EXCEPTION: only applies to an actual EDIT/regeneration request ("expand
    # the section about X", "add a slide", "update the pricing table") — NOT a
    # plain follow-up QUESTION about the attachment's content ("what's the
    # uptime SLA?"). Without the edit-verb check, ANY question asked in a
    # MODIFY-tagged thread with a carried-forward attachment got hijacked into
    # "regenerate that file" — even when the LLM's own classifier correctly
    # said output_format="email". Verified this was a real, reproducible bug:
    # a plain "quick follow-up, what is X and Y?" question triggered a live
    # web search and a whole new (wrong) PDF instead of just answering inline.
    # Also skips when qa_fill was just deliberately downgraded to "email"
    # above (same reasoning — the attachment is reference material, not a
    # document to regenerate).
    _edit_action_verb = re.search(
        r"\b(add|change|update|modify|expand|remove|replace|insert|delete|edit|"
        r"revise|correct|append|rewrite|restructure)\b",
        body_lower,
    )
    if (not is_conversion and output_type == "email" and task_intent == "modify"
            and inbox_attachments and not _qa_fill_downgraded and _edit_action_verb):
        _prior_ext = os.path.splitext(inbox_attachments[0])[1].lstrip(".").lower()
        _ext_to_type = {"pptx": "ppt", "ppt": "ppt", "docx": "word", "doc": "word",
                        "pdf": "pdf", "xlsx": "excel", "xls": "excel", "csv": "csv"}
        if _prior_ext in _ext_to_type:
            output_type = _ext_to_type[_prior_ext]
            print(f"[{agent_name}] Task deliverable is {output_type} (from prior file) → regenerating")

    # The task resolver can know this is a PPT task even when the latest
    # follow-up says only "create it" or "go ahead" (no prior FILE carried
    # forward yet, e.g. still mid-CREATE-conversation). Preserve that decision
    # so a presentation task cannot silently degrade into a plain email reply.
    _explicit_create_file = bool(re.search(
        r"\b(create|make|generate|build|prepare|produce|design|draft|develop)\b",
        body_lower,
    ))
    if (not is_conversion and output_type == "email" and task_deliverable_type == "ppt"
            and (task_intent == "modify" or _explicit_create_file)):
        output_type = "ppt"
        print(f"[{agent_name}] Task deliverable is PPT → generating presentation")

    # "Save the report to the folder" with no explicit format still needs a real
    # file to deliver — default to a PDF document so nothing is falsely "saved".
    if _deliver and output_type == "email" and not is_conversion:
        output_type = "pdf"
        print(f"[{agent_name}] Folder delivery requested with no format → generating PDF")

    # Explicit slide-count request (e.g. "make it 6 slides") — 0 if the sender
    # didn't ask for a specific count, meaning the planner uses its own default.
    _slide_count = _extract_slide_count(body_lower) if output_type == "ppt" else 0
    if _slide_count:
        print(f"[{agent_name}] Explicit slide count requested: {_slide_count}")

    print(f"[{agent_name}] Output type: {output_type} | Conversion: {is_conversion}")

    # ── Calendar actions (schedule / cancel) — confirmation-gated, short-circuit
    # Both creating and cancelling a meeting are consequential (they email real
    # attendees), so neither happens in one step: the agent PROPOSES the action
    # and only executes it after the user confirms in the thread. A pending
    # action is checked FIRST and regardless of output_type, because a follow-up
    # like "yes" or "the second one" carries no scheduling keywords of its own.
    _thread_id = context.get("thread_id", "")
    from core.database.sqlite_db import get_pending_action, clear_pending_action
    _pending = get_pending_action(_thread_id) if _thread_id else None
    # Treat the message as a confirmation only if it looks like one (short reply,
    # yes/no, a number, or a selection word). Otherwise the user moved on — drop
    # the stale pending so we don't hijack a new request.
    _looks_like_confirmation = bool(_pending and (
        len(body.split()) <= 8
        or re.search(r"\b(yes|yep|yeah|no|nope|confirm|go ?ahead|do it|send it|cancel|none|"
                     r"first|second|third|fourth|number|that one|leave it|keep it|"
                     r"skip|don'?t|proceed)\b|\b\d{1,2}\b", body_lower)
    ))
    # A genuine confirmation is a short yes/no/number reply that is NOT itself a
    # DIFFERENT calendar command. If the new message is a fresh calendar request
    # (different action) or unrelated, drop the stale pending and handle the new
    # intent instead of mis-reading it as a confirmation of the old one.
    _pending_kind = _pending.get("kind") if _pending else None
    _is_confirmation = bool(
        _pending and _looks_like_confirmation
        and _calendar_action in ("none", _pending_kind)
    )
    if _pending and not _is_confirmation:
        clear_pending_action(_thread_id)
        _pending = None

    # Route on the LLM-classified calendar_action (regex fallback is baked into
    # classify_request_intent). A pending confirmation takes precedence.
    _calendar_answer = None
    if _pending and _pending.get("kind") == "cancel":
        _calendar_answer = _run_cancel_confirm_flow(body, model_label, agent_name, _thread_id, _pending["payload"])
    elif _pending and _pending.get("kind") == "schedule":
        _calendar_answer = _run_schedule_confirm_flow(body, model_label, agent_name, _thread_id, _pending["payload"])
    elif _pending and _pending.get("kind") == "update":
        _calendar_answer = _run_update_confirm_flow(body, model_label, agent_name, _thread_id, _pending["payload"])
    elif _calendar_action == "update":
        _calendar_answer = _run_update_flow(body, model_label, agent_name, _thread_id)
    elif _calendar_action == "cancel":
        _calendar_answer = _run_cancel_flow(body, model_label, agent_name, _thread_id)
    elif _calendar_action == "schedule":
        _calendar_answer = _run_meeting_flow(body, model_label, agent_name, sender, _thread_id)

    if _calendar_answer is not None:
        print(f"[{agent_name}] Done — calendar flow, {len(_calendar_answer)} chars reply")
        log_agent_execution(
            session_id=context.get("session_id", ""),
            message_id=context.get("message_id_header", ""),
            agent_name=agent_name,
            agent_input=body[:500],
            agent_output=_calendar_answer[:500],
            duration_ms=int((time.time() - start) * 1000),
        )
        return {"answer": _calendar_answer, "files": []}

    # ── Phase 1: GATHER — search + collect (no file-creating tools) ───────────
    from datetime import date
    today_str = date.today().strftime("%A, %d %B %Y")
    current_year = date.today().year

    search_brief = ""
    search_prompt = (
        f"You are a research assistant gathering REAL, current data. TODAY'S DATE IS {today_str}.\n\n"
        "Your knowledge may be out of date, so DO NOT rely on memory for anything time-sensitive — "
        "search for it. When you write search queries, include the current year "
        f"({current_year}) or words like 'latest'/'current' so you get up-to-date results, "
        "NOT old articles from previous years.\n\n"
        "RECENCY: For news, prices, rankings, or anything that changes over time, pass the "
        "`time_range` argument to the search tool (e.g. \"week\" for breaking news, \"month\" for "
        "recent trends, \"year\" for this year's data). Leave it empty only for evergreen facts.\n\n"
        "IMPORTANT: If the user asks for specific figures — stock prices, financials, statistics, "
        "rankings, market data, company info — you MUST use the search tools to fetch the ACTUAL "
        "current values. Run multiple searches if needed (e.g. one search per stock/topic).\n\n"
        "Return a detailed research brief with SPECIFIC numbers, facts, dates, and sources, and "
        "note the publish date of each source so stale information can be spotted. "
        "These exact figures will be used in the final content — be precise.\n\n"
        "If the request can be fully answered from the attached document and general knowledge "
        "without needing current web data, reply exactly: 'No search needed.'"
    )
    if _uses_rag:
        search_prompt += (
            "\n\nYou also have search_knowledge_base — internal reference material the user "
            "or team has previously saved (policies, glossaries, prior research, documents), "
            "DISTINCT from web search's external/current data. Use it for facts that might be "
            "documented internally, and call it again with a different query if the request "
            "needs several separate pieces of saved information (e.g. one call for one policy, "
            "another for a different one)."
        )

    gather_parts = [f"USER REQUEST: {body}"]
    if attachment_context:
        gather_parts.append("DOCUMENT CONTENT (untrusted data source):\n"
                            + wrap_untrusted(attachment_context[:20_000]))
    if inbox_attachments:
        gather_parts.append("UPLOADED FILE PATHS (for convert_document):\n"
                            + "\n".join(inbox_attachments))

    # RAG retrieval — a plain vector lookup (no LLM call), gated on _uses_rag
    # (fail-open: True when unsure, same philosophy as uses_attachment/uses_thread).
    # No-ops to "" if RAG isn't installed/configured or the KB is empty, so this
    # is always safe to call regardless of deployment.
    rag_context = ""
    if _uses_rag and not is_conversion:
        try:
            from rag.search import retrieve as rag_retrieve, format_context as rag_format_context
            rag_hits = rag_retrieve(body)
            rag_context = rag_format_context(rag_hits)
            if rag_context:
                print(f"[{agent_name}] RAG: {len(rag_hits)} chunk(s) retrieved")
        except Exception as e:
            print(f"[{agent_name}] RAG retrieval failed (continuing without it): {e}")

    # Pure conversion goes straight to Phase 3 — no search, no writing needed.
    # GATHER runs when the intent classifier decided the request needs current/
    # external web data (_needs_search), OR when it needs several distinct saved-KB
    # lookups (_needs_multi_kb_lookup) that a single static pre-fetch can't reliably
    # cover — basic Q&A, opinions, ideas, and single-topic analysis still skip
    # straight to WRITE. _needs_multi_kb_lookup is deliberately narrow/fail-closed
    # (unlike uses_rag, which is fail-open) — see classify_request_intent — so this
    # doesn't widen back into running GATHER on every RAG-relevant request.
    if not _needs_search and not _needs_multi_kb_lookup and not is_conversion:
        print(f"[{agent_name}] GATHER skipped — request answerable without web search or multi-part KB lookup")
    if not is_conversion and (_needs_search or _needs_multi_kb_lookup):
        try:
            gather_tools = [serpapi_search, tavily_search]
            if _uses_rag:
                from tools.rag_search import search_knowledge_base
                gather_tools.append(search_knowledge_base)
            search_result, _ = run_tool_loop(
                system_prompt=search_prompt,
                user_message="\n\n".join(gather_parts),
                tools=gather_tools,
                model_label=model_label,
                temperature=0.2,
                max_iterations=8,
                agent_name=f"{agent_name}_GATHER",
            )
            if search_result and "no search needed" not in search_result.lower():
                search_brief = search_result
                print(f"[{agent_name}] Research gathered: {len(search_brief)} chars")
        except LLMUnavailableError:
            raise  # provider is down (quota/billing/auth) — surface it, don't continue
        except Exception as e:
            print(f"[{agent_name}] Gather phase failed (continuing): {e}")

    # ── Phase 2: WRITE — dedicated LLM call focused entirely on content ───────
    written_content = ""
    files: list[str] = []
    answer = ""

    if is_conversion:
        # Skip write phase for pure conversions
        pass

    elif output_type == "qa_fill":
        # ── Q&A FILL: extract structure → hybrid batch answer → fill document ──
        # Reaching here means the pre-check above (right after output_type was
        # decided) already confirmed a genuine fillable Q&A structure exists in
        # an attachment — reuse that probe (_qa_probe) instead of re-parsing it.
        from tools.qa_filler import write_qa_responses
        import json as _json

        qa_file, qa_data = _qa_probe
        questions = qa_data["questions"]
        print(f"[{agent_name}] QA fill: {len(questions)} questions in {os.path.basename(qa_file)}")

        # Context for answering (attachment + search)
        context_parts = [f"REQUEST: {body}"]
        if attachment_context:
            context_parts.append("DOCUMENT CONTEXT:\n" + wrap_untrusted(attachment_context[:30_000]))
        if search_brief:
            context_parts.append(f"RESEARCH FINDINGS:\n{search_brief}")
        context_message = "\n\n".join(context_parts)

        # Hybrid: single-pass or batch
        answer_map = _run_qa_hybrid(questions, context_message, model_label)

        # Detect desired output format (same as source by default)
        out_fmt = "same"
        if re.search(r"\bpdf\b", body_lower):
            out_fmt = "pdf"
        elif re.search(r"\bword\b|\bdocx\b", body_lower):
            out_fmt = "word"
        elif re.search(r"\bexcel\b|\bxlsx\b", body_lower):
            out_fmt = "excel"

        responses_json = _json.dumps(
            [{"id": qid, "answer": ans} for qid, ans in answer_map.items()]
        )
        result = write_qa_responses.invoke({
            "file_path":      qa_file,
            "responses_json": responses_json,
            "output_format":  out_fmt,
        })
        if isinstance(result, str) and os.path.isfile(result):
            files = [result]
            print(f"[{agent_name}] QA filled: {os.path.basename(result)}")
        else:
            print(f"[{agent_name}] write_qa_responses returned: {result}")

    else:
        write_prompt = _WRITE_PROMPTS.get(output_type, _WRITE_PROMPTS["email"])
        # Apply the configurable agent name (dashboard-overridable) — covers the
        # "You are Sally" persona and the signature line in the prompt.
        from core.config import get_agent_name, brand_text
        _aname = get_agent_name()
        if _aname != "Sally":
            write_prompt = write_prompt.replace("Sally", _aname)
        # Apply the configurable company identity — covers the "at EAI Systems
        # Pvt. Ltd." persona line and the signature's company name/website.
        write_prompt = brand_text(write_prompt)
        write_prompt += TOOL_LOOP_INJECTION_GUARD  # protect against prompt-injection in docs

        if output_type == "ppt" and _slide_count:
            # Overrides the static "12-15 slides" hint baked into _WRITE_PROMPTS["ppt"]
            # — that number is just a generic richness cue, not what the user asked
            # for. Content depth should roughly match the requested count so a
            # 3-slide ask doesn't get told to write enough material for 12-15.
            write_prompt += (f"\n\nSLIDE COUNT (explicit user request — overrides the default "
                             f"slide-count guidance above): write enough content for EXACTLY "
                             f"{_slide_count} slides, no more and no fewer.")

        # Reply in the same language as the request (detected in classify_request_intent).
        # Quoted thread history / attachments / KB content may be in a different
        # language than the actual ask — follow the ASK's language, not theirs.
        # Signature reminder appended to the language instruction below — writing in
        # Arabic otherwise tends to pull the model toward generic Arabic letter
        # templates (which commonly end in a placeholder like "[اسمك]"/"[Your name]"),
        # overriding the earlier "sign as {name}" instruction. Restating it AFTER the
        # language switch, in the same breath, keeps it from being the first casualty
        # of the translation. Uses the configured name (_aname), not a hardcoded one,
        # same as the rest of the prompt.
        _sig_reminder = (f" Still sign off as \"{_aname}\" exactly as given above (keep the name "
                         f"and company name in Latin script, unchanged) — translate only the "
                         f"closing phrase itself (e.g. 'Best regards' -> a natural Arabic closing "
                         f"like 'مع أطيب التحيات'), never replace the signer's name with a "
                         f"placeholder.")
        if _content_lang == "ar":
            # An EXPLICIT content-language request (e.g. "write me an essay in
            # Arabic about India") — distinct from, and takes priority over,
            # detected_lang (which is about the covering email's own language,
            # not what language the deliverable content itself was asked for).
            # Stated as its own, unambiguous, high-priority instruction because
            # burying "in Arabic" inside a business-report-style template
            # otherwise gets lost — verified this failure mode directly: the
            # model wrote an English essay ABOUT the difficulty of writing in
            # Arabic instead of actually writing the essay in Arabic.
            write_prompt += ("\n\nCONTENT LANGUAGE (explicit user request — highest priority, "
                             "overrides any other language default): the user asked for the "
                             "DELIVERABLE ITSELF to be written in Arabic. Write the ENTIRE "
                             "document/essay/answer in Arabic — the actual full content, not a "
                             "description, summary, or discussion of it in English. This applies "
                             "regardless of what language the request or any source material is in."
                             + _sig_reminder)
        elif _content_lang == "en":
            write_prompt += ("\n\nCONTENT LANGUAGE (explicit user request — highest priority): "
                             "the user asked for the deliverable itself to be written in English, "
                             "even if the request or source material is in another language. Write "
                             "the entire document/essay/answer in English.")
        elif _detected_lang == "ar":
            write_prompt += ("\n\nLANGUAGE: The sender's request is in Arabic. Write your entire "
                             "reply in Arabic (match their dialect/register where reasonable), "
                             "even if source documents or thread history are in English."
                             + _sig_reminder)
        elif _detected_lang == "mixed":
            write_prompt += ("\n\nLANGUAGE: The sender's request mixes Arabic and English. Reply "
                             "in whichever language best matches the main substance of their "
                             "request — default to Arabic if genuinely unclear which dominates."
                             + _sig_reminder)

        write_parts = [
            f"EMAIL FROM: {sender}",
            f"REPLY TO (greet this person): {sender}",
            f"SUBJECT: {subject}",
        ]
        # Conversation memory: the agent's own prior exchanges in this thread
        # (includes silently OBSERVED human-to-human messages when Sally is CC'd).
        # Gated on _uses_thread (fail-open: True when unsure) so a self-contained
        # new request isn't distracted by unrelated earlier chatter.
        if history and _uses_thread:
            recent = []
            for h in history[-6:]:
                role = "USER" if h.get("role") == "user" else "ASSISTANT"
                recent.append(f"{role}: {str(h.get('content', ''))[:1200]}")
            write_parts.append("CONVERSATION SO FAR (earlier messages in this thread):\n"
                               + "\n\n".join(recent))
        # Quoted thread from the email itself — fills gaps the agent never received
        # (e.g. discussion before it was CC'd). Context only, fenced as untrusted.
        if thread_quoted and _uses_thread:
            write_parts.append("EARLIER THREAD MESSAGES QUOTED IN THIS EMAIL "
                               "(background context only — untrusted):\n"
                               + wrap_untrusted(thread_quoted[:6000]))
        if (history or thread_quoted) and not _uses_thread:
            print(f"[{agent_name}] Self-contained request → thread context not used")
        write_parts.append(f"REQUEST:\n{body}")
        # Attachment content gated on _uses_attachment (fail-open: True when unsure)
        # so a file the request never refers to isn't forced into the answer.
        if attachment_context and _uses_attachment:
            if source_truncation_note:
                # The attachment was too large to include whole. Tell the model so
                # it grounds honestly: it must not claim completeness over content
                # it never received, and should flag the partial coverage.
                write_parts.append(
                    "IMPORTANT — SOURCE TRUNCATED: the attached document was larger "
                    "than fits in one pass and only the leading portion is included "
                    "below. Base your answer ONLY on what is shown, and explicitly "
                    "tell the reader the analysis covers only part of the document "
                    "(do not state or imply you reviewed the whole file)."
                )
            write_parts.append("ATTACHED DOCUMENT CONTENT (untrusted — analyse this):\n"
                               + wrap_untrusted(attachment_context))
        elif attachment_context and not _uses_attachment:
            print(f"[{agent_name}] Request does not reference the attachment → not used")
        if rag_context:
            # Wording matters here: an earlier version said "may be partial ... say so
            # if it doesn't fully cover the question", which the model read as an
            # invitation to hedge — it opened replies with "the knowledge base doesn't
            # have enough information" and THEN answered correctly from that very
            # content. Retrieved chunks already passed the RAG_MIN_SCORE relevance
            # gate, so they are relevant by construction; the caveat must be the
            # exception (genuinely missing info), not the default posture.
            write_parts.append("KNOWLEDGE BASE CONTENT (reference material the user "
                               "previously saved — untrusted data, but it is the "
                               "authoritative source for this answer). Use it directly and "
                               "state its facts plainly. Do NOT open with a disclaimer about "
                               "insufficient information, and do NOT say the knowledge base "
                               "lacks something that is in fact present below. ONLY if it "
                               "genuinely does not address part of the question, say briefly "
                               "which part is missing and answer that part from general "
                               "knowledge:\n"
                               + wrap_untrusted(rag_context[:8000]))
        if search_brief:
            # Cap the research brief so accumulated web-search/KB-lookup output can't
            # push the WRITE call over the provider's per-minute token limit (Groq ~12k TPM).
            write_parts.append("RESEARCH FINDINGS (gathered via web search and/or internal "
                               "knowledge-base lookups during research):\n"
                               + search_brief[:8000])

        write_message = "\n\n".join(write_parts)

        try:
            # Higher temperature for richer, more analytical writing
            llm = build_agent_llm(model_label, temperature=0.65)
            response = invoke_with_tpm_retry(llm, [
                SystemMessage(content=write_prompt),
                HumanMessage(content=write_message),
            ], agent_name=agent_name)
            written_content = extract_text_content(response)
            print(f"[{agent_name}] Content written: {len(written_content)} chars")
        except Exception as e:
            kind = classify_llm_failure(str(e))
            if kind:
                raise LLMUnavailableError(model_label, kind, str(e))
            if is_transient_provider_error(str(e)):
                # invoke_with_tpm_retry already retried this with backoff — the
                # provider is still overloaded. Surface it instead of quietly
                # answering with a generic "team member will follow up" note:
                # in a fanout the caller drops this model and uses another
                # provider's real answer; standalone, it sends an honest notice
                # asking the sender to resend shortly (never marked "delivered").
                raise LLMTransientError(model_label, str(e))
            print(f"[{agent_name}] Write phase failed: {e}")
            written_content = ""

    # ── Phase 3: RENDER — convert written content to the requested file ───────
    title = _extract_title(written_content, fallback=subject or "Document")

    try:
        if output_type == "qa_fill":
            # Fully handled in Phase 2 above — that block already set `answer`
            # (an early-exit message) or `files` (the filled document) directly,
            # never `written_content`. Without this no-op, qa_fill fell through
            # to the catch-all `else: answer = written_content` below, which
            # SILENTLY WIPED OUT whatever qa_fill had already set (since
            # written_content is always "" for this path) — discarding a
            # perfectly good "no unanswered questions found" message and
            # replacing it with the generic "unable to complete" apology.
            # Verified this was a real, reproducible bug, not local-model-specific.
            pass

        elif is_conversion:
            # Pure format conversion — target is the format named AFTER "to"
            # (the destination), not the first format word (usually the source).
            _src_ext = (os.path.splitext(inbox_attachments[0])[1].lstrip(".").lower()
                        if inbox_attachments else "")
            fmt = _detect_conversion_target(body_lower, _src_ext)
            print(f"[{agent_name}] Conversion target format: {fmt}")
            for path in inbox_attachments:
                result = convert_document.invoke({"file_path": path, "output_format": fmt})
                if isinstance(result, str) and os.path.isfile(result):
                    files.append(result)
                else:
                    print(f"[{agent_name}] Conversion did not produce a file: {result}")

        elif output_type in ("pdf", "word") and written_content:
            result = create_document.invoke({
                "title":   title,
                "content": written_content,
                "format":  output_type,
            })
            if isinstance(result, str) and os.path.isfile(result):
                files.append(result)
                print(f"[{agent_name}] Rendered {output_type.upper()}: {os.path.basename(result)}")
            else:
                print(f"[{agent_name}] {output_type.upper()} generation did not produce a file: {result}")

        elif output_type in ("excel", "csv") and written_content:
            result = create_spreadsheet.invoke({
                "title":   title,
                "content": written_content,
                "format":  output_type,
            })
            if isinstance(result, str) and os.path.isfile(result):
                files.append(result)
                print(f"[{agent_name}] Rendered {output_type.upper()}: {os.path.basename(result)}")
            else:
                print(f"[{agent_name}] {output_type.upper()} generation did not produce a file: {result}")

        elif output_type == "ppt" and written_content:
            template = "sales_blue"
            if re.search(r"sales.*orange|orange.*template", body_lower):
                template = "sales_orange"
            elif re.search(r"marketing|dark.*template", body_lower):
                template = "marketing"
            elif re.search(r"internal|technical|light.*template", body_lower):
                template = "internal"

            result = generate_presentation.invoke({
                "topic":       title,
                "context":     written_content,
                "template":    template,
                "model_label": model_label,
                "task_number": task_number,
                "content_language": _content_lang,
                "slide_count": _slide_count,
            })
            if isinstance(result, str) and os.path.isfile(result):
                files.append(result)
                print(f"[{agent_name}] Rendered PPT: {os.path.basename(result)}")
            else:
                print(f"[{agent_name}] PPT generation did not produce a file: {result}")

        else:
            # Email reply — written_content IS the answer
            answer = written_content

    except Exception as e:
        print(f"[{agent_name}] Render phase failed: {e}")

    # ── Build covering email for file outputs ─────────────────────────────────
    if files and not answer:
        fnames = ", ".join(os.path.basename(f) for f in files)
        answer = (
            f"Hello,\n\nPlease find the requested file attached.\n\n"
            f"File: {fnames}\n\n" + _sig()
        )
    elif not answer:
        answer = (
            "Thank you for your email. I was unable to complete the request "
            "automatically — a team member will follow up shortly.\n\n" + _sig()
        )

    # Tag output files with model + task number
    files = [_rename_with_provenance(f, model_label, task_number) for f in files]

    # Folder delivery: if requested, drop the file(s) into the configured folder
    # and reply with a confirmation instead of attaching them to the email.
    if files and _deliver:
        answer, files = _deliver_to_folder(files)
        print(f"[{agent_name}] Delivered to folder instead of emailing.")

    print(f"[{agent_name}] Done — {len(files)} file(s), {len(answer)} chars reply")
    duration_ms = int((time.time() - start) * 1000)
    log_agent_execution(
        session_id=context.get("session_id", ""),
        message_id=context.get("message_id_header", ""),
        agent_name=agent_name,
        agent_input=body[:500],
        agent_output=f"{answer[:500]} | files={files}",
        duration_ms=duration_ms,
    )
    return {"answer": answer, "files": files}
