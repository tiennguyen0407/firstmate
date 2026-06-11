from __future__ import annotations

from manager.state import FirstMateState
from manager.services.config_loader import get_sres_for_service, get_lead
from manager.services.runner_registry import get_online_runner_for_sre
from shared.models import JobStatus


async def assign_sre(state: FirstMateState) -> dict:
    """Tìm SRE tiếp theo chưa được thử, có Runner đang online."""
    attempted = state.get("assignment_attempts", [])
    service = state["service"]

    candidates = get_sres_for_service(service, exclude=attempted)

    for sre in candidates:
        sre_id = sre["id"]
        online_runners = get_online_runner_for_sre(sre["sre_id"])
        if online_runners:
            return {
                "assigned_sre": sre_id,
                "assignment_attempts": attempted + [sre_id],
                "status": JobStatus.WAITING_SRE,
                "sre_response": None,  # reset
            }

    # Không còn SRE nào available
    return {
        "assigned_sre": None,
        "status": JobStatus.ESCALATED,
    }


async def assign_lead(state: FirstMateState) -> dict:
    """Tìm SRE-Lead để approve."""
    lead = get_lead()
    if not lead:
        return {"assigned_lead": None, "status": JobStatus.ESCALATED}
    return {
        "assigned_lead": lead["id"],
        "status": JobStatus.WAITING_LEAD,
        "lead_response": None,
    }
