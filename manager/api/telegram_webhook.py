from __future__ import annotations

import json
import logging
import os
import asyncio
import traceback
import uuid
from collections import deque

from fastapi import APIRouter, Request
from langgraph.types import Command
from pydantic import BaseModel
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, CallbackQuery

from manager.graph import graph
from shared.models import JobStatus

logger = logging.getLogger("firstmate.webhook")

router = APIRouter(prefix="/webhook", tags=["telegram"])

# ── Config ────────────────────────────────────────────────────────
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

# ── AgentBase Memory service ──────────────────────────────────────

async def _save_memory(actor_id: str, content: str, role: str = "user",
                       session_id: str = ""):
    """Save conversation event to AgentBase Memory (best-effort, non-blocking)."""
    try:
        from manager.services.memory_save import get_memory_service
        svc = get_memory_service()
        if not svc:
            return
        result = await svc.save(
            actor_id=actor_id,
            content=content,
            source="conversation",
            metadata={"role": role, "session_id": session_id},
        )
        logger.info(f"[Memory] {result.get('status')} actor={actor_id} role={role} len={len(content)}")
    except Exception as exc:
        logger.error(f"[Memory] save failed: {exc}")

# ── Error log ────────────────────────────────────────────────────
error_log: deque = deque(maxlen=100)

# ── State ─────────────────────────────────────────────────────────
# job_id → {requester_chat_id, requester_name, service, text, sre_id}
_debug_jobs: dict[str, dict] = {}

# chat_id → conversation context (ai biết đang trong job nào, vai trò gì)
# role: "dev_qc" | "sre"  /  status: "active" | "completed"
_active_conv: dict[str, dict] = {}

# chat_id → deque of last _MAX_HISTORY messages (raw text) — ngữ cảnh cho LLM phân loại
_chat_history: dict[str, deque] = {}
_MAX_HISTORY = 20


_last_saved: dict[str, str] = {}  # chat_id → last content hash (dedup)

def _add_to_history(chat_id: str, content: str, role: str = "user") -> None:
    if chat_id not in _chat_history:
        _chat_history[chat_id] = deque(maxlen=_MAX_HISTORY)
    _chat_history[chat_id].append(content)
    # Dedup: skip if same chat_id + role + content just saved
    dedup_key = f"{chat_id}:{role}:{content[:100]}"
    if _last_saved.get(chat_id) == dedup_key:
        return
    _last_saved[chat_id] = dedup_key
    # Save event to AgentBase Memory (non-blocking)
    conv = _active_conv.get(chat_id, {})
    session_id = conv.get("job_id", f"chat-{chat_id}")
    asyncio.ensure_future(_save_memory(
        actor_id=chat_id, content=content, role=role, session_id=session_id,
    ))


_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    return _bot


async def _safe_task(coro, chat_id: str):
    """Wrapper cho create_task: catch exception và notify user thay vì swallow silently."""
    try:
        await coro
    except Exception as exc:
        tb = traceback.format_exc()
        error_log.append({"error": str(exc), "traceback": tb, "source": "task"})
        logger.error(f"task error chat={chat_id}: {exc}\n{tb}")
        try:
            await get_bot().send_message(
                chat_id,
                f"❌ Lỗi xử lý: `{type(exc).__name__}: {str(exc)[:200]}`\n\n"
                f"Dùng /debug để xem chi tiết.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# MAIN WEBHOOK — nhận TẤT CẢ updates từ Telegram (message + callback)
# Bug trước: /webhook/telegram/callback không bao giờ được gọi vì
# Telegram gửi callback_query đến cùng 1 URL với message
# ══════════════════════════════════════════════════════════════════

@router.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, get_bot())

    # ── Callback query (button click) ────────────────────────────
    if update.callback_query:
        asyncio.create_task(_handle_callback(update.callback_query))
        return {"ok": True}

    # ── Regular message ──────────────────────────────────────────
    if not update.message or not update.message.text:
        return {"ok": True}

    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    user = update.message.from_user
    requester_name = user.full_name or user.username or chat_id

    # ── Special commands ─────────────────────────────────────────
    if text in ("/start", "/myid"):
        await get_bot().send_message(
            chat_id,
            f"👋 Xin chào *{requester_name}*!\n\n"
            f"Telegram ID: `{chat_id}`\n\n"
            f"Điền vào `telegram_id` trong `services.yaml`.\n"
            f"_(DEBUG: {'ON' if DEBUG_MODE else 'OFF'})_",
            parse_mode="Markdown",
        )
        return {"ok": True}

    if text == "/debug":
        from manager.services.kb_loader import _load_raw as _kb_raw
        kb = _kb_raw()
        kb_summary = (f"servers={len(kb.get('servers',[]))} "
                      f"namespaces={len(kb.get('namespaces',[]))} "
                      f"services={len(kb.get('services',[]))}") if kb else "empty"
        lines = [f"🔍 *Debug — FirstMate*\n_(DEBUG: {'ON' if DEBUG_MODE else 'OFF'})_\n"]
        lines.append(f"*Active convs* ({len(_active_conv)}):")
        for cid, c in _active_conv.items():
            lines.append(f"  `{cid[:10]}` role={c.get('role')} status={c.get('status')} job=`{c.get('job_id','')[:8]}`")
        lines.append(f"\n*Active jobs* ({len(_debug_jobs)})")
        lines.append(f"\n*Knowledge Base*: {kb_summary}")
        lines.append(f"\n*Recent errors* ({len(error_log)}):")
        for e in list(error_log)[-5:]:
            lines.append(f"  • {str(e.get('error',''))[:120]}")
        await get_bot().send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        return {"ok": True}

    if text == "/kb":
        from manager.services.kb_loader import format_for_prompt as kb_fmt
        content = kb_fmt()
        if not content:
            await get_bot().send_message(chat_id, "Knowledge Base trống. Xem `manager/config/knowledge_base.yaml`.")
        else:
            await _send_long_message(chat_id, _md_to_html(content))
        return {"ok": True}

    if text.startswith("/follow"):
        action = text[len("/follow"):].strip()
        if not action:
            await get_bot().send_message(
                chat_id,
                "Usage: `/follow <action>`\n\nVí dụ:\n"
                "• `/follow scale lên 13 pod`\n"
                "• `/follow restart loyalty-reward-store`\n\n"
                "_Firstmate sẽ dùng context từ task trước để điền service nếu thiếu._",
                parse_mode="Markdown",
            )
            return {"ok": True}
        # Đọc service từ conv cũ TRƯỚC khi xóa (dùng làm fallback nếu action không nêu rõ service)
        prev_service = _active_conv.get(chat_id, {}).get("service")
        if chat_id in _active_conv:
            del _active_conv[chat_id]
        _add_to_history(chat_id, action)
        asyncio.create_task(_safe_task(
            _classify_and_respond(chat_id, action, requester_name, detected_service=prev_service, force_action=True),
            chat_id,
        ))
        return {"ok": True}

    # Ghi lịch sử (không ghi /commands)
    _add_to_history(chat_id, text)

    # ── Fast-path: completion keywords khi không có conv → không classify ──
    _DONE_KW = {"done", "xong", "hoàn thành", "xong rồi", "đã xong",
                "finish", "finished", "xong hết", "ok done", "done rồi"}
    if text.lower().strip() in _DONE_KW and chat_id not in _active_conv:
        await get_bot().send_message(
            chat_id,
            "Không có task nào đang hoạt động. Nhắn yêu cầu mới nếu cần hỗ trợ.",
        )
        return {"ok": True}

    # ── Nếu đang trong conv (active hoặc completed) → route ──────
    if chat_id in _active_conv:
        asyncio.create_task(_safe_task(_route_message(chat_id, text, requester_name), chat_id))
        return {"ok": True}

    # ── Không trong conv → phân loại: câu hỏi hay action ──────────
    asyncio.create_task(_safe_task(_classify_and_respond(chat_id, text, requester_name), chat_id))
    return {"ok": True}


# ── Giữ endpoint cũ để tránh 404 nếu có gì redirect đến ─────────
@router.post("/telegram/callback")
async def telegram_callback_legacy(request: Request):
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# ROUTE MESSAGE — phân loại follow-up hay task mới khi conv completed
# ══════════════════════════════════════════════════════════════════

