"""Player ratings -> stake tilt (study of 21 nov, 20 days of real matches, out-of-sample 12-20 nov).

What drives the Under edge besides the house's own bias is WHO plays: some players systematically finish below the
market's expected total, others above. Ridge model on past matches only:

    (final total - market centre after set 1) = mu + theta[a] + theta[b] + noise

* Fit on matches already finished, weights halve every 5 days (HALF_DAYS), ridge strength LAM = 250 (tuned only on 5-11 Nov).
* z = (theta[a] + theta[b]) / sd. Low-scoring players (z < 0) are worth MORE Under edge: mult = clip(1 - C*z, LO, HI),
  re-centred so the average multiplier is 1 (same total exposure; the tilt only moves stake between alerts).
* Out of sample (1,485 real ST3+ST5 alerts): ROI +16.0 % -> +19.3 %, uplift IC95 [+1.1, +5.5] pts, permutation p~0.02,
  logloss 0.6868 -> 0.6852. The tilt never decides whether to alert: it only scales the stake of alerts that already pass.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np

LAM, HALF_DAYS = 250.0, 5.0
C, LO, HI, Z_CLIP = 0.52, 0.4, 1.6, 3.0
MIN_MATCHES = 400                       # below this the ratings are not trusted: multiplier 1.0
REFIT_S = 3600.0


class Ratings:
    def __init__(self) -> None:
        self.theta: dict[str, float] = {}
        self.sd, self.norm, self.n, self.fit_ts = 1.0, 1.0, 0, 0.0

    @staticmethod
    def load(st2_db: sqlite3.Connection, calibra_db: sqlite3.Connection) -> list[tuple[str, str, float, float]]:
        """(player a, player b, residual, ts_days) of every finished match: final total minus the centre seen after set 1."""
        fin = dict(calibra_db.execute("SELECT game_id, MAX(final) FROM mkt WHERE final IS NOT NULL GROUP BY game_id").fetchall())
        out = []
        for gid, ts, data in st2_db.execute("SELECT game_id, ts_ms, data FROM obs WHERE k=1"):
            if gid not in fin:
                continue
            try:
                d = json.loads(data)
                a, b = [x.strip() for x in d["players"].split(" vs ")]
                out.append((a, b, float(fin[gid]) - float(d["centre"]), ts / 86_400_000.0))
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def fit(self, rows: list[tuple[str, str, float, float]], now_days: float) -> bool:
        if len(rows) < MIN_MATCHES:
            self.theta, self.n = {}, len(rows)
            return False
        names = sorted({r[0] for r in rows} | {r[1] for r in rows}); ix = {p: i for i, p in enumerate(names)}; P = len(names)
        ia = np.array([ix[r[0]] for r in rows]); ib = np.array([ix[r[1]] for r in rows])
        y = np.array([r[2] for r in rows]); age = np.maximum(now_days - np.array([r[3] for r in rows]), 0.0)
        w = 0.5 ** (age / HALF_DAYS); mu = np.average(y, weights=w); y = y - mu
        G = np.zeros((P, P)); np.add.at(G, (ia, ia), w); np.add.at(G, (ib, ib), w); np.add.at(G, (ia, ib), w); np.add.at(G, (ib, ia), w)
        G[np.diag_indices(P)] += LAM
        rhs = np.zeros(P); np.add.at(rhs, ia, w * y); np.add.at(rhs, ib, w * y)
        th = np.linalg.solve(G, rhs)
        s = th[ia] + th[ib]
        self.sd = float(np.std(s)) or 1.0
        m = np.clip(1 - C * np.clip(s / self.sd, -Z_CLIP, Z_CLIP), LO, HI)
        self.norm = float(m.mean()) or 1.0
        self.theta, self.n = {p: float(th[ix[p]]) for p in names}, len(rows)
        return True

    def z(self, players: str) -> float:
        try:
            a, b = [x.strip() for x in players.split(" vs ")]
        except ValueError:
            return 0.0
        if a not in self.theta or b not in self.theta:
            return 0.0                              # an unseen player says nothing: neutral
        return float(np.clip((self.theta[a] + self.theta[b]) / self.sd, -Z_CLIP, Z_CLIP))

    def mult(self, players: str) -> float:
        if not self.theta:
            return 1.0
        return float(np.clip(1 - C * self.z(players), LO, HI) / self.norm)

    def maybe_refit(self, st2_db, calibra_db, now_s: float) -> None:
        if now_s - self.fit_ts < REFIT_S:
            return
        self.fit_ts = now_s
        try:
            self.fit(self.load(st2_db, calibra_db), now_s / 86400.0)
        except Exception:  # noqa: BLE001 - ratings are an optimisation, never a reason to lose an alert
            self.theta = {}
