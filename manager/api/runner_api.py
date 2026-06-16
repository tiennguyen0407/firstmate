from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from manager.graph import graph
from manager.services import runner_registry, job_store
from shared.models import RunnerInfo, TaskResult, PollResponse, Job

router = APIRouter(prefix="/api/runner", tags=["runner"])


@router.post("/register")
async def register(info: RunnerInfo):
    runner_registry.register(info)
    return {"status": "ok", "runner_id": info.runner_id}


@router.post("/heartbeat/{runner_id}")
async def heartbeat(runner_id: str):
    if not runner_registry.heartbeat(runner_id):
        # Runner bị mất khỏi registry (manager restart) — yêu cầu re-register
        raise HTTPException(status_code=410, detail="Runner lost after restart, please re-register")
    return {"status": "ok"}


@router.post("/poll/{runner_id}", response_model=PollResponse)
async def poll(runner_id: str):
    """Long-poll: giữ connection tối đa 30s, trả job khi có."""
    if not runner_registry.is_online(runner_id):
        raise HTTPException(status_code=403, detail="Runner not registered or offline")

    queue = runner_registry.get_queue(runner_id)
    try:
        job: Job = await asyncio.wait_for(queue.get(), timeout=30.0)
        return PollResponse(job=job)
    except asyncio.TimeoutError:
        return PollResponse(job=None)  # 200 với job=null → poll lại


@router.post("/job/{job_id}/result")
async def submit_result(job_id: str, result: TaskResult):
    """Runner post kết quả sau khi chạy xong."""
    resolved = job_store.resolve_result(result)
    if not resolved:
        # Không có graph đang chờ → vẫn lưu lại
        job = job_store.get(job_id)
        if job:
            return {"status": "ok", "note": "no graph waiting"}
        raise HTTPException(status_code=404, detail="Job not found")

    # Resume LangGraph waiting_runner node
    from manager.api.telegram_webhook import _debug_jobs
    requester_id = (_debug_jobs.get(job_id) or {}).get("requester_chat_id", "unknown")
    thread_config = {"configurable": {"thread_id": job_id, "actor_id": requester_id}}
    await asyncio.to_thread(
        graph.invoke,
        Command(resume=result.model_dump()),
        thread_config,
    )
    return {"status": "ok"}
