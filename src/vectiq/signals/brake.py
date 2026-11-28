"""Book brake: ONE stake multiplier (0.25 .. 1.0) applied equally to ST3, ST5 and ST0 and to every private user.

Why it exists (28 nov 2026 study): in %-of-bank mode `Collector._flat` ignores the graded brakes of the paper books
(evidence/drawdown shrink, CUSUM watchdog), so only the hard stops (30 % drawdown, 15 % day stop, rolling-ROI kill) acted.
A volatility-drag world with no edge loses ~70 % of the bank in two months at these stakes, so a graded layer is needed.

Two signals, both computed on the settled bets of the three paper books at the CURRENT stake weights (all alerts taken):

* edge tracker: exponentially weighted ROI (half-life 120 h, prior 800 bets at +10 %); x = clip(ROI_hat / 10 %, 0.25, 1).
  On the real 14-27 nov sequence it was below 1 about 15 % of the time and cost ~6 % of final growth; in a simulated
  "edge disappears" world it cut the median drawdown from 80 % to 41 %.
* drawdown brake: x = 0.5 when the combined path is 20 % under its 30-day peak, back to 1 when it recovers to 10 %.
  It never fired on the real sequence (max drawdown 18 %).

The brake never silences an alert (the hard stops still do that); it only scales the stake.
"""
from __future__ import annotations

import math
import sqlite3

HALF_H, REF_ROI, PRIOR_N, FLOOR = 120.0, 0.10, 800.0, 0.25
DD_ON, DD_OFF, DD_X = 0.20, 0.10, 0.5
WINDOW_S = 30 * 86400.0
LIVE_SETTLE_S, PRE_SETTLE_S = 9 * 60.0, 26 * 60.0       # estimated end of a live bet / of a pre-match bet after the start
MIN_EVENTS = 200                                        # below this the brake stays at 1.0


def load_events(dbs: dict[str, sqlite3.Connection | None], weights: dict[str, float], now_s: float) -> list[tuple[float, float, float]]:
    """(settle_s, stake weight, unit result) of every settled bet of the last 30 days, oldest first."""
    ev: list[tuple[float, float, float]] = []
    for kind, db in dbs.items():
        w = weights.get(kind, 0.0)
        if db is None or w <= 0:
            continue
        try:
            rows = db.execute("SELECT ts_ms, start_ts, price, result FROM bets WHERE result IN ('W','L') AND ts_ms >= ?",
                              (int((now_s - WINDOW_S) * 1000),)).fetchall()
        except sqlite3.Error:
            continue
        for ts_ms, start_ts, price, res in rows:
            t = ts_ms / 1000.0
            settle = (float(start_ts) + PRE_SETTLE_S) if (kind == "st0" and start_ts) else t + LIVE_SETTLE_S
            ev.append((min(max(settle, t), now_s), w, (float(price) - 1.0) if res == "W" else -1.0))
    ev.sort()
    return ev


def multiplier(events: list[tuple[float, float, float]], now_s: float) -> tuple[float, dict]:
    """(x, info). x in [FLOOR, 1]. 1.0 while there is too little history."""
    if len(events) < MIN_EVENTS:
        return 1.0, {"n": len(events), "x_edge": 1.0, "x_dd": 1.0, "roi": REF_ROI, "dd": 0.0}
    lam = math.log(2.0) / (HALF_H * 3600.0)
    sw, swu = PRIOR_N, PRIOR_N * REF_ROI
    bank = peak = 1.0
    x_dd = 1.0
    dd = 0.0
    last = events[0][0]
    for t, w, u in events:
        dec = math.exp(-lam * (t - last))
        sw, swu, last = sw * dec, swu * dec, t
        x = min(min(1.0, max(FLOOR, (swu / sw) / REF_ROI)), x_dd)
        bank *= 1.0 + w * x * u
        peak = max(peak, bank)
        dd = 1.0 - bank / peak
        x_dd = DD_X if dd >= DD_ON else (1.0 if dd <= DD_OFF else x_dd)
        sw, swu = sw + 1.0, swu + u
    roi = swu / sw                                  # decay hits numerator and denominator alike: the ratio is unchanged
    x_edge = min(1.0, max(FLOOR, roi / REF_ROI))
    return min(x_edge, x_dd), {"n": len(events), "x_edge": x_edge, "x_dd": x_dd, "roi": roi, "dd": dd}


def reason(info: dict) -> str:
    bits = []
    if info["x_edge"] < 0.995:
        bits.append(f"ROI reciente {info['roi'] * 100:.1f} % (esperado +10 %)")
    if info["x_dd"] < 0.995:
        bits.append(f"caída de cartera {info['dd'] * 100:.0f} %")
    return " · ".join(bits)
