"""CALIBRA (SHADOW ONLY): the learning layer that watches ANCLA (old Telegram model pre-match + ST3 blowouts).

It never touches stakes or alerts. It does four measurable things and logs them:

1. SENSOR. For every Masters match it stores the house's central total line at stage k = 0, 1, 2 completed sets
   (k=0: last pre-match snapshot; k=1,2: first snapshot with <= 4 points of the next set played) and, when the match
   ends, the real total. ``bias = centre - real`` (positive = the house set the total too high = our Under edge).
   Each UTC day the mean bias per stage is compared with a reference learned from the first 3 full days, with a
   one-sided CUSUM that only fires when the edge SHRINKS (bias falls towards 0). It looks at the cause (the house's
   calibration over hundreds of matches a day), not at the symptom (our own noisy ROI).
2. BELIEF. For each (strategy, league) a Normal-Normal posterior of the true ROI, built from the real settled bets of
   that strategy; prior = half of the strategy's pooled ROI with the weight of ~100 bets.
3. STAKE. What CALIBRA would have staked on every ANCLA bet: fractional Kelly (1/4) on the LOWER credible bound,
   0.2 % exploration while evidence is thin, zero if the cell looks dead, halved while the sensor alarm is on,
   never above 1 %. Logged next to the stake ANCLA used; compared blind later with ``comparison``.
4. SCOUT. A from-zero explorer: every (stage, league, side, ladder rung) cell is tested walk-forward (selected only
   with earlier days, evaluated on the next day), with a Bonferroni threshold over all cells. It reports candidates;
   it never bets.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from statistics import NormalDist

from vectiq.feed.schema import MatchState
from vectiq.signals.st2 import _centre

STAGES = (0, 1, 2)
BASE_DAYS, BASE_MIN_N, DAY_MIN_N = 3, 100, 30     # reference = first 3 days with >= 100 finished matches in the stage
CUSUM_K, CUSUM_H = 0.5, 5.0                       # one-sided, in standard errors per day
KELLY, TECHO, EXPLORA, EXPLORA_N = 0.25, 0.01, 0.002, 20
PRIOR_SD, PRIOR_SHRINK, PRIOR_MIN_N, BRAKE = 0.10, 0.5, 50, 0.5
SCOUT_MIN_DAYS, SCOUT_MIN_N, SCOUT_ALPHA = 4, 50, 0.05

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt(game_id INTEGER, k INTEGER, ts_ms INTEGER, day TEXT, league TEXT, centre REAL,
    ladder TEXT, final REAL, PRIMARY KEY(game_id, k));
CREATE TABLE IF NOT EXISTS shadow(game_id INTEGER, strat TEXT, ts_ms INTEGER, league TEXT, price REAL, ancla REAL,
    calibra REAL, brake INTEGER, media REAL, sd REAL, n INTEGER, PRIMARY KEY(game_id, strat));
CREATE TABLE IF NOT EXISTS sensor(k INTEGER, day TEXT, n INTEGER, bias REAL, se REAL, z REAL, s REAL, alarm INTEGER,
    PRIMARY KEY(k, day));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def _eligible(s: MatchState) -> bool:
    league = (s.league or "").lower()
    return league.startswith("masters") and "super league" not in league and "china" not in league \
        and s.best_of in (7, None)


def _ladder(s: MatchState) -> list[tuple[float, float, float]]:
    lad: dict[float, dict[str, float]] = {}
    for q in s.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
            lad.setdefault(float(q.line), {})[q.selection] = q.price
    return [(ln, v["over"], v["under"]) for ln, v in sorted(lad.items())
            if "over" in v and "under" in v and v["over"] > 1 and v["under"] > 1]


def _day(ts_ms: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000.0))


class Belief:
    __slots__ = ("n", "media", "sd", "mean_price")

    def __init__(self, n: int, media: float, sd: float, mean_price: float) -> None:
        self.n, self.media, self.sd, self.mean_price = n, media, sd, mean_price


def belief(db: sqlite3.Connection, strat: str, league: str) -> Belief:
    """Posterior of the true ROI for (strategy, league) from the settled bets of that strategy's ledger."""
    cond = "result IN ('W','L') AND stake>0" + (" AND lead_min IS NOT NULL" if strat == "pre" else "")
    rows = db.execute(f"SELECT league, pnl/stake, price FROM bets WHERE {cond}").fetchall()
    allx = [r[1] for r in rows]
    cell = [r for r in rows if r[0] == league]
    x = [r[1] for r in cell]
    prices = [r[2] for r in (cell or rows)] or [2.0]
    c = sum(prices) / len(prices)
    mu0 = PRIOR_SHRINK * (sum(allx) / len(allx)) if len(allx) >= PRIOR_MIN_N else 0.0
    n = len(x)
    xbar = sum(x) / n if n else 0.0
    sigma = math.sqrt(max(c - 1.0, 0.05))
    if n >= 30:
        var = sum(v * v for v in x) / n - xbar * xbar
        if var > 1e-9:
            sigma = math.sqrt(var)
    w0 = (sigma / PRIOR_SD) ** 2
    return Belief(n, (w0 * mu0 + n * xbar) / (w0 + n), sigma / math.sqrt(w0 + n), c)


