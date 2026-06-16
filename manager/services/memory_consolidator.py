"""
Memory Consolidator Agent — đọc conversation events từ AgentBase Memory,
tổng hợp thành structured facts (dynamic KB) dùng cho LLM context.

Chạy sau mỗi job hoàn thành (non-blocking, fire-and-forget).
Kết quả lưu vào _dynamic_kb[chat_id] trong webhook.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("firstmate.consolidator")

_SYSTEM_PROMPT = """/no_think
Bạn là Memory Consolidator Agent của FirstMate.
Nhiệm vụ: đọc lịch sử hội thoại giữa developer/QC và SRE, trích xuất facts có giá trị lâu dài.

Trả về JSON thuần (không markdown block):
{
  "service_namespace_map": {
    "<service-name>": "<namespace>"
  },
  "known_issues": [
    "<mô tả ngắn vấn đề đã gặp — service, triệu chứng, root cause nếu biết>"
  ],
  "environment_notes": "<ghi chú về môi trường: cluster, conventions, đặc thù hệ thống>",
  "active_services": ["<service đang được team quan tâm gần đây>"]
}

Rules:
- service_namespace_map: CHỈ điền khi biết chắc namespace từ conversation (không đoán)
- known_issues: tối đa 10 issues gần nhất, ưu tiên issues chưa được giải quyết hoặc có thể tái diễn
- environment_notes: ngắn gọn, 1-3 câu, thông tin structural (không phải trạng thái tạm thời)
- active_services: tối đa 5 services, dựa trên tần suất xuất hiện gần đây
- Bỏ qua thông tin nhạy cảm (credentials, tokens, IPs nội bộ)
- Nếu không đủ thông tin cho field nào, để array/object rỗng hoặc string rỗng
"""


async def consolidate(chat_id: str) -> dict | None:
    """Đọc memory events của chat_id, dùng LLM trích xuất structured facts.

    Returns dict facts hoặc None nếu không đủ dữ liệu / lỗi.
    """
    try:
        from manager.services.memory_save import get_memory_service
        svc = get_memory_service()
        if not svc:
            logger.debug("[Consolidator] memory service not configured, skip")
            return None

        events = await svc.get_all_recent_events(actor_id=chat_id, limit=50)
        if len(events) < 3:
            logger.debug(f"[Consolidator] too few events ({len(events)}), skip")
            return None

        conversation = "\n".join(
            f"[{e.get('role', '?')}] {e.get('message', '')[:400]}"
            for e in events[-40:]
        )

        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatOpenAI(
            model="qwen/qwen3-5-27b",
            base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
            api_key=os.environ["GREENNODE_API_KEY"],
            temperature=0,
            max_tokens=2048,
            timeout=30,
            max_retries=0,
        )

        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"Lịch sử hội thoại ({len(events)} events):\n\n{conversation}"),
        ])

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        # Strip <think> tags if present
        import re
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        facts = json.loads(raw)
        logger.info(
            f"[Consolidator] chat={chat_id} "
            f"services={list(facts.get('service_namespace_map', {}).keys())} "
            f"issues={len(facts.get('known_issues', []))} "
            f"active={facts.get('active_services', [])}"
        )
        return facts

    except Exception as exc:
        logger.warning(f"[Consolidator] failed for chat={chat_id}: {exc}")
        return None
