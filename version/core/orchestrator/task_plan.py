"""
core/orchestrator/task_plan.py — Task Planning and Dependency Graph (DAG)

Defines task and subtask representations, execution states, and cycle-free
dependency graph validation for safe concurrent multi-agent coordination.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid
from datetime import datetime


class SubtaskState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


@dataclass
class Subtask:
    subtask_key: str
    agent_name: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str = ""
    parent_task_id: str = ""
    status: SubtaskState = SubtaskState.PENDING
    attempt_count: int = 0
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "parent_task_id": self.parent_task_id,
            "subtask_key": self.subtask_key,
            "agent_name": self.agent_name,
            "input_data": self.input_data,
            "dependencies": self.dependencies,
            "status": self.status.value if isinstance(self.status, SubtaskState) else str(self.status),
            "attempt_count": self.attempt_count,
            "max_retries": self.max_retries,
            "result_data": self.result_data,
            "error_message": self.error_message,
            "idempotency_key": self.idempotency_key,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class TaskPlan:
    plan_id: str
    parent_task_id: str
    subtasks: Dict[str, Subtask] = field(default_factory=dict)

    def add_subtask(self, subtask: Subtask):
        subtask.plan_id = self.plan_id
        subtask.parent_task_id = self.parent_task_id
        self.subtasks[subtask.subtask_key] = subtask

    def validate_graph(self) -> bool:
        """
        Validate that all dependencies exist and the dependency graph has NO cycles.
        Raises ValueError if a cycle or unknown dependency is detected.
        """
        # 1. Verify that all declared dependencies exist in the plan
        for key, st in self.subtasks.items():
            for dep in st.dependencies:
                if dep not in self.subtasks:
                    raise ValueError(f"Subtask '{key}' depends on non-existent subtask '{dep}'.")

        # 2. Cycle detection using Depth-First Search (3-color marking)
        # 0 = unvisited, 1 = visiting (in recursion stack), 2 = visited
        visited: Dict[str, int] = {k: 0 for k in self.subtasks}
        cycle_path: List[str] = []

        def dfs(node: str) -> bool:
            visited[node] = 1
            cycle_path.append(node)
            for dep in self.subtasks[node].dependencies:
                if visited[dep] == 1:
                    cycle_path.append(dep)
                    return True  # Cycle found
                if visited[dep] == 0:
                    if dfs(dep):
                        return True
            cycle_path.pop()
            visited[node] = 2
            return False

        for node in self.subtasks:
            if visited[node] == 0:
                if dfs(node):
                    cycle_str = " -> ".join(cycle_path)
                    raise ValueError(f"Dependency cycle detected in task plan: {cycle_str}")

        return True

    def get_ready_subtasks(self) -> List[Subtask]:
        """
        Return all subtasks that are PENDING or READY and whose prerequisite
        dependencies have all SUCCEEDED.
        """
        ready = []
        for key, st in self.subtasks.items():
            if st.status in (SubtaskState.PENDING, SubtaskState.READY):
                deps_met = True
                for dep_key in st.dependencies:
                    dep_st = self.subtasks.get(dep_key)
                    if not dep_st or dep_st.status != SubtaskState.SUCCEEDED:
                        deps_met = False
                        break
                if deps_met:
                    st.status = SubtaskState.READY
                    ready.append(st)
        return ready

    def update_blocked_subtasks(self):
        """
        Mark subtasks as BLOCKED if any of their prerequisite dependencies
        failed or were cancelled.
        """
        for key, st in self.subtasks.items():
            if st.status in (SubtaskState.PENDING, SubtaskState.READY):
                for dep_key in st.dependencies:
                    dep_st = self.subtasks.get(dep_key)
                    if dep_st and dep_st.status in (SubtaskState.FAILED, SubtaskState.CANCELLED, SubtaskState.BLOCKED):
                        st.status = SubtaskState.BLOCKED
                        st.error_message = f"Prerequisite subtask '{dep_key}' failed or was blocked."
                        break

    def is_complete(self) -> bool:
        """Return True when all subtasks have reached a terminal state."""
        terminal = {
            SubtaskState.SUCCEEDED,
            SubtaskState.FAILED,
            SubtaskState.CANCELLED,
            SubtaskState.BLOCKED,
        }
        return all(st.status in terminal for st in self.subtasks.values())

    def has_failures(self) -> bool:
        """Return True if any subtask ended in FAILED or BLOCKED."""
        return any(st.status in (SubtaskState.FAILED, SubtaskState.BLOCKED) for st in self.subtasks.values())
