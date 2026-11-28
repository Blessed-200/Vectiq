"""Live bank + dynamic stake (19 nov study, 12 real days): recalculating the stake from the bank every hour or after a
+-5 % move earned ~2.5x the once-a-day rule on paper (+40 % with 5.8 ROI points less) with almost the same risk.

* The bot keeps its own picture of the REAL bank: the last balance the user typed (▶ INICIAR, /banca, ⏹ STOP) plus the
  result of every alert sent after it, at the dollar stake the alert quoted. An alert counts as TAKEN unless the user
  pressed "No llegué": the normal path needs zero taps, and /banca corrects any drift (dead phone, lost connection).
* The stake = pct x base, rounded to $0.50, min $1, max $3,000. `base` is the tracked bank at the last recalculation;
  it is refreshed when an hour has passed or the bank moved +-5 % from it.
"""
from __future__ import annotations

import os

import sqlite3

REL_ST3 = float(os.environ.get("VECTIQ_ST3_REL", "0.7"))   # ST3 stake = STAKE_PCT x this (28 nov risk-matched study: ST3 0.7 / ST5 1.0 / ST0 0.65 %)
STAKE_PCT = 0.01      # x0.6 of the original 1.75 %: typical 11-day dip 15 %, not 25 % (study of 22 nov)
RECALC_S, RECALC_MOVE = 3600.0, 0.05
MIN_STAKE, MAX_STAKE = 1.0, 3000.0
QUOTED = "CREATE TABLE IF NOT EXISTS quoted(game_id INTEGER, kind TEXT, ts_ms INTEGER, stake REAL, PRIMARY KEY(game_id, kind))"


def record_quote(db: sqlite3.Connection, game_id: int, kind: str, ts_ms: int, stake: float) -> None:
    db.execute(QUOTED)
    db.execute("INSERT OR REPLACE INTO quoted VALUES(?,?,?,?)", (game_id, kind, ts_ms, stake))
    db.commit()


def _result(ledger_db: sqlite3.Connection, game_id: int) -> float | None:
    """Unit result (pnl per $1 staked) of the paper bet on this match, None while unsettled."""
    row = ledger_db.execute("SELECT pnl, stake FROM bets WHERE game_id=? AND result IN ('W','L') LIMIT 1", (game_id,)).fetchone()
    return row[0] / row[1] if row and row[1] else None


def tracked_bank(anchor: float, anchor_ts_s: float, st3_db: sqlite3.Connection, st5_db: sqlite3.Connection | None,
                 st0_db: sqlite3.Connection | None = None) -> tuple[float, int, int]:
    """(bank, settled alerts counted, alerts excluded by 'No llegué')."""
    st3_db.execute(QUOTED)
    skipped = {r[0] for r in st3_db.execute("SELECT game_id FROM exec WHERE status='no_llego'")} if _has(st3_db, "exec") else set()
    bank, n, ex = anchor, 0, 0
    for gid, kind, stake in st3_db.execute("SELECT game_id, kind, stake FROM quoted WHERE ts_ms>?", (int(anchor_ts_s * 1000),)).fetchall():
        led = st5_db if kind == "st5" else st0_db if kind == "st0" else st3_db
        u = _result(led, gid) if led is not None else None
        if u is None:
            continue
        if gid in skipped:
            ex += 1
            continue
        bank += stake * u
        n += 1
    return max(bank, 0.0), n, ex


OPEN_MAX_S = 3 * 3600.0       # a Masters match ends within ~1 h; an alert still unsettled after 3 h is a vanished/void match, not money at risk


def _row(db: sqlite3.Connection | None, game_id: int) -> tuple | None:
    if db is None:
        return None
    return db.execute("SELECT result, pnl, stake, players, side, line, price FROM bets WHERE game_id=? LIMIT 1", (game_id,)).fetchone()


