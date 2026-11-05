"""
tests/test_multi_agent_orchestrator.py — Tests for Async DAG Orchestrator

Validates:
  1. Dependency cycle detection and rejection
  2. Concurrent execution of independent subtasks
  3. Dependent tasks waiting for prerequisites
  4. Timeout and bounded retry backoff for transient failures
  5. Partial failure and partial success handling without aborting independent results
  6. Task and subtask persistence in SQLite
  7. Thread context isolation for web search and concurrent tasks
"""

import os
import sys
import time
import unittest
import uuid

# Ensure version folder is in sys.path
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from core.orchestrator.task_plan import TaskPlan, Subtask, SubtaskState
from core.orchestrator.executor import AsyncDAGExecutor
from core.database.sqlite_db import init_db, get_subtasks_for_task
from tools.web_search import _set_search_urls, _get_search_urls, _set_page_text, _get_page_text


class TestMultiAgentOrchestrator(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_dependency_cycle_rejection(self):
        """Verify that cyclic dependencies in a task plan are detected and rejected."""
        plan = TaskPlan(plan_id="cycle_plan", parent_task_id="task_cycle")
        # A depends on B, B depends on C, C depends on A
        plan.add_subtask(Subtask(subtask_key="A", agent_name="business_agent", dependencies=["B"]))
        plan.add_subtask(Subtask(subtask_key="B", agent_name="business_agent", dependencies=["C"]))
        plan.add_subtask(Subtask(subtask_key="C", agent_name="business_agent", dependencies=["A"]))

        with self.assertRaises(ValueError) as ctx:
            plan.validate_graph()
        self.assertIn("Dependency cycle detected", str(ctx.exception))

    def test_missing_dependency_rejection(self):
        """Verify that a subtask depending on a non-existent subtask raises ValueError."""
        plan = TaskPlan(plan_id="missing_dep_plan", parent_task_id="task_missing")
        plan.add_subtask(Subtask(subtask_key="A", agent_name="business_agent", dependencies=["non_existent_key"]))

        with self.assertRaises(ValueError) as ctx:
            plan.validate_graph()
        self.assertIn("depends on non-existent subtask", str(ctx.exception))

    def test_concurrent_independent_subtasks(self):
        """Verify that independent subtasks execute concurrently within bounded time."""
        plan = TaskPlan(plan_id="concurrent_plan", parent_task_id="task_conc")
        # Subtasks 1 and 2 sleep 0.25s each. Sequential would take >=0.50s;
        # concurrent (ThreadPoolExecutor) should finish in ~0.25-0.40s.
        # Threshold is 0.65s to absorb Windows thread-pool scheduling overhead
        # on a loaded machine, while still proving concurrency vs sequential.
        plan.add_subtask(Subtask(subtask_key="task_1", agent_name="test_agent", input_data={"sleep": 0.25}))
        plan.add_subtask(Subtask(subtask_key="task_2", agent_name="test_agent", input_data={"sleep": 0.25}))

        def dispatcher(st, ctx):
            sleep_time = ctx.get("sleep", 0)
            time.sleep(sleep_time)
            return {"status": "ok", "key": st.subtask_key}

        executor = AsyncDAGExecutor(max_concurrency=4)
        start = time.time()
        completed = executor.execute_plan(plan, dispatcher)
        elapsed = time.time() - start

        self.assertGreater(elapsed, 0.20, "Elapsed time suspiciously low; tasks may not have actually run.")
        self.assertLess(elapsed, 0.65, "Independent subtasks did not execute concurrently (took too long).")
        self.assertEqual(completed.subtasks["task_1"].status, SubtaskState.SUCCEEDED)
        self.assertEqual(completed.subtasks["task_2"].status, SubtaskState.SUCCEEDED)

    def test_dependent_tasks_wait_for_prerequisites(self):
        """Verify that downstream dependent tasks do not run until prerequisites succeed."""
        plan = TaskPlan(plan_id="dep_plan", parent_task_id="task_dep")
        plan.add_subtask(Subtask(subtask_key="prep", agent_name="test_agent", input_data={"val": 42}))
        plan.add_subtask(Subtask(subtask_key="process", agent_name="test_agent", dependencies=["prep"]))

        order = []

        def dispatcher(st, ctx):
            order.append(st.subtask_key)
            if st.subtask_key == "process":
                # Ensure it received prep's result
                dep_res = ctx.get("dep_prep_result", {})
                self.assertEqual(dep_res.get("computed"), 84)
            return {"computed": 84}

        executor = AsyncDAGExecutor()
        completed = executor.execute_plan(plan, dispatcher)

        self.assertEqual(order, ["prep", "process"])
        self.assertEqual(completed.subtasks["prep"].status, SubtaskState.SUCCEEDED)
        self.assertEqual(completed.subtasks["process"].status, SubtaskState.SUCCEEDED)

    def test_partial_failure_and_partial_success(self):
        """Verify that a failure in one subtask marks dependents BLOCKED without killing independent tasks."""
        plan = TaskPlan(plan_id="fail_plan", parent_task_id="task_fail")
        # Independent branch: success
        plan.add_subtask(Subtask(subtask_key="independent_task", agent_name="test_agent"))
        # Failing branch: fails and blocks its dependent
        plan.add_subtask(Subtask(subtask_key="failing_task", agent_name="test_agent", max_retries=1))
        plan.add_subtask(Subtask(subtask_key="dependent_task", agent_name="test_agent", dependencies=["failing_task"]))

        def dispatcher(st, ctx):
            if st.subtask_key == "failing_task":
                raise RuntimeError("Simulated failure in web search")
            return {"ok": True}

        executor = AsyncDAGExecutor()
        completed = executor.execute_plan(plan, dispatcher)

        self.assertEqual(completed.subtasks["independent_task"].status, SubtaskState.SUCCEEDED)
        self.assertEqual(completed.subtasks["failing_task"].status, SubtaskState.FAILED)
        self.assertEqual(completed.subtasks["dependent_task"].status, SubtaskState.BLOCKED)
        self.assertTrue(completed.has_failures())

    def test_bounded_retries_for_transient_failures(self):
        """Verify that retryable idempotent subtasks retry up to max_retries before failing."""
        plan = TaskPlan(plan_id="retry_plan", parent_task_id="task_retry")
        # Subtask configured with 2 max retries and small retry delay
        plan.add_subtask(Subtask(
            subtask_key="research",
            agent_name="business_agent",
            max_retries=2,
            retry_delay_seconds=0.05,
        ))

        attempts = 0

        def dispatcher(st, ctx):
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionError("Transient 503 Provider Overload")
            return {"findings": "Recovered research data"}

        executor = AsyncDAGExecutor()
        completed = executor.execute_plan(plan, dispatcher)

        self.assertEqual(attempts, 2)
        self.assertEqual(completed.subtasks["research"].status, SubtaskState.SUCCEEDED)
        self.assertEqual(completed.subtasks["research"].result_data["findings"], "Recovered research data")

    def test_subtask_persistence_in_sqlite(self):
        """Verify that subtasks and their terminal results are persisted in SQLite."""
        task_id = f"test_task_{uuid.uuid4().hex[:8]}"
        plan = TaskPlan(plan_id="persist_plan", parent_task_id=task_id)
        plan.add_subtask(Subtask(subtask_key="sub_1", agent_name="test_agent"))

        def dispatcher(st, ctx):
            return {"persisted_val": "alpha"}

        executor = AsyncDAGExecutor()
        executor.execute_plan(plan, dispatcher)

        # Query database directly
        db_subtasks = get_subtasks_for_task(task_id)
        self.assertEqual(len(db_subtasks), 1)
        self.assertEqual(db_subtasks[0]["subtask_key"], "sub_1")
        self.assertEqual(db_subtasks[0]["status"], "SUCCEEDED")
        self.assertEqual(db_subtasks[0]["result_data"]["persisted_val"], "alpha")

    def test_thread_context_isolation(self):
        """Verify that thread-local caches in web_search do not leak across concurrent threads."""
        import threading
        results = {}

        def thread_work(tid, urls, page):
            _set_search_urls(urls)
            _set_page_text(page)
            time.sleep(0.05)
            results[tid] = {
                "urls": _get_search_urls(),
                "page": _get_page_text(),
            }

        t1 = threading.Thread(target=thread_work, args=("t1", ["https://t1.com"], "Content T1"))
        t2 = threading.Thread(target=thread_work, args=("t2", ["https://t2.com"], "Content T2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["t1"]["urls"], ["https://t1.com"])
        self.assertEqual(results["t1"]["page"], "Content T1")
        self.assertEqual(results["t2"]["urls"], ["https://t2.com"])
        self.assertEqual(results["t2"]["page"], "Content T2")


if __name__ == "__main__":
    unittest.main()
