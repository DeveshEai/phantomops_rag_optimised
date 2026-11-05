"""
core/orchestrator/orchestrator.py — Central Multi-Agent Orchestrator

The central coordination engine for PhantomOps. Dispatches requests across
specialized capabilities (Business, Arabic, Scheduling), executes independent
subtasks concurrently via the DAG engine, enforces dependencies, validates
deliverables, and produces a single unified final deliverable or email response.
"""

import os
from typing import Any, Dict, List, Optional

from core.orchestrator.task_plan import TaskPlan, Subtask, SubtaskState
from core.orchestrator.planner import DAGPlanner
from core.orchestrator.executor import AsyncDAGExecutor
from core.artifacts.artifact_manager import ArtifactLifecycleManager
from agents.business_agent import BusinessAgent
from agents.arabic_agent import ArabicLanguageAgent
from agents.scheduling_agent import MeetingSchedulingAgent, CalendarProvider, MockCalendarProvider
from core.config import get_email_signature


class CentralOrchestrator:
    """Coordinates specialized agents and manages async DAG execution."""

    def __init__(
        self,
        planner: Optional[DAGPlanner] = None,
        executor: Optional[AsyncDAGExecutor] = None,
        calendar_provider: Optional[CalendarProvider] = None,
        max_concurrency: int = 4,
    ):
        self.planner = planner or DAGPlanner()
        self.executor = executor or AsyncDAGExecutor(max_concurrency=max_concurrency)
        self.business_agent = BusinessAgent()
        self.arabic_agent = ArabicLanguageAgent()
        self.scheduling_agent = MeetingSchedulingAgent(provider=calendar_provider)
        self.artifact_manager = ArtifactLifecycleManager()

    def _agent_dispatcher(self, subtask: Subtask, context: Dict[str, Any]) -> Dict[str, Any]:
        """Routes a single subtask to the appropriate specialized agent."""
        agent = subtask.agent_name
        key = subtask.subtask_key
        inputs = subtask.input_data

        # ── 1. Business Agent Capability ──────────────────────────────────────
        if agent == "business_agent":
            if key == "research":
                query = inputs.get("query") or context.get("body", "")
                return self.business_agent.perform_research(
                    query=query,
                    use_web=inputs.get("use_web", True),
                    use_rag=inputs.get("use_rag", True),
                )
            elif key in ("draft_proposal", "draft_presentation"):
                research_res = context.get("dep_research_result", {})
                research_context = research_res.get("findings", "")
                topic = inputs.get("topic") or context.get("subject", "Business Deliverable")
                client = inputs.get("client_name") or context.get("sender", "")
                return self.business_agent.prepare_proposal(
                    topic=topic,
                    research_context=research_context,
                    client_name=client,
                )
            elif key == "render_deliverable":
                # Look for proposal from arabic translation or draft proposal
                prop = (
                    context.get("dep_arabic_translation_result")
                    or context.get("dep_draft_proposal_result")
                    or context.get("dep_draft_presentation_result")
                    or {}
                )
                fmt = inputs.get("output_format", "docx")
                task_num = inputs.get("task_number") or context.get("task_number", 1)
                file_path = self.business_agent.render_deliverable(
                    proposal_dict=prop,
                    output_format=fmt,
                    model_label=context.get("force_model", "groq"),
                    task_number=task_num,
                )
                if not file_path or not os.path.isfile(file_path):
                    raise RuntimeError(f"Rendering failed to produce valid {fmt} file.")

                # Register artifact with lifecycle manager
                reg = self.artifact_manager.register_artifact(
                    parent_task_id=subtask.parent_task_id,
                    file_path=file_path,
                    artifact_type=fmt,
                    subtask_id=subtask.id,
                )
                return {"file_path": file_path, "artifact": reg}

        # ── 2. Arabic Language Capability ─────────────────────────────────────
        elif agent == "arabic_agent":
            if key == "arabic_translation":
                # If upstream produced a structured proposal, transform the full proposal
                upstream_prop = context.get("dep_draft_proposal_result")
                if upstream_prop and isinstance(upstream_prop, dict) and "sections" in upstream_prop:
                    transformed = self.arabic_agent.transform_proposal(
                        upstream_prop,
                        model_label=context.get("force_model"),
                    )
                    return transformed
                # Otherwise translate raw text
                text_to_translate = inputs.get("text") or context.get("body", "")
                translated = self.arabic_agent.translate_text(
                    text_to_translate,
                    model_label=context.get("force_model"),
                )
                return {"translated_text": translated}

        # ── 3. Meeting Scheduling Capability ──────────────────────────────────
        elif agent == "scheduling_agent":
            req_text = inputs.get("request_text") or context.get("body", "")
            sender = inputs.get("sender") or context.get("sender", "")
            user_confirmed = inputs.get("user_confirmed", False)
            res = self.scheduling_agent.execute_scheduling(
                request_text=req_text,
                sender=sender,
                user_confirmed=user_confirmed,
            )
            return res

        # ── 4. Orchestrator Synthesis ─────────────────────────────────────────
        elif agent == "orchestrator":
            research = context.get("dep_research_result", {}).get("findings", "")
            sched = context.get("dep_meeting_scheduling_result", {}).get("response_text", "")
            return {
                "synthesis": f"{research}\n\n---\n\n{sched}".strip()
            }

        raise ValueError(f"Unknown agent/subtask handler: {agent} / {key}")

    def coordinate_request(
        self,
        context: Dict[str, Any],
        parent_task_id: str,
    ) -> Dict[str, Any]:
        """
        Main entry point for coordinating a multi-agent user request.
        Returns unified answer, list of attachments, and subtask execution audit.
        """
        request_text = context.get("body", "")
        plan = self.planner.create_plan(
            request_text=request_text,
            parent_task_id=parent_task_id,
            context=context,
        )

        # Execute DAG plan
        completed_plan = self.executor.execute_plan(
            plan=plan,
            agent_dispatcher=self._agent_dispatcher,
            shared_context=context,
        )

        # Collect files and text outputs
        files = []
        answer_parts = []
        partial_failures = []
        agents_used = set()

        for key, st in completed_plan.subtasks.items():
            agents_used.add(st.agent_name)
            if st.status == SubtaskState.SUCCEEDED and st.result_data:
                # Capture files
                if "file_path" in st.result_data:
                    fp = st.result_data["file_path"]
                    if fp not in files and os.path.isfile(fp):
                        files.append(fp)
                # Capture text responses
                if "response_text" in st.result_data:
                    answer_parts.append(st.result_data["response_text"])
                elif "translated_text" in st.result_data:
                    answer_parts.append(st.result_data["translated_text"])
                elif "findings" in st.result_data:
                    answer_parts.append(st.result_data["findings"])
                elif "synthesis" in st.result_data:
                    answer_parts.append(st.result_data["synthesis"])
            elif st.status in (SubtaskState.FAILED, SubtaskState.BLOCKED):
                partial_failures.append(f"{st.subtask_key}: {st.error_message or 'Failed'}")

        # Build unified final answer
        if files and not answer_parts:
            fnames = ", ".join(os.path.basename(f) for f in files)
            final_answer = (
                f"Hello,\n\nPlease find your requested deliverable attached.\n\n"
                f"Files: {fnames}\n\n{get_email_signature()}"
            )
        elif answer_parts:
            final_answer = "\n\n".join(answer_parts)
        else:
            final_answer = (
                f"Hello,\n\nI processed your request but could not complete all subtasks.\n\n"
                f"{get_email_signature()}"
            )

        if partial_failures:
            final_answer += (
                f"\n\n[Notice: Some subtasks could not complete: {'; '.join(partial_failures)}]"
            )

        return {
            "answer": final_answer,
            "files": files,
            "plan_id": plan.plan_id,
            "agents_used": list(agents_used),
            "has_failures": completed_plan.has_failures(),
            "subtasks": {k: st.to_dict() for k, st in completed_plan.subtasks.items()},
        }
