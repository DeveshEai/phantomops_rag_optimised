# Test Strategy & Verification Suite

**System:** PhantomOps Enterprise Autonomous Operations System  
**Test Harness:** Standard Python `unittest` framework  
**Environment:** Python 3.11.9 (`.venv`)  
**Date:** October 30, 2026  

---

## 1. Testing Philosophy & Scope

All tests are deterministic, hermetic, and require no live paid API keys or active external mailbox/calendar accounts. Mock providers (`MockCalendarProvider`) and local fallback synthesis guarantee that the test suite runs offline and rapidly in CI/CD pipelines.

---

## 2. Test Suites & Coverage

| Test Module | Coverage & Test Cases | Status |
|---|---|---|
| **`tests/test_marp_image_pipeline.py`** | 14 tests covering Marp Markdown compilation, 10-theme PPT rota, Pexels caching, image byte validation, local asset priority, and broken link pruning. | **14/14 PASS** |
| **`tests/test_multi_agent_orchestrator.py`** | 8 tests covering DAG cycle rejection, missing dependency detection, concurrent independent subtasks, dependency ordering, partial failure with BLOCKED cascading, bounded exponential backoff retries, SQLite subtask persistence, and thread-local context isolation. | **8/8 PASS** |
| **`tests/test_specialized_agents.py`** | 11 tests covering Business research/proposals (Scenario A), Arabic translation & review, Arabic sales proposal DAG chain (Scenario B), Research + Meeting concurrency (Scenario C), Scheduling missing parameter detection (`needs_info`), draft proposal guard, mock calendar booking, mock calendar failure, and domain allowlist filtering. | **11/11 PASS** |
| **`tests/test_document_and_artifact_workflows.py`** | 7 tests covering Word document rendering, Excel CSV/Formula injection protection (`=cmd...`), numeric/negative value preservation, multi-sheet generation, PDF conversion failure safety, 0-byte file rejection, and SHA-256 duplicate delivery prevention (Scenario E). | **7/7 PASS** |

**Total Suite Result:** **40/40 tests executed and passing** in ~34.8 seconds.

---

## 3. How to Run Tests Locally

```powershell
# From the version directory:
& "..\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

Or individual suites:
```powershell
& "..\.venv\Scripts\python.exe" -m unittest tests/test_multi_agent_orchestrator.py -v
& "..\.venv\Scripts\python.exe" -m unittest tests/test_specialized_agents.py -v
& "..\.venv\Scripts\python.exe" -m unittest tests/test_document_and_artifact_workflows.py -v
& "..\.venv\Scripts\python.exe" -m unittest tests/test_marp_image_pipeline.py -v
```
