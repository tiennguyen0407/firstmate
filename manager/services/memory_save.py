"""
Memory::SaveService — save conversation events to AgentBase Memory (short-term).

Uses the SDK's create_event_async to store conversation turns as events.
Events are organized by actor_id (requester) and session_id (job/task).
Events expire after the memory store's configured eventExpiryDuration (90 days).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("firstmate.memory")

# ── Constants ────────────────────────────────────────────────────

VALID_CATEGORIES = {
    "preference", "project_context", "technical_context",
    "business_rule", "workflow", "decision", "system_config", "other",
}

_SENSITIVE_RE = re.compile(
    r"password|passwd|token|secret|api[_\-]?key|private[_\-]?key|credential",
    re.IGNORECASE,
)

_MIN_CONTENT_LENGTH = 10


class MemorySaveService:
    """Validate and save conversation events to AgentBase Memory."""

    def __init__(self, memory_id: str):
        self._memory_id = memory_id
        self._client = None

    def _get_client(self):
        if self._client is None:
            from greennode_agentbase.memory import MemoryClient
            # Use MEMORY_CLIENT_ID/SECRET if set (same account that owns the memory),
            # otherwise fall back to auto-injected GREENNODE_CLIENT_ID/SECRET.
            mem_client_id = os.getenv("MEMORY_CLIENT_ID") or os.getenv("GREENNODE_CLIENT_ID")
            mem_client_secret = os.getenv("MEMORY_CLIENT_SECRET") or os.getenv("GREENNODE_CLIENT_SECRET")
            if mem_client_id and mem_client_secret:
                from greennode_agentbase.identity import IAMCredentials
                creds = IAMCredentials(client_id=mem_client_id, client_secret=mem_client_secret)
                self._client = MemoryClient(iam_credentials=creds)
                logger.info(f"[Memory] MemoryClient using client_id={mem_client_id[:8]}...")
            else:
                self._client = MemoryClient()
                logger.info("[Memory] MemoryClient using default credentials")
        return self._client

    # ── Public entry point ───────────────────────────────────────

    async def save(
        self,
        actor_id: str,
        content: str,
        source: str = "conversation",
        metadata: Optional[dict] = None,
    ) -> dict:
        metadata = metadata or {}

        # Validate
        if not self._memory_id:
            return _rejected("invalid_memory_id")
        if not actor_id:
            return _rejected("invalid_actor_id")
        if not content or not content.strip():
            return _rejected("empty_content")
        if _SENSITIVE_RE.search(content):
            return _rejected("content_contains_sensitive_information")
        if len(content.strip()) < _MIN_CONTENT_LENGTH:
            return _skipped("low_value")

        # Save as short-term event
        return await self._save_event(actor_id, content, source, metadata)

    # ── Save event via SDK ───────────────────────────────────────

    async def _save_event(
        self, actor_id: str, content: str, source: str, metadata: dict,
    ) -> dict:
        try:
            from greennode_agentbase.memory.models import EventCreateRequest, EventPayload

            role = metadata.get("role", "system")
            session_id = metadata.get("session_id", f"session-{actor_id}")
            request = EventCreateRequest(
                payload=EventPayload(type="conversational", role=role, message=content),
            )
            await self._get_client().create_event_async(
                id=self._memory_id,
                actorId=actor_id,
                sessionId=session_id,
                request=request,
            )
            logger.info(f"[Memory] saved actor={actor_id} session={session_id[:12]} role={role} len={len(content)}")
            return {
                "status": "saved",
                "memory_record": {
                    "memory_id": self._memory_id,
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "type": "short_term",
                    "content": content[:200],
                    "source": source,
                },
            }
        except Exception as exc:
            logger.error(f"[Memory] save failed: {exc}")
            return _rejected(f"save_failed: {exc}")


    # ── Retrieve recent events (for pre-analysis) ─────────────────

    async def get_recent_events(
        self, actor_id: str, session_id: str = "", limit: int = 20,
    ) -> list[dict]:
        """Fetch recent short-term events for an actor/session."""
        if not session_id:
            session_id = f"chat-{actor_id}"
        try:
            result = await self._get_client().list_events_async(
                id=self._memory_id,
                actorId=actor_id,
                sessionId=session_id,
                page=1,
                size=limit,
            )
            events = []
            for e in getattr(result, "list_data", []):
                payload = getattr(e, "payload", None)
                if payload:
                    events.append({
                        "role": getattr(payload, "role", "?"),
                        "message": getattr(payload, "message", ""),
                    })
            # API returns newest-first, reverse to chronological
            events.reverse()
            logger.info(f"[Memory] fetched {len(events)} events actor={actor_id} session={session_id[:12]}")
            return events
        except Exception as exc:
            logger.warning(f"[Memory] get_recent_events failed: {exc}")
            return []

    async def get_all_recent_events(self, actor_id: str, limit: int = 30) -> list[dict]:
        """Fetch events across all sessions for an actor (uses chat-{id} + recent job sessions)."""
        all_events = []
        # Primary: the default chat session
        events = await self.get_recent_events(actor_id, f"chat-{actor_id}", limit=limit)
        all_events.extend(events)
        return all_events


# ── Helpers ──────────────────────────────────────────────────────

def _rejected(reason: str) -> dict:
    return {"status": "rejected", "reason": reason}


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}


# ── Singleton ────────────────────────────────────────────────────

_MEMORY_ID = os.getenv("AGENTBASE_MEMORY_ID", "")
_instance: Optional[MemorySaveService] = None


def get_memory_service() -> Optional[MemorySaveService]:
    global _instance
    if _instance is None and _MEMORY_ID:
        _instance = MemorySaveService(memory_id=_MEMORY_ID)
    return _instance
