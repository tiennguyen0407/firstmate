from __future__ import annotations

import asyncio
import time
from shared.models import RunnerInfo

# runner_id → { info, last_seen }
_registry: dict[str, dict] = {}
_OFFLINE_AFTER = 60  # seconds


def register(info: RunnerInfo) -> None:
    _registry[info.runner_id] = {
        "info": info,
        "last_seen": time.time(),
    }


def heartbeat(runner_id: str) -> bool:
    if runner_id not in _registry:
        return False
    _registry[runner_id]["last_seen"] = time.time()
    return True


def is_online(runner_id: str) -> bool:
    entry = _registry.get(runner_id)
    if not entry:
        return False
    return (time.time() - entry["last_seen"]) < _OFFLINE_AFTER


def get_online_runner_for_sre(sre_id: str) -> list[str]:
    """Trả về runner IDs của SRE đang online."""
    return [
        rid for rid, entry in _registry.items()
        if entry["info"].sre_id == sre_id and is_online(rid)
    ]


def get_all_online() -> list[RunnerInfo]:
    return [
        entry["info"]
        for entry in _registry.values()
        if is_online(entry["info"].runner_id)
    ]


# ── Job queue per runner ───────────────────────────────────────
_queues: dict[str, asyncio.Queue] = {}


def get_queue(runner_id: str) -> asyncio.Queue:
    if runner_id not in _queues:
        _queues[runner_id] = asyncio.Queue()
    return _queues[runner_id]
