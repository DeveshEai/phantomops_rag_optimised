# PhantomOps — How It All Works

> A plain-English walkthrough of the complete system architecture.

---

## 📧 Step 1 — Someone Emails Sally

You (or anyone) send an email to Sally's inbox (Gmail or Outlook). That's all you do.  
The system picks it up automatically **every 30 seconds**.

---

## 🔀 Step 2 — The Router Reads & Understands the Email

The **Email Router & Intent Classifier** reads your email and answers:

- *Who is this for?* (Sally? Someone else?)
- *What do they want?* (A Word doc? A PPT? Schedule a meeting? Arabic document?)
- *What language?* (English or Arabic?)

---

## ❓ Step 3 — Simple or Complex?

The system makes a routing decision:

| **Simple Request** | **Complex Request** |
|---|---|
| "Make me a Word doc on topic X" | "Make an Arabic proposal + schedule a meeting" |
| "Create a PPT about Y" | "Research Company Z and schedule a call" |
| "Build an Excel report" | Any Arabic content or calendar actions |

---

## 🤖 Simple Path → Assistant Agent (Original Full Pipeline)

Three steps run in sequence, **all powered by the LLM (Groq)**:

1. **🔍 GATHER** — Searches the web for real, current information on the topic
2. **✍️ WRITE** — The AI actually *writes* the full document content from scratch
3. **📄 RENDER** — Creates the actual `.docx`, `.xlsx`, or `.pptx` file

> This is why your Word doc on "Pega AI v25/v26" contains real research — not a template.

---

## 🧠 Complex Path → Central Orchestrator (Multi-Agent Team)

Three specialist AIs work **simultaneously**:

| Agent | What it does |
|---|---|
| **💼 Business Agent** | Researches topics and drafts structured proposals |
| **🌐 Arabic Agent** | Translates and localises content into Modern Standard Arabic |
| **📅 Scheduling Agent** | Parses meeting requests, validates attendees, books calendar invites |

---

## 📁 Step 4 — Output File Created

Whichever path ran, it produces one of:

- **📝 Word** `.docx` — professional document
- **📊 Excel** `.xlsx` — spreadsheet / report
- **🎨 PowerPoint** `.pptx` — presentation (rotates through **10 designer themes**)

---

## 📤 Step 5 — Auto-Reply Sent

The file is attached to an email reply and sent back to you **automatically**.  
You never had to open the app.

---

## 🗄️ Supporting Systems (always running in the background)

| System | What it does |
|---|---|
| **SQLite Database** | Remembers every email, task, and file ever processed |
| **ChromaDB RAG** | Your internal knowledge base (requires Ollama to be running) |
| **Pexels API** | Fetches real photos for PPT slides |
| **Groq AI** | The AI brain doing all the writing and thinking |
| **`agent_config.yaml`** | Your settings file — name, company, mailbox, etc. |

---

## Routing Summary

```
Incoming Email
      │
      ▼
Email Router & Intent Classifier
      │
      ├─── Simple (Word / Excel / PPT / General)
      │         │
      │         ▼
      │    Assistant Agent
      │    GATHER → WRITE → RENDER
      │
      └─── Complex (Arabic / Meeting / Calendar)
                │
                ▼
          Central Orchestrator
          ┌──────────────────────────────┐
          │ Business │ Arabic │ Schedule │
          └──────────────────────────────┘
                │
                ▼
          Output File (Word / Excel / PPT)
                │
                ▼
          📤 Auto email reply with attachment
```

---

*Last updated: 2026-10-30*
