#!/usr/bin/env python3
"""
Local test REPL cho FirstMate — không cần Telegram, không cần deploy.

Usage:
    python chat_test.py
    python chat_test.py --chat-id 743616350 --name "Tien"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DEBUG", "true")

# ── Load .env ────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    try:
        for line in open(".env"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


# ── Mock Telegram Bot ─────────────────────────────────────────────
class _MockMessage:
    message_id = 1

    def __init__(self, text=""):
        self.text = text


class _MockBot:
    """Prints bot replies to stdout instead of sending to Telegram."""

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kwargs):
        print(f"\n{'─'*60}")
        # Strip HTML tags for readability
        import re
        clean = re.sub(r"<[^>]+>", "", text)
        print(clean.strip())
        if reply_markup and hasattr(reply_markup, "inline_keyboard"):
            for row in reply_markup.inline_keyboard:
                print("  " + "   |   ".join(f"[{b.text}]" for b in row))
        print()
        return _MockMessage()

    async def edit_message_text(self, *a, **k):
        return _MockMessage()

    async def edit_message_reply_markup(self, *a, **k):
        return _MockMessage()

    async def edit_message_caption(self, *a, **k):
        return _MockMessage()

    async def answer_callback_query(self, *a, **k):
        pass

    async def forward_message(self, *a, **k):
        return _MockMessage()

    async def send_document(self, *a, **k):
        return _MockMessage()


# Inject mock BEFORE importing webhook so get_bot() uses it
import manager.api.telegram_webhook as _wh
_wh._bot = _MockBot()  # type: ignore


# ── REPL ──────────────────────────────────────────────────────────

async def _run_and_wait(coro):
    """Run coro, then drain any background tasks it spawned."""
    # Collect tasks created during the call
    created: list[asyncio.Task] = []
    _orig = asyncio.get_event_loop().create_task

    def _tracking_create_task(c, *a, **k):
        t = _orig(c, *a, **k)
        created.append(t)
        return t

    loop = asyncio.get_event_loop()
    loop.create_task = _tracking_create_task  # type: ignore
    try:
        await coro
    finally:
        loop.create_task = _orig  # type: ignore

    # Wait for all spawned background tasks
    if created:
        await asyncio.gather(*created, return_exceptions=True)


async def repl(chat_id: str, name: str):
    print()
    print("═" * 60)
    print("  FirstMate — Local Test REPL")
    print(f"  chat_id={chat_id}  name={name}")
    print("  Ctrl+C / Ctrl+D to exit")
    print("  /debug  — xem trạng thái conv")
    print("  /reset  — xóa toàn bộ conv + history")
    print("═" * 60)
    print()

    loop = asyncio.get_event_loop()

    while True:
        try:
            text = await loop.run_in_executor(None, lambda: input("You: "))
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        text = text.strip()
        if not text:
            continue

        if text == "/reset":
            _wh._active_conv.pop(chat_id, None)
            _wh._chat_history.pop(chat_id, None)
            _wh._debug_jobs.clear()
            print("[reset] conv + history cleared\n")
            continue

        if text == "/accept":
            # Giả lập SRE accept job đang pending → conv chuyển sang completed
            conv = _wh._active_conv.get(chat_id)
            if conv and conv.get("role") == "dev_qc":
                job_id = conv.get("job_id", "")
                conv["sre_chat_id"] = "sre-local-sim"
                conv["status"] = "completed"
                if job_id in _wh._debug_jobs:
                    _wh._debug_jobs[job_id]["sre_chat_id"] = "sre-local-sim"
                print(f"[accept] Job {job_id[:8]} accepted, conv marked completed\n")
            else:
                print("[accept] Không có job đang pending\n")
            continue

        # ── Simulate main webhook handler routing ────────────────
        if text.startswith("/follow"):
            # /follow: reads prev_service before deleting conv, then classify+synthesize
            action = text[len("/follow"):].strip()
            if not action:
                print("Usage: /follow <action>\n")
                continue
            prev_service = _wh._active_conv.get(chat_id, {}).get("service")
            _wh._active_conv.pop(chat_id, None)
            _wh._add_to_history(chat_id, action)
            coro = _wh._classify_and_respond(
                chat_id, action, name,
                detected_service=prev_service,
                force_action=True,
            )
        else:
            # Normal messages — add to history, then route
            if not text.startswith("/"):
                _wh._add_to_history(chat_id, text)

            if chat_id in _wh._active_conv:
                coro = _wh._route_message(chat_id, text, name)
            else:
                coro = _wh._classify_and_respond(chat_id, text, name)

        try:
            await _run_and_wait(coro)
        except Exception as exc:
            print(f"\n[ERROR] {type(exc).__name__}: {exc}\n")


def main():
    parser = argparse.ArgumentParser(description="FirstMate local test REPL")
    parser.add_argument("--chat-id", default="local-test-999", help="Simulated Telegram chat_id")
    parser.add_argument("--name", default="LocalUser", help="Simulated user display name")
    args = parser.parse_args()

    asyncio.run(repl(args.chat_id, args.name))


if __name__ == "__main__":
    main()
