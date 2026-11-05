# PhantomOps Engineering Roadmap

**Document Version:** 2.0  
**Updated:** October 30, 2026  
**Status:** Post-Phase 10 Production Refactor  

---

## 1. Completed in This Refactor

* [x] **DAG Task Planning & Dependency Resolution**: `core/orchestrator/task_plan.py` with DFS cycle detection and topological sorting.
* [x] **Safe Asynchronous DAG Executor**: `core/orchestrator/executor.py` with configurable concurrency limits, task timeouts, bounded retries with exponential backoff, and non-idempotent action protections.
* [x] **Specialized Capability Agents**:
  - `agents/business_agent.py`: Market research, proposal authoring, multi-sheet Excel and Word deliverables.
  - `agents/arabic_agent.py`: Translation, RTL review, and proposal localization preserving numbers and tables.
  - `agents/scheduling_agent.py`: Provider abstraction, domain filtering, draft invitations, and mock calendar testing.
* [x] **Protected PPT Pipeline Invariance**: Fully preserved 10-theme rotation, Marp compilation, Pexels integration, and slide reviewer untouched.
* [x] **Artifact Lifecycle Management**: `core/artifacts/artifact_manager.py` with SHA-256 integrity checks, 0-byte detection, and duplicate delivery prevention.
* [x] **Thread-Level Context Isolation**: Thread-local storage (`threading.local()`) for web search URL and page caching to prevent concurrent subtask clobbering.
* [x] **Database Schema Migrations**: Added `subtasks`, `subtask_events`, and `artifacts` tables to SQLite with cascading foreign keys and auto-recovery.
* [x] **Streamlit Visual Dashboard**: Enhanced `ui/dashboard.py` with multi-agent DAG subtask status indicators and validated artifact cards.
* [x] **Automated Regression Suite**: 40 unit and integration tests covering Scenarios A through F.

---

## 2. Next Milestones & Prioritized Next Steps

### Milestone 1: Multi-Tenant RBAC & Knowledge Base Partitioning
* **Priority:** High
* **Goal:** Currently, the local ChromaDB vector store and SQLite database are shared across mail threads. Add workspace/tenant partition keys to `rag_sources` and query filters in Chroma to strictly enforce tenant-level isolation when multi-tenant deployments are enabled.

### Milestone 2: Native Microsoft Graph Calendar Webhooks
* **Priority:** Medium
* **Goal:** Add bidirectional push webhooks for calendar invite responses (Accepted/Declined/Tentative) to automatically update task status when recipients respond to meeting invites.

### Milestone 3: Real-Time Streamlit DAG WebSocket Updates
* **Priority:** Medium
* **Goal:** Enable live streaming of subtask status updates in the Streamlit UI via server-sent events or WebSockets so operators can watch subtasks complete in real time during email processing.

### Milestone 4: Optional PPT Enhancements (Post-Freeze)
* **Priority:** Low (Deferred)
* **Goal:** Once the PPT freeze window closes, explore adding animated slide transitions in Marp and automated speaker note synthesis.
