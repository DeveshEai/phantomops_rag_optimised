# Sally — Database Schema & Relationships

**Status:** Living document
**Last updated:** 2026-05-28
**Legend:** `████` = current tables &nbsp;&nbsp; `░░░░` = Phase 2 additions (planned)

---

## Entity Relationship Diagram

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                       SALLY — DATABASE SCHEMA (ERD)                            ║
║                                                                                ║
║   ████ = current tables      ░░░░ = Phase 2 additions (planned)                ║
╚══════════════════════════════════════════════════════════════════════════════════╝


  ┌─────────────────────────────────────────┐
  │ ████  sessions                          │
  ├─────────────────────────────────────────┤
  │ PK  id          VARCHAR(36)   UUID      │
  │     thread_id   VARCHAR(255)  UNIQUE    │ ← Outlook conversation ID
  │     sender      VARCHAR(255)            │ ← first person who emailed
  │     created_at  DATETIME                │
  │     updated_at  DATETIME                │
  └──────────────┬──────────────────────────┘
                 │ 1
                 │
        ┌────────┼────────────────────────────────────────────────────┐
        │        │                                                    │
        │ N      │ N                                               N  │
        │        │                                                    │
  ┌─────▼────────────────────────┐   ┌───────────────────────────────▼──────────────────────┐
  │ ████  messages               │   │ ████  email_drafts                                    │
  ├──────────────────────────────┤   ├──────────────────────────────────────────────────────┤
  │ PK  id          BIGINT  AI   │   │ PK  id                    BIGINT  AI                 │
  │ FK  session_id  VARCHAR(36)  │   │ FK  session_id            VARCHAR(36) ───────────────┼──► sessions.id
  │     role        ENUM         │   │     model_name            VARCHAR(100)               │
  │                 user|asst    │   │     thread_id             VARCHAR(255)               │
  │     content     TEXT         │   │     message_id            VARCHAR(255)               │ ← Outlook msg ID
  │     created_at  DATETIME     │   │     sender                VARCHAR(255)               │
  │                              │   │     subject               VARCHAR(255)               │
  │  ░░ sender      VARCHAR(255) │   │     user_question         TEXT                       │
  │  ░░ task_id     VARCHAR(36)  │   │     ai_draft              TEXT                       │
  └──────────────────────────────┘   │     inbox_attachment_path VARCHAR(1000)               │ ← user's files
                                     │     attachment_path        VARCHAR(500)               │ ← generated files
                                     │     agents_used           VARCHAR(500)               │
                                     │     status                ENUM                       │
                                     │                  pending|sent|rejected|failed         │
                                     │     created_at            DATETIME                   │
                                     │     updated_at            DATETIME                   │
                                     │                                                      │
                                     │  ░░ task_id               VARCHAR(36)               │
                                     │                                                      │
                                     │ UNIQUE KEY (message_id, model_name)                  │
                                     └──────────────────────────────────────────────────────┘


  ┌──────────────────────────────────────────────────────┐
  │ ████  agent_executions                               │
  ├──────────────────────────────────────────────────────┤
  │ PK  id           BIGINT  AI                          │
  │ FK  session_id   VARCHAR(36) ──────────────────────────► sessions.id
  │     message_id   VARCHAR(255)                        │ ← Outlook msg ID (ref only, not FK)
  │     agent_name   VARCHAR(100)                        │ e.g. SUPERVISOR, RESEARCH, PPT
  │     agent_input  TEXT                                │
  │     agent_output TEXT                                │
  │     duration_ms  INT                                 │
  │     status       ENUM  success|error|skipped         │
  │     created_at   DATETIME                            │
  └──────────────────────────────────────────────────────┘


