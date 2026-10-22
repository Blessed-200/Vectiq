"""Telegram delivery and message formatting (plain, dense, no emoji noise)."""
from __future__ import annotations

import html
import os

import requests

from vectiq.signals.engine import Signal


def format_signal(s: Signal, mode: str = "PAPER") -> str:
    sets = "  ".join(f"{a}-{b}" for a, b in s.sets)
    side = "MÁS DE" if s.side == "over" else "MENOS DE"
    e = html.escape
    return (
        f"<b>VECTIQ</b> · {mode}\n"
        f"{e(s.players)}\n"
        f"<i>{e(s.league)}</i> · sets {e(sets)}\n"
        f"────────────────\n"
        f"<b>Total del partido  {side} {s.line:g}</b>\n"
        f"Cuota casa      {s.price:.3f}\n"
        f"Cuota justa     {s.fair_odds:.3f}   (P {s.p_model * 100:.1f} %)\n"
        f"Ventaja (EV)    {s.ev * 100:+.1f} %\n"
        f"Stake sugerido  {s.stake_frac * 100:.2f} % del banco\n"
        f"────────────────\n"
        f"<code>modelo {s.model_total:.1f} · mercado {s.market_median:.1f} · id {s.game_id}</code>"
    )


def send(text: str, token: str | None = None, chat_id: str | None = None) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10,
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    return r.ok
