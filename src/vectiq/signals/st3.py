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
STAKE_FRAC = 0.010                            # CAP of the evidence-based stake (60-day simulations on the real bets: 0-3 % chance of a 30 % drawdown)
PRE_CAP = 0.005                               # pre-match alerts (old Telegram model): never above 0.5 % of the bankroll (paper ROI since 5 Nov only ~+3 %)
PER_MATCH_CAP = 0.02                          # PRE + ST3 together on one match
KILL_WINDOW, KILL_ROI = 150, -0.08           # pause when the last 150 settled bets lose more than 8 %
DAY_STOP = 0.15                               # pause until 6 AM Cuba after the paper books lose 15 % of the bankroll (10 % was inside normal noise)
DAY_START_UTC_H = 11                          # the betting day starts at 6:00 AM Cuba (11:00 UTC), not at 7 PM
HALVE_DD, PAUSE_DD = 0.15, 0.30               # drawdown from the equity peak: stake shrinks smoothly, stops at 30 %

# Evidence-based stake (2026-11-10, simulated on the real paper bets): quarter Kelly on the LOWER BOUND of the recent ROI.
EV_WINDOW, EV_PRIOR_N, EV_PRIOR_ROI = 1000, 150, 0.0  # last 1000 settled bets (~6 days), shrunk toward 0 % with a prior worth 150 bets
EV_Z, EV_LAMBDA, EV_SD, EV_MIN_N = 1.0, 0.25, 1.05, 30
CUSUM_R0, CUSUM_R1, CUSUM_H = 0.10, 0.0, 5.0   # alarm: ROI fell from the realistic +10 % toward 0 % (h=5 ~ 2 sigma of evidence)


def day_start_ms(now_s: float) -> int:
    """Start (ms, UTC) of the current betting day: 11:00 UTC = 6:00 AM Cuba."""
    off = DAY_START_UTC_H * 3600
    return (((int(now_s) - off) // 86400) * 86400 + off) * 1000


def evidence_frac(db: sqlite3.Connection, cap: float, where: str = "") -> float:
    """Quarter Kelly on (shrunk ROI - 1 sigma) of the last ``EV_WINDOW`` settled bets of one book, capped. 0 when the
    evidence does not clear zero; a small default while there are fewer than ``EV_MIN_N`` bets."""
    q = "SELECT result, price FROM bets WHERE result IN ('W','L')" + (f" AND {where}" if where else "")
    rows = db.execute(q + " ORDER BY ts_ms DESC, id DESC LIMIT ?", (EV_WINDOW,)).fetchall()
    n = len(rows)
    if n < EV_MIN_N:
        return min(cap, 0.005)
    u = [(p - 1.0) if r == "W" else -1.0 for r, p in rows]
    roi = (EV_PRIOR_N * EV_PRIOR_ROI + sum(u)) / (EV_PRIOR_N + n)
    lb = roi - EV_Z * EV_SD / (EV_PRIOR_N + n) ** 0.5
    if lb <= 0:
        return 0.0
    odds = sum(p for _, p in rows) / n
    return min(cap, EV_LAMBDA * lb / max(odds - 1.0, 0.5))


def cusum_stat(db: sqlite3.Connection, where: str = "", limit: int = 3000) -> float:
    """Page CUSUM of the win/loss sequence of one book: log-likelihood ratio of ROI=r1 against ROI=r0 (alarm at CUSUM_H)."""
    import math
    q = "SELECT result, price FROM bets WHERE result IN ('W','L')" + (f" AND {where}" if where else "")
    rows = db.execute(q + " ORDER BY ts_ms DESC, id DESC LIMIT ?", (limit,)).fetchall()[::-1]
    s = 0.0
    for r, price in rows:
        p0, p1 = min(0.99, (1 + CUSUM_R0) / price), min(0.99, (1 + CUSUM_R1) / price)
        s = max(0.0, s + (math.log(p1 / p0) if r == "W" else math.log((1 - p1) / (1 - p0))))
    return s

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
    """Stake as a fraction of the bankroll: shrinks smoothly with the drawdown (to a quarter at 25 %), zero after 30 %."""
    if drawdown >= PAUSE_DD:
        return 0.0
    return base * max(0.25, 1.0 - max(0.0, drawdown) / 0.25)


def guard(db: sqlite3.Connection, bankroll: float, drawdown: float, now_s: float) -> tuple[bool, str]:
    """(alerts allowed?, reason). Reasons: drawdown, day stop, rolling-ROI stop."""
    if drawdown >= PAUSE_DD:
        return False, f"caída de banca en el límite ({PAUSE_DD * 100:.0f} %)"
    rows = db.execute("SELECT pnl,stake FROM bets WHERE result IN ('W','L') ORDER BY ts_ms DESC, id DESC LIMIT ?",
                      (KILL_WINDOW,)).fetchall()
    if len(rows) >= KILL_WINDOW:
        stake = sum(r[1] for r in rows)
        if stake and sum(r[0] for r in rows) / stake < KILL_ROI:
            return False, f"ROI de las últimas {KILL_WINDOW} apuestas por debajo de {KILL_ROI * 100:.0f} %"
    day0 = day_start_ms(now_s)
    pnl_day = db.execute("SELECT COALESCE(SUM(pnl),0) FROM bets WHERE result IN ('W','L') AND ts_ms>=?", (day0,)).fetchone()[0]
    if bankroll > 0 and pnl_day <= -DAY_STOP * bankroll:
        return False, f"pérdida del día en el límite ({DAY_STOP * 100:.0f} %)"
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
