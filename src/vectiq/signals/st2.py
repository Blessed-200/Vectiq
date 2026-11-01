"""ST2-core (SHADOW, paper only) + observation log for the ST2+/formula variants.

ST2-core rule (frozen 2026-11-01; research backtest +7.1 % +-2.8 on ~3.8k Masters matches, NOT yet forward-validated):
  Masters (not China / Super League), best-of-7, live, EXACTLY ONE completed set (stage k=1); the match-total ladder
  needs >= 2 lines with both sides priced; centre = interpolated line where the devigged Under probability is 0.5;
  line = ladder line nearest to the centre; bet Under there when its decimal price is < 1.85; first qualifying
  snapshot per match, one flat paper position per match.

Observation log (no betting): the first snapshot of every match at k=1 AND k=2 is stored with the full ladder, the
set scores and the in-play score, so ST2+ (recalibration), the 2-state mixture formula and the empirical table can be
computed offline on forward data and compared with the ST2-core result on the same matches.
"""
from __future__ import annotations

import json
import time

from vectiq.feed.schema import MatchState
from vectiq.signals.engine import Signal

MAX_PRICE = 1.85
STAKE_FRAC = 0.001          # flat ~$1 on the 1000 paper bankroll of the st2 ledger

OBS_SCHEMA = "CREATE TABLE IF NOT EXISTS obs(game_id INTEGER, k INTEGER, ts_ms INTEGER, bet INTEGER, data TEXT, PRIMARY KEY(game_id,k))"


def _centre(pts: list[tuple[float, float]]) -> float | None:
    pts = sorted(pts)
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 != p2 and (p1 - .5) * (p2 - .5) <= 0:
            return l1 + (.5 - p1) * (l2 - l1) / (p2 - p1)
    return None


def observe(s: MatchState) -> dict | None:
    league = (s.league or "").lower()
    if not league.startswith("masters") or "super league" in league or "china" in league:
        return None
    if s.best_of not in (7, None) or not s.live:
        return None
    done = s.completed_sets
    k = len(done)
    if k not in (1, 2):
        return None
    lad: dict[float, dict[str, float]] = {}
    for q in s.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
            lad.setdefault(float(q.line), {})[q.selection] = q.price
    lad = {ln: v for ln, v in lad.items() if "over" in v and "under" in v and v["over"] > 1 and v["under"] > 1}
    if len(lad) < 2:
        return None
    ct = _centre([(ln, (1 / v["under"]) / ((1 / v["under"]) + (1 / v["over"]))) for ln, v in lad.items()])
    if ct is None:
        return None
    near = min(lad, key=lambda x: abs(x - ct))
    ip = s.in_play_set
    return dict(game_id=s.game_id, k=k, ts_ms=int(s.fetched_ms), league=s.league, players=f"{s.home} vs {s.away}",
                line=near, price=lad[near]["under"], over=lad[near]["over"], centre=ct,
                ladder=[(ln, lad[ln]["over"], lad[ln]["under"]) for ln in sorted(lad)],
                sets=[tuple(x) for x in done], ip=tuple(ip) if ip else (0, 0), hour_utc=time.gmtime(s.fetched_ms / 1000).tm_hour
                + time.gmtime(s.fetched_ms / 1000).tm_min / 60, start_ts=s.start_ts)


def log_obs(db, obs: dict) -> bool:
    """Insert the first snapshot of (match, k); False if one already exists."""
    db.execute(OBS_SCHEMA)
    cur = db.execute("INSERT OR IGNORE INTO obs VALUES(?,?,?,?,?)",
                     (obs["game_id"], obs["k"], obs["ts_ms"], 0, json.dumps(obs, default=list)))
    db.commit()
    return cur.rowcount == 1


def mark_bet(db, game_id: int, k: int) -> None:
    db.execute("UPDATE obs SET bet=1 WHERE game_id=? AND k=?", (game_id, k))
    db.commit()


def signal_from(obs: dict) -> Signal | None:
    if obs["k"] != 1 or obs["price"] >= MAX_PRICE:
        return None
    return Signal(game_id=obs["game_id"], players=obs["players"], league=obs["league"], sets=[tuple(x) for x in obs["sets"]],
                  side="under", line=obs["line"], price=obs["price"], p_model=1.0 / obs["price"], ev=0.0,
                  fair_odds=obs["price"], stake_frac=STAKE_FRAC, model_total=float(sum(a + b for a, b in obs["sets"])),
                  market_median=obs["centre"])
