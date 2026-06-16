"""POST /api/v1/memories/save — save conversation events to AgentBase Memory."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from manager.services.memory_save import get_memory_service

logger = logging.getLogger("firstmate.memory_api")
router = APIRouter(prefix="/api/v1/memories", tags=["memory"])


class MemorySaveRequest(BaseModel):
    actor_id: str
    content: str
    source: str = "conversation"
    metadata: Optional[dict] = None


class MemorySaveResponse(BaseModel):
    status: str
    reason: Optional[str] = None
    memory_record: Optional[dict] = None


@router.post("/save", response_model=MemorySaveResponse)
async def save_memory(req: MemorySaveRequest):
    svc = get_memory_service()
    if not svc:
        raise HTTPException(status_code=503, detail="Memory service not configured (AGENTBASE_MEMORY_ID missing)")

    result = await svc.save(
        actor_id=req.actor_id,
        content=req.content,
        source=req.source,
        metadata=req.metadata,
    )

    return MemorySaveResponse(**result)
