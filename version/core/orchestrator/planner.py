"""
core/orchestrator/planner.py — Intent-driven DAG Task Planner

Analyzes user requests, context, and output requirements to construct an optimal,
dependency-ordered TaskPlan for the specialized agents.
"""

import uuid
import re
from typing import Any, Dict, Optional
from core.orchestrator.task_plan import TaskPlan, Subtask, SubtaskState
from core.content_types import is_arabic_text


class DAGPlanner:
    """Plans multi-agent execution graphs with explicit dependency ordering."""

    def create_plan(
        self,
        request_text: str,
        parent_task_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        """
        Analyze the incoming user request and build the DAG execution plan.
        """
        ctx = context or {}
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        plan = TaskPlan(plan_id=plan_id, parent_task_id=parent_task_id)

        text_lower = request_text.lower()

        # Detect intent flags
        needs_arabic = (
            "arabic" in text_lower
            or "بالعربية" in request_text
            or is_arabic_text(request_text)
            or ctx.get("content_language") == "ar"
            or ctx.get("detected_language") == "ar"
        )

        needs_meeting = (
            any(w in text_lower for w in ["meeting", "schedule", "calendar", "invite", "arrange a call", "sync"])
            or ctx.get("calendar_action") in ("schedule", "confirm")
        )

        needs_ppt = (
            any(w in text_lower for w in ["ppt", "presentation", "slide", "deck", "powerpoint"])
            or ctx.get("output_format") == "ppt"
            or ctx.get("task_deliverable_type") == "ppt"
        )

        needs_excel = (
            any(w in text_lower for w in ["excel", "spreadsheet", "xlsx", "csv", "table", "financial model"])
            or ctx.get("output_format") in ("xlsx", "csv")
        )

        needs_proposal = (
            any(w in text_lower for w in ["proposal", "sow", "quote", "report", "briefing", "summary"])
            or ctx.get("output_format") in ("docx", "pdf", "word")
            or ctx.get("task_deliverable_type") == "proposal"
        )

        # ── Scenario C: Research + Meeting Request ─────────────────────────────
        if needs_meeting and ("research" in text_lower or "prospect" in text_lower or "company" in text_lower):
            # 1. Independent Research
            plan.add_subtask(Subtask(
                subtask_key="research",
                agent_name="business_agent",
                input_data={"query": request_text, "use_web": True, "use_rag": True},
                dependencies=[],
            ))
            # 2. Independent Meeting Scheduling Parse & Validation (runs concurrently)
            plan.add_subtask(Subtask(
                subtask_key="meeting_scheduling",
                agent_name="scheduling_agent",
                input_data={
                    "request_text": request_text,
                    "sender": ctx.get("sender", ""),
                    "user_confirmed": ctx.get("user_confirmed", False),
                },
                dependencies=[],
            ))
            # 3. Final Synthesis waiting on both
            plan.add_subtask(Subtask(
                subtask_key="synthesis",
                agent_name="orchestrator",
                input_data={"request_text": request_text},
                dependencies=["research", "meeting_scheduling"],
            ))

        # ── Meeting Scheduling Only ───────────────────────────────────────────
        elif needs_meeting and not needs_proposal and not needs_ppt:
            plan.add_subtask(Subtask(
                subtask_key="meeting_scheduling",
                agent_name="scheduling_agent",
                input_data={
                    "request_text": request_text,
                    "sender": ctx.get("sender", ""),
                    "user_confirmed": ctx.get("user_confirmed", False),
                },
                dependencies=[],
            ))

        # ── Scenario B: Arabic Sales Proposal ─────────────────────────────────
        elif needs_arabic and (needs_proposal or needs_ppt or "proposal" in text_lower):
            target_fmt = "ppt" if needs_ppt else ("xlsx" if needs_excel else "docx")
            # 1. Research
            plan.add_subtask(Subtask(
                subtask_key="research",
                agent_name="business_agent",
                input_data={"query": request_text, "use_web": True, "use_rag": True},
                dependencies=[],
            ))
            # 2. Draft Proposal (English content grounded in research)
            plan.add_subtask(Subtask(
                subtask_key="draft_proposal",
                agent_name="business_agent",
                input_data={"topic": request_text, "client_name": ctx.get("sender", "")},
                dependencies=["research"],
            ))
            # 3. Arabic Translation / Localization stage
            plan.add_subtask(Subtask(
                subtask_key="arabic_translation",
                agent_name="arabic_agent",
                input_data={"target_format": target_fmt},
                dependencies=["draft_proposal"],
            ))
            # 4. Render deliverable
            plan.add_subtask(Subtask(
                subtask_key="render_deliverable",
                agent_name="business_agent",
                input_data={"output_format": target_fmt, "task_number": ctx.get("task_number", 1)},
                dependencies=["arabic_translation"],
            ))

        # ── Arabic Translation / Text transformation Only ─────────────────────
        elif needs_arabic and not needs_proposal:
            plan.add_subtask(Subtask(
                subtask_key="arabic_translation",
                agent_name="arabic_agent",
                input_data={"text": request_text},
                dependencies=[],
            ))

        # ── Scenario F: PowerPoint Presentation ───────────────────────────────
        elif needs_ppt:
            plan.add_subtask(Subtask(
                subtask_key="research",
                agent_name="business_agent",
                input_data={"query": request_text, "use_web": True, "use_rag": True},
                dependencies=[],
            ))
            plan.add_subtask(Subtask(
                subtask_key="draft_presentation",
                agent_name="business_agent",
                input_data={"topic": request_text},
                dependencies=["research"],
            ))
            plan.add_subtask(Subtask(
                subtask_key="render_deliverable",
                agent_name="business_agent",
                input_data={"output_format": "ppt", "task_number": ctx.get("task_number", 1)},
                dependencies=["draft_presentation"],
            ))

        # ── Scenario A: Business Research & Proposal / Document ───────────────
        else:
            output_fmt = "xlsx" if needs_excel else ("docx" if needs_proposal else "text")
            plan.add_subtask(Subtask(
                subtask_key="research",
                agent_name="business_agent",
                input_data={"query": request_text, "use_web": True, "use_rag": True},
                dependencies=[],
            ))
            if output_fmt != "text":
                plan.add_subtask(Subtask(
                    subtask_key="draft_proposal",
                    agent_name="business_agent",
                    input_data={"topic": request_text, "client_name": ctx.get("sender", "")},
                    dependencies=["research"],
                ))
                plan.add_subtask(Subtask(
                    subtask_key="render_deliverable",
                    agent_name="business_agent",
                    input_data={"output_format": output_fmt, "task_number": ctx.get("task_number", 1)},
                    dependencies=["draft_proposal"],
                ))

        # Validate before returning
        plan.validate_graph()
        return plan
