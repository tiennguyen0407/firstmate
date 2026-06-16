#!/usr/bin/env python3
"""
Local dev: chạy FirstMate (manager + runner) với bot Telegram thật qua polling.

Usage:
    python run_local_bot.py          # chỉ manager + polling (không chạy runner)
    python run_local_bot.py --full   # manager + runner + polling (end-to-end)

.env.local cần có:
    TELEGRAM_BOT_TOKEN=<token-bot-test>   # bot test từ BotFather
    DEBUG=true
    GREENNODE_API_KEY=<key>
    SRE_TELEGRAM_IDS=<your-telegram-id>

    # Chỉ cần khi --full:
    RUNNER_ID=sre-local
    SRE_ID=yourname@company.com
    SRE_TELEGRAM_ID=<your-telegram-id>
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DEBUG", "true")

# ── Load .env.local (override .env) ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(".env.local", override=True)
    load_dotenv(".env")
except ImportError:
    for fname in (".env.local", ".env"):
        try:
            for line in open(fname):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        except FileNotFoundError:
            pass

# ── Validate token ────────────────────────────────────────────────────────────
if not os.environ.get("TELEGRAM_BOT_TOKEN"):
    print("❌ TELEGRAM_BOT_TOKEN chưa được set.")
    print("   Tạo file .env.local với token bot test từ BotFather.")
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("firstmate.local")

# ── Import AFTER env is set ───────────────────────────────────────────────────
from telegram import Bot, Update
import manager.api.telegram_webhook as _wh

LOCAL_PORT = 18080  # port cố định cho local, tránh conflict


async def _process_update(update: Update):
    chat = update.effective_chat
    user = update.effective_user
    if chat:
        logger.info(f"update from chat={chat.id} user={user.username if user else '?'}")
    await _wh._dispatch_update(update)


async def _polling_loop(bot: Bot):
    me = None
    for attempt in range(10):
        try:
            me = await bot.get_me()
            break
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"get_me attempt {attempt + 1}/10 failed: {exc}")
            await asyncio.sleep(3)
    if me is None:
        logger.error("Không kết nối được Telegram sau 10 lần thử. Dừng polling.")
        return

    logger.info(f"Bot: @{me.username} — polling started")
    print(f"\n✅ Bot @{me.username} đang chạy local (polling mode)")
    print("   Gửi tin nhắn cho bot để test. Ctrl+C để dừng.\n")

    await bot.delete_webhook(drop_pending_updates=True)

    offset = 0
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message", "callback_query"],
            )
            for upd in updates:
                offset = upd.update_id + 1
                asyncio.create_task(_process_update(upd))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"polling error: {exc}")
            await asyncio.sleep(2)


async def _run_server(port: int):
    import uvicorn
    from manager.main import app
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info(f"FastAPI server listening on :{port}")
    await server.serve()


async def _run_runner():
    from runner.poller import RunnerPoller
    from runner.terminal import open_claude_terminal

    runner_id = os.environ.get("RUNNER_ID", "sre-local")
    sre_id = os.environ.get("SRE_ID", "local@dev")
    telegram_id = os.environ.get("SRE_TELEGRAM_ID", "")

    # Đợi server sẵn sàng trước khi register
    import httpx
    for _ in range(20):
        try:
            async with httpx.AsyncClient() as c:
                await c.get(f"http://localhost:{LOCAL_PORT}/health", timeout=2)
            break
        except Exception:
            await asyncio.sleep(0.5)

    os.environ["MANAGER_URL"] = f"http://localhost:{LOCAL_PORT}"
    poller = RunnerPoller(runner_id=runner_id, sre_id=sre_id, telegram_id=telegram_id)
    logger.info(f"Runner {runner_id} starting → manager at localhost:{LOCAL_PORT}")
    await poller.run(on_job=open_claude_terminal)


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                        help="Chạy cả runner local (end-to-end, không cần terminal riêng)")
    args = parser.parse_args()

    bot = _wh.get_bot()

    if args.full:
        print(f"  Mode: FULL (manager + runner local, port {LOCAL_PORT})")
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_run_server(LOCAL_PORT))
            tg.create_task(_polling_loop(bot))
            tg.create_task(_run_runner())
    else:
        print(f"  Mode: MANAGER ONLY (polling, không có runner)")
        print(f"  Dùng --full để chạy cả runner local\n")
        await _polling_loop(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDừng.")