async def _route_message(chat_id: str, text: str, name: str):
    """Định tuyến tin nhắn vào conv hiện tại hoặc bắt đầu task mới."""
    conv = _active_conv.get(chat_id)
    if not conv:
        # Race condition fallback
        await _classify_and_respond(chat_id, text, name)
        return

    # Đang chờ xác nhận action vague → xử lý confirm/cancel
    if conv.get("role") == "pending_confirm":
        await _handle_pending_confirm(chat_id, text, name, conv)
        return

    # Đang chờ user chọn loại task → xử lý reply (text thay vì button)
    if conv.get("role") == "pending_type_select":
        await _handle_pending_type_select_text(chat_id, text, name, conv)
        return

    # SRE → xử lý conversation reply trực tiếp (không classify)
    if conv.get("role") == "sre":
        await _handle_conversation_reply(chat_id, text, name)
        return

    # Dev/QC → classify + synthesize trong 1 LLM call
    bot = get_bot()
    await bot.send_message(chat_id, "⌛ Đang phân tích...")

    history = list(_chat_history.get(chat_id, []))
    try:
        result = await _classify_and_synthesize(text, history)
    except Exception as exc:
        error_log.append({"error": f"{type(exc).__name__}: {exc}", "source": "classify_and_synthesize"})
        logger.error(f"classify_and_synthesize error: {exc}")
        result = {"type": "action", "service": None, "task_kind": "k8s", "task_description": text}

    rtype = result.get("type", "action")
    logger.info(f"route chat={chat_id} status={conv.get('status')} type={rtype} service={result.get('service')} text={text[:60]!r}")

    # Knowledge → manager trả lời luôn
    if rtype == "knowledge":
        answer = result.get("answer", "")
        if answer:
            await _send_long_message(chat_id, _md_to_html(answer))
            _add_to_history(chat_id, answer, role="assistant")
        else:
            await bot.send_message(chat_id, "⚠️ Không có câu trả lời.")
        return

    # Action chưa rõ và conv chưa done → followup cho conv hiện tại
    if conv.get("status") != "completed":
        await _handle_conversation_reply(chat_id, text, name)
        return

    # Conv đã xong → xác định new task hay followup
    try:
        chat_type = await _classify_chat(chat_id, text, conv.get("service"))
    except Exception as exc:
        error_log.append({"error": f"{type(exc).__name__}: {exc}", "source": "classify_chat"})
        logger.error(f"classify_chat error: {exc}")
        chat_type = "followup"

    logger.info(f"classify chat_id={chat_id} → {chat_type} text={text[:60]!r}")

    if chat_type == "new_task":
        del _active_conv[chat_id]
        if rtype == "action":
            await _start_new_task(
                chat_id, text, name,
                task_kind=result.get("task_kind", "k8s"),
                service=result.get("service"),
                task_description=result.get("task_description"),
            )
        else:
            # clarify / unknown → classify_and_respond xử lý (hỏi user)
            await _classify_and_respond(chat_id, text, name)
    else:
        await _handle_conversation_reply(chat_id, text, name)


# ══════════════════════════════════════════════════════════════════
# PRE-ANALYSIS — read memory before escalating to SRE
# ══════════════════════════════════════════════════════════════════

async def _pre_analyze_with_memory(chat_id: str, text: str, service: str) -> str | None:
    """Read short-term memory and try to answer without SRE.

    Returns an answer string if AI can handle it, or None if SRE escalation needed.
    """
    try:
        from manager.services.memory_save import get_memory_service
        svc = get_memory_service()

        # Gather context: local history + memory events
        local_history = list(_chat_history.get(chat_id, []))
        memory_events = []
        if svc:
            memory_events = await svc.get_all_recent_events(actor_id=chat_id, limit=20)

        # Build context string from memory
        memory_context = ""
        if memory_events:
            lines = []
            for evt in memory_events[-15:]:  # last 15 events
                role = evt.get("role", "?")
                msg = evt.get("message", "")[:500]
                lines.append(f"[{role}] {msg}")
            memory_context = "\n".join(lines)

        # If no memory context and no local history, can't pre-analyze
        if not memory_context and not local_history:
            return None

        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        from manager.services.kb_loader import format_for_prompt as kb_prompt

        llm = ChatOpenAI(
            model="qwen/qwen3-5-27b",
            base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
            api_key=os.environ["GREENNODE_API_KEY"],
            temperature=0,
            max_tokens=2048,
            timeout=15,
            max_retries=0,
        )

        kb = kb_prompt() or ""
        system = (
            "/no_think\n"
            "Bạn là FirstMate — AI assistant DevOps/SRE.\n\n"
            "NHIỆM VỤ: Phân tích yêu cầu của user dựa trên context có sẵn. "
            "Trả lời user TRƯỚC nếu có thể, CHỈ escalate SRE khi thật sự cần.\n\n"
            "BẠN CÓ THỂ TRẢ LỜI khi:\n"
            "- Thông tin đã có trong memory/context (kết quả check trước đó)\n"
            "- Câu hỏi về kiến thức DevOps/K8s chung\n"
            "- So sánh, tổng hợp từ dữ liệu đã thu thập\n"
            "- Giải thích lỗi/log đã có trong context\n\n"
            "CHỈ ESCALATE SRE khi:\n"
            "- Cần truy cập live system (kubectl, SSH, DB, Redis)\n"
            "- Cần quyền admin/production\n"
            "- Cần kiểm tra log/metric realtime\n"
            "- Memory không đủ thông tin để kết luận\n"
            "- Có nguy cơ incident, mất dữ liệu, bảo mật\n\n"
            "TRẢ VỀ JSON:\n"
            '- Tự trả lời: {"can_answer": true, "answer": "<Markdown tiếng Việt>"}\n'
            '- Cần SRE: {"can_answer": false, "reason": "<lý do cụ thể cần SRE check gì>"}\n\n'
            "Khi escalate, nói CỤ THỂ SRE cần check gì, ví dụ:\n"
            '"Cần SRE check logs của pod loyalty-tier-core trong namespace production vì memory không có thông tin lỗi gần đây."\n\n'
            "KHÔNG nói chung chung 'hãy hỏi SRE kiểm tra'.\n"
        )
        if kb:
            system += f"\n--- Knowledge Base ---\n{kb}\n"

        user_msg = f"Service: {service}\nYêu cầu: {text}\n"
        if memory_context:
            user_msg += f"\n--- Short-term memory (recent events) ---\n{memory_context}\n"
        if local_history:
            user_msg += f"\n--- Chat history ---\n" + "\n".join(local_history[-5:]) + "\n"

        response = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=user_msg),
        ])

        raw = response.content.strip()
        raw = _strip_think_tags(raw)
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        result = json.loads(raw)

        if result.get("can_answer"):
            answer = result.get("answer", "")
            if answer and len(answer) >= 20:
                logger.info(f"[PreAnalysis] answered from memory chat={chat_id} len={len(answer)}")
                return answer

        # Can't answer — log reason for debugging
        reason = result.get("reason", "unknown")
        logger.info(f"[PreAnalysis] escalate chat={chat_id} reason={reason[:100]}")
        return None

    except Exception as exc:
        logger.warning(f"[PreAnalysis] failed, escalating: {exc}")
        return None


async def _start_new_task(
    chat_id: str, text: str, requester_name: str,
    task_kind: str = "k8s", service: str | None = None,
    task_description: str | None = None,
):
    """Tạo job mới — nhưng trước hết phân tích memory xem có thể trả lời không cần SRE."""
    if service is None or service == "unknown":
        service = _detect_service(text)

    # ── Pre-analysis: read memory → try to answer before escalating ──
    pre_result = await _pre_analyze_with_memory(chat_id, text, service)
    if pre_result:
        # AI answered without SRE — done
        await _send_long_message(chat_id, _md_to_html(pre_result))
        _add_to_history(chat_id, pre_result, role="assistant")
        return

    # ── Need SRE — escalate ──
    job_id = str(uuid.uuid4())

    if DEBUG_MODE:
        logger.info(f"[DEBUG] new job={job_id[:8]} service={service} kind={task_kind} from={chat_id}")
        _active_conv[chat_id] = {
            "role": "dev_qc",
            "job_id": job_id,
            "service": service,
            "description": text,
            "sre_chat_id": None,
            "status": "active",
        }
        await get_bot().send_message(chat_id, "🔍 Đang tìm SRE...")
        asyncio.create_task(
            _debug_forward_to_sre(job_id, text, chat_id, requester_name, service, task_kind=task_kind, synthesized_prompt=task_description)
        )
    else:
        logger.info(f"new job={job_id[:8]} service={service} from={chat_id}")
        initial_state = {
            "job_id": job_id, "user_message": text,
            "requester_telegram_id": chat_id, "requester_name": requester_name,
            "service": service, "env": "production",
            "messages": [], "findings": [], "write_ops": [],
            "assignment_attempts": [], "needs_lead_approval": False,
            "status": JobStatus.PENDING,
        }
        thread_config = {"configurable": {"thread_id": job_id, "actor_id": chat_id}}
        asyncio.create_task(_run_graph(job_id, initial_state, thread_config, chat_id))
        await get_bot().send_message(chat_id, "🔍 Đang kiểm tra...")


