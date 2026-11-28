"""ST0 "prepartido": Under on the LOWEST rung of the match-total ladder in the minutes before the first point.

Why it exists (study of 22 nov, raw tape 9-21 Nov, 2,391 Masters matches with the rule, no China / Super League):
the house prices the whole Masters total ladder about 4-5 points above what is played, and the bias is largest BEFORE
the match (the in-play trading corrects part of it later). The lowest of the three rungs pays the most and wins
more than the price says: 51-52 % hit rate against 43-44 % implied by the no-vig price.

Rule (frozen 22 nov, do not retune on forward data):
  Masters (not China / Super League), no completed set, no point played yet, ladder with the three rungs;
  bet UNDER on the lowest rung when its Under pays >= 2.20, or >= 2.10 when the two players are "low scorers"
  (rating z <= -0.4, see ``players``). One bet per match, first snapshot that qualifies.

Evidence (final totals taken from tape + CALIBRA + ledgers; the 95 matches still without a final counted as LOSSES):
  ROI +10.9 % (n = 2,391); +13.3 % on the matches with a known final. Both halves of the window agree (+11.7 / +14.2 %
  with price >= 2.1), 0 negative days of 13, ~184 bets/day. Also seen in the 22-30 Oct tape (+14 % pre-match), i.e. in
  two windows two weeks apart. Delaying the bet 3 minutes keeps +16 % (the rung is still offered 90 % of the time).
Correlation with the other strategies is low (daily result vs ST3 0.2, vs ST5 0.0), but 1 match in 3 is also an ST3/ST5
match, so the exposure is shared there.
Execution note: the live feed shows the ladder only ~2.5 minutes before the start (median) and pre-match limits on
Betwinner are lower than live ones (docs/MASTERS_UNDER.md: 150-310 USD).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from vectiq.feed.schema import MatchState
from vectiq.signals import st3
from vectiq.signals.engine import Signal

MIN_PRICE = float(os.environ.get("VECTIQ_ST0_MIN_PRICE", "2.20"))
MIN_PRICE_LOW_SCORERS = float(os.environ.get("VECTIQ_ST0_MIN_PRICE_PLAYERS", "2.10"))
Z_LOW = float(os.environ.get("VECTIQ_ST0_Z", "-0.4"))       # rating z of the pair; theta <= -0.12 in the study (sd 0.29)
RUNGS = 3
STAKE_FRAC = 0.0045                                         # paper-book fraction and evidence cap; alerts use the % of the bank
PER_MATCH_CAP = 0.05
STAKE_PCT = float(os.environ.get("VECTIQ_ST0_PCT", "0.0065"))   # share of the bank per alert (ST3 0.7 %, ST5 1.0 % since 28 nov)


@dataclass(slots=True)
class Alert:
    game_id: int
    players: str
    league: str
    line: float
    price: float
    min_price: float
    ladder: list[tuple[float, float, float]]
    ts_ms: int
    start_s: float | None = None
    url: str = ""


def evaluate(s: MatchState, z: float = 0.0) -> Alert | None:
    """The ST0 entry for this snapshot, or None. ``z``: player-rating z of the pair (0 = unknown / neutral)."""
    if not st3._league_ok(s):
        return None
    if s.completed_sets or sum(a + b for a, b in s.sets) > 0:
        return None
    lad = st3.ladder(s)
    if len(lad) < RUNGS:
        return None
    ln, _, under = min(lad, key=lambda r: r[0])
    if under >= MIN_PRICE:
        need = MIN_PRICE
    elif under >= MIN_PRICE_LOW_SCORERS and z <= Z_LOW:
        need = MIN_PRICE_LOW_SCORERS
    else:
        return None
    return Alert(s.game_id, f"{s.home} vs {s.away}", s.league, ln, under, need, lad, int(s.fetched_ms),
                 float(s.start_ts) if s.start_ts else None)


def signal_from(al: Alert, stake_frac: float) -> Signal:
    return Signal(game_id=al.game_id, players=al.players, league=al.league, sets=[], side="under", line=al.line,
                  price=al.price, p_model=min(0.99, 0.52), ev=0.52 * al.price - 1.0, fair_odds=1.0 / 0.52,
                  stake_frac=stake_frac, model_total=0.0, market_median=al.line)
