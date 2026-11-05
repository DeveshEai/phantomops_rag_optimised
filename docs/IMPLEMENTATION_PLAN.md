# Implementation Plan: PhantomOps Multi-Agent Async Execution Upgrade

**Plan Version:** 1.0  
**Status:** In Progress (Incremental Engineering)  
**Lead AI Systems Architect & Senior Python/Test Engineer:** Antigravity  

---

## Architecture Overview

```mermaid
flowchart TD
    A[Incoming Request: Email / UI / Test] --> B[Master Skill Router]
    B --> C[Email Skill Lifecycle: create_session -> load_memory -> resolve_task]
    C --> D[Central Multi-Agent Orchestrator]
    
    subgraph PlanDAG ["Task Planner & Execution DAG"]
        D --> E[Build Task Plan & Dependencies]
        E --> F{Validate Cycle-Free DAG}
        F --> G[Async Scheduler / ThreadPoolExecutor]
    end
    
    subgraph SpecializedAgents ["Specialized Capability Agents"]
        G --> H1[Sales / Business Agent\n- Research: Web / RAG\n- Proposal & Deliverable Prep]
        G --> H2[Arabic Language Agent\n- Translation & Localization\n- RTL / Linguistic Review]
        G --> H3[Meeting Scheduling Agent\n- Provider Abstraction\n- Availability / Drafts / Mock]
    end

    subgraph ProtectedPPT ["FROZEN Protected Pipeline"]
        H1 -.->|PPT Deck Request| I[tools/ppt_agent.py & tools/ppt_rota.py]
    end

    subgraph DocumentRenderers ["Shared Document Workflows"]
        H1 --> J1[tools/doc_creator.py (Word/PDF)]
        H1 --> J2[tools/excel_creator.py (Excel/CSV)]
    end

    subgraph OutputDelivery ["Artifact Lifecycle & Persistence"]
        H1 & H2 & H3 & ProtectedPPT & DocumentRenderers --> K[Artifact Lifecycle Manager\n- Checksum & Size Validation\n- Idempotency & Delivery Guard]
        K --> L[SQLite DB: tasks, subtasks, task_events]
        K --> M[Response Delivery: Reply / Folder]
    end
```

---

## Incremental Implementation Steps

### Step 1: Database & Persistence Extension (Phase 2 & Phase 7)
* Add `subtasks` and `subtask_events` tables to SQLite schema in `version/core/database/sqlite_db.py`.
* Ensure backward-compatible migrations for existing `tasks` table without dropping data.
* Implement transactional task state transitions:
  `PENDING`, `READY`, `RUNNING`, `SUCCEEDED`, `FAILED`, `RETRY_WAIT`, `CANCELLED`, `BLOCKED`.
* Implement subtask result and idempotency caching.

### Step 2: Shared Content Contract & Context Isolation (Phase 4 & Phase 6)
* Create `version/core/content_types.py` defining lightweight structured content representations (`StructuredContent`, `ContentSection`, `ContentTable`, `ArtifactMetadata`).
* Implement task-scoped execution context (`TaskExecutionContext`) to isolate memory, web search caches, and conversation history between concurrent tasks.
* Sanitize formula injection inputs for Excel (`tools/excel_creator.py`) and validate tables and headings for Word/PDF (`tools/doc_creator.py`).

### Step 3: Central Orchestrator & Async DAG Engine (Phase 1 & Phase 2)
* Create `version/core/orchestrator/`:
  - `task_plan.py`: Data models for `Subtask`, `TaskPlan`, dependency resolution, and cycle detection (topological sort).
  - `planner.py`: Intent analysis to generate structured multi-agent execution plans.
  - `executor.py`: Asynchronous execution engine using bounded concurrency (`ThreadPoolExecutor`), dependency resolution, timeouts, retry backoff, and partial failure handling.
  - `orchestrator.py`: Main coordinator coordinating specialized agents, collecting outputs, and rendering final deliverables.

### Step 4: Specialized Capability Agents (Phase 1)
* **Sales / Business Agent** (`version/agents/business_agent.py`):
  - Handles business research (web search, RAG), proposal generation, financial/data spreadsheets, and reports.
  - Seamlessly calls the existing protected PPT pipeline when presentation output is needed.
* **Arabic Language Agent** (`version/agents/arabic_agent.py`):
  - Translates text/sections into Modern Standard Arabic while preserving numbers, technical terminology, and dates.
  - RTL formatting support and linguistic review.
  - Operates as a transformation/review step in multi-agent chains (e.g. Research $\to$ Business Proposal $\to$ Arabic Agent $\to$ Word/PDF).
* **Meeting Scheduling Agent** (`version/agents/scheduling_agent.py`):
  - Provider abstraction `CalendarProvider` with `GoogleCalendarProvider`, `OutlookCalendarProvider`, and `MockCalendarProvider`.
  - Parses participant requirements, date/time, and duration.
  - Drafts meeting invitations, verifies internal attendee domain limits, and safely halts if required info or approval is missing.

### Step 5: Integration with Email Skill & Supervisor (Phase 1 & Phase 3)
* Wire `core/orchestrator/orchestrator.py` into `agents/supervisor.py` and `skills/email_skill/graph.py` while preserving existing fallback and single-provider behaviors.
* Maintain complete backward-compatibility for existing email handling, watermark tracking, and CLI workflows.

### Step 6: Artifact Lifecycle & Delivery (Phase 5)
* Implement `version/core/artifacts/artifact_manager.py`:
  - Enforce existence, non-empty check, SHA-256 calculation, and duplicate delivery prevention.
  - Maintain consistent records in SQLite.

### Step 7: Streamlit UI Visibility (Phase 8)
* Update `version/ui/dashboard.py` to add a Multi-Agent DAG Execution view showing subtasks, current statuses, dependencies, and artifact verification cards.

### Step 8: Automated Regression & Unit Testing (Phase 9)
* Implement tests covering Scenarios A through F and all test requirements:
  - Routing and business-only workflow.
  - Arabic transformation and dependency ordering.
  - Meeting scheduling (missing info, success, failure, mock provider).
  - Parallel subtask execution & cycle detection.
  - Retry, timeouts, and partial failure handling.
  - Duplicate-action prevention & idempotency.
  - Task persistence & restart recovery.
  - Document generation (Word, Excel formulas/tables, PDF).
  - PPT pipeline regression (ensuring PPT outputs and rota remain 100% intact).
