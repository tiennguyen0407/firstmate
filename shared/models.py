from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class JobType(str, Enum):
    INCIDENT = "incident"
    DEBUG = "debug"
    DATA_CHECK = "data_check"


class JobStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    WAITING_SRE = "waiting_sre"
    WAITING_RUNNER = "waiting_runner"
    WAITING_LEAD = "waiting_lead"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"


class Command(BaseModel):
    description: str
    cmd: str
    permission: Permission
    tool: str  # k8s | redis | db


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: JobType
    service: str
    env: str
    description: str
    commands: list[Command]
    requester_telegram_id: str
    requester_name: str
    assigned_sre: Optional[str] = None
    status: JobStatus = JobStatus.PENDING
    prev_summary: Optional[str] = None  # kết quả điều tra trước (follow-up tasks)


class TaskResult(BaseModel):
    job_id: str
    runner_id: str
    status: str             # success | failed | partial
    output: str
    needs_lead_approval: bool = False


class RunnerInfo(BaseModel):
    runner_id: str
    sre_id: str
    telegram_id: str
    capabilities: list[str]
    status: str = "online"  # online | offline


class PollResponse(BaseModel):
    job: Optional[Job] = None


class SREResponse(str, Enum):
    ACCEPTED = "accepted"
    BUSY = "busy"
    DECLINED = "declined"
    TIMEOUT = "timeout"


class LeadResponse(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
