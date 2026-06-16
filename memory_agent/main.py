"""
FirstMate Memory Agent — xử lý memory thành dynamic knowledge base.

Endpoints:
  POST /consolidate/{chat_id}  — đọc memory events, chạy LLM, cập nhật KB
  GET  /kb/{chat_id}           — trả về dynamic KB (global + per-user facts)
  GET  /kb/global              — trả về global KB tích lũy từ mọi user
  DELETE /kb/{chat_id}         — xoá cache per-user (force rebuild)
  GET  /health                 — health check
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("firstmate.memory-agent")

# ── In-process caches ─────────────────────────────────────────────
_kb_cache: dict[str, dict] = {}

# Global KB — shared across all users, persisted to AgentBase Memory
_global_service_map: dict[str, str] = {}   # service → namespace
_global_gateway_map: dict[str, dict] = {}  # domain → {ip, env, log_path, ...}

_GLOBAL_ACTOR = "firstmate-system"
_GLOBAL_SESSION = "global-kb"

# ── LLM prompt ────────────────────────────────────────────────────

_SYSTEM_PROMPT = """/no_think
Bạn là Memory Consolidator Agent của FirstMate.
Nhiệm vụ: đọc lịch sử hội thoại giữa developer/QC và SRE, trích xuất facts có giá trị lâu dài.

Trả về JSON thuần (không markdown block):
{
  "service_namespace_map": {
    "<service-name>": "<namespace>"
  },
  "gateway_map": {
    "<domain>": {
      "ip": "<gateway IP>",
      "env": "<môi trường: dev/stg/prod/...>",
      "log_path": "<đường dẫn file log nếu biết>"
    }
  },
  "known_issues": [
    "<mô tả ngắn vấn đề đã gặp — service, triệu chứng, root cause nếu biết>"
  ],
  "environment_notes": "<ghi chú về môi trường: cluster, conventions, đặc thù hệ thống>",
  "active_services": ["<service đang được team quan tâm gần đây>"]
}

Rules cho service_namespace_map:
- Extract TẤT CẢ service/deployment names khi biết chắc namespace từ conversation.
- Khi thấy kết quả kubectl liệt kê nhiều deployment trong 1 namespace (ví dụ: kubectl get deploy -n zpp-loyalty-qc),
  hãy extract TOÀN BỘ tên deployment vào service_namespace_map với namespace tương ứng.
- Không đoán namespace — chỉ điền khi namespace xuất hiện rõ ràng trong conversation.
- Tên service: dùng đúng tên deployment/pod (lowercase, kebab-case).

Rules cho gateway_map:
- Extract thông tin gateway/reverse-proxy khi conversation nhắc đến domain + IP gateway.
- Ví dụ: "dev.zalopay.vn → gateway 10.40.81.2, log /zserver/nginx/logs/dev.zalopay.vn/access.log"
- Chỉ điền field nào có thông tin rõ ràng, để chuỗi rỗng "" cho field không biết.
- domain: dùng hostname đầy đủ (ví dụ: dev.zalopay.vn, stg.zalopay.vn).

