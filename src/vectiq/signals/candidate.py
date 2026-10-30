"""Frozen offline candidate MS_G17_UNDER_K1_PB12_BUD6 (Manus spec, 2026-10-29), run in SHADOW only.

Rule (verbatim from the spec): Masters (not China), best-of-7, live, exactly one completed set, the set in progress has
>= 12 combined points (PB12), line = the match-total ladder entry with the smallest |over - under| (decimal odds),
budget = floor((line - points_played) / 10) == 6; Under at that line; earliest qualifying snapshot per game.
It was picked out of ~794 explored cells, so its +11 % history is inflated: forward data decides."""
from __future__ import annotations

import math

from vectiq.feed.schema import MatchState
from vectiq.signals.engine import Signal


def evaluate_candidate(s: MatchState) -> Signal | None:
    league = (s.league or "").lower()
    if not (league.startswith("masters") and "china" not in league and s.best_of == 7 and s.live):
        return None
    done, ip = s.completed_sets, s.in_play_set
    if len(done) != 1 or ip is None:
        return None
    ph, pa = int(ip[0]), int(ip[1])
    if ph + pa < 12:
        return None
    lad: dict[float, dict[str, float]] = {}
    for q in s.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
            lad.setdefault(float(q.line), {})[q.selection] = q.price
    lad = {ln: v for ln, v in lad.items() if "over" in v and "under" in v}
    if not lad:
        return None
    line = min(lad, key=lambda x: abs(lad[x]["over"] - lad[x]["under"]))
    points = sum(a + b for a, b in done) + ph + pa
    if math.floor((line - points) / 10) != 6:
        return None
    price = lad[line]["under"]
    return Signal(game_id=s.game_id, players=f"{s.home} vs {s.away}", league=s.league, sets=list(s.sets), side="under",
                  line=line, price=price, p_model=1.0 / price, ev=0.0, fair_odds=price, stake_frac=0.001,
                  model_total=float(points), market_median=line)
