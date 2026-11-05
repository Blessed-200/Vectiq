"""ST3 (paper book + Telegram alert): the rule that came out of the post-set-1 "blowout" finding.

Rule (frozen 2026-11-04; backtest on 8 days of tape +13.9 % ROI, 1,342 bets, 9/9 positive days; forward 1.7 days
+10.4 % on 319 bets -- NOT yet significant forward):

  Masters (not China / Super League), best-of-7, live, EXACTLY ONE completed set, and the set-1 winner won by
  ``MIN_MARGIN`` (5) points or more; at most ``MAX_INPLAY`` (4) points already played in set 2;
  bet UNDER on the LOWEST rung of the match-total ladder whose Under pays >= ``MIN_ODDS`` (2.00);
  first qualifying snapshot per match, one position per match. Stake = ``stake_frac`` of the (compounding) bankroll,
  halved after a 15 % drawdown, paused after 30 % / after a -10 % day / when the last 150 bets lose more than 8 %.

Two ingredients carry all the value (the rest of the AI's 40-constant model added nothing out of sample):
the blowout (it breaks the market's "both players are identical" prior) and the low rung (priced too long).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from vectiq.feed.schema import MatchState
from vectiq.signals.engine import Signal

MIN_MARGIN = 5
MAX_INPLAY = 4
MIN_ODDS = 2.00
STAKE_FRAC = 0.01
PRE_CAP = 0.01                                # pre-match alerts (old Telegram model): never above 1 % of the bankroll
PER_MATCH_CAP = 0.015                         # PRE + ST3 together on one match
KILL_WINDOW, KILL_ROI = 150, -0.08           # pause when the last 150 settled bets lose more than 8 %
DAY_STOP = 0.10                               # pause for the rest of the day after losing 10 % of the bankroll
HALVE_DD, PAUSE_DD = 0.15, 0.30               # drawdown from the equity peak: halve the stake / stop

EXEC_SCHEMA = ("CREATE TABLE IF NOT EXISTS exec(update_id INTEGER PRIMARY KEY, ts_ms INTEGER, game_id INTEGER, "
               "status TEXT, who TEXT)")
ALERT_SCHEMA = ("CREATE TABLE IF NOT EXISTS alerts(game_id INTEGER, kind TEXT, ts_ms INTEGER, message_id INTEGER, "
                "line REAL, price REAL, PRIMARY KEY(game_id, kind))")


@dataclass(slots=True)
class Alert:
    game_id: int
    players: str
    league: str
    set1: tuple[int, int]
    inplay: tuple[int, int]
    line: float
    price: float
    ladder: list[tuple[float, float, float]]
    ts_ms: int
    url: str = ""


def _league_ok(s: MatchState) -> bool:
    league = (s.league or "").lower()
    return league.startswith("masters") and "super league" not in league and "china" not in league \
        and s.best_of in (7, None)


def ladder(s: MatchState) -> list[tuple[float, float, float]]:
    """[(line, over, under)] for every match-total line with both sides priced and not blocked."""
    lad: dict[float, dict[str, float]] = {}
    for q in s.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
            lad.setdefault(float(q.line), {})[q.selection] = q.price
    return [(ln, v["over"], v["under"]) for ln, v in sorted(lad.items())
            if "over" in v and "under" in v and v["over"] > 1 and v["under"] > 1]


def evaluate(s: MatchState) -> Alert | None:
    """The ST3 entry for this snapshot, or None."""
    if not _league_ok(s) or not s.live:
        return None
    done = s.completed_sets
    if len(done) != 1:
        return None
    a, b = done[0]
    if abs(a - b) < MIN_MARGIN:
        return None
    ip = tuple(s.in_play_set) if s.in_play_set else (0, 0)
    if sum(ip) > MAX_INPLAY:
        return None
    lad = ladder(s)
    ok = [r for r in lad if r[2] >= MIN_ODDS]
    if not ok:
        return None
    ln, _, under = min(ok, key=lambda r: r[0])
    return Alert(s.game_id, f"{s.home} vs {s.away}", s.league, (a, b), ip, ln, under, lad, int(s.fetched_ms))


# Heads-up while set 1 is still going: leader >= ARM_MAX points and ahead by >= ARM_LEAD. Calibrated on 4,586 tape
# matches (26 Oct - 3 Nov): 80 % of heads-ups end in a blowout (margin >= 5), 80 % of ST3 bets are announced first,
# median 66 s ahead of the bet alert (the bet line itself lives ~18 s). Looser triggers warn more but are right only
# 41-62 % of the time; stricter ones (lead 6) miss half of the bets.
ARM_LEAD, ARM_MAX = 5, 9


def arm(s: MatchState) -> tuple[int, int] | None:
    """Heads-up score (leader first) while set 1 is in progress and looks like a blowout; None otherwise."""
    if not _league_ok(s) or not s.live or s.completed_sets or not s.sets:
        return None
    a, b = s.sets[-1]
    hi, lo = max(a, b), min(a, b)
    if hi >= ARM_MAX and hi - lo >= ARM_LEAD and hi <= 10:
        return (a, b)
    return None


def signal_from(al: Alert, stake_frac: float) -> Signal:
    return Signal(game_id=al.game_id, players=al.players, league=al.league, sets=[al.set1], side="under", line=al.line,
                  price=al.price, p_model=1.0 / al.price, ev=0.0, fair_odds=al.price, stake_frac=stake_frac,
                  model_total=float(sum(al.set1)), market_median=al.line)


# --------------------------------------------------------------------------- risk control
def stake_fraction(drawdown: float, base: float = STAKE_FRAC) -> float:
    """Stake as a fraction of the bankroll: full, halved after a 15 % drawdown, zero after 30 %."""
    if drawdown >= PAUSE_DD:
        return 0.0
    return base / 2 if drawdown >= HALVE_DD else base


def guard(db: sqlite3.Connection, bankroll: float, drawdown: float, now_s: float) -> tuple[bool, str]:
    """(alerts allowed?, reason). Reasons: drawdown, day stop, rolling-ROI stop."""
    if drawdown >= PAUSE_DD:
        return False, f"caída de banca {drawdown * 100:.0f} % (límite {PAUSE_DD * 100:.0f} %)"
    rows = db.execute("SELECT pnl,stake FROM bets WHERE result IN ('W','L') ORDER BY ts_ms DESC, id DESC LIMIT ?",
                      (KILL_WINDOW,)).fetchall()
    if len(rows) >= KILL_WINDOW:
        stake = sum(r[1] for r in rows)
        if stake and sum(r[0] for r in rows) / stake < KILL_ROI:
            return False, f"ROI de las últimas {KILL_WINDOW} apuestas por debajo de {KILL_ROI * 100:.0f} %"
    day0 = (int(now_s) // 86400) * 86400 * 1000
    pnl_day = db.execute("SELECT COALESCE(SUM(pnl),0) FROM bets WHERE result IN ('W','L') AND ts_ms>=?", (day0,)).fetchone()[0]
    if bankroll > 0 and pnl_day <= -DAY_STOP * bankroll:
        return False, f"pérdida del día {abs(pnl_day) / bankroll * 100:.0f} % (límite {DAY_STOP * 100:.0f} %)"
    return True, ""


# --------------------------------------------------------------------------- execution log (Telegram buttons)
def init_db(db: sqlite3.Connection) -> None:
    db.execute(EXEC_SCHEMA)
    db.execute(ALERT_SCHEMA)
    db.commit()


def record_alert(db: sqlite3.Connection, al: Alert, kind: str, message_id: int | None) -> bool:
    """True if this (match, kind) had not been alerted yet."""
    init_db(db)
    cur = db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?)",
                     (al.game_id, kind, al.ts_ms, message_id, al.line, al.price))
    db.commit()
    return cur.rowcount == 1


def already_alerted(db: sqlite3.Connection, game_id: int, kind: str) -> bool:
    init_db(db)
    return db.execute("SELECT 1 FROM alerts WHERE game_id=? AND kind=?", (game_id, kind)).fetchone() is not None


def record_exec(db: sqlite3.Connection, update_id: int, game_id: int, status: str, who: str = "") -> bool:
    init_db(db)
    cur = db.execute("INSERT OR IGNORE INTO exec VALUES(?,?,?,?,?)",
                     (update_id, int(time.time() * 1000), game_id, status, who))
    db.commit()
    return cur.rowcount == 1


def exec_summary(db: sqlite3.Connection) -> dict[str, int]:
    init_db(db)
    out = {"alerts": db.execute("SELECT COUNT(*) FROM alerts WHERE kind='bet'").fetchone()[0]}
    for st, n in db.execute("SELECT status, COUNT(*) FROM exec GROUP BY status"):
        out[st] = n
    return out
