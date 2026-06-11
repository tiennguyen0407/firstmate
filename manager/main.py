from __future__ import annotations

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from manager.api.runner_api import router as runner_router
from manager.api.telegram_webhook import router as telegram_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("FirstMate-Manager starting...")
    yield
    print("FirstMate-Manager shutting down.")


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
    return {"status": "ok", "service": "firstmate-manager"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "manager.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        reload=os.getenv("ENV") == "development",
    )
