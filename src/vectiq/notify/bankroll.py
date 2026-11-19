"""Live bank + dynamic stake (19 nov study, 12 real days): recalculating the stake from the bank every hour or after a
+-5 % move earned ~2.5x the once-a-day rule on paper (+40 % with 5.8 ROI points less) with almost the same risk.

* The bot keeps its own picture of the REAL bank: the last balance the user typed (▶ INICIAR, /banca, ⏹ STOP) plus the
  result of every alert sent after it, at the dollar stake the alert quoted. An alert counts as TAKEN unless the user
  pressed "No llegué": the normal path needs zero taps, and /banca corrects any drift (dead phone, lost connection).
* The stake = pct x base, rounded to $0.50, min $1, max $3,000. `base` is the tracked bank at the last recalculation;
  it is refreshed when an hour has passed or the bank moved +-5 % from it.
"""
from __future__ import annotations

import sqlite3

STAKE_PCT = 0.0175
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


def tracked_bank(anchor: float, anchor_ts_s: float, st3_db: sqlite3.Connection, st5_db: sqlite3.Connection | None) -> tuple[float, int, int]:
    """(bank, settled alerts counted, alerts excluded by 'No llegué')."""
    st3_db.execute(QUOTED)
    skipped = {r[0] for r in st3_db.execute("SELECT game_id FROM exec WHERE status='no_llego'")} if _has(st3_db, "exec") else set()
    bank, n, ex = anchor, 0, 0
    for gid, kind, stake in st3_db.execute("SELECT game_id, kind, stake FROM quoted WHERE ts_ms>?", (int(anchor_ts_s * 1000),)).fetchall():
        led = st5_db if kind == "st5" else st3_db
        u = _result(led, gid) if led is not None else None
        if u is None:
            continue
        if gid in skipped:
            ex += 1
            continue
        bank += stake * u
        n += 1
    return max(bank, 0.0), n, ex


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def stake_now(d: dict, bank: float, now_s: float, pct: float = STAKE_PCT) -> tuple[float, bool]:
    """Dollar stake per alert from the session dict `d` (keeps stake_base / stake_base_ts). Returns (stake, recalculated)."""
    base, ts = d.get("stake_base"), float(d.get("stake_base_ts") or 0.0)
    redo = base is None or now_s - ts >= RECALC_S or abs(bank / base - 1.0) >= RECALC_MOVE if base else True
    if redo:
        d["stake_base"], d["stake_base_ts"], base = bank, now_s, bank
    return min(MAX_STAKE, max(MIN_STAKE, round(pct * base * 2) / 2)), bool(redo)
