"""Telegram delivery and message formatting (plain, dense, no emoji noise)."""
from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone

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


# --------------------------------------------------------------------------- ST3 alerts (Cuba time, 12 h, buttons)
def _cuba_dt(ts_s: float) -> datetime:
    """Cuba wall clock as a fixed UTC offset (default -4, what Cuban phones show now).

    tzdata is NOT used on purpose: its Havana rules disagree with what Cuba actually applies this autumn, and a slim
    runner may not have it. If Cuba changes its clock again, set VECTIQ_CUBA_UTC (hours, e.g. -5) without a code change.
    """
    try:
        off = float(os.environ.get("VECTIQ_CUBA_UTC", "-4"))
    except ValueError:
        off = -4.0
    return datetime.fromtimestamp(ts_s, timezone(timedelta(hours=off)))


def cuba_clock(ts_s: float) -> str:
    """Wall clock in Cuba, 12-hour style (``8:20 PM``)."""
    d = _cuba_dt(ts_s)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def cuba_day(ts_s: float) -> str:
    return _cuba_dt(ts_s).strftime("%d/%m")


def exec_buttons(game_id: int) -> list[list[tuple[str, str]]]:
    return [[("Aceptada", f"x:{game_id}:ok"), ("Cuota menor", f"x:{game_id}:low")],
            [("No llegué / rechazada", f"x:{game_id}:no")]]


def format_st3(al, bankroll: float, stake_frac: float, url: str, *, note: str = "") -> str:
    """ST3 bet alert: decision first, one number per line, time in Cuba 12-hour clock."""
    e = html.escape
    a, b = al.set1
    stake = bankroll * stake_frac
    lines = [
        "<b>ST3 · APUESTA YA</b>",
        f"<b>MENOS DE {al.line:g}  @ {al.price:.2f}</b>   (mínima {2.00:.2f}; si baja de eso, no apuestes)",
        f"{e(al.players)} · {e(al.league)}",
        f"Set 1: {a}-{b} (ventaja {abs(a - b)}) · alerta {cuba_clock(al.ts_ms / 1000)} Cuba",
        f"Apostar ≈ ${stake:,.2f} ({stake_frac * 100:.1f} % de ${bankroll:,.2f})",
    ]
    if note:
        lines.append(e(note))
    lines.append(f'<a href="{e(url)}">Abrir partido en Betwinner</a>')
    return "\n".join(lines)


def format_st3_arm(players: str, league: str, score: tuple[int, int], url: str, ts_s: float) -> str:
    e = html.escape
    return "\n".join([
        "<b>ST3 · PREPÁRATE</b>",
        f"{e(players)} · {e(league)}",
        f"Set 1 va {score[0]}-{score[1]} · {cuba_clock(ts_s)} Cuba",
        "Si termina con ventaja de 5 o más, en segundos llega la alerta de apuesta.",
        f'<a href="{e(url)}">Abrir partido en Betwinner</a>',
    ])


def send_alert(text: str, buttons: list[list[tuple[str, str]]] | None = None, silent: bool = False,
               token: str | None = None, chat_id: str | None = None) -> int | None:
    """Like ``send`` but returns the message id and can attach inline buttons ``[[(label, callback_data), ...], ...]``."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                  "disable_notification": silent}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10, json=body)
    return r.json()["result"]["message_id"] if r.ok else None


def get_updates(offset: int | None = None, token: str | None = None) -> list[dict]:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return []
    params: dict = {"timeout": 0, "allowed_updates": '["callback_query"]'}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=10)
        return r.json().get("result", []) if r.ok else []
    except Exception:  # noqa: BLE001 - polling is best-effort
        return []


def ack_callback(callback_id: str, text: str, chat_id: str | None = None, message_id: int | None = None,
                 label: str | None = None, token: str | None = None) -> None:
    """Toast the user and replace the buttons with one line saying what was recorded."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    base = f"https://api.telegram.org/bot{token}"
    try:
        requests.post(f"{base}/answerCallbackQuery", timeout=10, json={"callback_query_id": callback_id, "text": text})
        if chat_id and message_id and label:
            requests.post(f"{base}/editMessageReplyMarkup", timeout=10, json={
                "chat_id": chat_id, "message_id": message_id,
                "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]}})
    except Exception:  # noqa: BLE001
        pass
