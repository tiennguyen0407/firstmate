from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runner.poller import RunnerPoller
from runner.executor import execute_job


async def main():
    runner_id  = os.environ["RUNNER_ID"]
    sre_id     = os.environ["SRE_ID"]
    telegram_id = os.environ.get("TELEGRAM_ID", "")

    poller = RunnerPoller(
        runner_id=runner_id,
        sre_id=sre_id,
        telegram_id=telegram_id,
    )

    print(f"FirstMate-Runner [{runner_id}] starting...")
    await poller.run(on_job=execute_job)


if __name__ == "__main__":
    asyncio.run(main())
