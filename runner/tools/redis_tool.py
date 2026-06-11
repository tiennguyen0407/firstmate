from __future__ import annotations

import os


async def run_redis(cmd: str) -> str:
    """Chạy Redis read-only command (GET, SCAN, TTL, TYPE, LLEN...)."""
    allowed_prefixes = ("GET ", "SCAN ", "TTL ", "TYPE ", "LLEN ",
                        "HGET ", "HGETALL ", "SMEMBERS ", "ZRANGE ")
    cmd_upper = cmd.strip().upper()
    if not any(cmd_upper.startswith(p) for p in allowed_prefixes):
        return f"[blocked: only read commands allowed. Got: {cmd[:50]}]"

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        parts = cmd.split()
        result = await r.execute_command(*parts)
        await r.aclose()
        return str(result)
    except ImportError:
        return "[error: redis package not installed]"
    except Exception as e:
        return f"[error: {e}]"
