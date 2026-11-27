"""Multi-user private mode: every person talks to the bot in a PRIVATE chat and gets alerts sized on HIS OWN bank.

* /start            -> the person is registered as pending; the owner (who administers the channel) gets a message in the
                       channel with ✅/❌ buttons. Nothing is sent to an unapproved person.
* /banca 30         -> the person's real balance; stake = pct x bank (recalculated hourly or after a +-5 % move), times the
                       player-rating tilt, rounded to $0.10 (small banks need cents: a $1 floor would be 3 % of $30).
* "No llegué"       -> that alert is not counted in his bank; every other alert counts as taken (same rule as the owner's).
State is one JSON file that travels with the other ledgers between collector runs. Users are identified by their Telegram
id (never by phone number).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from vectiq.notify import bankroll
from vectiq.notify.session import parse_amount

MIN_STAKE, MAX_STAKE, STEP = 0.30, 3000.0, 0.10
KEEP_QUOTES = 2000
HELP = ("<b>VECTIQ</b>\n/banca 30 · tu saldo real en USDT\n/estado · tu banca y tu stake\n"
        "/pausa · dejar de recibir alertas · /reanudar · volver a recibirlas")


class Users:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.d: dict = {"users": {}, "last_update": 0}
        if path is not None and path.exists():
            try:
                self.d.update(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if self.path is not None:
            try:
                self.path.write_text(json.dumps(self.d))
            except OSError:
                pass

    def seen(self, update_id: int) -> bool:
        if update_id <= int(self.d.get("last_update") or 0):
            return True
        self.d["last_update"] = update_id
        return False

    # -- registry --------------------------------------------------------------------
    def get(self, uid: str) -> dict | None:
        return self.d["users"].get(str(uid))

    def active(self) -> list[tuple[str, dict]]:
        """Approved, not paused and with a bank: the people who receive alerts."""
        return [(k, u) for k, u in self.d["users"].items() if u.get("ok") and not u.get("paused") and u.get("bank")]

    def register(self, uid: str, name: str) -> tuple[dict, bool]:
        u = self.d["users"].get(str(uid))
        new = u is None
        if new:
            u = {"name": name or str(uid), "ok": False, "bank": None, "bank_ts": 0.0, "paused": False, "ask": False,
                 "q": [], "skip": [], "stake_base": None, "stake_base_ts": 0.0}
            self.d["users"][str(uid)] = u
            self.save()
        return u, new

    def approve(self, uid: str, ok: bool) -> dict | None:
        u = self.get(uid)
        if u is None:
            return None
        u["ok"], u["ask"] = ok, ok
        self.save()
        return u

    def listing(self) -> str:
        if not self.d["users"]:
            return "Sin usuarios todavía."
        rows = []
        for k, u in self.d["users"].items():
            st = "✅" if u.get("ok") else "⏳"
            rows.append(f"{st} {u['name']} · id <code>{k}</code> · banca " + (f"${u['bank']:,.2f}" if u.get("bank") else "—")
                        + (" · en pausa" if u.get("paused") else ""))
        return "<b>Usuarios</b>\n" + "\n".join(rows)

    # -- bank + stake ----------------------------------------------------------------
    def bank(self, u: dict, dbs: dict[str, sqlite3.Connection | None]) -> float:
        """Typed balance + settled results of the alerts he was sent after typing it (minus «No llegué»)."""
        b = float(u.get("bank") or 0.0)
        skip = set(u.get("skip") or [])
        since = int(float(u.get("bank_ts") or 0.0) * 1000)
        for gid, kind, ts, stake in u.get("q", []):
            if ts <= since or gid in skip:
                continue
            db = dbs.get(kind)
            r = bankroll._result(db, gid) if db is not None else None
            if r is not None:
                b += stake * r
        return max(b, 0.0)

    def stake(self, u: dict, bank: float, now_s: float, pct: float, mult: float = 1.0) -> tuple[float, bool]:
        base, ts = u.get("stake_base"), float(u.get("stake_base_ts") or 0.0)
        redo = True if not base else (now_s - ts >= bankroll.RECALC_S or abs(bank / base - 1.0) >= bankroll.RECALC_MOVE)
        if redo:
            u["stake_base"], u["stake_base_ts"], base = bank, now_s, bank
        s = round(pct * base * mult / STEP) * STEP
        return round(min(MAX_STAKE, max(MIN_STAKE, s)), 2), bool(redo)

    def record(self, u: dict, gid: int, kind: str, ts_ms: int, stake: float) -> None:
        u["q"] = (u.get("q") or [])[-(KEEP_QUOTES - 1):] + [[gid, kind, ts_ms, stake]]

    def mark_skip(self, u: dict, gid: int) -> None:
        sk = u.setdefault("skip", [])
        if gid not in sk:
            sk.append(gid)
            u["skip"] = sk[-KEEP_QUOTES:]

    # -- private chat commands -------------------------------------------------------
    def handle(self, uid: str, name: str, text: str, now_s: float, dbs: dict, pct: float) -> tuple[str, str | None]:
        """A private message. Returns (reply, owner_notice): owner_notice is posted in the channel (new registration)."""
        t = (text or "").strip()
        u, new = self.register(uid, name)
        cmd = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", t)
        c = cmd.group(1).lower() if cmd else ""
        if not u["ok"]:
            note = None
            if new:
                note = f"🆕 <b>{name}</b> pide entrar al bot (id <code>{uid}</code>)."
            return ("Hola. Tu acceso está pendiente de aprobación. Cuando te lo aprueben, te aviso aquí mismo.", note)
        if c in ("start", "ayuda", "help"):
            u["ask"] = not u.get("bank")
            self.save()
            if not u.get("bank"):
                return ("✅ Estás dentro. Escribe tu saldo real en USDT (solo el número, ej. 30). "
                        "Con eso calculo tu stake por alerta.\n\n" + HELP, None)
            return (HELP, None)
        if c == "pausa":
            u["paused"] = True
            self.save()
            return ("⏸ Alertas en pausa. Escribe /reanudar para volver a recibirlas.", None)
        if c == "reanudar":
            u["paused"] = False
            self.save()
            return ("▶ Alertas activas." if u.get("bank") else "▶ Activas. Falta tu saldo: escribe /banca 30", None)
        if c == "estado":
            if not u.get("bank"):
                return ("Aún no tengo tu saldo. Escribe /banca 30", None)
            bank = self.bank(u, dbs)
            stake, _ = self.stake(dict(u), bank, now_s, pct)
            try:
                snap = bankroll.snapshot(float(u["bank"]), float(u.get("bank_ts") or 0.0), [tuple(q) for q in u.get("q", [])], dbs,
                                         set(u.get("skip") or []), now_s)
                snap["stake"] = stake
                return (bankroll.estado_text(snap, float(u["bank"]), "<i>Si tu saldo real es otro, escribe /banca 123.45</i>"), None)
            except Exception:  # noqa: BLE001 - the status must never fail because of the open-bets detail
                return (f"<b>VECTIQ · TU ESTADO</b>\nÚltimo saldo que escribiste: <b>${float(u['bank']):,.2f}</b>\n"
                        f"Banca que calcula el bot: <b>${bank:,.2f}</b>\nStake base por alerta: <b>${stake:,.2f}</b>\n"
                        "<i>Si tu saldo real es otro, escribe /banca 123.45</i>", None)
        arg = cmd.group(2) if c == "banca" else (t if not cmd and u.get("ask") else None)
        if arg is not None:
            v = parse_amount(arg)
            if v is None:
                return ("No entendí el saldo. Escribe /banca 30", None)
            u.update(bank=v, bank_ts=now_s, stake_base=None, ask=False, q=[], skip=[])
            self.save()
            s, _ = self.stake(u, v, now_s, pct)
            self.save()
            return (f"✅ Banca: <b>${v:,.2f}</b>. Tu stake base por alerta ahora: <b>${s:,.2f}</b> "
                    "(cada alerta te dice el monto exacto).", None)
        return (HELP, None)
