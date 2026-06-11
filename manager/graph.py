from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from manager.state import FirstMateState
from manager.nodes.investigate import investigate
from manager.nodes.assign_sre import assign_sre, assign_lead
from manager.nodes.waiting_nodes import waiting_sre, waiting_runner, waiting_lead
from manager.nodes.reporter import report_result, escalated
from shared.models import JobStatus


# ── Routing functions ──────────────────────────────────────────

def route_after_investigate(state: FirstMateState) -> str:
    if state.get("write_ops"):
        return "assign_sre"
    return "report_result"


def route_sre_response(state: FirstMateState) -> str:
    response = state.get("sre_response")
    if response == "accepted":
        return "waiting_runner"
    if state.get("status") == JobStatus.ESCALATED:
        return "escalated"
    # busy | declined | timeout → thử SRE khác
    return "assign_sre"


def route_after_runner(state: FirstMateState) -> str:
    if state.get("needs_lead_approval"):
        return "assign_lead"
    return "report_result"


def route_lead_response(state: FirstMateState) -> str:
    response = state.get("lead_response")
    if response == "approved":
        return "report_result"
    return "escalated"


def route_assign_sre(state: FirstMateState) -> str:
    if state.get("status") == JobStatus.ESCALATED:
        return "escalated"
    return "waiting_sre"


# ── Build graph ────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(FirstMateState)

    builder.add_node("investigate",    investigate)
    builder.add_node("assign_sre",     assign_sre)
    builder.add_node("waiting_sre",    waiting_sre)
    builder.add_node("waiting_runner", waiting_runner)
    builder.add_node("assign_lead",    assign_lead)
    builder.add_node("waiting_lead",   waiting_lead)
    builder.add_node("report_result",  report_result)
    builder.add_node("escalated",      escalated)

    builder.add_edge(START, "investigate")

    builder.add_conditional_edges("investigate", route_after_investigate, {
        "assign_sre":   "assign_sre",
        "report_result":"report_result",
    })

    builder.add_conditional_edges("assign_sre", route_assign_sre, {
        "waiting_sre": "waiting_sre",
        "escalated":   "escalated",
    })

    builder.add_conditional_edges("waiting_sre", route_sre_response, {
        "waiting_runner": "waiting_runner",
        "assign_sre":     "assign_sre",
        "escalated":      "escalated",
    })

    builder.add_conditional_edges("waiting_runner", route_after_runner, {
        "assign_lead":  "assign_lead",
        "report_result":"report_result",
    })

    builder.add_edge("assign_lead", "waiting_lead")

    builder.add_conditional_edges("waiting_lead", route_lead_response, {
        "report_result": "report_result",
        "escalated":     "escalated",
    })

    builder.add_edge("report_result", END)
    builder.add_edge("escalated",     END)

    return builder.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["waiting_sre", "waiting_runner", "waiting_lead"],
    )


graph = build_graph()
