from __future__ import annotations

from langgraph.types import interrupt

from manager.state import FirstMateState
from manager.services.config_loader import get_sre_timeout, get_lead_timeout
from shared.models import JobStatus


async def waiting_sre(state: FirstMateState) -> dict:
    """
    Pause graph, chờ SRE reply trên Telegram.
    Resume khi Telegram callback gọi graph.invoke(Command(resume=action)).
    """
    sre_id = state["assigned_sre"]
    timeout = get_sre_timeout()

    # interrupt() suspend graph tại đây
    # Telegram callback sẽ resume với action string
    response = interrupt({
        "type": "sre_approval",
        "sre_id": sre_id,
        "job_id": state["job_id"],
        "description": "\n".join(
            f"• {op.description}: `{op.cmd}`"
            for op in state["write_ops"]
        ),
        "timeout_seconds": timeout,
    })

    return {
        "sre_response": response,  # accepted | busy | declined | timeout
        "status": JobStatus.WAITING_RUNNER
        if response == "accepted" else JobStatus.WAITING_SRE,
    }


async def waiting_runner(state: FirstMateState) -> dict:
    """
    Pause graph, chờ Runner post kết quả về /api/runner/job/{id}/result.
    """
    response = interrupt({
        "type": "runner_result",
        "job_id": state["job_id"],
        "assigned_sre": state["assigned_sre"],
    })

    # response là TaskResult dict từ Runner
    return {
        "runner_output": response.get("output", ""),
        "needs_lead_approval": response.get("needs_lead_approval", False),
        "status": JobStatus.WAITING_LEAD
        if response.get("needs_lead_approval") else JobStatus.COMPLETED,
    }


async def waiting_lead(state: FirstMateState) -> dict:
    """
    Pause graph, chờ SRE-Lead approve trên Telegram (optional).
    """
    lead_id = state["assigned_lead"]
    timeout = get_lead_timeout()

    response = interrupt({
        "type": "lead_approval",
        "lead_id": lead_id,
        "job_id": state["job_id"],
        "runner_output": state.get("runner_output", ""),
        "timeout_seconds": timeout,
    })

    return {
        "lead_response": response,  # approved | rejected | timeout
        "status": JobStatus.COMPLETED
        if response == "approved" else JobStatus.ESCALATED,
    }