# ══════════════════════════════════════════════════════════════════
# CONVERSATION REPLY — phân tích free text của SRE / Dev/QC
# ══════════════════════════════════════════════════════════════════

async def _handle_conversation_reply(chat_id: str, text: str, name: str):
    conv = _active_conv.get(chat_id)
    if not conv:
        return

    bot = get_bot()

    if conv["role"] == "dev_qc":
        sre_chat = conv.get("sre_chat_id")
        service = conv.get("service", "unknown")
        if sre_chat:
            # Tạo job ID cho follow-up — SRE có thể click "Nhận task" để mở terminal mới
            orig_job = _debug_jobs.get(conv["job_id"], {})
            follow_job_id = str(uuid.uuid4())
            # Re-classify task_kind từ text mới (user có thể đổi loại task),
            # fallback về task_kind của job gốc nếu text không rõ ràng
            follow_kind = _quick_classify(text) or orig_job.get("task_kind", "k8s")
            if follow_kind == "knowledge":
                follow_kind = orig_job.get("task_kind", "k8s")
            _debug_jobs[follow_job_id] = {
                "requester_chat_id": chat_id,
                "requester_name": name,
                "service": service,
                "text": text,
                "task_kind": follow_kind,
                "sre_id": orig_job.get("sre_id", ""),
                "sre_chat_id": sre_chat,
                # Truyền kết quả điều tra trước để Claude không cần scan lại
                "prev_summary": orig_job.get("claude_summary"),
            }
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Nhận task",  callback_data=f"dbg:accepted:{follow_job_id}"),
                InlineKeyboardButton("🔄 Bận",        callback_data=f"dbg:busy:{follow_job_id}"),
                InlineKeyboardButton("❌ Từ chối",    callback_data=f"dbg:declined:{follow_job_id}"),
            ]])
            await bot.send_message(
                sre_chat,
                f"💬 *{name}* (requester) nhắn thêm về `{service}`:\n\n{text}",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            await bot.send_message(chat_id, "📤 Đã gửi cho SRE. Chờ xác nhận...")
        else:
            # SRE chưa accept → re-forward job gốc kèm message mới
            orig_job = _debug_jobs.get(conv["job_id"], {})
            if orig_job:
                orig_text = orig_job.get("text", "")
                orig_service = orig_job.get("service", service)
                combined = f"{orig_text}\n\n📎 Thêm từ requester: {text}" if text != orig_text else orig_text
                await bot.send_message(chat_id, "🔁 SRE chưa nhận task — đang gửi lại...")
                asyncio.create_task(
                    _debug_forward_to_sre(conv["job_id"], combined, chat_id, name, orig_service, task_kind=orig_job.get("task_kind", "k8s"))
                )
            else:
                await bot.send_message(chat_id, "⏳ Task đang chờ SRE nhận. Thông tin đã ghi nhận.")
        return

    if conv["role"] == "sre":
        requester = conv.get("requester_chat_id")
        job = _debug_jobs.get(conv["job_id"], {})
        name = conv.get("sre_display", name)  # dùng SRE-tiennt14 thay tên Telegram

        # Fast-path: keyword quen thuộc → bỏ qua LLM (~2s) hoàn toàn
        text_lower = text.lower().strip()
        _COMPLETE_KW = {"xong", "done", "hoàn thành", "xong rồi", "ok done",
                        "đã xong", "xong hết", "finish", "finished", "complete"}
        _COMPLETE_PHRASES = ["xong rồi", "đã fix", "đã xử lý", "fix xong",
                             "xử lý xong", "đã restart", "done rồi", "đã scale",
                             "đã rollback", "đã revert", "đã deploy"]
        _UPDATE_KW = {"ok", "đang check", "chờ tí", "đang xem", "đang điều tra", "đang xử lý"}

        if text_lower in _COMPLETE_KW or any(p in text_lower for p in _COMPLETE_PHRASES):
            analysis: dict = {"intent": "complete", "target": None, "summary": text}
        elif text_lower in _UPDATE_KW:
            analysis = {"intent": "update", "target": None, "summary": text}
        else:
            # Gọi LLM cho các trường hợp phức tạp
            try:
                analysis = await _analyze_sre_message(text, conv)
            except Exception as exc:
                logger.error(f"analyze error: {exc}")
                analysis = {"intent": "update", "target": None, "summary": text[:200]}

        intent = analysis.get("intent", "update")
        summary = analysis.get("summary", text[:200])
        target = analysis.get("target")

        logger.info(f"SRE reply job={conv['job_id'][:8]} intent={intent} target={target}")

        if intent == "complete":
            # Thông báo Dev/QC
            if requester:
                complete_msg = f"✅ SRE {name} báo hoàn thành\n\n{summary}"
                await bot.send_message(
                    requester,
                    f"✅ *SRE {name} báo hoàn thành*\n\n{summary}\n\n"
                    f"_Có yêu cầu tiếp theo? Nhắn thẳng vào đây hoặc dùng /follow <action>._",
                    parse_mode="Markdown",
                )
                _add_to_history(requester, complete_msg, role="assistant")
            await bot.send_message(chat_id, "✅ Task hoàn thành. Conv đã đóng.")
            # Đánh dấu dev/qc conv completed (giữ để hỏi lại nếu follow-up thiếu context)
            if requester and requester in _active_conv:
                _active_conv[requester]["status"] = "completed"
            # Đóng conv SRE — không cần nhắn gì thêm
            if chat_id in _active_conv:
                del _active_conv[chat_id]

        elif intent == "need_lead":
            # SRE cần Lead approve → tìm lead và notify
            lead = _find_runner_by_role(is_lead=True)
            if lead and lead.get("telegram_id"):
                lead_telegram = str(lead["telegram_id"])
                await bot.send_message(
                    lead_telegram,
                    f"📋 *SRE {name} cần Lead approval*\n\n"
                    f"Service: `{conv.get('service', 'unknown')}`\n"
                    f"Nội dung: {summary}\n\n"
                    f"_Job: `{conv['job_id']}`_",
                    parse_mode="Markdown",
                )
                await bot.send_message(
                    chat_id,
                    f"📤 Đã gửi yêu cầu approve tới Lead *{lead['id']}*.",
                    parse_mode="Markdown",
                )
            else:
                await bot.send_message(chat_id, "❌ Không tìm thấy Lead trong config.")

        elif intent == "reassign":
            # SRE muốn chuyển task → tìm người được chỉ định
            new_sre = _find_runner_by_name(target or "")
            if new_sre and new_sre.get("telegram_id"):
                new_telegram = str(new_sre["telegram_id"])
                new_job_id = str(uuid.uuid4())
                _debug_jobs[new_job_id] = {
                    "requester_chat_id": requester or chat_id,
                    "requester_name": job.get("requester_name", "?"),
                    "service": conv.get("service", "unknown"),
                    "text": conv.get("description", ""),
                    "sre_id": new_sre["id"],
                }
                msg = (
                    f"📟 *Task chuyển từ {name}*\n\n"
                    f"Service: `{conv.get('service')}`\n"
                    f"Yêu cầu: {conv.get('description', '')}\n\n"
                    f"_Job ID: `{new_job_id}`_"
                )
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Nhận task", callback_data=f"dbg:accepted:{new_job_id}"),
                    InlineKeyboardButton("🔄 Bận",       callback_data=f"dbg:busy:{new_job_id}"),
                    InlineKeyboardButton("❌ Từ chối",   callback_data=f"dbg:declined:{new_job_id}"),
                ]])
                await bot.send_message(new_telegram, msg, reply_markup=keyboard, parse_mode="Markdown")
                await bot.send_message(chat_id, f"📤 Đã chuyển task sang *{new_sre['id']}*.", parse_mode="Markdown")
                if requester:
                    await bot.send_message(
                        requester,
                        f"🔄 SRE *{name}* đã chuyển task sang *{new_sre['id']}*.",
                        parse_mode="Markdown",
                    )
                # Xóa conv của SRE cũ
                del _active_conv[chat_id]
            else:
                await bot.send_message(
                    chat_id,
                    f"❌ Không tìm thấy SRE tên `{target}` trong config.\n"
                    f"Các SRE hiện có: {', '.join(_list_sre_names())}",
                    parse_mode="Markdown",
                )

        elif intent == "question":
            # SRE hỏi Dev/QC → forward
            if requester:
                question_msg = f"❓ SRE {name} hỏi:\n\n{text}"
                await bot.send_message(
                    requester,
                    f"❓ *SRE {name} hỏi*:\n\n{text}\n\n_Trả lời thẳng vào đây để SRE nhận được._",
                    parse_mode="Markdown",
                )
                _add_to_history(requester, question_msg, role="assistant")
            await bot.send_message(chat_id, "📨 Đã gửi câu hỏi tới requester. Chờ phản hồi...")

        else:
            # update / other → forward cho Dev/QC như status update
            if requester:
                update_msg = f"📊 SRE {name} cập nhật:\n\n{summary}"
                await bot.send_message(
                    requester,
                    f"📊 *SRE {name} cập nhật*:\n\n{summary}",
                    parse_mode="Markdown",
                )
                _add_to_history(requester, update_msg, role="assistant")
            await bot.send_message(chat_id, "📨 Đã gửi cập nhật cho requester.")


