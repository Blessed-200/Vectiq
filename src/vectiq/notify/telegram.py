"""Telegram delivery and message formatting (plain, dense, no emoji noise)."""
from __future__ import annotations

import html
import os

import requests

from vectiq.signals.engine import Signal


def shadow_chat() -> str | None:
    return os.environ.get("TELEGRAM_CHAT_ID_SHADOW") or os.environ.get("TELEGRAM_CHAT_ID")


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


def send_document(path, caption: str = "", token: str | None = None, chat_id: str | None = None) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument", timeout=30,
                          data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"document": fh})
    return r.ok


def send_photo(path, caption: str = "", token: str | None = None, chat_id: str | None = None) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", timeout=30,
                          data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": fh})
    return r.ok


def upsert_status(text: str, state_file, token: str | None = None, chat_id: str | None = None) -> bool:
    """One pinned message that is edited in place (no chat spam). Message id lives in ``state_file`` (handed over
    between runs); if the old message is gone a new one is sent and pinned."""
    import json
    from pathlib import Path
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    sf = Path(state_file)
    mid = None
    if sf.exists():
        try:
            mid = json.loads(sf.read_text()).get("message_id")
        except ValueError:
            mid = None
    base = f"https://api.telegram.org/bot{token}"
    if mid:
        r = requests.post(f"{base}/editMessageText", timeout=10, json={
            "chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": "HTML"})
        if r.ok or "not modified" in r.text:
            return True
    r = requests.post(f"{base}/sendMessage", timeout=10, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    if not r.ok:
        return False
    mid = r.json()["result"]["message_id"]
    sf.write_text(json.dumps({"message_id": mid}))
    requests.post(f"{base}/pinChatMessage", timeout=10, json={"chat_id": chat_id, "message_id": mid,
                                                              "disable_notification": True})
    return True


def format_masters(pick, bankroll: float, *, mode: str = "PRUEBA", sprt: str = "on", drawdown: float = 0.0) -> str:
    """Short alert for the Masters UNDER engine (``pick`` is ``signals.masters.Pick``). Decision first, detail last."""
    e = html.escape
    sg = pick.signal
    when = f"prepartido, empieza en {pick.minutes_to_start:.0f} min" if pick.minutes_to_start is not None else \
        f"EN VIVO, {pick.stage} ({'  '.join(f'{a}-{b}' for a, b in sg.sets)})"
    alt = " · ".join(f"{ln:g}@{o:.2f}" for ln, o, _, _ in pick.alts[:2])
    return "\n".join([
        f"<b>MASTERS UNDER</b> · {e(mode)}",
        f"<b>MENOS DE {sg.line:g}  @ {sg.price:.2f}</b>   (mínima {pick.min_odds:.2f})",
        e(sg.players) + " · " + e(sg.league),
        when,
        f"Apostar ≈ ${bankroll * sg.stake_frac:,.2f} ({sg.stake_frac * 100:.1f} %) · EV {sg.ev * 100:+.1f} %",
        f'<a href="{e(pick.url)}">Abrir en Betwinner</a>',
        f"<code>P {sg.p_model * 100:.1f} % · mercado {pick.p_market * 100:.1f} % · alt {alt or '-'} · "
        f"caída {drawdown * 100:.1f} % · {sprt.upper()} · id {sg.game_id}</code>",
    ])


def format_report(title: str, summ: dict, sprt: str, drawdown: float) -> str:
    return (f"<b>VECTIQ · {html.escape(title)}</b>\n"
            f"Apuestas liquidadas {summ['settled']} · P&L {summ['pnl']:+.2f} · ROI {summ['roi'] * 100:+.1f} %\n"
            f"CLV {summ['clv'] * 100:+.2f} % · banco {summ['bankroll']:.2f} · caída {drawdown * 100:.1f} %\n"
            f"Ventaja (SPRT): <b>{sprt.upper()}</b>")
