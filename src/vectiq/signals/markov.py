"""Markov model of a Masters match, independent of the bookmaker's line.

Each match has a latent point-win probability p for the home player (iid points, prior fitted by EM on 2,784 finished
matches with results only). A set to 11 win-by-2 is an absorbing chain: negative binomial up to 10-10, then a geometric
tail at deuce. The posterior of p after the sets already played gives the exact distribution of the REMAINING total
points of a best-of-7, hence P(Under L) for every rung of the ladder, with no use of the house's own prices.

Backtest (prior frozen on 22-27 Oct; Under, k=1-2, model P(Under) >= 65 %, EV >= +5 %, one bet per match):
22-27 Oct +15.5 % (171 bets, 62 % won) / 28-31 Oct +26.8 % (125, 68 %) / 2-7 Nov +19.4 % (192, 64 %).
"""
from __future__ import annotations

import json
from functools import lru_cache
from math import comb
from pathlib import Path

import numpy as np

TM = 400
_PRIOR = json.loads((Path(__file__).with_name("markov_prior.json")).read_text())
PG = np.array(_PRIOR["p_grid"])
PI = np.array(_PRIOR["weights"])


def _setpmf(p: float) -> tuple[np.ndarray, np.ndarray]:
    q = 1 - p
    wa, wb = np.zeros(80), np.zeros(80)
    for j in range(10):
        wa[11 + j] = comb(10 + j, j) * p ** 11 * q ** j
        wb[11 + j] = comb(10 + j, j) * q ** 11 * p ** j
    d = comb(20, 10) * (p * q) ** 10
    for r in range(1, 30):
        t = 20 + 2 * r
        if t < 80:
            wa[t] += d * (2 * p * q) ** (r - 1) * p * p
            wb[t] += d * (2 * p * q) ** (r - 1) * q * q
    return wa, wb


def _lik_set(x: int, y: int, p: float) -> float:
    q = 1 - p
    if x > y:
        if x == 11 and y <= 9:
            return comb(10 + y, y) * p ** 11 * q ** y
        if x >= 12 and x - y == 2:
            return comb(20, 10) * (p * q) ** 10 * (2 * p * q) ** (x - 11) * p * p
    else:
        if y == 11 and x <= 9:
            return comb(10 + x, x) * q ** 11 * p ** x
        if y >= 12 and y - x == 2:
            return comb(20, 10) * (p * q) ** 10 * (2 * p * q) ** (y - 11) * q * q
    return 1e-12


def _match_dp(p: float) -> dict[tuple[int, int], np.ndarray]:
    wa, wb = _setpmf(p)
    f: dict[tuple[int, int], np.ndarray] = {}
    for a in range(5):
        for b in range(5):
            if a == 4 or b == 4:
                z = np.zeros(TM)
                z[0] = 1
                f[(a, b)] = z
    for tot in range(6, -1, -1):
        for a in range(4):
            b = tot - a
            if 0 <= b < 4:
                f[(a, b)] = np.convolve(wa, f[(a + 1, b)])[:TM] + np.convolve(wb, f[(a, b + 1)])[:TM]
    return f


_DP: list | None = None


def _dp() -> list:
    global _DP
    if _DP is None:
        _DP = [_match_dp(float(p)) for p in PG]
    return _DP


@lru_cache(maxsize=4096)
def remaining_cdf(sets: tuple[tuple[int, int], ...]) -> np.ndarray:
    """CDF of the points still to be played after the completed ``sets`` (home first); length TM."""
    a = sum(x > y for x, y in sets)
    b = sum(y > x for x, y in sets)
    if a >= 4 or b >= 4:
        raise ValueError("match already decided")
    lik = np.array([np.prod([_lik_set(x, y, float(p)) for x, y in sets]) if sets else 1.0 for p in PG])
    w = PI * lik
    w = w / w.sum()
    dp = _dp()
    pm = sum(w[i] * dp[i][(a, b)] for i in range(len(PG)))
    return np.cumsum(pm)


def p_under(sets: list[tuple[int, int]], line: float) -> float:
    """P(final total < line) given the completed sets (half lines)."""
    played = sum(x + y for x, y in sets)
    rem = line - played
    if rem < 0:
        return 0.0
    cdf = remaining_cdf(tuple((int(x), int(y)) for x, y in sets))
    return float(cdf[min(int(np.floor(rem)), TM - 1)])


def best_under(sets: list[tuple[int, int]], ladder: list[tuple[float, float, float]], min_p: float, min_ev: float):
    """The ladder rung with the highest EV whose model probability >= min_p and EV >= min_ev: (line, price, p, ev) or None."""
    best = None
    for ln, _over, under in ladder:
        p = p_under(sets, ln)
        ev = p * under - 1
        if p >= min_p and ev >= min_ev and (best is None or ev > best[3]):
            best = (ln, under, p, ev)
    return best
