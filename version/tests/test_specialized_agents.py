"""
tests/test_specialized_agents.py — Tests for Business, Arabic, and Scheduling Agents

Validates:
  1. Business Agent research and proposal authoring (Scenario A)
  2. Arabic Agent translation, proposal transformation, and RTL review
  3. Arabic sales proposal dependency chain (Scenario B)
  4. Meeting Scheduling Agent missing parameter detection (needs_info)
  5. Meeting Scheduling Agent draft proposal creation (draft_proposal)
  6. Meeting Scheduling with successful MockCalendarProvider
  7. Meeting Scheduling with failing MockCalendarProvider
  8. Meeting Scheduling external domain attendees filtering
  9. Research + Meeting Scheduling concurrent coordination (Scenario C)
"""

import os
import sys
import unittest

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from agents.business_agent import BusinessAgent
from agents.arabic_agent import ArabicLanguageAgent
from agents.scheduling_agent import MeetingSchedulingAgent, MockCalendarProvider
from core.orchestrator.planner import DAGPlanner
from core.orchestrator.orchestrator import CentralOrchestrator
from core.database.sqlite_db import init_db


class TestSpecializedAgents(unittest.TestCase):

    def setUp(self):
        init_db()
        self.business_agent = BusinessAgent()
        self.arabic_agent = ArabicLanguageAgent()
        self.mock_calendar = MockCalendarProvider()
        self.scheduling_agent = MeetingSchedulingAgent(provider=self.mock_calendar)
        self.planner = DAGPlanner()

    # ── Scenario A: Business Workflow ──────────────────────────────────────────

    def test_business_agent_research_and_proposal(self):
        """Verify business agent generates research findings and structured proposal."""
        research = self.business_agent.perform_research("Acme Corp Cloud Strategy", use_web=False, use_rag=False)
        self.assertIn("Acme Corp", research["query"])
        self.assertTrue(len(research["findings"]) > 0)

        proposal = self.business_agent.prepare_proposal(
            topic="Acme Cloud Strategy",
            research_context=research["findings"],
            client_name="Acme Corp",
        )
        self.assertEqual(proposal["title"], "Acme Cloud Strategy")
        self.assertEqual(proposal["recipient"], "Acme Corp")
        self.assertGreaterEqual(len(proposal["sections"]), 3)
        # Verify scope and deliverables table exists
        deliverables_sec = [s for s in proposal["sections"] if "Scope" in s["heading"]][0]
        self.assertIn("table", deliverables_sec)
        self.assertIn("Milestone", deliverables_sec["table"]["headers"])

    # ── Scenario B: Arabic Language & Sales Proposal ──────────────────────────

    def test_arabic_agent_translation_and_review(self):
        """Verify Arabic agent translates business terms and reviews text."""
        translated = self.arabic_agent.translate_text("business proposal for sales proposal")
        self.assertTrue(self.arabic_agent.is_arabic(translated))
        self.assertTrue(len(translated) > 0)

        review = self.arabic_agent.review_arabic_content(translated, target_format="docx")
        self.assertTrue(review["valid"])
        self.assertTrue(review["has_arabic"])

    def test_arabic_proposal_transformation(self):
        """Verify structured proposal transformation preserves numbers and tables in Arabic."""
        raw_proposal = {
            "title": "Business Proposal",
            "summary": "Executive summary for enterprise expansion.",
            "sections": [
                {
                    "heading": "Market Analysis",
                    "content": "Overview of regional opportunities.",
                    "table": {
                        "headers": ["Project Phase", "Budget (USD)"],
                        "rows": [["Phase 1", 25000], ["Phase 2", 15000]],
                    },
                }
            ],
        }
        arabic_proposal = self.arabic_agent.transform_proposal(raw_proposal)
        self.assertEqual(arabic_proposal["language"], "ar")
        self.assertTrue(arabic_proposal["is_rtl"])
        self.assertTrue(self.arabic_agent.is_arabic(arabic_proposal["title"]))
        # Verify numeric values are preserved
        table_rows = arabic_proposal["sections"][0]["table"]["rows"]
        self.assertEqual(table_rows[0][1], 25000)
        self.assertEqual(table_rows[1][1], 15000)

    def test_arabic_sales_proposal_dag_plan(self):
        """Verify Scenario B creates the exact expected dependency chain."""
        plan = self.planner.create_plan(
            request_text="Research this prospect, prepare a sales proposal in Arabic, and save the final document.",
            parent_task_id="task_scenario_b",
        )
        # Expected subtasks: research, draft_proposal, arabic_translation, render_deliverable
        self.assertIn("research", plan.subtasks)
        self.assertIn("draft_proposal", plan.subtasks)
        self.assertIn("arabic_translation", plan.subtasks)
        self.assertIn("render_deliverable", plan.subtasks)

        # Dependency chain verification:
        self.assertEqual(plan.subtasks["research"].dependencies, [])
        self.assertEqual(plan.subtasks["draft_proposal"].dependencies, ["research"])
        self.assertEqual(plan.subtasks["arabic_translation"].dependencies, ["draft_proposal"])
        self.assertEqual(plan.subtasks["render_deliverable"].dependencies, ["arabic_translation"])

    # ── Scenario C: Research + Meeting Scheduling ─────────────────────────────

    def test_research_and_meeting_concurrent_dag_plan(self):
        """Verify Scenario C plans independent parallel research and meeting extraction."""
        plan = self.planner.create_plan(
            request_text="Research Acme and arrange a meeting with the team next week regarding the proposal.",
            parent_task_id="task_scenario_c",
        )
        self.assertIn("research", plan.subtasks)
        self.assertIn("meeting_scheduling", plan.subtasks)
        self.assertIn("synthesis", plan.subtasks)

        # Independent subtasks have no dependencies and run concurrently
        self.assertEqual(plan.subtasks["research"].dependencies, [])
        self.assertEqual(plan.subtasks["meeting_scheduling"].dependencies, [])
        # Synthesis waits for both
        self.assertIn("research", plan.subtasks["synthesis"].dependencies)
        self.assertIn("meeting_scheduling", plan.subtasks["synthesis"].dependencies)

    # ── Scheduling Agent Direct Tests ─────────────────────────────────────────

    def test_scheduling_missing_details(self):
        """Verify scheduling agent asks for missing details instead of guessing or fabricating."""
        res = self.scheduling_agent.execute_scheduling(
            request_text="Can we schedule a meeting next week?",
            sender="alex@eaisystems.com",
            user_confirmed=False,
        )
        self.assertEqual(res["status"], "needs_info")
        self.assertFalse(res["booked"])
        self.assertIn("proposed date and time", " ".join(res["missing_fields"]))

    def test_scheduling_draft_proposal_when_unconfirmed(self):
        """Verify scheduling agent drafts the invitation and requires confirmation before booking."""
        res = self.scheduling_agent.execute_scheduling(
            request_text="Schedule a 30-min meeting with team@eaisystems.com on 2026-11-15T14:00:00 about budget",
            sender="alex@eaisystems.com",
            user_confirmed=False,
        )
        self.assertEqual(res["status"], "draft_proposal")
        self.assertFalse(res["booked"])
        self.assertIn("Please reply **yes** to confirm", res["response_text"])

    def test_scheduling_successful_booking_with_mock_provider(self):
        """Verify scheduling books and sends invites when authorized with successful provider."""
        res = self.scheduling_agent.execute_scheduling(
            request_text="Schedule a 30-min meeting with team@eaisystems.com on 2026-11-15T14:00:00 about budget",
            sender="alex@eaisystems.com",
            user_confirmed=True,
        )
        self.assertEqual(res["status"], "booked")
        self.assertTrue(res["booked"])
        self.assertIn("Done — I have scheduled", res["response_text"])
        self.assertEqual(len(self.mock_calendar.scheduled_events), 1)

    def test_scheduling_failing_provider_handles_error(self):
        """Verify scheduling provider error is cleanly reported without claiming false success."""
        failing_mock = MockCalendarProvider(simulate_failure=True)
        res = self.scheduling_agent.execute_scheduling(
            request_text="Schedule a meeting with team@eaisystems.com on 2026-11-15T14:00:00 about budget",
            sender="alex@eaisystems.com",
            user_confirmed=True,
            override_provider=failing_mock,
        )
        self.assertEqual(res["status"], "failed")
        self.assertFalse(res["booked"])
        self.assertIn("reported an error", res["response_text"])
        self.assertIn("No calendar invitation was sent", res["response_text"])

    def test_scheduling_domain_filtering(self):
        """Verify external email domains are filtered according to guardrails."""
        from tools.meeting_scheduler import filter_attendees_by_domain
        attendees = ["internal@allowed.com", "external@untrusted.org"]
        kept, blocked = filter_attendees_by_domain(attendees, allowed_domains=["allowed.com"])
        self.assertEqual(kept, ["internal@allowed.com"])
        self.assertEqual(blocked, ["external@untrusted.org"])


if __name__ == "__main__":
    unittest.main()
