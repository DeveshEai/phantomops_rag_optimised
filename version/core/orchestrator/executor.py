"""
core/orchestrator/executor.py — Safe Asynchronous DAG Execution Engine

Coordinates concurrent execution of independent subtasks respecting dependencies,
enforcing concurrency limits, timeouts, bounded exponential backoff retries for
transient failures, non-idempotent action protections, and SQLite state persistence.
"""

import time
import concurrent.futures
from typing import Any, Callable, Dict, Optional, Set
from datetime import datetime

from core.orchestrator.task_plan import TaskPlan, Subtask, SubtaskState
from core.database.sqlite_db import save_subtask, update_subtask_status, log_subtask_event


# Safe actions that can be automatically retried upon transient failure
_IDEMPOTENT_ACTIONS = {
    "research",
    "web_search",
    "rag_lookup",
    "draft_proposal",
    "translate_arabic",
    "review_arabic",
    "render_document",
    "render_spreadsheet",
    "render_presentation",
}


class AsyncDAGExecutor:
    """
    Executes a TaskPlan DAG with bounded concurrency, dependency gates,
    and durable SQLite persistence.
    """

    def __init__(self, max_concurrency: int = 4, default_timeout_seconds: float = 60.0):
        self.max_concurrency = max(1, int(max_concurrency))
        self.default_timeout_seconds = float(default_timeout_seconds)

    def execute_plan(
        self,
        plan: TaskPlan,
        agent_dispatcher: Callable[[Subtask, Dict[str, Any]], Dict[str, Any]],
        shared_context: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        """
        Execute the provided TaskPlan to completion.
        1. Validates cycle-free DAG
        2. Persists initial subtask records to SQLite
        3. Fuses and dispatches ready subtasks concurrently up to max_concurrency
        4. Enforces timeouts, retries, and failure blocking
        5. Persists terminal states to SQLite
        """
        # Validate dependency graph
        plan.validate_graph()

        shared_ctx = dict(shared_context or {})
        
        # Persist all subtasks initially
        for st in plan.subtasks.values():
            try:
                save_subtask(st.to_dict())
            except Exception as e:
                print(f"[DAGExecutor] Warning: Failed to save subtask {st.subtask_key}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            active_futures: Dict[concurrent.futures.Future, Subtask] = {}

            while not plan.is_complete():
                # Check for blocked tasks if any dependencies failed
                plan.update_blocked_subtasks()

                # Get ready subtasks
                ready_subtasks = plan.get_ready_subtasks()

                # Submit ready subtasks up to available capacity
                for st in ready_subtasks:
                    if len(active_futures) >= self.max_concurrency:
                        break

                    st.status = SubtaskState.RUNNING
                    st.started_at = datetime.utcnow().isoformat()
                    st.attempt_count += 1
                    update_subtask_status(
                        st.id,
                        status="RUNNING",
                        attempt_count=st.attempt_count,
                    )
                    log_subtask_event(st.id, "RUNNING", f"Attempt {st.attempt_count} started.")

                    # Pass dependency results into input data for downstream tasks
                    merged_inputs = dict(st.input_data)
                    for dep_key in st.dependencies:
                        dep_subtask = plan.subtasks.get(dep_key)
                        if dep_subtask and dep_subtask.result_data:
                            merged_inputs[f"dep_{dep_key}_result"] = dep_subtask.result_data

                    fut = pool.submit(agent_dispatcher, st, {**shared_ctx, **merged_inputs})
                    active_futures[fut] = st

                if not active_futures:
                    # No active tasks and nothing ready: either done or deadlocked/blocked
                    break

                # Wait for at least one future to complete
                done, _ = concurrent.futures.wait(
                    active_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                    timeout=self.default_timeout_seconds,
                )

                if not done:
                    # Timeout reached for all active futures
                    for fut, st in list(active_futures.items()):
                        st.status = SubtaskState.FAILED
                        st.error_message = f"Execution timed out after {self.default_timeout_seconds}s"
                        st.completed_at = datetime.utcnow().isoformat()
                        update_subtask_status(st.id, "FAILED", error_message=st.error_message)
                        log_subtask_event(st.id, "FAILED", st.error_message)
                        del active_futures[fut]
                    continue

                for fut in done:
                    st = active_futures.pop(fut)
                    try:
                        result = fut.result()
                        # Verify if result indicates failure
                        if isinstance(result, dict) and result.get("error"):
                            raise RuntimeError(result.get("error"))

                        st.status = SubtaskState.SUCCEEDED
                        st.result_data = result if isinstance(result, dict) else {"output": result}
                        st.completed_at = datetime.utcnow().isoformat()
                        update_subtask_status(st.id, "SUCCEEDED", result_data=st.result_data)
                        log_subtask_event(st.id, "SUCCEEDED", "Subtask completed successfully.")
                    except Exception as exc:
                        err_msg = str(exc)
                        # Check retry eligibility: must be idempotent and under max retries
                        is_idempotent = st.subtask_key in _IDEMPOTENT_ACTIONS or st.agent_name in _IDEMPOTENT_ACTIONS
                        if is_idempotent and st.attempt_count < st.max_retries:
                            backoff = st.retry_delay_seconds * (2 ** (st.attempt_count - 1))
                            st.status = SubtaskState.RETRY_WAIT
                            st.error_message = f"Transient failure: {err_msg} (Retrying in {backoff}s)"
                            update_subtask_status(st.id, "RETRY_WAIT", error_message=st.error_message)
                            log_subtask_event(st.id, "RETRY_WAIT", st.error_message)
                            time.sleep(backoff)
                            st.status = SubtaskState.PENDING  # will be re-picked as READY
                        else:
                            st.status = SubtaskState.FAILED
                            st.error_message = f"Failed permanently: {err_msg}"
                            st.completed_at = datetime.utcnow().isoformat()
                            update_subtask_status(st.id, "FAILED", error_message=st.error_message)
                            log_subtask_event(st.id, "FAILED", st.error_message)

        return plan