Rules khác:
- known_issues: tối đa 10 issues gần nhất, ưu tiên issues chưa được giải quyết hoặc có thể tái diễn
- environment_notes: ngắn gọn, 1-3 câu, thông tin structural (không phải trạng thái tạm thời)
- active_services: tối đa 5 services, dựa trên tần suất xuất hiện gần đây
- Bỏ qua thông tin nhạy cảm (credentials, tokens)
- Nếu không đủ thông tin cho field nào, để array/object rỗng hoặc string rỗng
"""


# ── Memory client ─────────────────────────────────────────────────

def _get_memory_client():
    from greennode_agentbase.memory import MemoryClient
    from greennode_agentbase.identity import IAMCredentials
    client_id = os.getenv("MEMORY_CLIENT_ID") or os.getenv("GREENNODE_CLIENT_ID")
    client_secret = os.getenv("MEMORY_CLIENT_SECRET") or os.getenv("GREENNODE_CLIENT_SECRET")
    if client_id and client_secret:
        return MemoryClient(iam_credentials=IAMCredentials(client_id=client_id, client_secret=client_secret))
    return MemoryClient()


async def _fetch_events(chat_id: str, limit: int = 50) -> list[dict]:
    memory_id = os.getenv("AGENTBASE_MEMORY_ID", "")
    if not memory_id:
        return []
    try:
        client = _get_memory_client()
        result = await client.list_events_async(
            id=memory_id,
            actorId=chat_id,
            sessionId=f"chat-{chat_id}",
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
        events.reverse()
        return events
    except Exception as exc:
        logger.warning(f"fetch_events failed chat={chat_id}: {exc}")
        return []


# ── Global KB persistence ─────────────────────────────────────────

async def _save_global_kb() -> None:
    memory_id = os.getenv("AGENTBASE_MEMORY_ID", "")
    if not memory_id or (not _global_service_map and not _global_gateway_map):
        return
    try:
        from greennode_agentbase.memory.models import EventCreateRequest, EventPayload
        payload = json.dumps({
            "service_namespace_map": _global_service_map,
            "gateway_map": _global_gateway_map,
        }, ensure_ascii=False)
        request = EventCreateRequest(
            payload=EventPayload(type="conversational", role="system", message=payload),
        )
        await _get_memory_client().create_event_async(
            id=memory_id,
            actorId=_GLOBAL_ACTOR,
            sessionId=_GLOBAL_SESSION,
            request=request,
        )
        logger.info(
            f"[GlobalKB] saved {len(_global_service_map)} services, "
            f"{len(_global_gateway_map)} gateways to memory"
        )
    except Exception as exc:
        logger.warning(f"[GlobalKB] save failed: {exc}")


async def _load_global_kb() -> None:
    memory_id = os.getenv("AGENTBASE_MEMORY_ID", "")
    if not memory_id:
        return
    try:
        client = _get_memory_client()
        result = await client.list_events_async(
            id=memory_id,
            actorId=_GLOBAL_ACTOR,
            sessionId=_GLOBAL_SESSION,
            page=1,
            size=50,
        )
        events = list(getattr(result, "list_data", []))
        if not events:
            logger.info("[GlobalKB] no persisted data found, starting fresh")
            return
        latest = events[0]
        payload = getattr(latest, "payload", None)
        msg = getattr(payload, "message", "") if payload else ""
        data = json.loads(msg)
        loaded_svc = data.get("service_namespace_map", {})
        loaded_gw = data.get("gateway_map", {})
        _global_service_map.update(loaded_svc)
        _global_gateway_map.update(loaded_gw)
        logger.info(
            f"[GlobalKB] loaded {len(loaded_svc)} services, "
            f"{len(loaded_gw)} gateways from memory"
        )
    except Exception as exc:
        logger.warning(f"[GlobalKB] load failed: {exc}")


# ── Lifespan (startup/shutdown) ───────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _load_global_kb()
    yield


app = FastAPI(title="FirstMate Memory Agent", lifespan=lifespan)


# ── Consolidation logic ───────────────────────────────────────────

async def _run_consolidation(chat_id: str) -> Optional[dict]:
    events = await _fetch_events(chat_id)
    if len(events) < 3:
        logger.info(f"[Consolidator] too few events ({len(events)}) for chat={chat_id}, skip")
        return None

    conversation = "\n".join(
        f"[{e.get('role', '?')}] {e.get('message', '')}"
        for e in events[-40:]
    )
    if len(conversation) > 80_000:
        conversation = conversation[-80_000:]

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    logger.info(f"[Consolidator] chat={chat_id} calling LLM with {len(conversation)} chars")

    llm = ChatOpenAI(
        model="qwen/qwen3-5-27b",
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        api_key=os.environ["GREENNODE_API_KEY"],
        temperature=0,
        max_tokens=8192,
        timeout=120,
        max_retries=0,
    )

    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=f"Lịch sử hội thoại ({len(events)} events):\n\n{conversation}"),
    ])

    raw = response.content.strip()
    think_len = len(re.findall(r"<think>.*?</think>", raw, flags=re.DOTALL))
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    logger.info(f"[Consolidator] LLM response: think_blocks={think_len} json_len={len(raw)} preview={raw[:100]!r}")

    try:
        facts = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[Consolidator] JSON parse failed (len={len(raw)}), attempting repair")
        # Try truncating at the last closing brace to recover partial JSON
        repaired = None
        last_brace = raw.rfind("}")
        while last_brace > 0:
            try:
                repaired = json.loads(raw[:last_brace + 1])
                break
            except json.JSONDecodeError:
                last_brace = raw.rfind("}", 0, last_brace)
        if repaired is None:
            logger.error("[Consolidator] JSON repair failed, skipping")
            return None
        facts = repaired
        logger.info("[Consolidator] JSON repaired successfully")

    # Merge service map
    new_services = facts.get("service_namespace_map", {})
    svc_added = 0
    if new_services:
        before = len(_global_service_map)
        _global_service_map.update(new_services)
        svc_added = len(_global_service_map) - before

    # Merge gateway map
    new_gateways = facts.get("gateway_map", {})
    gw_added = 0
    if new_gateways:
        before = len(_global_gateway_map)
        _global_gateway_map.update(new_gateways)
        gw_added = len(_global_gateway_map) - before

    if svc_added or gw_added:
        logger.info(
            f"[Consolidator] chat={chat_id} "
            f"+{svc_added} services (total={len(_global_service_map)}), "
            f"+{gw_added} gateways (total={len(_global_gateway_map)}): {list(_global_gateway_map.keys())}"
        )
        asyncio.ensure_future(_save_global_kb())
    else:
        logger.info(
            f"[Consolidator] chat={chat_id} no new data "
            f"issues={len(facts.get('known_issues', []))} active={facts.get('active_services', [])}"
        )

    return facts


# ── Endpoints ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "firstmate-memory",
        "global_services": len(_global_service_map),
        "global_gateways": len(_global_gateway_map),
    }


async def _consolidate_bg(chat_id: str) -> None:
    try:
        facts = await _run_consolidation(chat_id)
        if facts:
            _kb_cache[chat_id] = facts
    except Exception as exc:
        logger.error(f"consolidate bg error chat={chat_id}: {exc}")


@app.post("/consolidate/{chat_id}", status_code=202)
async def consolidate(chat_id: str):
    asyncio.ensure_future(_consolidate_bg(chat_id))
    return {"status": "accepted", "chat_id": chat_id}


@app.get("/kb/global")
async def get_global_kb():
    return {
        "service_namespace_map": _global_service_map,
        "gateway_map": _global_gateway_map,
    }


@app.get("/kb/{chat_id}")
async def get_kb(chat_id: str):
    if chat_id not in _kb_cache:
        try:
            facts = await _run_consolidation(chat_id)
            if facts:
                _kb_cache[chat_id] = facts
        except Exception as exc:
            logger.warning(f"get_kb auto-consolidate failed chat={chat_id}: {exc}")

    user_kb = _kb_cache.get(chat_id) or {}

    merged_service_map = dict(_global_service_map)
    merged_service_map.update(user_kb.get("service_namespace_map", {}))

    merged_gateway_map = dict(_global_gateway_map)
    merged_gateway_map.update(user_kb.get("gateway_map", {}))

    return {
        **user_kb,
        "service_namespace_map": merged_service_map,
        "gateway_map": merged_gateway_map,
    }


@app.delete("/kb/{chat_id}")
async def clear_kb(chat_id: str):
    _kb_cache.pop(chat_id, None)
    return {"status": "cleared", "chat_id": chat_id}


@app.delete("/kb/global/reset")
async def reset_global_kb():
    _global_service_map.clear()
    _global_gateway_map.clear()
    return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
