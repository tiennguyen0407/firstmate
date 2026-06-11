from __future__ import annotations

import asyncio
from shared.models import Job, TaskResult

# In-memory store — upgrade to Redis/Postgres khi production
_jobs: dict[str, Job] = {}

# Pending runner results: job_id → Future (LangGraph waiting_runner resume)
_result_futures: dict[str, asyncio.Future] = {}


def save(job: Job) -> None:
    _jobs[job.id] = job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def wait_for_result(job_id: str) -> asyncio.Future:
    """LangGraph waiting_runner node gọi cái này để chờ Runner post result."""
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    _result_futures[job_id] = future
    return future


def resolve_result(result: TaskResult) -> bool:
    """Runner API gọi cái này khi Runner post kết quả về."""
    future = _result_futures.pop(result.job_id, None)
    if future and not future.done():
        future.set_result(result)
        return True
    return False
