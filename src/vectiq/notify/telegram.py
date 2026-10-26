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


def format_masters(pick, bankroll: float, *, mode: str = "PRUEBA · sin dinero", sprt: str = "on", drawdown: float = 0.0) -> str:
    """Alert for the Masters UNDER engine. ``pick`` is ``signals.masters.Pick``."""
    e = html.escape
    sg = pick.signal
    when = f"empieza en {pick.minutes_to_start:.0f} min" if pick.minutes_to_start is not None else "EN VIVO"
    sets = "  ".join(f"{a}-{b}" for a, b in sg.sets)
    usd = bankroll * sg.stake_frac
    lines = [
        f"<b>VECTIQ · MASTERS UNDER</b> · {e(mode)}",
        e(sg.players),
        f"<i>{e(sg.league)}</i> · {e(pick.stage)} · {when}" + (f" · sets {e(sets)}" if sets else ""),
        "────────────────",
        f"<b>Total  MENOS DE {sg.line:g}   @ {sg.price:.3f}</b>",
        f"Cuota mínima aceptable   {pick.min_odds:.3f}  (por debajo, no apostar)",
        f"P estimada {sg.p_model * 100:.1f} % · cuota justa {sg.fair_odds:.3f} · EV {sg.ev * 100:+.1f} %",
        f"Mercado (línea {sg.market_median:g}): Under {pick.p_market * 100:.1f} %",
        f"Stake  {sg.stake_frac * 100:.2f} % del banco  ≈ ${usd:,.2f} de ${bankroll:,.2f}",
    ]
    if pick.alts:
        lines.append("Alternativas: " + " · ".join(f"{ln:g} @ {o:.2f} (EV {ev * 100:+.0f} %)" for ln, o, _, ev in pick.alts))
    lines += ["────────────────",
              f"<code>muestra {pick.sample_n} · ventaja {sprt.upper()} · caída {drawdown * 100:.1f} % · id {sg.game_id}</code>",
              f'<a href="{e(pick.url)}">Abrir el partido en Betwinner</a>']
    return "\n".join(lines)


def format_report(title: str, summ: dict, sprt: str, drawdown: float) -> str:
    return (f"<b>VECTIQ · {html.escape(title)}</b>\n"
            f"Apuestas liquidadas {summ['settled']} · P&L {summ['pnl']:+.2f} · ROI {summ['roi'] * 100:+.1f} %\n"
            f"CLV {summ['clv'] * 100:+.2f} % · banco {summ['bankroll']:.2f} · caída {drawdown * 100:.1f} %\n"
            f"Ventaja (SPRT): <b>{sprt.upper()}</b>")
