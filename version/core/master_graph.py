"""
agent/graph.py — Sally Master Skill Router (LangGraph)

The top-level LangGraph for Sally. Routes incoming skill requests to the
appropriate compiled skill sub-graph.

Current skills:
  "email"  →  agent/skills/email_skill.py

Adding a new skill in the future:
  1. Create agent/skills/<new_skill>.py with its own StateGraph
  2. Pass the compiled skill into build_master_graph()
  3. Add one new node + one new branch in route_skill() below
"""

from typing import TypedDict
from langgraph.graph import StateGraph, START, END


# ── Master State ───────────────────────────────────────────────────────────────

class SallyMasterState(TypedDict):
    skill:        str   # Which skill to invoke: "email" | "slack" | …
    skill_input:  dict  # Skill-specific input payload (passed to sub-graph)
    skill_output: dict  # Skill-specific output (written back after sub-graph)


# ── Master Graph Builder ───────────────────────────────────────────────────────

def build_master_graph(email_skill):
    """
    Build and compile Sally's master skill router graph.

    Args:
        email_skill: A compiled LangGraph returned by build_email_skill().

    Returns:
        A compiled LangGraph that accepts SallyMasterState as input.
    """

    # Node: router — reads state["skill"] to decide which branch to take
    def skill_router_node(state: SallyMasterState) -> dict:
        print(f"[Sally] Routing to skill: '{state['skill']}'")
        return {}   # pass-through; routing logic is in the conditional edge

    # Node: email skill — invokes the email sub-graph
    def email_skill_node(state: SallyMasterState) -> dict:
        result = email_skill.invoke(state["skill_input"])
        return {"skill_output": dict(result)}

    # Conditional edge: decide which skill node to call
    def route_skill(state: SallyMasterState) -> str:
        skill = state.get("skill", "")
        if skill == "email":
            return "email_skill"
        # Unknown skill — graceful no-op
        print(f"[Sally] Unknown skill '{skill}' — skipping.")
        return END

    # ── Graph wiring ──────────────────────────────────────────────────────────
    graph = StateGraph(SallyMasterState)

    graph.add_node("skill_router", skill_router_node)
    graph.add_node("email_skill",  email_skill_node)

    graph.add_edge(START, "skill_router")
    graph.add_conditional_edges("skill_router", route_skill, {
        "email_skill": "email_skill",
        END: END,
    })
    graph.add_edge("email_skill", END)

    return graph.compile()