░░░░░░░░░░░░░░░░░░░░░░░░  PHASE 2 — PLANNED  ░░░░░░░░░░░░░░░░░░░░░░░░

  ┌─────────────────────────────────────────────────────────────┐
  │ ░░░░  tasks                                                 │
  ├─────────────────────────────────────────────────────────────┤
  │ PK  id               VARCHAR(36)  UUID                      │
  │     task_number      INT                                    │ ← per-thread seq (T1, T2…)
  │     thread_id        VARCHAR(255)                           │ ← Outlook conversation ID
  │ FK  session_id       VARCHAR(36) ──────────────────────────────► sessions.id
  │     task_label       VARCHAR(255)                           │ e.g. "AI Presentation"
  │     deliverable_type ENUM  ppt|proposal|info|general        │
  │     status           ENUM  open|delivered|in_revision|closed│
  │     created_by       VARCHAR(255)                           │ ← email of requester
  │     output_path      VARCHAR(1000)                          │ ← current live file on disk
  │     created_at       DATETIME                               │
  │     updated_at       DATETIME                               │
  │                                                             │
  │ UNIQUE KEY (thread_id, task_number)                         │
  └───────────────┬─────────────────────────────────────────────┘
                  │ 1
                  │
                  │ N
  ┌───────────────▼─────────────────────────────────────────────┐
  │ ░░░░  task_events                                           │
  ├─────────────────────────────────────────────────────────────┤
  │ PK  id          BIGINT  AI                                  │
  │ FK  task_id     VARCHAR(36) ──────────────────────────────────► tasks.id
  │     event_type  ENUM  created|change_request|delivered|failed│
  │     sender      VARCHAR(255)                                │ ← who triggered this event
  │     message     TEXT                                        │ ← cleaned message body
  │     model_name  VARCHAR(100)                                │ ← which model produced output
  │     file_path   VARCHAR(1000)                               │ ← file from this event
  │     created_at  DATETIME                                    │
  └─────────────────────────────────────────────────────────────┘
```

---

## Relationship Summary

| From | To | Type | Key | Notes |
|---|---|---|---|---|
| `sessions` | `messages` | 1 → N | `messages.session_id` | ON DELETE CASCADE |
| `sessions` | `email_drafts` | 1 → N | `email_drafts.session_id` | ON DELETE CASCADE |
| `sessions` | `agent_executions` | 1 → N | `agent_executions.session_id` | ON DELETE CASCADE |
| `sessions` | `tasks` *(Phase 2)* | 1 → N | `tasks.session_id` | ON DELETE CASCADE |
| `tasks` | `task_events` *(Phase 2)* | 1 → N | `task_events.task_id` | Full audit trail |
| `tasks` | `email_drafts` *(Phase 2)* | 1 → N | `email_drafts.task_id` | Column added to existing table |
| `tasks` | `messages` *(Phase 2)* | 1 → N | `messages.task_id` | Column added to existing table |

---

## Unique Constraints

| Table | Unique Key | Purpose |
|---|---|---|
| `sessions` | `thread_id` | One session per Outlook conversation thread |
| `email_drafts` | `(message_id, model_name)` | One draft per email per model — prevents duplicate sends |
| `tasks` *(Phase 2)* | `(thread_id, task_number)` | Task numbers are sequential per thread (T1, T2, T3…) |

---

## Common Query Patterns

```
Thread arrives     →  sessions.thread_id              → get/create session_id
Load history       →  messages.session_id              → ordered conversation turns
Save draft         →  email_drafts.(message_id,        → deduplication guard
                                    model_name)
Get prior files    →  email_drafts.(thread_id,         → carry-forward context
                                    status='sent')
Trace agents       →  agent_executions.message_id      → who ran, how long, result

[Phase 2]
Resolve task       →  tasks.(thread_id, task_number)   → identify T1, T2…
Task history       →  task_events.task_id              → full change audit trail
Task's live file   →  tasks.output_path                → current deliverable on disk
```

---

## Design Notes

- **`sessions` is the hub.** Every table anchors to `session_id`. One Outlook conversation thread = one session.
- **`email_drafts` is the audit log.** Every generated output — whether pending, sent, or rejected — is preserved for traceability. The `UNIQUE KEY (message_id, model_name)` ensures each model writes exactly one draft per inbound email.
- **`agent_executions` is observability.** Not linked to a specific draft — it records every subagent that fired for a given message regardless of outcome.
- **`message_id` is a soft reference.** `agent_executions.message_id` references the Outlook message ID but is not a formal FK — this is intentional so agent logs survive even if the draft is deleted.
- **Phase 2 adds a second hub: `tasks`.** Tasks sit inside a session and own their own event log (`task_events`). Files belong to tasks, not people — one live file per task, overwritten in place on each revision.
