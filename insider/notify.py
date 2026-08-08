"""One Telegram message per alert. https://core.telegram.org/bots/api#sendmessage"""
from __future__ import annotations

import os

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


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
