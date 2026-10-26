"""Set-break signal engine: model fair price vs the live Betwinner ladder.

Guards (each one exists because of a failure seen in the audit):
* only best-of-5 matches are priced (the pricer is calibrated on Liga Pro);
* only the bookmaker's *own* score is used (a lagging third-party score makes a
  stale line look like an edge);
* a model-vs-market median gap above ``max_gap`` marks the state as suspect;
* blocked or aged quotes are ignored.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from vectiq.feed.schema import MatchState, Quote
from vectiq.pricing.hier_pricer import HierMatchPricer

_PRICER = HierMatchPricer()

# Leagues whose set-break pricer may alert. Default: none -- the 22-26 Oct blind test showed no edge
# for it (see docs/HYPOTHESES.md), so it runs in shadow mode only. Masters Under lives in masters.py.
CALIBRATED = tuple(x.strip().lower() for x in
                   os.environ.get("VECTIQ_CALIBRATED_LEAGUES", "").split(",") if x.strip())


def is_calibrated(league: str) -> bool:
    return league.strip().lower() in CALIBRATED


@dataclass(slots=True)
class Signal:
    game_id: int
    players: str
    league: str
    sets: list[tuple[int, int]]
    side: str  # over / under
    line: float
    price: float
    p_model: float
    ev: float
    fair_odds: float
    stake_frac: float
    model_total: float
    market_median: float


def devig_pair(p_over: float, p_under: float) -> float:
    return (1 / p_over) / (1 / p_over + 1 / p_under)


def implied_rating_diff(home_price: float, away_price: float) -> float | None:
    """Rating gap (Liga Pro points) such that the pricer's pre-match P(home) equals the devigged winner price."""
    if home_price <= 1 or away_price <= 1:
        return None
    target = devig_pair(home_price, away_price)
    lo, hi = -900.0, 900.0
    for _ in range(40):
        mid = (lo + hi) / 2
        p = _PRICER.price([], mid, 0.0, True).p_a_match
        lo, hi = (mid, hi) if p < target else (lo, mid)
    return (lo + hi) / 2


def total_ladder(state: MatchState) -> dict[float, dict[str, Quote]]:
    lad: dict[float, dict[str, Quote]] = {}
    for q in state.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
            lad.setdefault(q.line, {})[q.selection] = q
    return {k: v for k, v in lad.items() if "over" in v and "under" in v}


def at_set_break(state: MatchState) -> bool:
    done = state.completed_sets
    if not done or state.in_play_set is not None:
        return False
    return max(state.sets_won) < 3 and len(done) == len(state.sets)


def evaluate(state: MatchState, rating_diff: float | None, *, min_ev: float = 0.06, max_gap: float = 6.0,
             kelly_fraction: float = 0.25, max_stake: float = 0.02) -> tuple[list[Signal], str]:
    """Return (signals, reason). ``reason`` explains why nothing was emitted."""
    if state.best_of != 5:
        return [], "uncalibrated_format"
    if rating_diff is None:
        return [], "no_prematch_anchor"
    if not at_set_break(state):
        return [], "not_set_break"
    lad = total_ladder(state)
    if not lad:
        return [], "no_ladder"
    res = _PRICER.price(state.completed_sets, rating_diff, 0.0, True)
    lines = sorted(lad)
    mid = [ln for ln in lines if abs(devig_pair(lad[ln]["over"].price, lad[ln]["under"].price) - 0.5) < 0.2]
    med = min(lines, key=lambda ln: abs(devig_pair(lad[ln]["over"].price, lad[ln]["under"].price) - 0.5))
    if abs(res.expected_total - med) > max_gap:
        return [], "state_mismatch"
    out: list[Signal] = []
    for ln in lines:
        for side in ("over", "under"):
            q = lad[ln][side]
            p = res.p_over(ln) if side == "over" else res.p_under(ln)
            ev = p * q.price - 1
            if ev >= min_ev and 0.05 < p < 0.95:
                kelly = ev / (q.price - 1)
                out.append(Signal(state.game_id, f"{state.home} vs {state.away}", state.league,
                                  state.completed_sets, side, ln, q.price, p, ev, 1 / p,
                                  min(max_stake, kelly * kelly_fraction), res.expected_total, med))
    out.sort(key=lambda s: -s.ev)
    return out[:1], ("ok" if out else "no_edge")
