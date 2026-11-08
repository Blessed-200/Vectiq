"""ST4 ("segunda oportunidad"): the same blowout idea one set later.

Rule (found 2026-11-07 on 3,775 tape matches of 22-31 Oct, +15.0 % ROI; confirmed blind on 2-7 Nov, 627 bets,
+14.4 % +/-8.3, positive on each of the 6 days; a regularized model trained on October did NOT beat it blind):

  Masters (not China / Super League), best-of-7, live, EXACTLY TWO completed sets, the SAME player won both, and the
  sum of the two winning margins is >= ``MIN_MS`` (8); at most ``MAX_INPLAY`` (4) points already played in set 3;
  bet UNDER on the LOWEST rung of the match-total ladder whose Under pays >= 2.00. One position per match.

NOT a hedge for a PRE/ST3 Under in trouble: after a 1-1 the house re-prices the line correctly (raises it ~+4) and a
second Under there earns ~+2-5 %. The edge sits in 2-0 blowouts, which is also where PRE/ST3 are already winning, so
the bets are ~0.75 correlated: when a PRE/ST3 position exists on the match the stake is halved and the match total is
capped at ``PER_MATCH_CAP``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from vectiq.feed.schema import MatchState
from vectiq.signals import st3
from vectiq.signals.engine import Signal

MIN_MS = int(os.environ.get("VECTIQ_ST4_MS", "8"))
MAX_INPLAY = 4
STAKE_FRAC = 0.01
SHARED_FRAC = 0.005                          # a PRE/ST3 bet already sits on this match
PER_MATCH_CAP = 0.02                         # PRE + ST3 + ST4 on one match


@dataclass(slots=True)
class Alert:
    game_id: int
    players: str
    league: str
    sets: list[tuple[int, int]]
    inplay: tuple[int, int]
    line: float
    price: float
    ladder: list[tuple[float, float, float]]
    ts_ms: int
    url: str = ""


def evaluate(s: MatchState) -> Alert | None:
    if not st3._league_ok(s) or not s.live:
        return None
    done = s.completed_sets
    if len(done) != 2:
        return None
    (a1, b1), (a2, b2) = done
    if a1 == b1 or a2 == b2 or (a1 > b1) != (a2 > b2):
        return None
    if abs(a1 - b1) + abs(a2 - b2) < MIN_MS:
        return None
    ip = tuple(s.in_play_set) if s.in_play_set else (0, 0)
    if sum(ip) > MAX_INPLAY:
        return None
    lad = st3.ladder(s)
    ok = [r for r in lad if r[2] >= st3.MIN_ODDS]
    if not ok:
        return None
    ln, _, under = min(ok, key=lambda r: r[0])
    return Alert(s.game_id, f"{s.home} vs {s.away}", s.league, [tuple(done[0]), tuple(done[1])], ip, ln, under, lad, int(s.fetched_ms))


def signal_from(al: Alert, stake_frac: float) -> Signal:
    return Signal(game_id=al.game_id, players=al.players, league=al.league, sets=list(al.sets), side="under", line=al.line,
                  price=al.price, p_model=1.0 / al.price, ev=0.0, fair_odds=al.price, stake_frac=stake_frac,
                  model_total=float(sum(a + b for a, b in al.sets)), market_median=al.line)


def stake_for(drawdown: float, other_stake_frac: float) -> tuple[float, str]:
    """(fraction of the bankroll, note). Halved after a drawdown, halved when PRE/ST3 already hold the match, capped."""
    base = st3.stake_fraction(drawdown, STAKE_FRAC)
    note = "Banca en caída: apuesta reducida a la mitad." if 0 < base < STAKE_FRAC else ""
    if other_stake_frac > 0:
        base = min(base, SHARED_FRAC)
        note = "Ya hay apuesta en este partido: esta va a la mitad."
    left = PER_MATCH_CAP - other_stake_frac
    if left < base:
        base, note = max(0.0, left), "Tope 2 % por partido entre todas las apuestas."
    return base, note
