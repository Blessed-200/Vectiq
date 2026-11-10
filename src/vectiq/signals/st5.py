"""ST5 "alta probabilidad": Under on the rung where the Markov model (no house line used) gives >= 65 % and EV >= +5 %.

Backtest, frozen prior (fit 22-27 Oct), Under, after set 1 or 2, one bet per match (see ``markov``):
22-27 Oct +15.5 % (171 bets, 62 % won) / 28-31 Oct +26.8 % (125, 68 %) / 2-7 Nov +19.4 % (192, 64 %), ~30 signals/day,
average price 1.88, positive on 14 of 16 days. 94 % of the signals come after set 2. After set 3 it looked similar
(Oct only: +21 % / +26 %) but has no November data yet, so k=3 is recorded on paper and never alerted.
Stake 1 % (was 2 % on the first day): live paper over the first 80 bets won 58.8 % vs 66.4 % predicted (-1.4 sd) and returned +6.8 % vs +19 % in the backtest; back to 2 % only after >=150 live bets confirm >= 62 %.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from vectiq.feed.schema import MatchState
from vectiq.signals import markov, st3
from vectiq.signals.engine import Signal

MIN_P = 0.65
MIN_EV = 0.05
MAX_INPLAY = 4
STAKE_FRAC = float(os.environ.get("VECTIQ_ST5_FRAC", "0.01"))
PER_MATCH_CAP = 0.03                         # PRE + ST3 + ST4 + ST5 on one match
ALERT_K = (1, 2)                             # alerts; k = 3 is paper only
PAPER_K = (1, 2, 3)


@dataclass(slots=True)
class Alert:
    game_id: int
    players: str
    league: str
    sets: list[tuple[int, int]]
    inplay: tuple[int, int]
    line: float
    price: float
    p: float
    ev: float
    min_price: float
    ts_ms: int
    url: str = ""


def evaluate(s: MatchState) -> Alert | None:
    if not st3._league_ok(s) or not s.live:
        return None
    done = [tuple(x) for x in s.completed_sets]
    if len(done) not in PAPER_K:
        return None
    ip = tuple(s.in_play_set) if s.in_play_set else (0, 0)
    if sum(ip) > MAX_INPLAY:
        return None
    lad = st3.ladder(s)
    pick = markov.best_under(done, lad, MIN_P, MIN_EV)
    if pick is None:
        return None
    ln, price, p, ev = pick
    return Alert(s.game_id, f"{s.home} vs {s.away}", s.league, done, ip, ln, price, p, ev, round((1 + MIN_EV) / p, 2), int(s.fetched_ms))


def signal_from(al: Alert, stake_frac: float) -> Signal:
    return Signal(game_id=al.game_id, players=al.players, league=al.league, sets=list(al.sets), side="under", line=al.line,
                  price=al.price, p_model=al.p, ev=al.ev, fair_odds=1.0 / al.p, stake_frac=stake_frac,
                  model_total=float(sum(a + b for a, b in al.sets)), market_median=al.line)


def stake_for(drawdown: float, other_stake_frac: float, base: float | None = None) -> tuple[float, str]:
    top = STAKE_FRAC if base is None else min(base, STAKE_FRAC)      # `base`: the evidence-based fraction (never above the cap)
    base = st3.stake_fraction(drawdown, top)
    note = "Banca en caída: apuesta reducida." if 0 < base < top else ""
    left = PER_MATCH_CAP - other_stake_frac
    if left < base:
        base, note = max(0.0, left), "Ya hay apuestas en este partido: tope 3 % entre todas."
    return base, note
