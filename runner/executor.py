from __future__ import annotations

import httpx
import os
from shared.models import Job, TaskResult, Permission
from runner.approval import ask_confirm
from runner.tools.k8s_tool import run_kubectl
from runner.tools.redis_tool import run_redis
from runner.tools.db_tool import run_db_query


async def execute_job(job: Job) -> None:
    """Nhận job từ Manager, chạy từng command, post kết quả về."""
    outputs = []
    needs_lead = False

    for cmd in job.commands:
        if cmd.permission == Permission.WRITE:
            confirmed = await ask_confirm(cmd)
            if not confirmed:
                outputs.append(f"[SKIPPED] {cmd.description}")
                continue

        result = await _run_command(cmd)
        outputs.append(f"[{cmd.tool.upper()}] {cmd.description}:\n{result}")

    final_output = "\n\n".join(outputs)
    task_result = TaskResult(
        job_id=job.id,
        runner_id=os.environ["RUNNER_ID"],
        status="success",
        output=final_output,
        needs_lead_approval=needs_lead,
    )
    await _post_result(task_result)


async def _run_command(cmd) -> str:
    if cmd.tool == "k8s":
        return run_kubectl(cmd.cmd)
    if cmd.tool == "redis":
        return await run_redis(cmd.cmd)
    if cmd.tool == "db":
        return await run_db_query(cmd.cmd)
    return f"[unknown tool: {cmd.tool}]"


async def _post_result(result: TaskResult) -> None:
    base_url = os.environ["MANAGER_URL"].rstrip("/")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{base_url}/api/runner/job/{result.job_id}/result",
            json=result.model_dump(),
            timeout=10,
        )