# ══════════════════════════════════════════════════════════════════
# CALLBACK HANDLER — xử lý button clicks (dbg/sre/lead)
# ══════════════════════════════════════════════════════════════════

async def _safe_edit(query: CallbackQuery, text: str = None, reply_markup=None):
    """Edit callback message, ignoring expired query errors."""
    try:
        if text is not None:
            await query.edit_message_text(text)
        if reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
    except Exception:
        pass  # query expired or message unchanged


async def _handle_callback(query: CallbackQuery):
    try:
        try:
            await query.answer()
        except Exception:
            pass  # query already expired, continue processing
        parts = query.data.split(":")
        if len(parts) != 3:
            return

        kind, action, job_id = parts

        # ── type_select: user chọn loại task từ inline keyboard ──
        if kind == "type_select":
            task_kind = action  # "gateway_log" hoặc "k8s"
            user_chat_id = job_id  # job_id field thực ra là chat_id ở đây
            conv = _active_conv.get(user_chat_id)
            if not conv or conv.get("role") != "pending_type_select":
                return
            original_text = conv["original_text"]
            name = conv.get("requester_name", "?")
            del _active_conv[user_chat_id]
            label = "🌐 Gateway log" if task_kind == "gateway_log" else "☸️ Kubernetes"
            await query.edit_message_text(
                f"Yêu cầu: _{original_text}_\nLoại: *{label}*",
                parse_mode="Markdown",
            )
            await _classify_and_respond(user_chat_id, original_text, name)
            return

        sre_chat_id = str(query.from_user.id)

        # Dùng sre_id từ config (format: SRE-tiennt14), fallback về Telegram username
        from manager.services.config_loader import load_config as _lc
        _runners = _lc().get("runners", {})
        sre_display = next(
            (f"SRE-{info['sre_id'].split('@')[0]}"
             for info in _runners.values()
             if str(info.get("telegram_id")) == sre_chat_id),
            query.from_user.username or query.from_user.full_name or "SRE",
        )
        sre_name = sre_display  # dùng cho tất cả messages bên dưới

        # ── Debug mode ────────────────────────────────────────────
        if kind == "dbg":
            job_meta = _debug_jobs.get(job_id)
            if not job_meta:
                await _safe_edit(query, text="⚠️ Job không tìm thấy (server đã restart).")
                return

            requester = job_meta["requester_chat_id"]

            if action == "accepted":
                from manager.services.runner_registry import get_queue
                from shared.models import Job, JobType

                job = Job(
                    id=job_id,
                    type=JobType.INCIDENT,
                    task_kind=job_meta.get("task_kind", "k8s"),
                    service=job_meta["service"],
                    env="production",
                    description=job_meta["text"],
                    commands=[],
                    requester_telegram_id=requester,
                    requester_name=job_meta["requester_name"],
                    assigned_sre=job_meta["sre_id"],
                    prev_summary=job_meta.get("prev_summary"),
                    synthesized_prompt=job_meta.get("synthesized_prompt"),
                )
                await get_queue(job_meta["sre_id"]).put(job)
                logger.info(f"[DEBUG] job={job_id[:8]} accepted by {sre_name}, queued runner")

                # Lưu sre_chat_id vào _debug_jobs để dùng fallback khi _active_conv bị clear
                job_meta["sre_chat_id"] = sre_chat_id

                # Thêm SRE vào active conv
                _active_conv[sre_chat_id] = {
                    "role": "sre",
                    "job_id": job_id,
                    "service": job_meta["service"],
                    "description": job_meta["text"],
                    "requester_chat_id": requester,
                    "status": "active",
                    "sre_display": sre_name,
                }
                # Cập nhật sre_chat_id + status trong Dev/QC conv
                if requester in _active_conv:
                    _active_conv[requester]["sre_chat_id"] = sre_chat_id
                    _active_conv[requester]["status"] = "active"

                await get_bot().send_message(
                    requester,
                    f"✅ *{sre_name}* đã nhận task. Runner đang xử lý...\n\n"
                    f"_Bạn có thể nhắn thêm thông tin bất cứ lúc nào._",
                    parse_mode="Markdown",
                )
                await _safe_edit(query, text=(
                    f"✅ Đã nhận task\n"
                    f"Service: {job_meta['service']}\n\n"
                    f"Runner đang mở terminal Claude Code...\n"
                    f"Nhắn tin vào đây để cập nhật trạng thái (xong/cần lead/chuyển task)."
                ))

            elif action in ("busy", "declined"):
                label = "đang bận" if action == "busy" else "từ chối task"
                icon = "🔄" if action == "busy" else "❌"
                await _safe_edit(query, text=f"{icon} Đã báo {label}.")
                await get_bot().send_message(
                    requester,
                    f"{icon} *{sre_name}* {label}. Đang tìm SRE khác...",
                    parse_mode="Markdown",
                )
                # Try next SRE, excluding all already-tried ones
                tried = job_meta.get("tried_sres", [job_meta.get("sre_id", "")])
                asyncio.create_task(_debug_forward_to_sre(
                    job_id=job_id,
                    text=job_meta["text"],
                    chat_id=requester,
                    requester_name=job_meta["requester_name"],
                    service=job_meta["service"],
                    task_kind=job_meta.get("task_kind", "k8s"),
                    exclude=tried,
                ))

        # ── Claude done verify ────────────────────────────────────
        elif kind == "verify":
            job_meta = _debug_jobs.get(job_id)
            if not job_meta:
                await _safe_edit(query, text="⚠️ Job không tìm thấy.")
                return

            requester = job_meta.get("requester_chat_id")
            summary = job_meta.get("claude_summary", "(không có kết quả)")

            if action == "done":
                if requester:
                    html_summary = _md_to_html(summary)
                    header = "✅ <b>Kết quả điều tra từ FirstMate</b>\n\n"
                    footer = "\n\nCó yêu cầu tiếp theo? Nhắn thẳng vào đây hoặc dùng /follow &lt;action&gt;."
                    full = header + html_summary + footer
                    if len(full) <= 4000:
                        await get_bot().send_message(requester, full, parse_mode="HTML")
                    else:
                        await _send_long_message(requester, header + html_summary)
                        await get_bot().send_message(
                            requester,
                            "Có yêu cầu tiếp theo? Nhắn thẳng vào đây hoặc dùng /follow &lt;action&gt;.",
                            parse_mode="HTML",
                        )
                    # Save result to AgentBase Memory
                    _add_to_history(requester, summary, role="assistant")
                    # Đánh dấu dev/qc conv completed
                    if requester in _active_conv:
                        _active_conv[requester]["status"] = "completed"
                try:
                    await _safe_edit(query, reply_markup=None)
                except Exception:
                    pass
                await get_bot().send_message(sre_chat_id, "✅ Đã gửi kết quả. Task hoàn thành.")
                # Đóng conv SRE
                if sre_chat_id in _active_conv:
                    del _active_conv[sre_chat_id]

            elif action == "more":
                if requester:
                    await get_bot().send_message(
                        requester,
                        "⏳ *Thông báo*: Kết quả ban đầu đã có nhưng đang được SRE verify thêm.\n"
                        "Sẽ nhắn lại khi kiểm tra xong. Vui lòng chờ.",
                        parse_mode="Markdown",
                    )
                # Xóa buttons nhưng giữ nguyên nội dung summary
                try:
                    await _safe_edit(query, reply_markup=None)
                except Exception:
                    pass
                await get_bot().send_message(
                    sre_chat_id,
                    "🔍 Đang tiếp tục kiểm tra. Requester đã được thông báo chờ.\n"
                    "Nhắn kết quả cuối vào đây khi xong.",
                )

        # ── Normal LangGraph mode ─────────────────────────────────
        elif kind in ("sre", "lead"):
            requester_id = (_debug_jobs.get(job_id) or {}).get("requester_chat_id", "unknown")
            thread_config = {"configurable": {"thread_id": job_id, "actor_id": requester_id}}
            result = await graph.ainvoke(Command(resume=action), thread_config)
            state = graph.get_state(thread_config)
            if state.next:
                next_node = state.next[0]
                interrupt_data = state.tasks[0].interrupts[0].value if state.tasks else {}
                requester = result.get("requester_telegram_id", "")
                await _handle_langgraph_interrupt(job_id, next_node, interrupt_data, requester)
                await _safe_edit(query, text=f"✅ Đã nhận: {action}")
            else:
                final = result.get("final_report") or "✅ Xử lý xong."
                requester_id = result.get("requester_telegram_id", "")
                if requester_id:
                    await get_bot().send_message(requester_id, final, parse_mode="Markdown")
                await _safe_edit(query, text=f"✅ Hoàn thành — {action}")

    except Exception as exc:
        tb = traceback.format_exc()
        error_log.append({"error": str(exc), "traceback": tb})
        logger.error(f"callback error: {exc}\n{tb}")
        try:
            await _safe_edit(query, text=f"❌ Lỗi: {type(exc).__name__}: {str(exc)[:200]}")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# LLM — phân tích câu trả lời của SRE