def stake(b: Belief, braked: bool = False) -> float:
    """Fraction of the bankroll CALIBRA would stake. Shadow only."""
    if b.n < EXPLORA_N:
        f = EXPLORA if b.media >= -0.05 else 0.0
    elif b.media <= 0.0:
        f = 0.0
    else:
        lower = b.media - b.sd
        f = EXPLORA if lower <= 0 else min(TECHO, KELLY * lower / max(b.mean_price - 1.0, 0.05))
    return round(f * (BRAKE if braked else 1.0), 6)


class Calibra:
    def __init__(self, path) -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._seen: set[tuple[int, int]] = set()
        self._pre_t: dict[int, float] = {}

    def reopen(self) -> None:
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._seen.clear()
        self._pre_t.clear()

    # ---- 1. sensor: raw observations --------------------------------------------------------------
    def observe(self, s: MatchState) -> None:
        if not _eligible(s):
            return
        if not s.live:
            if s.sets:
                return
            k, prematch = 0, True
        else:
            k, prematch = len(s.completed_sets), False
            if k not in STAGES or (sum(s.in_play_set) if s.in_play_set else 0) > (2 if k == 0 else 4):
                return
        now = float(s.fetched_ms)
        if prematch:
            if now - self._pre_t.get(s.game_id, 0.0) < 60_000:
                return
            self._pre_t[s.game_id] = now
        elif (s.game_id, k) in self._seen:
            return
        lad = _ladder(s)
        centre = _centre([(ln, (1 / u) / ((1 / u) + (1 / o))) for ln, o, u in lad]) if len(lad) >= 2 else None
        if centre is None:
            return
        row = (s.game_id, k, int(now), _day(now), s.league, centre, json.dumps(lad))
        if prematch:
            self.db.execute("INSERT INTO mkt(game_id,k,ts_ms,day,league,centre,ladder) VALUES(?,?,?,?,?,?,?) "
                            "ON CONFLICT(game_id,k) DO UPDATE SET ts_ms=excluded.ts_ms, day=excluded.day, "
                            "centre=excluded.centre, ladder=excluded.ladder WHERE final IS NULL", row)
        else:
            self.db.execute("INSERT OR IGNORE INTO mkt(game_id,k,ts_ms,day,league,centre,ladder) VALUES(?,?,?,?,?,?,?)", row)
            self._seen.add((s.game_id, k))
        self.db.commit()

    def set_final(self, game_id: int, total: float) -> None:
        self.db.execute("UPDATE mkt SET final=? WHERE game_id=?", (float(total), game_id))
        self.db.commit()

    # ---- 1. sensor: daily verdict -------------------------------------------------------------------
    def _stats(self, k: int, day: str) -> tuple[int, float, float]:
        xs = [r[0] for r in self.db.execute("SELECT centre-final FROM mkt WHERE k=? AND day=? AND final IS NOT NULL", (k, day))]
        n = len(xs)
        if n < 2:
            return n, 0.0, 0.0
        m = sum(xs) / n
        return n, m, math.sqrt(sum((v - m) ** 2 for v in xs) / (n - 1) / n)

    def _meta(self, key: str) -> float | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return float(r[0]) if r else None

    def sensor_day(self, k: int, day: str) -> dict | None:
        """Process one finished UTC day for stage k (idempotent). None if it was already processed or too thin."""
        if self.db.execute("SELECT 1 FROM sensor WHERE k=? AND day=?", (k, day)).fetchone():
            return None
        n, bias, se = self._stats(k, day)
        if n < DAY_MIN_N or se <= 0:
            return None
        base, base_se = self._meta(f"base_{k}"), self._meta(f"base_se_{k}")
        if base is None:
            self.db.execute("INSERT INTO sensor VALUES(?,?,?,?,?,NULL,0,0)", (k, day, n, bias, se))
            rows = self.db.execute("SELECT n,bias,se FROM sensor WHERE k=? AND n>=? ORDER BY day LIMIT ?", (k, BASE_MIN_N, BASE_DAYS)).fetchall()
            if len(rows) >= BASE_DAYS:
                w = [1 / r[2] ** 2 for r in rows]
                base = sum(wi * r[1] for wi, r in zip(w, rows)) / sum(w)
                self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"base_{k}", str(base)))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"base_se_{k}", str(1 / math.sqrt(sum(w)))))
            self.db.commit()
            return {"k": k, "n": n, "bias": bias, "se": se, "base": base, "z": None, "alarm": 0, "learning": base is None}
        z = (base - bias) / math.sqrt(se * se + (base_se or 0.0) ** 2)    # > 0: the edge is shrinking
        prev = self.db.execute("SELECT s, alarm FROM sensor WHERE k=? AND z IS NOT NULL ORDER BY day DESC LIMIT 1", (k,)).fetchone()
        s_prev, a_prev = (prev[0], prev[1]) if prev else (0.0, 0)
        s_new = max(0.0, s_prev + z - CUSUM_K)
        alarm = 1 if s_new > CUSUM_H else (0 if s_new == 0.0 else a_prev)
        self.db.execute("INSERT INTO sensor VALUES(?,?,?,?,?,?,?,?)", (k, day, n, bias, se, z, s_new, alarm))
        self.db.commit()
        return {"k": k, "n": n, "bias": bias, "se": se, "base": base, "z": z, "s": s_new, "alarm": alarm, "learning": False}

    def braked(self, k: int) -> bool:
        r = self.db.execute("SELECT alarm FROM sensor WHERE k=? AND z IS NOT NULL ORDER BY day DESC LIMIT 1", (k,)).fetchone()
        return bool(r and r[0])

    # ---- 2+3. belief and shadow stake -------------------------------------------------------------------
    def shadow_decide(self, strat: str, ledger_db: sqlite3.Connection, game_id: int, league: str, price: float,
                      ancla_frac: float, ts_ms: int) -> float:
        k = 0 if strat == "pre" else 1
        b = belief(ledger_db, strat, league)
        braked = self.braked(k)
        f = stake(b, braked)
        self.db.execute("INSERT OR IGNORE INTO shadow VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (game_id, strat, ts_ms, league, price, ancla_frac, f, int(braked), b.media, b.sd, b.n))
        self.db.commit()
        return f

    # ---- 4. scout ----------------------------------------------------------------------------------------------
    def scout(self) -> dict:
        rows = self.db.execute("SELECT day,k,league,ladder,final FROM mkt WHERE final IS NOT NULL").fetchall()
        by_day: dict[str, list[tuple[tuple, float]]] = defaultdict(list)
        for day, k, league, lad_json, final in rows:
            lad = sorted(json.loads(lad_json))
            last = len(lad) - 1
            for i, (ln, over, under) in enumerate(lad):
                if final == ln:
                    continue
                rung = "baja" if i == 0 else "alta" if i == last else "media"
                by_day[day].append(((k, league, "under", rung), (under - 1.0) if final < ln else -1.0))
                by_day[day].append(((k, league, "over", rung), (over - 1.0) if final > ln else -1.0))
        days = sorted(by_day)
        stat: dict[tuple, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
        total = defaultdict(int)
        for d in days:
            for cell, _ in by_day[d]:
                total[cell] += 1
        ncells = sum(1 for v in total.values() if v >= SCOUT_MIN_N) or 1
        zc = NormalDist().inv_cdf(1 - SCOUT_ALPHA / ncells)

        def tstat(st):
            n, sm, sq = st
            if n < SCOUT_MIN_N:
                return None
            m = sm / n
            var = max(sq / n - m * m, 1e-9)
            return m / math.sqrt(var / n), m, n

        oos_n, oos_sum = 0, 0.0
        for i, d in enumerate(days):
            if i >= SCOUT_MIN_DAYS:
                chosen = {c for c, st in stat.items() if (t := tstat(st)) and t[0] > zc and t[1] > 0}
                for cell, u in by_day[d]:
                    if cell in chosen:
                        oos_n += 1
                        oos_sum += u
            for cell, u in by_day[d]:
                st = stat[cell]
                st[0] += 1
                st[1] += u
                st[2] += u * u
        cands = sorted(((c, t[2], t[1], t[0]) for c, st in stat.items() if (t := tstat(st)) and t[0] > zc and t[1] > 0),
                       key=lambda x: -x[3])
        return {"days": len(days), "cells": ncells, "zcrit": zc, "oos_n": oos_n,
                "oos_roi": (oos_sum / oos_n) if oos_n else None, "candidates": cands[:5]}

    # ---- daily message ------------------------------------------------------------------------------------------
    def close_day(self, day: str) -> str | None:
        names = {0: "antes de empezar", 1: "tras set 1", 2: "tras set 2"}
        parts, alarms, learning, watch = [], [], False, []
        for k in STAGES:
            r = self.sensor_day(k, day)
            if r is None:
                continue
            if r["learning"]:
                learning = True
                parts.append(f"{names[k]} {r['bias']:+.1f} (n={r['n']})")
            else:
                parts.append(f"{names[k]} {r['bias']:+.1f} (ref {r['base']:+.1f}, n={r['n']})")
                if r["alarm"]:
                    alarms.append(names[k])
                elif r["s"] > 1.0:
                    watch.append(names[k])
        if not parts:
            return None
        sc = self.scout()
        if sc["days"] < SCOUT_MIN_DAYS + 1:
            ex = "Explorador: reuniendo días."
        else:
            oos = f"fuera de muestra n={sc['oos_n']}" + (f" ROI {sc['oos_roi'] * 100:+.1f} %" if sc["oos_roi"] is not None else "")
            cand = "; ".join(f"{c[0][1].replace('Masters. ', '')} k{c[0][0]} {c[0][2]} {c[0][3]} ({c[2] * 100:+.0f} %, n={c[1]})"
                             for c in sc["candidates"][:3]) or "ninguna"
            ex = f"Explorador: {sc['cells']} celdas, umbral t>{sc['zcrit']:.1f}, {oos}. Candidatas: {cand}."
        state = ("⚠ ALARMA: el error de la casa se acerca a 0 " + ", ".join(alarms) + ". CALIBRA (sombra) reduce a la mitad."
                 if alarms else ("Aprendiendo la referencia (3 días)." if learning else
                                 ("Vigilando " + ", ".join(watch) + ": el error bajó, aún no es concluyente." if watch
                                  else "Sin alarma: la casa sigue igual.")))
        return ("<b>CALIBRA · sensor del mercado</b> (sombra, no toca tu dinero)\n"
                f"Error de la casa (línea − real, puntos) {day[8:]}/{day[5:7]}: " + " · ".join(parts) + f"\n{state}\n{ex}")


# ---- blind comparison ANCLA vs CALIBRA (never sent to Telegram) -----------------------------------------------------
def comparison(calibra_db: sqlite3.Connection, ledgers: dict[str, sqlite3.Connection], start: float = 100.0) -> dict:
    """Compounded growth, max drawdown and capital deployed of both arms over the same bets (placement order)."""
    events = []
    for strat, led in ledgers.items():
        res = {r[0]: r[1] / r[2] for r in led.execute("SELECT game_id, pnl, stake FROM bets WHERE result IN ('W','L') AND stake>0"
                                                      + (" AND lead_min IS NOT NULL" if strat == "pre" else ""))}
        for gid, ts, a, c in calibra_db.execute("SELECT game_id, ts_ms, ancla, calibra FROM shadow WHERE strat=?", (strat,)):
            if gid in res:
                events.append((ts, a, c, res[gid]))
    events.sort()
    out = {"n": len(events)}
    for arm, idx in (("ancla", 1), ("calibra", 2)):
        bank = peak = start
        dd = deployed = 0.0
        for ev in events:
            f = ev[idx]
            deployed += bank * f
            bank += bank * f * ev[3]
            peak = max(peak, bank)
            dd = max(dd, 1 - bank / peak)
        out[arm] = {"growth": bank / start - 1, "max_drawdown": dd, "deployed": deployed}
    return out


if __name__ == "__main__":   # python -m vectiq.signals.calibra <dir with calibra.sqlite, masters.sqlite, st3.sqlite>
    import sys
    from pathlib import Path
    d = Path(sys.argv[1])
    cdb = sqlite3.connect(d / "calibra.sqlite")
    print(json.dumps(comparison(cdb, {"pre": sqlite3.connect(d / "masters.sqlite"), "st3": sqlite3.connect(d / "st3.sqlite")}), indent=1))
