from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runner.poller import RunnerPoller
from runner.terminal import open_claude_terminal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("firstmate.runner")


async def main():
    runner_id   = os.environ["RUNNER_ID"]
    sre_id      = os.environ["SRE_ID"]
    telegram_id = os.environ.get("SRE_TELEGRAM_ID", "")
    manager_url = os.environ["MANAGER_URL"]

    print()
    print("╔══════════════════════════════════════════╗")
    print("║       FirstMate Runner — Starting        ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  Runner ID : {runner_id:<28}║")
    print(f"║  SRE       : {sre_id:<28}║")
    print(f"║  Server    : {manager_url[:28]:<28}║")
    print("╚══════════════════════════════════════════╝")
    print()
    print("  Nhận job → mở terminal mới → Claude Code tự điều tra")
    print("  Write ops → approval trong terminal + Telegram alert")
    print()

    poller = RunnerPoller(
        runner_id=runner_id,
        sre_id=sre_id,
        telegram_id=telegram_id,
    )

    await poller.run(on_job=open_claude_terminal)


if __name__ == "__main__":
    asyncio.run(main())
