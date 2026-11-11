"""Operator session: the user's REAL balance, kept 1:1 with the bot through two buttons in the channel.

* ▶ INICIAR  -> the bot asks for the real balance; the number typed next becomes the bank every alert quotes dollars on.
* ⏹ STOP     -> the bot asks for the closing balance; the session is closed and its real result logged.

Alerts are never switched off: the session only tells the system whether the user is active and what he really holds.
State lives in one small JSON file that travels with the other ledgers between collector runs.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ASK_TTL_S = 900.0                        # the number must arrive within 15 minutes of the button press
MIN_BANK, MAX_BANK = 1.0, 1_000_000.0
PANEL_TEXT = ("<b>VECTIQ · PANEL</b>\n▶ <b>INICIAR</b> al sentarte a apostar: te pido tu saldo real.\n"
              "⏹ <b>STOP</b> al terminar: te pido tu saldo final.\nLas alertas siguen llegando igual.")
PANEL_BUTTONS = [[("▶ INICIAR", "s:start"), ("⏹ STOP", "s:stop")]]


def parse_amount(text: str) -> float | None:
    """'103.5', '103,50', '$ 1 03.5' -> 103.5 ; anything that is not a single plain number -> None."""
    t = (text or "").strip().replace("$", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return v if MIN_BANK <= v <= MAX_BANK else None


class Session:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.d: dict = {"active": False, "bank": None, "start_bank": None, "start_ts": None, "ask": None, "ask_ts": 0.0,
                        "panel_id": None, "last_update": 0, "log": []}
        self.reload()

    # -- persistence ---------------------------------------------------------------
    def reload(self) -> None:
        if self.path is not None and self.path.exists():
            try:
                self.d.update(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if self.path is not None:
            try:
                self.path.write_text(json.dumps(self.d))
            except OSError:
                pass

    # -- state ---------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.d.get("active"))

    @property
    def bank(self) -> float | None:
        return self.d.get("bank")

    def seen(self, update_id: int) -> bool:
        """True if this Telegram update was already handled (the polling offset is lost between runs)."""
        if update_id <= int(self.d.get("last_update") or 0):
            return True
        self.d["last_update"] = update_id
        return False

    def press(self, action: str, now_s: float | None = None) -> str:
        """Button pressed ('start' | 'stop'): the next plain number is the balance. Returns the message to post."""
        self.d["ask"], self.d["ask_ts"] = action, now_s if now_s is not None else time.time()
        self.save()
        if action == "start":
            return "▶ <b>INICIAR</b>\nEscribe ahora tu saldo real en USDT (solo el número, ej. 103.5)."
        return "⏹ <b>STOP</b>\nEscribe ahora tu saldo final en USDT (solo el número, ej. 110.2)."

    def submit(self, text: str, now_s: float | None = None) -> str | None:
        """A channel message arrived. Returns the reply to post, or None when it is not for us."""
        now = now_s if now_s is not None else time.time()
        t = (text or "").strip()
        cmd = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", t)
        if cmd and cmd.group(1).lower() in ("iniciar", "start", "inicio"):       # typed instead of pressing the button
            return self.press("start", now) if not cmd.group(2) else self._start_with(cmd.group(2), now)
        if cmd and cmd.group(1).lower() in ("stop", "parar", "fin"):
            return self.press("stop", now) if not cmd.group(2) else self._stop_with(cmd.group(2), now)
        m = re.match(r"^/banca(?:@\w+)?\s+(.+)$", t, re.I)
        if m:                                                      # /banca 103.5 corrects the bank at any time
            v = parse_amount(m.group(1))
            if v is None:
                return "No entendí el saldo. Escribe /banca 103.5"
            self.d["bank"] = v
            self.save()
            return f"✅ Banca corregida: <b>${v:,.2f}</b>. Los dólares de las alertas usan este saldo."
        if cmd and cmd.group(1).lower() not in ("banca", "panel"):
            return ("Comandos: /iniciar (o /iniciar 103.5), /stop (o /stop 110), /banca 103.5, /panel.")
        ask = self.d.get("ask")
        if ask not in ("start", "stop") or now - float(self.d.get("ask_ts") or 0) > ASK_TTL_S:
            return None
        v = parse_amount(t)
        if v is None:
            return "No entendí el número. Escribe solo el saldo, por ejemplo 103.5"
        self.d["ask"] = None
        if ask == "start":
            self.d.update(active=True, bank=v, start_bank=v, start_ts=now)
            self.save()
            return (f"✅ <b>Sesión iniciada</b> con saldo real <b>${v:,.2f}</b>.\n"
                    f"Los dólares de cada alerta se calculan sobre esta banca. Banca fija durante la sesión.")
        start = self.d.get("start_bank")
        res = {"start_ts": self.d.get("start_ts"), "stop_ts": now, "start_bank": start, "end_bank": v}
        self.d["log"] = (self.d.get("log") or [])[-49:] + [res]
        self.d.update(active=False, bank=v)
        self.save()
        if start:
            pnl = v - start
            return (f"⏹ <b>Sesión cerrada</b>: ${start:,.2f} → ${v:,.2f} "
                    f"(<b>{pnl:+,.2f}</b> · {pnl / start * 100:+.1f} %).")
        return f"⏹ <b>Sesión cerrada</b> con saldo ${v:,.2f}."

    def _start_with(self, amount: str, now: float) -> str:
        self.d["ask"], self.d["ask_ts"] = "start", now
        return self.submit(amount, now) or "No entendí el saldo."

    def _stop_with(self, amount: str, now: float) -> str:
        self.d["ask"], self.d["ask_ts"] = "stop", now
        return self.submit(amount, now) or "No entendí el saldo."

    def footer(self) -> str:
        """Line added under every alert while no session is running."""
        return "" if self.active else "\n<i>⏸ Sesión no iniciada: toca ▶ INICIAR en el panel para fijar tu banca.</i>"