# ══════════════════════════════════════════════════════════════════

async def _analyze_sre_message(text: str, conv: dict) -> dict:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model="qwen/qwen3-5-27b",
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        api_key=os.environ["GREENNODE_API_KEY"],
        temperature=0,
        timeout=15,
        max_retries=0,
    )

    system = """/no_think
Phân tích tin nhắn của SRE trong hệ thống quản lý incident/task DevOps.

Phân loại thành 1 intent và trả về JSON:
- complete: SRE báo hoàn thành ("xong rồi", "đã fix", "ok done", ...)
- need_lead: SRE cần Lead/Manager approve ("cần lead duyệt", "cần approval", ...)
- reassign: SRE chuyển task cho người khác ("nhắn X", "forward X", "chuyển cho X", ...)
- question: SRE hỏi requester thêm thông tin
- update: cập nhật trạng thái thông thường (mặc định)

Response format (JSON ONLY, no explanation):
{"intent": "...", "target": "tên người hoặc null", "summary": "tóm tắt tiếng Việt ngắn"}

VÍ DỤ:
"xong rồi, tôi đã restart pod payment" → {"intent": "complete", "target": null, "summary": "Đã restart pod payment"}
"tôi cần lead approve để scale up" → {"intent": "need_lead", "target": null, "summary": "Cần Lead approve scale up"}
"bận rồi, nhắn cho nhuttc giúp" → {"intent": "reassign", "target": "nhuttc", "summary": "Chuyển sang nhuttc"}
"bạn có thể gửi log cụ thể không?" → {"intent": "question", "target": null, "summary": "Hỏi log cụ thể"}
"đang check, chờ tí" → {"intent": "update", "target": null, "summary": "Đang điều tra"}"""

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=(
            f"Service: {conv.get('service', 'unknown')}\n"
            f"Task: {conv.get('description', '')[:150]}\n\n"
            f"Tin nhắn SRE: {text}"
        )),
    ])

    raw = response.content.strip()
    # Strip markdown code block nếu có
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        logger.warning(f"LLM parse failed: {raw!r}")
        return {"intent": "update", "target": None, "summary": text[:200]}


async def _classify_chat(chat_id: str, text: str, service: str | None = None) -> str:
    """Dùng LLM phân loại tin nhắn: 'followup' (tiếp tục job cũ) hay 'new_task' (yêu cầu mới).

    Chỉ gọi khi conv đã completed. Dùng lịch sử _MAX_HISTORY tin nhắn gần nhất.
    """
    history = list(_chat_history.get(chat_id, []))
    if len(history) < 2:
        return "new_task"

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model="qwen/qwen3-5-27b",
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        api_key=os.environ["GREENNODE_API_KEY"],
        temperature=0,
        timeout=15,
        max_retries=0,
    )

    history_text = "\n".join(f"• {m}" for m in history[-15:])
    service_ctx = f"\nService đang theo dõi: {service}" if service else ""

    system = """/no_think
Bạn là AI phân loại yêu cầu trong hệ thống DevOps support.

Xem lịch sử các tin nhắn gần đây và quyết định tin nhắn mới là:
- "followup": tiếp tục/liên quan đến yêu cầu cũ (cùng service, cùng vấn đề, ra lệnh tiếp theo, dùng đại từ "nó/đó")
- "new_task": yêu cầu mới hoàn toàn không liên quan đến lịch sử

followup ví dụ: "scale lên 11 pod", "restart lại đi", "kiểm tra lại xem sao", "pod đó thế nào rồi"
new_task ví dụ: "payment service đang lỗi 500" (khi đang nói về service khác), "check database user-service"

Trả về JSON: {"type": "followup"} hoặc {"type": "new_task"}"""

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=(
            f"Lịch sử tin nhắn:{service_ctx}\n{history_text}\n\n"
            f"Tin nhắn mới: {text}"
        )),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw).get("type", "new_task")
    except Exception:
        logger.warning(f"classify_chat parse failed: {raw!r}")
        return "followup"  # safe default: treat unknown as follow-up


import re as _re

_KNOWLEDGE_RE = _re.compile(
    r'(là gì|là cái gì|nghĩa là gì'
    r'|what is |what does |how does |explain |giải thích '
    r')',
    _re.IGNORECASE,
)
# domain-like pattern: ít nhất 2 dấu chấm, không phải IP (full hoặc partial)
# (?<![0-9]\.) block match bắt đầu từ giữa IP (vd: 102.5.66 trong 118.102.5.66)
_DOMAIN_RE = _re.compile(
    r'\b(?<![0-9]\.)(?!(?:\d{1,3}\.){3}\d{1,3}\b)[a-z0-9]([a-z0-9-]*[a-z0-9])?'
    r'(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?){2,}\b',
    _re.IGNORECASE,
)
_GATEWAY_KW_RE = _re.compile(
    r'\b(log gateway|nginx log|gateway log|check log gateway|access log)\b',
    _re.IGNORECASE,
)
_K8S_RE = _re.compile(
    r'\b(scale|restart|rollback|deploy|redeploy|xem log|check log|tail log'
    r'|số pod|số replica|đang (lỗi|crash|down|fail|chậm)'
    r'|bị (lỗi|crash|down|fail|oom|killed)'
    r'|không (chạy|start|up|respond|healthy)'
    r'|kubectl|namespace|deployment|pod|replica)\b'
    r'|^(check|kiểm tra) \S',
    _re.IGNORECASE,
)


def _quick_classify(text: str) -> str | None:
    """Regex pre-classifier — chỉ dùng cho knowledge rõ ràng (định nghĩa thuần túy).
    Mọi câu k8s/gateway_log đều qua LLM để classify và extract service chính xác.

    Returns 'knowledge' | None (→ LLM).
    """
    t = text.strip()
    if _KNOWLEDGE_RE.search(t):
        return "knowledge"
    return None


def _strip_think_tags(raw: str) -> str:
    """Xoá <think>...</think> blocks mà qwen3 đôi khi output dù có /no_think."""
    import re as _re2
    raw = _re2.sub(r"<think>.*?</think>", "", raw, flags=_re2.DOTALL)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


async def _classify_and_synthesize(
    text: str,
    history: list | None = None,
    detected_service: str | None = None,
) -> dict:
    """1 LLM call: classify + synthesize task description (replaces classify_and_answer + enrich_action + synthesize_task).

    Returns one of:
        {"type": "knowledge", "answer": "<markdown>"}
        {"type": "action", "service": "svc", "task_kind": "k8s"|"gateway_log", "task_description": "..."}
        {"type": "clarify", "service": "svc|null", "inferred": "...", "question": "..."}
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from manager.services.kb_loader import format_for_prompt as kb_prompt

    llm = ChatOpenAI(
        model="qwen/qwen3-5-27b",
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        api_key=os.environ["GREENNODE_API_KEY"],
        temperature=0,
        max_tokens=8192,
        timeout=90 if os.environ.get("DEBUG") else 30,
        max_retries=0,
    )

    kb = kb_prompt() or ""

    history_section = ""
    if history:
        history_text = "\n".join(f"• {m}" for m in history[-10:])
        history_section = f"\n=== LỊCH SỬ TRÒ CHUYỆN ===\n{history_text}\n"

    service_hint = ""
    if detected_service and detected_service != "unknown":
        service_hint = f'\nService đã biết từ context trước: "{detected_service}" — ưu tiên dùng nếu message thiếu service.\n'

    system = f"""/no_think
Bạn là FirstMate — AI assistant DevOps/SRE. Nhận tin nhắn từ developer/QC, phân tích và trả về JSON.
{history_section}{service_hint}
=== KNOWLEDGE BASE ===
{kb}

=== OUTPUT FORMAT (JSON thuần, không markdown block) ===

Câu hỏi kiến thức (không cần kubectl/SSH vào live system):
{{"type": "knowledge", "answer": "<markdown tiếng Việt>"}}

Action rõ ràng (có service):
{{"type": "action", "service": "kebab-case-name", "task_kind": "k8s" hoặc "gateway_log", "task_description": "**Objective**: ...\\n**Service**: ...\\n**Namespace**: ...\\n**Scope**: ...\\n**Output**: ..."}}

