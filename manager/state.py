from __future__ import annotations

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

from shared.models import Command, JobType, JobStatus


class FirstMateState(TypedDict):
    # ── Input ──────────────────────────────────────────────
    job_id: str
    user_message: str
    requester_telegram_id: str
    requester_name: str
    service: str
    env: str
    job_type: JobType

    # ── LLM conversation ───────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Investigation results ──────────────────────────────
    findings: list[str]
    hypothesis: str
    write_ops: list[Command]        # WRITE ops cần SRE approve

    # ── SRE assignment ─────────────────────────────────────
    assignment_attempts: list[str]  # SRE IDs đã thử (để tránh assign lại)
    assigned_sre: Optional[str]
    sre_response: Optional[str]     # accepted | busy | declined | timeout

    # ── Runner result ──────────────────────────────────────
    runner_output: Optional[str]
    needs_lead_approval: bool

    # ── Lead approval ──────────────────────────────────────
    assigned_lead: Optional[str]
    lead_response: Optional[str]    # approved | rejected | timeout

    # ── Final ──────────────────────────────────────────────
    status: JobStatus
    final_report: Optional[str]
