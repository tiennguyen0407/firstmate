from __future__ import annotations

import os
import asyncio
from fastapi import APIRouter, Request
from langgraph.types import Command
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application

from manager.graph import graph
from manager.services.config_loader import get_sres_for_service
from shared.models import JobStatus

router = APIRouter(prefix="/webhook", tags=["telegram"])

_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    return _bot


# ── Inbound message từ Dev/QC ──────────────────────────────────

@router.post("/telegram")
async def telegram_message(request: Request):
    data = await request.json()
    update = Update.de_json(data, get_bot())

    if not update.message or not update.message.text:
        return {"ok": True}

    text = update.message.text
    chat_id = str(update.message.chat_id)
    user = update.message.from_user
    requester_name = user.full_name or user.username or chat_id

    # Parse service từ message (đơn giản: tìm service name trong text)
    service = _detect_service(text)

    import uuid
    job_id = str(uuid.uuid4())

    initial_state = {
        "job_id": job_id,
        "user_message": text,
        "requester_telegram_id": chat_id,
        "requester_name": requester_name,
        "service": service,
        "env": "production",
        "messages": [],
        "findings": [],
        "write_ops": [],
        "assignment_attempts": [],
        "needs_lead_approval": False,
        "status": JobStatus.PENDING,
    }

    thread_config = {"configurable": {"thread_id": job_id}}

    # Chạy graph async, không block webhook response
    asyncio.create_task(_run_graph(job_id, initial_state, thread_config, chat_id))
    await get_bot().send_message(chat_id, "🔍 Đang kiểm tra...")
    return {"ok": True}


async def _run_graph(job_id, initial_state, thread_config, chat_id):
    result = await asyncio.to_thread(graph.invoke, initial_state, thread_config)
    state = graph.get_state(thread_config)

    # Graph bị interrupt → cần SRE action
    if state.next:
        next_node = state.next[0]
        interrupt_data = state.tasks[0].interrupts[0].value if state.tasks else {}
        await _handle_interrupt(job_id, next_node, interrupt_data, chat_id)
        return

    # Graph done → gửi final report về Dev/QC
    final = result.get("final_report", "Done.")
    await get_bot().send_message(chat_id, final, parse_mode="Markdown")


async def _handle_interrupt(job_id, next_node, interrupt_data, requester_chat_id):
    bot = get_bot()

    if next_node == "waiting_sre":
        sre_id = interrupt_data.get("sre_id")
        from manager.services.config_loader import load_config
        cfg = load_config()
        runner_cfg = cfg["runners"].get(sre_id, {})
        sre_telegram = runner_cfg.get("telegram_id", "")

        description = interrupt_data.get("description", "")
        text = (
            f"📟 *FirstMate — Task mới*\n\n"
            f"Service: `{interrupt_data.get('service', 'unknown')}`\n"
            f"Cần thực hiện:\n{description}\n\n"
            f"_Job ID: `{job_id}`_"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"sre:accepted:{job_id}"),
            InlineKeyboardButton("🔄 Bận",   callback_data=f"sre:busy:{job_id}"),
            InlineKeyboardButton("❌ Từ chối",callback_data=f"sre:declined:{job_id}"),
        ]])
        # Gửi cho SRE (cần resolve telegram_id → chat_id, tạm dùng username)
        # Production: lưu map telegram_username → chat_id khi SRE /start bot
        await bot.send_message(sre_telegram, text,
                               reply_markup=keyboard, parse_mode="Markdown")

    elif next_node == "waiting_lead":
        lead_telegram = interrupt_data.get("lead_id", "")
        runner_output = interrupt_data.get("runner_output", "")
        text = (
            f"📋 *Cần Lead approve*\n\n"
            f"SRE đã thực hiện xong:\n{runner_output}\n\n"
            f"_Job ID: `{job_id}`_"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"lead:approved:{job_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"lead:rejected:{job_id}"),
        ]])
        await bot.send_message(lead_telegram, text,
                               reply_markup=keyboard, parse_mode="Markdown")


# ── Callback từ inline buttons ─────────────────────────────────

@router.post("/telegram/callback")
async def telegram_callback(request: Request):
    data = await request.json()
    update = Update.de_json(data, get_bot())

    if not update.callback_query:
        return {"ok": True}

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return {"ok": True}

    kind, action, job_id = parts
    thread_config = {"configurable": {"thread_id": job_id}}

    # Resume đúng graph instance
    result = await asyncio.to_thread(
        graph.invoke,
        Command(resume=action),
        thread_config,
    )

    state = graph.get_state(thread_config)

    # Nếu còn interrupt tiếp (waiting_runner, waiting_lead)
    if state.next:
        next_node = state.next[0]
        interrupt_data = state.tasks[0].interrupts[0].value if state.tasks else {}
        # Lấy requester chat_id từ state để báo về
        requester = result.get("requester_telegram_id", "")
        await _handle_interrupt(job_id, next_node, interrupt_data, requester)
        await query.edit_message_text(f"✅ Đã nhận: {action}")
        return {"ok": True}

    # Graph done
    final = result.get("final_report", "Done.")
    requester_chat_id = result.get("requester_telegram_id", "")
    if requester_chat_id:
        await get_bot().send_message(requester_chat_id, final, parse_mode="Markdown")
    await query.edit_message_text(f"✅ Đã xử lý xong — {action}")
    return {"ok": True}


def _detect_service(text: str) -> str:
    """Đơn giản: tìm service name trong message. Production dùng NER."""
    from manager.services.config_loader import load_config
    services = load_config().get("services", {})
    text_lower = text.lower()
    for svc in services:
        if svc in text_lower:
            return svc
    return "unknown"