Action thiếu context (không biết service):
{{"type": "clarify", "service": null, "task_kind": "k8s", "inferred": "mô tả đầy đủ nhất có thể", "question": "Câu hỏi ngắn gọn"}}

=== PHÂN LOẠI ===
- knowledge: câu hỏi có thể trả lời không cần live system (định nghĩa, tính toán mạng, kiến thức tĩnh)
  VD: "statefulset là gì", "subnet /28 có bao nhiêu host", "IP 172.16.0.1 là public hay private"
- gateway_log: kiểm tra nginx gateway log (có domain 2+ dấu chấm, hoặc "log gateway")
- k8s: kiểm tra/thay đổi pod/deployment/service Kubernetes

=== RULES ===
- service LUÔN kebab-case (vd: loyalty-tier-core, payment-service)
- Nếu có lịch sử + action thiếu service → suy luận service từ lịch sử → type="action"
- Nếu không suy luận được service → type="clarify"
- task_description: ngắn gọn, đủ để agent thực thi không hỏi thêm

=== VÍ DỤ ===
Lịch sử: ["check pod loyalty-tier-core", "4/4 Running"]
Yêu cầu: "scale lên 12 pod"
→ {{"type": "action", "service": "loyalty-tier-core", "task_kind": "k8s", "task_description": "**Objective**: Scale loyalty-tier-core lên 12 pod\\n**Service**: loyalty-tier-core\\n**Scope**: kubectl scale deployment — KHÔNG đụng vào service khác\\n**Output**: Số pod sau khi scale, trạng thái rolling update"}}

Yêu cầu: "check số pod của loyalty tier core" (không có lịch sử)
→ {{"type": "action", "service": "loyalty-tier-core", "task_kind": "k8s", "task_description": "**Objective**: Kiểm tra số pod của loyalty-tier-core\\n**Service**: loyalty-tier-core\\n**Scope**: kubectl get pods + deployment status — KHÔNG cần logs hay events\\n**Output**: Số pod running/total, trạng thái từng pod"}}

Yêu cầu: "scale lên 12 pod" (không có lịch sử, không có service hint)
→ {{"type": "clarify", "service": null, "task_kind": "k8s", "inferred": "scale [?] lên 12 pod", "question": "Bạn muốn scale service nào lên 12 pod?"}}

Yêu cầu: "check log dev.zalopay.vn"
→ {{"type": "action", "service": "dev.zalopay.vn", "task_kind": "gateway_log", "task_description": "**Objective**: Kiểm tra log nginx gateway cho dev.zalopay.vn\\n**Service**: dev.zalopay.vn\\n**Scope**: query gateway log — KHÔNG cần kubectl\\n**Output**: Recent errors, status codes bất thường"}}

Yêu cầu: "statefulset là gì"
→ {{"type": "knowledge", "answer": "..."}}
"""

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=text),
    ])

    raw = response.content.strip()
    if not raw:
        raw = (response.additional_kwargs.get("reasoning_content") or "").strip()
    raw = _strip_think_tags(raw)

    try:
        result = json.loads(raw)
        logger.info(f"classify_synthesize type={result.get('type')} service={result.get('service')} text={text[:50]!r}")
        return result
    except Exception:
        logger.warning(f"classify_synthesize parse failed: {raw[:200]!r}")
        return {"type": "action", "service": None, "task_kind": "k8s", "task_description": text}


async def _classify_and_respond(
    chat_id: str, text: str, requester_name: str,
    detected_service: str | None = None,
    force_action: bool = False,
):
    """Unified entry: 1 LLM call để classify + synthesize, rồi xử lý kết quả."""
    bot = get_bot()
    await bot.send_message(chat_id, "⌛ Đang phân tích...")

    history = list(_chat_history.get(chat_id, []))
    try:
        result = await _classify_and_synthesize(text, history, detected_service=detected_service)
    except Exception as exc:
        error_log.append({"error": f"{type(exc).__name__}: {exc}", "source": "classify_and_synthesize"})
        logger.error(f"classify_and_synthesize error: {exc}")
        result = {"type": "action", "service": detected_service, "task_kind": "k8s", "task_description": text}

    rtype = result.get("type", "action")
    if force_action and rtype == "knowledge":
        rtype = "action"  # /follow luôn là action, không answer knowledge
    logger.info(f"classify_respond chat={chat_id} type={rtype} service={result.get('service')} text={text[:60]!r}")

    if rtype == "knowledge":
        answer = result.get("answer", "")
        if answer:
            await _send_long_message(chat_id, _md_to_html(answer))
            _add_to_history(chat_id, answer, role="assistant")
        else:
            await bot.send_message(chat_id, "⚠️ Không có câu trả lời.")
    elif rtype == "action":
        await _start_new_task(
            chat_id, text, requester_name,
            task_kind=result.get("task_kind", "k8s"),
            service=result.get("service") or detected_service,
            task_description=result.get("task_description"),
        )
    elif rtype == "clarify":
        service = result.get("service") or detected_service
        _active_conv[chat_id] = {
            "role": "pending_confirm",
            "confirmed_text": result.get("inferred", text),
            "original_text": text,
            "task_kind": result.get("task_kind", "k8s"),
            "service": service,
        }
        question = result.get("question") or f'Có phải bạn muốn "{result.get("inferred", text)}" không?'
        await bot.send_message(
            chat_id,
            f"{question}\n\n💡 _Dùng `/follow <action>` để Firstmate tự đọc context._",
            parse_mode="Markdown",
        )
    else:
        await _ask_task_kind(chat_id, text, requester_name)


# ══════════════════════════════════════════════════════════════════
# CONTEXT CLARIFICATION — action vague, cần xác nhận
# ══════════════════════════════════════════════════════════════════

_YES_WORDS = {"ok", "yes", "đúng", "đúng rồi", "phải", "ừ", "đồng ý", "đúng vậy",
              "y", "yeah", "yep", "oke", "okie", "có", "vâng", "ừa", "ok rồi", "đúng đó"}
_NO_WORDS  = {"không", "no", "sai", "hủy", "cancel", "không phải", "nhầm", "ko", "k"}


async def _ask_task_kind(chat_id: str, text: str, name: str):
    """Hỏi user loại task khi classifier không xác định được."""
    _active_conv[chat_id] = {
        "role": "pending_type_select",
        "original_text": text,
        "requester_name": name,
    }
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌐 Gateway log", callback_data=f"type_select:gateway_log:{chat_id}"),
        InlineKeyboardButton("☸️ Kubernetes",  callback_data=f"type_select:k8s:{chat_id}"),
    ]])
    await get_bot().send_message(
        chat_id,
        f"Yêu cầu này thuộc loại nào?\n\n_{text}_",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def _handle_pending_type_select_text(chat_id: str, text: str, name: str, conv: dict):
    """Xử lý nếu user gõ text thay vì bấm nút chọn loại task."""
    t = text.lower().strip()
    if any(w in t for w in ["gateway", "nginx", "log", "domain", "web"]):
        task_kind = "gateway_log"
    elif any(w in t for w in ["k8s", "kubernetes", "pod", "kube"]):
        task_kind = "k8s"
    else:
        await get_bot().send_message(chat_id, "Vui lòng bấm nút bên trên để chọn loại task.")
        return
    del _active_conv[chat_id]
    await _classify_and_respond(chat_id, conv["original_text"], name)




async def _handle_pending_confirm(chat_id: str, text: str, name: str, conv: dict):
    """Xử lý phản hồi của dev/qc sau khi manager hỏi xác nhận action."""
    bot = get_bot()
    text_lower = text.lower().strip()

    if text_lower in _YES_WORDS or any(w in text_lower for w in _YES_WORDS):
        # Xác nhận → forward với action đầy đủ
        confirmed = conv["confirmed_text"]
        task_kind = conv.get("task_kind", "k8s")
        conv_service = conv.get("service")
        del _active_conv[chat_id]
        await _start_new_task(chat_id, confirmed, name, task_kind=task_kind, service=conv_service)

    elif text_lower in _NO_WORDS or any(w in text_lower for w in _NO_WORDS):
        # Hủy
        del _active_conv[chat_id]
        await bot.send_message(chat_id, "Đã hủy. Vui lòng nhắn lại yêu cầu cụ thể hơn.")

    else:
        # Dev/QC cung cấp thêm info / sửa lại → re-classify với text mới
        del _active_conv[chat_id]
        await _classify_and_respond(chat_id, text, name)


async def _enrich_action(text: str, history: list) -> dict:
    """LLM suy luận service/target từ lịch sử khi action thiếu context."""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model="qwen/qwen3-5-27b",
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        api_key=os.environ["GREENNODE_API_KEY"],
        temperature=0,
        timeout=15,
        max_retries=0,
    )

    system = """/no_think
