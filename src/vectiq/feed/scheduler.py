"""Adaptive detail-polling scheduler.

One list call covers every match (main market + three-line ladders). Detail
calls (full ladders, per-set markets, live stats) are budgeted and prioritised
by how much information a match is about to produce.
"""
from __future__ import annotations

from vectiq.feed.schema import MatchState


def interval_for(s: MatchState, now_s: float) -> float:
    """Seconds between detail polls for a match."""
    if s.live:
        play = s.in_play_set
        if play is None:  # set break: the highest-value window
            return 3.0
        if max(play) >= 8:  # closing in on a set end
            return 6.0
        return 15.0
    if s.start_ts and 0 <= s.start_ts - now_s <= 900:  # prematch: opening line drift
        return 60.0
    return 300.0


class DetailScheduler:
    def __init__(self, budget_per_cycle: int = 4) -> None:
        self.budget = budget_per_cycle
        self.next_due: dict[int, float] = {}

    def pick(self, states: list[MatchState], now_s: float) -> list[int]:
        due = [(self.next_due.get(s.game_id, 0.0) - now_s, s) for s in states]
        due = [(d, s) for d, s in due if d <= 0]
        due.sort(key=lambda x: (not x[1].live, interval_for(x[1], now_s), x[0]))  # live, tightest cadence, most overdue
        chosen = [s.game_id for _, s in due[: self.budget]]
        by_id = {s.game_id: s for s in states}
        for gid in chosen:
            self.next_due[gid] = now_s + interval_for(by_id[gid], now_s)
        return chosen

    def drop_missing(self, present: set[int]) -> None:
        for gid in [g for g in self.next_due if g not in present]:
            del self.next_due[gid]
