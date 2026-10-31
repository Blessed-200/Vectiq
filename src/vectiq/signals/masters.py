"""Masters (best-of-7) total-points UNDER engine.

Evidence (tape 22-26 Oct 2026, 1 269 finished Masters matches, all leagues except China):
the bookmaker's total line sits ~6 points above what is actually played (mean total 103.3 vs
line 109.6). Only the Under side is bettable; the Over side loses.

How a price is made (no player model, no training set beyond the book itself):
  * ``MastersBook`` keeps the empirical distribution of *points still to be played* after k completed
    sets, built only from matches that already finished (causal by construction);
  * every line of the ladder is priced with that distribution; the best Under with EV >= ``min_ev`` wins;
  * stake = fractional Kelly on a *shrunk* probability (half of the measured edge), capped, then scaled
    by a drawdown brake; a per-match exposure cap stops correlated snapshots from stacking;
  * matches that leave the feed without a final score (~16 %, mostly long ones) are NOT dropped: their total
    is imputed (``MastersBook.impute_total``). Ignoring them overstated the Under edge (survivorship bias);
  * ``sprt_state`` runs a Wald sequential test on the settled bets and mutes the engine when the
    evidence says the edge is gone.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from vectiq.feed.schema import MatchState
from vectiq.signals.engine import Signal, devig_pair, total_ladder

SEED_PATH = Path(__file__).with_name("masters_seed.json")
MIN_SAMPLE = 120          # past matches needed in a bucket before it is allowed to price anything
MAX_KEEP = 4000           # newest matches kept per bucket (the bias must be *current*)
STAGES = tuple(int(x) for x in os.environ.get("VECTIQ_MASTERS_STAGES", "0,1").split(",") if x.strip())
PRE_MAX_MIN = float(os.environ.get("VECTIQ_PRE_MAX_MIN", "120"))   # pre-match board shows Masters lines up to ~3 h ahead
EXCLUDE = tuple(x.strip().lower() for x in os.environ.get("VECTIQ_MASTERS_EXCLUDE", "china").split(",") if x.strip())


def is_masters(s: MatchState) -> bool:
    lg = s.league.strip().lower()
    # every Masters match observed so far is best-of-7; the pre-match board may omit the format text
    return lg.startswith("masters") and s.best_of in (7, None) and not any(x in lg for x in EXCLUDE)


# --------------------------------------------------------------------------- distribution book
class MastersBook:
    """Empirical remaining-points distribution by number of completed sets (0..6).

    ``order[k]`` keeps values in match-finish order (so the *oldest* can be retired); ``rem[k]`` is the
    same data sorted for O(log n) quantile queries."""

    def __init__(self) -> None:
        self.order: dict[int, deque[int]] = {k: deque() for k in range(7)}
        self.rem: dict[int, list[int]] = {k: [] for k in range(7)}
        self.gids: set[int] = set()
        self.set_pool: list[int] = []

    def _fill(self, order: dict[int, list[int]], gids: Iterable[int], pool: Iterable[int] = ()) -> None:
        self.set_pool = list(pool) or self.set_pool
        for k in range(7):
            xs = list(order.get(k, []))[-MAX_KEEP:]
            self.order[k] = deque(xs)
            self.rem[k] = sorted(xs)
        self.gids = set(gids)

    @classmethod
    def from_seed(cls, path: Path = SEED_PATH) -> "MastersBook":
        b = cls()
        if path.exists():
            d = json.loads(path.read_text())
            b._fill({int(k): v for k, v in d["remaining"].items()}, d.get("gids", []), d.get("set_pool", []))
        return b

    @classmethod
    def load(cls, path: Path) -> "MastersBook":
        if path.exists():
            try:
                d = json.loads(path.read_text())
                b = cls()
                b._fill({int(k): v for k, v in d["remaining"].items()}, d.get("gids", []), d.get("set_pool", []))
                if not b.set_pool:
                    b.set_pool = cls.from_seed().set_pool
                return b
            except (ValueError, KeyError, OSError):
                pass
        return cls.from_seed()

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"gids": sorted(self.gids), "set_pool": self.set_pool[-6000:],
                                   "remaining": {str(k): list(v) for k, v in self.order.items()}},
                                  separators=(",", ":")))
        tmp.replace(path)

    def add_total(self, gid: int, done: list[tuple[int, int]], total: int) -> bool:
        """Register one match: its completed sets and its (observed or imputed) final total."""
        if gid in self.gids:
            return False
        for k in range(min(len(done), 6) + 1):
            v = int(total - sum(a + b for a, b in done[:k]))
            self.order[k].append(v)
            bisect.insort(self.rem[k], v)
            if len(self.order[k]) > MAX_KEEP:
                old = self.order[k].popleft()
                del self.rem[k][bisect.bisect_left(self.rem[k], old)]
        self.gids.add(gid)
        return True

    def add_match(self, gid: int, sets: list[tuple[int, int]]) -> bool:
        """Register a match whose final score was seen. Idempotent per game id."""
        if len(sets) < 4:
            return False
        ok = self.add_total(gid, list(sets), sum(a + b for a, b in sets))
        if ok:
            self.set_pool.extend(a + b for a, b in sets)
            del self.set_pool[:-6000]
        return ok

    def impute_total(self, s: MatchState) -> int:
        """Plausible final total for a match last seen mid-way (best-of-7, points 50/50, set lengths drawn from
        the observed pool). Deterministic per game id so retries give the same answer."""
        rnd = random.Random(s.game_id)
        pool = self.set_pool or [19] * 50
        done = s.completed_sets
        wins = list(s.sets_won)
        total = sum(a + b for a, b in done)
        cur = s.in_play_set
        if cur is not None:
            a, b = cur
            while not (max(a, b) >= 11 and abs(a - b) >= 2):
                if rnd.random() < 0.5:
                    a += 1
                else:
                    b += 1
            total += a + b
            wins[0 if a > b else 1] += 1
        while max(wins) < 4:
            wins[0 if rnd.random() < 0.5 else 1] += 1
            total += rnd.choice(pool)
        return int(total)

    def add_censored(self, gid: int, s: MatchState) -> int:
        """Match left the feed without a final score: impute its total and learn from it. Returns the total."""
        total = self.impute_total(s)
        self.add_total(gid, s.completed_sets, total)
        return total

    def n(self, k: int) -> int:
        return len(self.rem.get(k, ()))

    def p_under(self, k: int, points_so_far: int, line: float) -> float | None:
        """P(final total < line) given k sets done and ``points_so_far`` already played."""
        xs = self.rem.get(k) or []
        if len(xs) < MIN_SAMPLE:
            return None
        limit = line - points_so_far            # remaining points must be strictly below this
        c = bisect.bisect_left(xs, limit)       # count of values < limit
        return (c + 0.5) / (len(xs) + 1.0)      # Jeffreys-style smoothing keeps p inside (0, 1)

    def mean_remaining(self, k: int) -> float | None:
        xs = self.rem.get(k) or []
        return sum(xs) / len(xs) if xs else None


# --------------------------------------------------------------------------- pick
@dataclass(slots=True)
class Pick:
    signal: Signal
    stage: str
    k: int
    min_odds: float
    p_market: float
    sample_n: int
    url: str
    minutes_to_start: float | None
    alts: list[tuple[float, float, float, float]] = field(default_factory=list)  # line, price, p, ev


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def match_url(s: MatchState, site: str | None = None, lang: str = "es") -> str:
    """Direct link, same shape as the site's own links, e.g.
    https://betwinner2.com/es/line/table-tennis/2685678-masters-poland/383584934-norbert-plachta-piotr-stepinski
    (``live`` replaces ``line`` once the match has started)."""
    site = (site or os.environ.get("VECTIQ_SITE") or (os.environ.get("BW_BASE_URLS") or "https://betwinner2.com").split(",")[0]).rstrip("/")
    kind = "live" if s.live else "line"
    lg = slug(s.league_en or s.league)
    return f"{site}/{lang}/{kind}/table-tennis/{s.league_id}-{lg}/{s.game_id}-{slug(s.home)}-{slug(s.away)}"


# --------------------------------------------------------------------------- staking and risk
def kelly(p: float, odds: float) -> float:
    return max(0.0, (p * odds - 1.0) / (odds - 1.0))


def drawdown_multiplier(drawdown: float, floor: float = 0.25) -> float:
    """1.0 until -15 %, half size until -25 %, then ``floor`` x size.

    Paper money must never stop completely: a paused book settles nothing new, so its equity can never recover and
    the pause would be permanent (it happened 2026-10-30 at -26 %). The pre-registered sample keeps growing; with real
    money the floor is simply set to 0."""
    if drawdown >= 0.25:
        return floor
    if drawdown >= 0.15:
        return 0.5
    return 1.0


def sprt_state(bets: Iterable[tuple[float, float, bool]], alpha: float = 0.10, beta: float = 0.10,
               shrink: float = 0.5) -> tuple[str, float]:
    """Wald SPRT over settled bets ``(p_model, odds, won)`` in chronological order.

    H1: win probability is the shrunk model probability (half of the edge). H0: no edge, p = 1/odds.
    Returns ("on" | "off", running log-likelihood ratio). Accepting H0 mutes the engine; accepting H1
    (re)enables it. Bounds are the classic ln(beta/(1-alpha)) and ln((1-beta)/alpha).

    Honest note: this test is deliberately patient (it needs several hundred bets to call a lost edge);
    the drawdown brake is what reacts quickly. Together: fast protection, slow verdict.
    """
    lo, hi = math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)
    state, llr = "on", 0.0
    for p, o, won in bets:
        p0 = 1.0 / o
        p1 = min(0.99, max(0.01, p0 + (p - p0) * shrink))
        llr += math.log(p1 / p0) if won else math.log((1 - p1) / (1 - p0))
        if llr >= hi:
            state, llr = "on", 0.0
        elif llr <= lo:
            state, llr = "off", 0.0
    return state, llr


# --------------------------------------------------------------------------- evaluation
def stage_of(s: MatchState, now_s: float) -> tuple[int, str] | None:
    """(k, label) for the moment this state allows an entry, or None."""
    if not s.live and not s.sets:
        if s.start_ts is None:
            return None
        ahead = s.start_ts - now_s
        return (0, "prepartido") if 90 <= ahead <= PRE_MAX_MIN * 60 else None
    done = s.completed_sets
    if s.live and done and s.in_play_set is None and len(done) == len(s.sets) and max(s.sets_won) < 4:
        k = len(done)
        if k in STAGES:
            return k, f"descanso tras el set {k}"
    return None


def evaluate_masters(s: MatchState, book: MastersBook, *, now_s: float, size_mult: float = 1.0,
                     exposure_left: float = 0.02, min_ev: float = 0.05, max_ev: float = 0.15,
                     kelly_fraction: float = 0.5, shrink: float = 0.5, cap: float = 0.01, site: str | None = None) -> tuple[Pick | None, str]:
    if not is_masters(s):
        return None, "not_masters"
    st = stage_of(s, now_s)
    if st is None:
        return None, "no_entry_window"
    k, label = st
    if size_mult <= 0:
        return None, "paused_drawdown"
    if exposure_left <= 0:
        return None, "exposure_cap"
    lad = total_ladder(s)
    if not lad:
        return None, "no_ladder"
    so_far = s.points_played if k else 0
    cands: list[tuple[float, float, float, float]] = []
    for ln, q in lad.items():
        p = book.p_under(k, so_far, ln)
        if p is None:
            return None, "book_too_small"
        o = q["under"].price
        ev = p * o - 1.0
        if min_ev <= ev <= max_ev and 0.05 < p < 0.95:   # above ~15 % the ECDF tails are over-optimistic
            cands.append((ev, ln, o, p))
    if not cands:
        return None, "no_edge"
    cands.sort(reverse=True)
    ev, ln, o, p = cands[0]
    p_shrunk = 1.0 / o + (p - 1.0 / o) * shrink
    frac = min(cap, exposure_left, kelly_fraction * kelly(p_shrunk, o)) * size_mult
    if frac <= 0:
        return None, "zero_stake"
    main = min(lad, key=lambda x: abs(devig_pair(lad[x]["over"].price, lad[x]["under"].price) - 0.5))
    p_mkt_under = 1.0 - devig_pair(lad[main]["over"].price, lad[main]["under"].price)
    mean_rem = book.mean_remaining(k) or 0.0
    sig = Signal(s.game_id, f"{s.home} vs {s.away}", s.league, list(s.completed_sets), "under", ln, o, p, ev,
                 1.0 / p, frac, so_far + mean_rem, main)
    alts = [(c[1], c[2], c[3], c[0]) for c in cands[1:4]]
    mins = None if s.start_ts is None or s.live else (s.start_ts - now_s) / 60.0
    return Pick(sig, label, k, round(1.02 / p, 3), p_mkt_under, book.n(k), match_url(s, site), mins, alts), "ok"
