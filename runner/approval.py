from __future__ import annotations

import asyncio
from shared.models import Command


async def ask_confirm(cmd: Command) -> bool:
    """Hỏi SRE confirm trước WRITE op. Chạy trong thread để không block event loop."""
    return await asyncio.to_thread(_prompt, cmd)


def _prompt(cmd: Command) -> bool:
    print(f"\n{'─'*50}")
    print(f"⚠️  WRITE OPERATION — cần confirm")
    print(f"   {cmd.description}")
    print(f"   $ {cmd.cmd}")
    print(f"{'─'*50}")
    answer = input("Chạy lệnh này? [y/N] ").strip().lower()
    return answer == "y"
