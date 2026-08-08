"""One Telegram message per alert. https://core.telegram.org/bots/api#sendmessage"""
from __future__ import annotations

import os

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"
GET_UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"


def send(text: str, *, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run telegram]\n{text}\n")
        return
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        API.format(token=token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    resp.raise_for_status()


def _latest_message(updates: list[dict]) -> dict | None:
    messages = [u["message"] for u in updates if "message" in u]
    return messages[-1] if messages else None


def reply_with_chat_id() -> None:
    """One-shot setup diagnostic, not a persistent responder: read the most recent
    incoming message via getUpdates and reply in that same chat with its chat_id -
    the thing you actually need to set as TELEGRAM_CHAT_ID. Run this locally right
    after sending a message in the target chat (e.g. "/start@YourBotName" in a group).
    """
    token = os.environ["TELEGRAM_TOKEN"]
    resp = requests.get(GET_UPDATES_API.format(token=token), timeout=15)
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    message = _latest_message(updates)
    if message is None:
        print("No messages found yet - send a message in the target chat, then re-run.")
        return

    chat = message["chat"]
    chat_id = chat["id"]
    chat_type = chat.get("type", "unknown")
    label = chat.get("title") or chat.get("username") or chat.get("first_name") or "this chat"

    reply = requests.post(
        API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": f"insider bot here — this chat's id is `{chat_id}` ({chat_type})",
            "parse_mode": "Markdown",
            "reply_to_message_id": message["message_id"],
        },
        timeout=15,
    )
    reply.raise_for_status()
    print(f"Replied in {label!r} ({chat_type}) with chat_id {chat_id}")
