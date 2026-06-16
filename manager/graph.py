from __future__ import annotations

import logging
from langgraph.graph import StateGraph, START, END
import os
from greennode_agent_bridge import AgentBaseMemoryEvents

from manager.state import FirstMateState

logger = logging.getLogger(__name__)
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

    # In DEBUG mode, graph is imported but never invoked — use MemorySaver (in-RAM)
    # to avoid requiring AgentBase Memory credentials at startup.
    # In NORMAL mode, use AgentBaseMemoryEvents for persistent checkpoints.
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"
    memory_id = os.getenv("AGENTBASE_MEMORY_ID", "")

    if not debug_mode and memory_id:
        mem_client_id = os.getenv("MEMORY_CLIENT_ID") or os.getenv("GREENNODE_CLIENT_ID")
        mem_client_secret = os.getenv("MEMORY_CLIENT_SECRET") or os.getenv("GREENNODE_CLIENT_SECRET")
        mem_client = None
        if mem_client_id and mem_client_secret:
            from greennode_agentbase.memory import MemoryClient
            from greennode_agentbase.identity import IAMCredentials
            mem_client = MemoryClient(iam_credentials=IAMCredentials(
                client_id=mem_client_id, client_secret=mem_client_secret,
            ))
            logger.info(f"[Memory] Using MEMORY_CLIENT_ID={mem_client_id[:8]}...")
        logger.info(f"[Memory] Initializing AgentBaseMemoryEvents with memory_id={memory_id}")
        checkpointer = LoggedCheckpointer(memory_id=memory_id, memory_client=mem_client)
    else:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info(f"[Memory] DEBUG mode or no MEMORY_ID — using in-memory checkpointer")
        checkpointer = MemorySaver()

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["waiting_sre", "waiting_runner", "waiting_lead"],
    )


class LoggedCheckpointer(AgentBaseMemoryEvents):
    """AgentBaseMemoryEvents with logging on put/get calls."""

    def __init__(self, memory_id: str, memory_client=None):
        super().__init__(memory_id=memory_id, memory_client=memory_client)

    def put(self, config, checkpoint, metadata, new_versions):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        actor_id = config.get("configurable", {}).get("actor_id", "?")
        node = (metadata or {}).get("source", "?")
        logger.info(f"[Memory] PUT thread={thread_id} actor={actor_id} node={node}")
        try:
            result = super().put(config, checkpoint, metadata, new_versions)
            logger.info(f"[Memory] PUT OK thread={thread_id}")
            return result
        except Exception as exc:
            logger.error(f"[Memory] PUT FAILED thread={thread_id}: {exc}")
            raise

    async def aput(self, config, checkpoint, metadata, new_versions):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        actor_id = config.get("configurable", {}).get("actor_id", "?")
        node = (metadata or {}).get("source", "?")
        logger.info(f"[Memory] APUT thread={thread_id} actor={actor_id} node={node}")
        try:
            result = await super().aput(config, checkpoint, metadata, new_versions)
            logger.info(f"[Memory] APUT OK thread={thread_id}")
            return result
        except Exception as exc:
            logger.error(f"[Memory] APUT FAILED thread={thread_id}: {exc}")
            raise

    def get_tuple(self, config):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        logger.info(f"[Memory] GET thread={thread_id}")
        try:
            result = super().get_tuple(config)
            found = result is not None
            logger.info(f"[Memory] GET thread={thread_id} found={found}")
            return result
        except Exception as exc:
            logger.error(f"[Memory] GET FAILED thread={thread_id}: {exc}")
            raise

    async def aget_tuple(self, config):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        logger.info(f"[Memory] AGET thread={thread_id}")
        try:
            result = await super().aget_tuple(config)
            found = result is not None
            logger.info(f"[Memory] AGET thread={thread_id} found={found}")
            return result
        except Exception as exc:
            logger.error(f"[Memory] AGET FAILED thread={thread_id}: {exc}")
            raise

    def list(self, config, **kwargs):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        logger.info(f"[Memory] LIST thread={thread_id}")
        return super().list(config, **kwargs)

    async def alist(self, config, **kwargs):
        thread_id = config.get("configurable", {}).get("thread_id", "?")
        logger.info(f"[Memory] ALIST thread={thread_id}")
        return super().alist(config, **kwargs)


graph = build_graph()