def snapshot(anchor: float, anchor_ts_s: float, quotes, dbs: dict, skipped: set, now_s: float, pct: float = STAKE_PCT,
             stake_state: dict | None = None) -> dict:
    """Bank as a bookmaker shows it. `quotes` = (game_id, kind, ts_ms, stake) of the alerts sent after the typed balance.

    realized  = typed balance + settled results                (what `tracked_bank` always returned: the stake base)
    in_play   = dollars held by alerts whose match is not settled yet (stake leaves the balance when the bet is placed)
    available = realized - in_play                             (what the sportsbook balance should read if every alert was played)
    Presentation only: the stake sizing keeps using `realized`, so alert amounts do not change.
    """
    realized, n_settled, excluded, open_items = anchor, 0, 0, []
    for gid, kind, ts_ms, stake in quotes:
        if ts_ms <= int(anchor_ts_s * 1000) or stake is None:
            continue
        row = _row(dbs.get(kind), gid)
        if gid in skipped:
            excluded += 1
            continue
        res = row[0] if row else None
        if res in ("W", "L") and row[2]:
            realized += stake * row[1] / row[2]
            n_settled += 1
        elif res in ("P", "V"):
            continue                                              # stake returned: neither in play nor a result
        elif now_s - ts_ms / 1000.0 <= OPEN_MAX_S:
            open_items.append((gid, kind, ts_ms, stake, row))
    realized = max(realized, 0.0)
    in_play = round(sum(i[3] for i in open_items), 2)
    stake_s = None
    if stake_state is not None:
        stake_s, _ = stake_now(stake_state, realized, now_s, pct)
    return {"anchor": anchor, "realized": realized, "profit": realized - anchor, "n_settled": n_settled, "excluded": excluded,
            "open_n": len(open_items), "in_play": in_play, "available": max(realized - in_play, 0.0), "stake": stake_s,
            "open": sorted(open_items, key=lambda i: i[2])}


def format_open(items: list, limit: int = 8) -> str:
    """One line per open alert, to reconcile against the sportsbook slip."""
    if not items:
        return ""
    out = []
    for gid, kind, ts_ms, stake, row in items[-limit:]:
        who = (row[3] if row and row[3] else f"partido {gid}")
        what = f"{'Under' if row and row[4] == 'under' else 'Over'} {row[5]:g} @ {row[6]:.2f}" if row else ""
        out.append(f"• {who} · {what} · ${stake:,.2f}")
    more = len(items) - limit
    return "\n".join(out) + (f"\n… y {more} más" if more > 0 else "")


def estado_text(snap: dict, typed: float, ask_hint: str) -> str:
    """Shared /estado body (owner and private users)."""
    sign = "+" if snap["profit"] >= 0 else "−"
    txt = (f"<b>VECTIQ · ESTADO</b>\nSaldo que escribiste: <b>${typed:,.2f}</b>\n"
           f"Resultados liquidados desde entonces: <b>{sign}${abs(snap['profit']):,.2f}</b> ({snap['n_settled']} alertas"
           + (f", {snap['excluded']} «No llegué»" if snap["excluded"] else "") + ")\n"
           f"En juego ahora: <b>{snap['open_n']}</b> alertas · <b>${snap['in_play']:,.2f}</b>\n"
           f"<b>Disponible: ${snap['available']:,.2f}</b> <i>(lo que debería mostrar tu casa si jugaste todas)</i>\n"
           f"Banca contando lo apostado: ${snap['realized']:,.2f}\n")
    if snap.get("stake") is not None:
        txt += f"Stake por alerta: <b>${snap['stake']:,.2f}</b>\n"
    lst = format_open(snap["open"])
    if lst:
        txt += "\n<b>Abiertas</b>\n" + lst + "\n"
    return txt + ask_hint


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def stake_now(d: dict, bank: float, now_s: float, pct: float = STAKE_PCT) -> tuple[float, bool]:
    """Dollar stake per alert from the session dict `d` (keeps stake_base / stake_base_ts). Returns (stake, recalculated)."""
    base, ts = d.get("stake_base"), float(d.get("stake_base_ts") or 0.0)
    redo = base is None or now_s - ts >= RECALC_S or abs(bank / base - 1.0) >= RECALC_MOVE if base else True
    if redo:
        d["stake_base"], d["stake_base_ts"], base = bank, now_s, bank
    return min(MAX_STAKE, max(MIN_STAKE, round(pct * base * 2) / 2)), bool(redo)