Bạn là AI phân tích yêu cầu DevOps.

Kiểm tra yêu cầu action mới có đủ thông tin để thực hiện không (cần rõ service/deployment name).
Dựa vào lịch sử trò chuyện, suy luận xem người dùng đang muốn action trên service nào.

LUÔN extract "service" từ context: tên service dạng kebab-case hoặc cụm từ gốc (vd: "loyalty-tier-core", "loyalty tier core"). Nếu không suy luận được → null.

Trả về JSON (không có markdown), LUÔN có "service" field:
- Nếu đủ context (action + service rõ): {"ready": true, "action": "mô tả đầy đủ", "service": "tên-service"}
- Nếu thiếu nhưng suy luận được: {"ready": false, "inferred": "mô tả đầy đủ", "question": "câu hỏi xác nhận ngắn", "service": "tên-service"}

Ví dụ:
Lịch sử: ["check pod loyalty-reward-store", "10 pod đang running"]
Yêu cầu: "scale lên 12 pod"
→ {"ready": false, "inferred": "scale loyalty-reward-store lên 12 pod", "question": "Có phải bạn muốn scale loyalty-reward-store lên 12 pod không?", "service": "loyalty-reward-store"}

Lịch sử: ["check số pod của loyalty tier core", "4/4 pod Running"]
Yêu cầu: "scale lên 12 pod"
→ {"ready": false, "inferred": "scale loyalty-tier-core lên 12 pod", "question": "Có phải bạn muốn scale loyalty-tier-core lên 12 pod không?", "service": "loyalty-tier-core"}

Lịch sử: ["deploy payment-service"]
Yêu cầu: "restart đi"
→ {"ready": false, "inferred": "restart payment-service", "question": "Bạn muốn restart payment-service không?", "service": "payment-service"}

Lịch sử: ["check log user-service"]
Yêu cầu: "scale loyalty-reward-store lên 5 pod"
→ {"ready": true, "action": "scale loyalty-reward-store lên 5 pod", "service": "loyalty-reward-store"}"""

    history_text = "\n".join(f"• {m}" for m in history[-10:])
    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=f"Lịch sử:\n{history_text}\n\nYêu cầu mới: {text}"),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        logger.warning(f"enrich_action parse failed: {raw!r}")
        return {"ready": True, "action": text}  # fallback: forward as-is


# ══════════════════════════════════════════════════════════════════
# TASK SYNTHESIS — manager tổng hợp task description cho Claude Code
# ══════════════════════════════════════════════════════════════════

async def _synthesize_task(user_message: str, service: str, task_kind: str) -> str:
    """Manager LLM tổng hợp thành structured task description cho Claude Code.

    Returns markdown task description hoặc raw user_message nếu fail.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from manager.services.kb_loader import format_for_prompt as kb_prompt

    try:
        llm = ChatOpenAI(
            model="qwen/qwen3-5-27b",
            base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
            api_key=os.environ["GREENNODE_API_KEY"],
            temperature=0,
            max_tokens=512,
            timeout=15,
            max_retries=0,
        )

        kb = kb_prompt() or ""
        system = f"""/no_think
Bạn là manager của một SRE agent (Claude Code). Dựa vào yêu cầu của dev/QC, tạo task description ngắn gọn, rõ ràng để giao cho agent thực thi.

Task description phải:
1. Objective rõ ràng (1 câu ngắn)
2. Service và namespace nếu biết
3. Scope: làm gì — không làm gì
4. Output cần báo cáo

Ngắn gọn, không giải thích thừa. Trả về markdown thuần túy, không JSON, không code block.

=== Knowledge Base ===
{kb}
"""
        examples = ""
        if task_kind == "k8s":
            examples = """Ví dụ:
Yêu cầu: "check số pod của loyalty-tier-core"
→
**Objective**: Kiểm tra số pod hiện tại của loyalty-tier-core
**Service**: loyalty-tier-core
**Scope**: kubectl get pods + deployment status — KHÔNG cần logs hay events
**Output**: Số pod running/total, trạng thái từng pod, có pod nào crash không"""

        response = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=f"{examples}\n\nYêu cầu: {user_message!r}\nService: {service}\nTask kind: {task_kind}"),
        ])

        raw = _strip_think_tags(response.content)
        if raw and len(raw) > 20:
            logger.info(f"synthesize_task service={service} len={len(raw)}")
            return raw
    except Exception as exc:
        logger.warning(f"synthesize_task failed: {exc}")

    return user_message  # fallback


# ══════════════════════════════════════════════════════════════════
# DEBUG MODE — forward task cho SRE
# ══════════════════════════════════════════════════════════════════

async def _debug_forward_to_sre(
    job_id: str, text: str, chat_id: str, requester_name: str, service: str,
    task_kind: str = "k8s", exclude: list[str] | None = None,
    synthesized_prompt: str | None = None,
):
    try:
        from manager.services.config_loader import load_config, get_sres_for_service
        cfg = load_config()
        runners = cfg.get("runners", {})
        exclude = exclude or []

        from manager.services.runner_registry import get_online_runner_for_sre

        sre_info: dict | None = None
        # 1) Service-aware + online: owner first, then SREs with services_owned
        for candidate in get_sres_for_service(service, exclude=exclude):
            if candidate.get("telegram_id") and get_online_runner_for_sre(candidate.get("sre_id", "")):
                sre_info = candidate
                break
        # 2) Fallback: any non-lead runner not excluded, must be online
        if not sre_info:
            for rid, info in runners.items():
                if rid in exclude or info.get("is_lead") or not info.get("telegram_id"):
                    continue
                if get_online_runner_for_sre(info.get("sre_id", "")):
                    sre_info = {"id": rid, **info}
                    break
        # 3) Last resort: any non-lead with telegram_id (offline, but can still approve via Telegram)
        if not sre_info:
            for candidate in get_sres_for_service(service, exclude=exclude):
                if candidate.get("telegram_id"):
                    sre_info = candidate
                    break
        if not sre_info:
            for rid, info in runners.items():
                if rid in exclude or info.get("is_lead") or not info.get("telegram_id"):
                    continue
                sre_info = {"id": rid, **info}
                break

        if not sre_info:
            await get_bot().send_message(
                chat_id,
                "❌ Không có SRE nào available. Vui lòng liên hệ trực tiếp.",
            )
            _cleanup_conv(job_id)
            return

        # Preserve tried list across retries
        tried = list(exclude) + [sre_info["id"]]

        _debug_jobs[job_id] = {
            "requester_chat_id": chat_id,
            "requester_name": requester_name,
            "service": service,
            "text": text,
            "synthesized_prompt": synthesized_prompt or text,
            "sre_id": sre_info["id"],
            "task_kind": task_kind,
            "tried_sres": tried,
        }

        sre_telegram = str(sre_info["telegram_id"])
        msg = (
            f"📟 *Task mới từ {requester_name}*\n\n"
            f"Service: `{service}`\n"
            f"Yêu cầu: {text}\n\n"
            f"_Write ops chỉ approve tại terminal local. Job: `{job_id}`_"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Nhận task",  callback_data=f"dbg:accepted:{job_id}"),
            InlineKeyboardButton("🔄 Bận",        callback_data=f"dbg:busy:{job_id}"),
            InlineKeyboardButton("❌ Từ chối",    callback_data=f"dbg:declined:{job_id}"),
        ]])
        await get_bot().send_message(
            sre_telegram, msg, reply_markup=keyboard, parse_mode="Markdown"
        )
        await get_bot().send_message(
            chat_id,
            f"📤 Đã gửi cho SRE *{sre_info['id']}*. Chờ xác nhận...\n"
            f"_(Nhắn thêm thông tin bất cứ lúc nào)_",
            parse_mode="Markdown",
        )
        logger.info(f"[DEBUG] job={job_id[:8]} sent to sre={sre_info['id']}")

    except Exception as exc:
        tb = traceback.format_exc()
        error_log.append({"job_id": job_id, "error": str(exc), "traceback": tb})
        logger.error(f"forward error job={job_id[:8]}: {exc}\n{tb}")
        await get_bot().send_message(chat_id, f"❌ Lỗi: {exc}")
        _cleanup_conv(job_id)


# ══════════════════════════════════════════════════════════════════
# NORMAL MODE — LangGraph interrupts
# ══════════════════════════════════════════════════════════════════

