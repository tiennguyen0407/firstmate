from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from manager.api.runner_api import router as runner_router
from manager.api.telegram_webhook import router as telegram_router, error_log, DEBUG_MODE

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("firstmate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "DEBUG (forward to SRE)" if DEBUG_MODE else "NORMAL (LangGraph)"
    logger.info(f"FirstMate-Manager starting — mode={mode}")
    yield
    logger.info("FirstMate-Manager shutting down.")


app = FastAPI(
    title="FirstMate-Manager",
    description="AI-powered DevOps coordination platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runner_router)
app.include_router(telegram_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "firstmate-manager", "debug_mode": DEBUG_MODE}


@app.get("/status")
async def status():
    """Xem trạng thái, runner registry và các lỗi gần đây."""
    import time
    from manager.services.runner_registry import _registry, _OFFLINE_AFTER

    runners_info = {}
    now = time.time()
    for rid, entry in _registry.items():
        age = now - entry["last_seen"]
        runners_info[rid] = {
            "online": age < _OFFLINE_AFTER,
            "last_seen_seconds_ago": round(age, 1),
            "sre_id": entry["info"].sre_id,
        }

    recent_errors = [
        {**e, "traceback": e.get("traceback", "")[-400:]}
        for e in list(error_log)[-20:]
    ]

    return {
        "debug_mode": DEBUG_MODE,
        "runners": runners_info,
        "error_count": len(error_log),
        "recent_errors": recent_errors,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "manager.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        reload=os.getenv("ENV") == "development",
        log_level="info",
    )