async def _run_graph(job_id: str, initial_state: dict, thread_config: dict, chat_id: str):
    try:
        result = await graph.ainvoke(initial_state, thread_config)
        state = graph.get_state(thread_config)
        if state.next:
            interrupt_data = state.tasks[0].interrupts[0].value if state.tasks else {}
            await _handle_langgraph_interrupt(job_id, state.next[0], interrupt_data, chat_id)
        else:
            final = result.get("final_report") or "✅ Xong."
            await get_bot().send_message(chat_id, final, parse_mode="Markdown")
    except Exception as exc:
        tb = traceback.format_exc()
        error_log.append({"job_id": job_id, "error": f"{type(exc).__name__}: {exc}", "traceback": tb})
        logger.error(f"graph error job={job_id[:8]}: {exc}\n{tb}")
        await get_bot().send_message(
            chat_id,
            f"❌ *Lỗi*\n```\n{type(exc).__name__}: {str(exc)[:300]}\n```",
            parse_mode="Markdown",
        )


async def _handle_langgraph_interrupt(
    job_id: str, next_node: str, interrupt_data: dict, requester_chat_id: str
):
    bot = get_bot()
    from manager.services.config_loader import load_config
    cfg = load_config()

    if next_node == "waiting_sre":
        sre_id = interrupt_data.get("sre_id", "")
        sre_tg = str(cfg["runners"].get(sre_id, {}).get("telegram_id", ""))
        description = interrupt_data.get("description", "")
        text = (
            f"📟 *FirstMate — Task mới*\n\nService: `{interrupt_data.get('service', '?')}`\n"
            f"{description}\n\n_Job: `{job_id}`_"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept",  callback_data=f"sre:accepted:{job_id}"),
            InlineKeyboardButton("🔄 Bận",    callback_data=f"sre:busy:{job_id}"),
            InlineKeyboardButton("❌ Từ chối",callback_data=f"sre:declined:{job_id}"),
        ]])
        await bot.send_message(sre_tg, text, reply_markup=keyboard, parse_mode="Markdown")

    elif next_node == "waiting_lead":
        lead_id = interrupt_data.get("lead_id", "")
        lead_tg = str(cfg["runners"].get(lead_id, {}).get("telegram_id", ""))
        runner_output = interrupt_data.get("runner_output", "")
        text = (
            f"📋 *Cần Lead approve*\n\nSRE đã xong:\n{runner_output}\n\n_Job: `{job_id}`_"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"lead:approved:{job_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"lead:rejected:{job_id}"),
        ]])
        await bot.send_message(lead_tg, text, reply_markup=keyboard, parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════
# RUNNER → MANAGER: Claude Code done
# ══════════════════════════════════════════════════════════════════

class _ClaudeDonePayload(BaseModel):
    job_id: str
    runner_id: str
    summary: str


@router.post("/job-complete")
async def job_complete(payload: _ClaudeDonePayload):
    """Runner gọi khi Claude Code hoàn thành — notify SRE để verify."""
    job = _debug_jobs.get(payload.job_id)
    if not job:
        return {"ok": False, "error": "job not found"}

    job["claude_summary"] = payload.summary
    # Note: summary saved to memory later when SRE clicks verify:done (avoids duplicate)

    # Tìm SRE chat_id từ active conv
    sre_chat = next(
        (cid for cid, c in _active_conv.items()
         if c.get("job_id") == payload.job_id and c.get("role") == "sre"),
        None,
    )
    if not sre_chat:
        # Fallback: dùng sre_chat_id đã lưu khi SRE accept (tồn tại dù _active_conv bị clear)
        sre_chat = job.get("sre_chat_id")
    if not sre_chat:
        logger.warning(f"job-complete: no SRE chat_id for job={payload.job_id[:8]}")
        return {"ok": False, "error": "no SRE chat_id — SRE chưa accept task?"}

    html_summary = _md_to_html(payload.summary)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Đã verify, gửi kết quả", callback_data=f"verify:done:{payload.job_id}"),
        InlineKeyboardButton("🔍 Cần check thêm",         callback_data=f"verify:more:{payload.job_id}"),
    ]])
    header = f"🤖 <b>FirstMate đã điều tra xong!</b>\n\n📊 <b>Kết quả:</b>\n"
    full_msg = header + html_summary + "\n\nVui lòng verify và xác nhận:"
    try:
        if len(full_msg) <= 4000:
            # Vừa một message → đính keyboard luôn
            await get_bot().send_message(sre_chat, full_msg, reply_markup=keyboard, parse_mode="HTML")
        else:
            # Quá dài → gửi toàn bộ nội dung trước (tự động split), keyboard ở message cuối
            await _send_long_message(sre_chat, header + html_summary)
            await get_bot().send_message(sre_chat, "Vui lòng verify và xác nhận:", reply_markup=keyboard, parse_mode="HTML")
    except Exception as exc:
        logger.error(f"job-complete send_message failed: {exc}")
        return {"ok": False, "error": f"send_message failed: {exc}"}
    logger.info(f"job-complete notified SRE={sre_chat} job={payload.job_id[:8]}")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

async def _send_long_message(chat_id: str, text: str, parse_mode: str = "HTML", **kwargs) -> None:
    """Gửi message dài bằng cách split ở ranh giới dòng (Telegram giới hạn 4096 ký tự)."""
    MAX = 4000  # buffer nhỏ hơn 4096 để tránh edge case
    if len(text) <= MAX:
        await get_bot().send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
        return
    # Split tại boundary dòng, ưu tiên đoạn trống
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX:
            if current:
                parts.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        parts.append(current.rstrip())
    for i, part in enumerate(parts):
        await get_bot().send_message(chat_id, part, parse_mode=parse_mode, **(kwargs if i == len(parts) - 1 else {}))


def _md_to_html(text: str) -> str:
    """Convert markdown → Telegram HTML. An toàn hơn MarkdownV2: chỉ cần escape &<>."""
    import re

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def inline_fmt(s: str) -> str:
        """Xử lý **bold** và `code` trong 1 dòng text thường."""
        out, i = [], 0
        while i < len(s):
            if s[i] == '`':
                j = s.find('`', i + 1)
                if j != -1:
                    out.append(f"<code>{esc(s[i+1:j])}</code>")
                    i = j + 1
                    continue
            if s[i:i+2] == '**':
                j = s.find('**', i + 2)
                if j != -1:
                    out.append(f"<b>{esc(s[i+2:j])}</b>")
                    i = j + 2
                    continue
            out.append(esc(s[i]))
            i += 1
        return ''.join(out)

    result = []
    for line in text.splitlines():
        # ## Header → <b>text</b>
        m = re.match(r'^#{1,3}\s+(.*)', line)
        if m:
            result.append(f"<b>{esc(m.group(1))}</b>")
            continue

        # Table separator |---|---| → skip
        if re.match(r'^\s*\|[-| :]+\|\s*$', line):
            continue

        # Table row | a | b | → • a — b
        if line.strip().startswith('|') and line.strip().endswith('|'):
            cells = [c.strip() for c in line.strip()[1:-1].split('|') if c.strip()]
            if cells:
                result.append('• ' + ' — '.join(esc(c) for c in cells))
            continue

        # List item (với indent tùy ý)
        m = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if m:
            result.append(m.group(1) + '• ' + inline_fmt(m.group(2)))
            continue

        result.append(inline_fmt(line))

    return '\n'.join(result)


def _detect_service(text: str) -> str:
    import re
    from manager.services.config_loader import load_config
    services = load_config().get("services", {})
    text_lower = text.lower()

    # Exact match với service trong config
    for svc in services:
        if svc in text_lower:
            return svc

    # Fallback: extract kebab-case hoặc snake_case word (dạng service name phổ biến)
    # VD: "loyalty-reward-store", "payment_service", "user-service"
    matches = re.findall(r'\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b', text_lower)
    if matches:
        return matches[0]

    return "unknown"


def _find_runner_by_role(is_lead: bool) -> dict | None:
    from manager.services.config_loader import load_config
    for rid, info in load_config().get("runners", {}).items():
        if bool(info.get("is_lead")) == is_lead:
            return {"id": rid, **info}
    return None


def _find_runner_by_name(name: str) -> dict | None:
    """Tìm SRE theo tên (fuzzy match runner_id hoặc sre_id)."""
    from manager.services.config_loader import load_config
    name_lower = name.lower().strip()
    for rid, info in load_config().get("runners", {}).items():
        if (name_lower in rid.lower()
                or name_lower in info.get("sre_id", "").lower()
                or name_lower in info.get("sre_id", "").split("@")[0].lower()):
            return {"id": rid, **info}
    return None


def _list_sre_names() -> list[str]:
    from manager.services.config_loader import load_config
    return [rid for rid, info in load_config().get("runners", {}).items()
            if not info.get("is_lead")]


def _cleanup_conv(job_id: str):
    """Xóa active conv liên quan đến job_id."""
    to_remove = [cid for cid, c in _active_conv.items() if c.get("job_id") == job_id]
    for cid in to_remove:
        del _active_conv[cid]
