"""SOMBRA INDIVIDUAL: ten mined rules (R1 R2 R3 R4 R5 R7 R10 R12 R13 R37) followed one by one on paper. Never alerts.

Why it exists: the VQD discovery run (24-26 Nov) mined rules over the whole situation table (match x stage x rung x side).
The owner wants each rule tracked ON ITS OWN, with the real quoted price, before any of them gets money. This module
reproduces the research table EXACTLY (same features, same float32 arithmetic, same "first executable snapshot of each
stage" rule, same one-bet-per-match-per-rule) so the live result is comparable with the backtest.

Research conventions that must not change (see ``tests/test_sombra.py`` and ``docs/SOMBRA.md``):
  * a snapshot is "executable" when the match is a Masters one with a non-empty total ladder and either no point played
    yet (k = 0) or live with k >= 1 completed sets and at most 4 points of the set in play;
  * each (match, k) is evaluated ONCE, at its first executable snapshot, and only up to k = 5;
  * rows of a snapshot are tried in the order rung (low line first), side (Under first); the first row that satisfies
    every atom of the rule is the bet; a rule never bets twice on a match;
  * ps_* = player rating, NOT the ridge of ``players.py``: exponentially weighted (half life 5 days, prior weight 3)
    mean of (final total points - centre of the ladder at the FIRST executable snapshot), shared by both players;
  * mk_* = Markov model of ``markov.py`` (best of 7 only).
Kept apart from production: its own SQLite file (``sombra.sqlite``), every call wrapped by the collector in try/except,
switch ``VECTIQ_SOMBRA=0`` turns it off. It touches no stake, no alert and no other ledger.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path

import numpy as np

from vectiq.feed.schema import MatchState
from vectiq.signals import markov

KMAX = 5
MAX_INPLAY = 4
HALF_LIFE_MS = 5 * 86400 * 1000
LAM0 = 3.0
CUBA_UTC = -4
SEED = Path(__file__).with_name("sombra_seed.json")

# name -> atoms (feature, op, threshold). Thresholds are the research ones, bit for bit (float32 values).
RULES: dict[str, list[tuple]] = {
    "R1": [('hour', '<=', 16.0), ('ps_min', '<=', -13.936817169189453)],
    "R2": [('s1_margin', '>=', 6.0), ('ps_min', '<=', -11.927257061004639), ('ps_n', '>=', 3.2421836853027344)],
    "R3": [('ps_min', '<=', -9.776334762573242), ('league', '==', 'Masters. Russia'), ('d:pdev-mk_edge', '<=', 0.3680398464202881)],
    "R4": [('ps_min', '<=', -11.927257061004639), ('country', '==', 'Russia')],
    "R5": [('rem_line', '<=', 56.5), ('sl_margin', '>=', 5.0), ('league', '!=', 'Masters. China'), ('d:mk_p-mk_ev', '<=', 0.45274398624897005), ('d:price_opp*mk_ev', '>=', 0.21717631816864014)],
    "R7": [('since_start', '>=', 13.02280044555664), ('league', '==', 'Masters. Russia'), ('d:mk_edge/mk_p', '>=', 0.1872989244759083)],
    "R10": [('k', '>=', 1.0), ('sl_tot', '<=', 15.0), ('ps_min', '<=', -9.776334762573242), ('d:mk_p-mk_ev', '<=', 0.5283199548721313)],
    "R12": [('rem_line', '<=', 56.5), ('pdev', '<=', 0.4599728584289551), ('sp_up', '<=', 4.0), ('country', '!=', 'China'), ('d:mk_edge/price_opp', '>=', 0.062361035495996475)],
    "R13": [('pdev', '<=', 0.4599728584289551), ('tt_diff', '>=', 10.0), ('country', '!=', 'China'), ('d:mk_edge/price_opp', '>=', 0.062361035495996475)],
    "R37": [('hour', '<=', 11.0), ('ps_min', '<=', -11.927257061004639), ('d:pdev-mk_ev', '<=', 0.3271462917327881)],
}

from vectiq.signals.sombra_text import DESCR  # noqa: E402,F401  (plain text for the page; no numpy needed there)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets(
  id INTEGER PRIMARY KEY, rule TEXT, ts_ms INTEGER, game_id INTEGER, players TEXT, league TEXT, k INTEGER, sets TEXT,
  side TEXT, line REAL, price REAL, stake REAL, final_total INTEGER, result TEXT, pnl REAL, src TEXT,
  champ_id INTEGER, start_ts INTEGER, UNIQUE(rule, game_id));
CREATE TABLE IF NOT EXISTS players(name TEXT PRIMARY KEY, sw REAL, swr REAL, t_last INTEGER);
CREATE TABLE IF NOT EXISTS games(game_id INTEGER PRIMARY KEY, centre REAL, last_ms INTEGER, home TEXT, away TEXT, stages TEXT, rated INTEGER DEFAULT 0, upd_ms INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


# ----------------------------------------------------------------------------------------------- features
def ladder_of(s: MatchState) -> list[tuple[float, float, float]]:
    """[(line, over price, under price)] sorted by line (same filter as the research extraction)."""
    by: dict[float, dict[str, float]] = {}
    for q in s.quotes:
        if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked and q.price > 1.0:
            by.setdefault(q.line, {})[q.selection] = q.price
    return sorted((ln, d["over"], d["under"]) for ln, d in by.items() if "over" in d and "under" in d)


def aux_of(s: MatchState) -> dict:
    """Other markets at the same instant (winner, handicap, team totals), as in the research extraction."""
    w = {q.selection: q.price for q in s.quotes if q.scope == "match" and q.market == "winner" and not q.blocked}
    hc = [q for q in s.quotes if q.scope == "match" and q.market == "handicap" and q.selection == "home" and not q.blocked]
    th = [q for q in s.quotes if q.scope == "match" and q.market == "team_total_home" and q.selection == "under" and not q.blocked]
    ta = [q for q in s.quotes if q.scope == "match" and q.market == "team_total_away" and q.selection == "under" and not q.blocked]
    pick = lambda L: min(L, key=lambda q: abs(q.price - 1.85)) if L else None  # noqa: E731
    h, a, c = pick(th), pick(ta), pick(hc)
    return dict(w_home=w.get("home"), w_away=w.get("away"), hc_line=c.line if c else None, hc_price=c.price if c else None,
                tth_line=h.line if h else None, tth_under=h.price if h else None, tta_line=a.line if a else None, tta_under=a.price if a else None)


def rec_of(s: MatchState, kind: str = "item"):
    """The research snapshot tuple for this state, or None when it is not an executable snapshot."""
    name = s.league_en or s.league or ""
    if "Masters" not in name:
        return None
    comp = [tuple(x) for x in s.completed_sets]
    ip = tuple(s.in_play_set) if s.in_play_set else (0, 0)
    k = len(comp)
    if k >= 1 and sum(ip) > MAX_INPLAY:
        return None
    if k == 0 and sum(a + b for a, b in s.sets) > 0:
        return None
    if k >= 1 and not s.live:
        return None
    lad = ladder_of(s)
    if not lad:
        return None
    return (s.fetched_ms, kind, bool(s.live), comp, ip, lad, s.league, s.league_en, s.home, s.away, s.best_of, s.start_ts, aux_of(s))


def centre_of(L, p) -> float:
    L = np.asarray(L, float)
    p = np.maximum.accumulate(np.asarray(p, float))
    if len(L) == 1:
        return float(L[0])
    if p[0] >= 0.5:
        s = (p[1] - p[0]) / (L[1] - L[0]) if p[1] > p[0] else 0.06
        return float(L[0] - (p[0] - 0.5) / max(s, 0.02))
    if p[-1] <= 0.5:
        s = (p[-1] - p[-2]) / (L[-1] - L[-2]) if p[-1] > p[-2] else 0.06
        return float(L[-1] + (0.5 - p[-1]) / max(s, 0.02))
    return float(np.interp(0.5, p, L))


def centre_of_rec(rec) -> float:
    lad = rec[5]
    L = np.array([x[0] for x in lad])
    O = np.array([x[1] for x in lad])
    U = np.array([x[2] for x in lad])
    return centre_of(L, (1 / U) / (1 / U + 1 / O))


def parse_league(name: str) -> tuple[str, str]:
    parts = [x.strip() for x in (name or "").split(".")]
    country = parts[1] if len(parts) > 1 else "?"
    low = (name or "").lower()
    return country, ("F" if ("women" in low or "mujer" in low or "жен" in low) else "M")


def _mk_cdf(comp, bo):
    if bo not in (7, None):
        return None
    a = sum(1 for x, y in comp if x > y)
    b = sum(1 for x, y in comp if y > x)
    if a >= 4 or b >= 4:
        return None
    return markov.remaining_cdf(tuple((int(x), int(y)) for x, y in comp))


def features(rec, k: int, ps: tuple[float, float, float], cdf=None) -> dict[str, np.ndarray]:
    """float32 feature columns for the rows of one snapshot, ordered (rung ascending, side Under first)."""
    t, _kind, _live, comp, ip, lad, _lg, _lge, _h, _a, _bo, start_ts, _aux = rec
    L = np.array([x[0] for x in lad])
    O = np.array([x[1] for x in lad])
    U = np.array([x[2] for x in lad])
    n = len(L)
    pu = (1 / U) / (1 / U + 1 / O)
    played = sum(a + b for a, b in comp)
    sp_up = np.r_[np.diff(L), np.nan]
    s1 = comp[0] if comp else (0, 0)
    sl = comp[-1] if comp else (0, 0)
    aux = rec[12]
    tth, tta = aux.get("tth_line"), aux.get("tta_line")
    ts = t / 1000.0
    st = start_ts if start_ts and start_ts < 1e11 else (start_ts / 1000.0 if start_ts else None)
    since = (ts - st) / 60.0 if st else np.nan
    hr = ((ts + CUBA_UTC * 3600) % 86400) // 3600
    if cdf is None:
        cdf = _mk_cdf(comp, rec[10])
    cols: dict[str, list] = {c: [] for c in ("line", "price", "price_opp", "pdev", "mk_p", "mk_ev", "mk_edge", "rem_line", "sp_up", "k", "ps_min", "ps_n",
                                              "s1_margin", "sl_margin", "sl_tot", "tt_diff", "hour", "since_start")}
    for i in range(n):
        for side in (0, 1):
            ln = L[i]
            price, opp, pd = (U[i], O[i], pu[i]) if side == 0 else (O[i], U[i], 1 - pu[i])
            if cdf is not None:
                rem = ln - played
                pun = 0.0 if rem < 0 else float(cdf[min(int(math.floor(rem)), len(cdf) - 1)])
                mk = pun if side == 0 else 1 - pun
                mk_ev, mk_edge = mk * price - 1, mk - pd
            else:
                mk = mk_ev = mk_edge = np.nan
            row = dict(line=ln, price=price, price_opp=opp, pdev=pd, mk_p=mk, mk_ev=mk_ev, mk_edge=mk_edge, rem_line=ln - played, sp_up=sp_up[i], k=k,
                       ps_min=min(ps[0], ps[1]), ps_n=ps[2], s1_margin=abs(s1[0] - s1[1]), sl_margin=abs(sl[0] - sl[1]), sl_tot=sl[0] + sl[1],
                       tt_diff=abs(tth - tta) if (tth is not None and tta is not None) else np.nan, hour=hr, since_start=since)
            for c, v in row.items():
                cols[c].append(v)
    return {c: np.asarray(v, dtype=np.float32) for c, v in cols.items()}


def _atom(f: dict, cat: dict, a: tuple) -> np.ndarray:
    name, op, v = a
    if name in cat:                                   # categorical: compared by label
        eq = cat[name] == v
        return eq if op == "==" else ~eq
    if name.startswith("d:"):
        body = name[2:]
        for o in "-*/":
            if o in body:
                xa_n, xb_n = body.split(o, 1)
                xa, xb = f[xa_n], f[xb_n]
                if o == "-":
                    x = xa - xb
                elif o == "*":
                    x = xa * xb
                else:
                    x = np.where(np.abs(xb) > 1e-6, xa / np.where(np.abs(xb) > 1e-6, xb, 1), np.nan)
                x = x.astype(np.float32)
                break
    else:
        x = f[name]
    v = np.float32(v)
    if op == "<=":
        return x <= v
    if op == ">=":
        return x >= v
    if op == "==":
        return x == v
    return (x != v) & np.isfinite(x)


def fire(rec, k: int, ps: tuple[float, float, float], rules: dict | None = None, skip: set | None = None) -> dict[str, dict]:
    """{rule: bet} for the rules that fire on this snapshot (first row in research order). ``skip``: rules already bet on."""
    f = features(rec, k, ps)
    n2 = len(f["line"])
    country, _g = parse_league(rec[7] or rec[6])
    cat = {"league": np.full(n2, rec[7] or rec[6], dtype=object), "country": np.full(n2, country, dtype=object)}
    out = {}
    for name, atoms in (rules or RULES).items():
        if skip and name in skip:
            continue
        m = np.ones(n2, bool)
        for a in atoms:
            m &= _atom(f, cat, a)
        ix = np.flatnonzero(m)
        if ix.size:
            j = int(ix[0])
            out[name] = dict(side="over" if j % 2 else "under", line=float(f["line"][j]), price=float(f["price"][j]), rung=j // 2)
    return out


# ----------------------------------------------------------------------------------------------- engine
class Sombra:
    """State (player ratings, per-game memory) + paper book of the ten rules. Compatible with the settle loops of the collector."""

    def __init__(self, path: Path | str = ":memory:", enabled: bool | None = None, seed: Path | str | None = SEED, rules: dict | None = None) -> None:
        self.path = str(path)
        self.enabled = (os.environ.get("VECTIQ_SOMBRA", "1") != "0") if enabled is None else enabled
        self.rules = rules or RULES
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.players: dict[str, list[float]] = {}
        self.games: dict[int, dict] = {}
        self._dirty: set[int] = set()
        self._flush_t = 0.0
        self._buffer: list[tuple[int, int]] = []        # finals seen while not trading
        self._seed = seed
        self._load()

    # -- persistence ---------------------------------------------------------------------------
    def _load(self) -> None:
        for name, sw, swr, tl in self.db.execute("SELECT name,sw,swr,t_last FROM players"):
            self.players[name] = [sw, swr, tl]
        for gid, centre, last_ms, h, a, stages, rated in self.db.execute("SELECT game_id,centre,last_ms,home,away,stages,rated FROM games"):
            self.games[gid] = dict(centre=centre, last_ms=last_ms or 0, home=h, away=a, stages={int(x) for x in (stages or "").split(",") if x}, rated=bool(rated))
        if not self.players and self._seed and Path(self._seed).exists() and not self.db.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone():
            doc = json.loads(Path(self._seed).read_text())
            self.players = {k: [float(v[0]), float(v[1]), float(v[2])] for k, v in doc["players"].items()}
            self.db.executemany("INSERT OR REPLACE INTO players VALUES(?,?,?,?)", [(k, v[0], v[1], int(v[2])) for k, v in self.players.items()])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('seeded',?)", (str(doc.get("asof_ms", 0)),))
            self.db.commit()

    def reopen(self) -> None:
        """Reconnect after the file was replaced on disk (state handed over by the previous run)."""
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def takeover(self) -> None:
        """This run starts trading: reload the previous run's state, then apply the finals seen while passive (once each)."""
        mem, buf = self.games, self._buffer
        self.reopen()
        self.players, self.games, self._buffer = {}, {}, []
        self._load()
        for gid, g in mem.items():                      # what this run saw on its own (matches already under way)
            d = self.games.get(gid)
            if d is None:
                self.games[gid] = g
                self._dirty.add(gid)
            else:
                d["last_ms"] = max(d["last_ms"], g["last_ms"])
                if d["centre"] is None and g["centre"] is not None:
                    d["centre"] = g["centre"]
                    self._dirty.add(gid)
        for gid, total in buf:
            self.final(gid, total, trading=True)
        self.flush(force=True)

    def flush(self, now: float | None = None, force: bool = False) -> None:
        now = now or time.time()
        if not force and now - self._flush_t < 60:
            return
        self._flush_t = now
        rows = []
        for gid in list(self._dirty):
            g = self.games.get(gid)
            if g is not None:
                rows.append((gid, g["centre"], g["last_ms"], g["home"], g["away"], ",".join(str(x) for x in sorted(g["stages"])), int(g["rated"]), int(now * 1000)))
        self._dirty.clear()
        if rows:
            self.db.executemany("INSERT OR REPLACE INTO games VALUES(?,?,?,?,?,?,?,?)", rows)
        horizon = int((now - 3 * 86400) * 1000)
        self.db.execute("DELETE FROM games WHERE upd_ms < ?", (horizon,))
        for gid in [g for g, v in self.games.items() if v["last_ms"] < horizon * 1.0 and v["rated"]]:
            self.games.pop(gid, None)
        self.db.commit()

    # -- player rating -------------------------------------------------------------------------
    def read(self, name: str, t: float) -> tuple[float, float]:
        s = self.players.get(name)
        if not s:
            return 0.0, 0.0
        f = 0.5 ** ((t - s[2]) / HALF_LIFE_MS)
        return s[1] * f / (s[0] * f + LAM0), s[0] * f

    def _upd(self, name: str, t: float, r: float) -> None:
        s = self.players.get(name)
        if s:
            f = 0.5 ** (max(t - s[2], 0.0) / HALF_LIFE_MS)
            s[0] = s[0] * f + 1
            s[1] = s[1] * f + r
            s[2] = max(t, s[2])
        else:
            s = self.players[name] = [1.0, r, t]
        self.db.execute("INSERT OR REPLACE INTO players VALUES(?,?,?,?)", (name, s[0], s[1], int(s[2])))

    def ps_of(self, home: str, away: str, t: float) -> tuple[float, float, float]:
        mh, nh = self.read(home, t)
        ma, na = self.read(away, t)
        return mh, ma, min(nh, na)

    # -- live path -----------------------------------------------------------------------------
    def _game(self, s_or_rec_ids) -> dict:
        gid, home, away = s_or_rec_ids
        g = self.games.get(gid)
        if g is None:
            g = self.games[gid] = dict(centre=None, last_ms=0, home=home, away=away, stages=set(), rated=False)
        return g

    def observe(self, s: MatchState, trading: bool = True, kind: str = "item") -> list[dict]:
        """One feed state. Returns the shadow bets opened (usually none). Never raises into the caller's logic."""
        if not self.enabled or "Masters" not in (s.league_en or s.league or ""):
            return []
        g = self._game((s.game_id, s.home, s.away))
        g["last_ms"] = max(g["last_ms"], int(s.fetched_ms))
        k = len(s.completed_sets)
        if k > KMAX or (trading and k in g["stages"]):
            return []
        if g["centre"] is not None and not trading:
            return []
        rec = rec_of(s, kind)
        if rec is None:
            return []
        return self.observe_rec(s.game_id, rec, trading, champ_id=s.league_id)

    def observe_rec(self, gid: int, rec, trading: bool = True, champ_id: int | None = None) -> list[dict]:
        """Core: a research-style snapshot tuple (also used by the offline replay)."""
        if not self.enabled:
            return []
        k = len(rec[3])
        if k > KMAX:
            return []
        g = self._game((gid, rec[8], rec[9]))
        g["last_ms"] = max(g["last_ms"], int(rec[0]))
        if g["centre"] is None:                          # centre of the FIRST executable snapshot (rating residual)
            g["centre"] = centre_of_rec(rec)
            self._dirty.add(gid)
        if not trading or k in g["stages"]:
            return []
        g["stages"].add(k)
        self._dirty.add(gid)
        opened = []
        ps = self.ps_of(rec[8], rec[9], rec[0])
        have = {r for (r,) in self.db.execute("SELECT rule FROM bets WHERE game_id=?", (gid,))}
        for name, b in fire(rec, k, ps, self.rules, skip=have).items():
            try:
                self.db.execute(
                    "INSERT INTO bets(rule,ts_ms,game_id,players,league,k,sets,side,line,price,stake,champ_id,start_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, int(rec[0]), gid, f"{rec[8]} vs {rec[9]}", rec[7] or rec[6], k, ";".join(f"{a}-{c}" for a, c in rec[3]), b["side"], b["line"],
                     b["price"], 1.0, champ_id, rec[11]))
            except sqlite3.IntegrityError:
                continue
            opened.append(dict(rule=name, game_id=gid, **b))
        if opened:
            self.db.commit()
        return opened

    # -- results -------------------------------------------------------------------------------
    def final(self, gid: int, total: int, trading: bool = True) -> None:
        """The match ended with ``total`` points: update both players' rating (once per match)."""
        if not self.enabled:
            return
        g = self.games.get(gid)
        if g is None or g["centre"] is None or g["rated"]:
            return
        if not trading:
            self._buffer.append((gid, total))
            return
        r = total - g["centre"]
        t = g["last_ms"]
        self._upd(g["home"], t, r)
        self._upd(g["away"], t, r)
        g["rated"] = True
        self._dirty.add(gid)
        self.db.commit()

    def open_games(self) -> set[int]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT game_id FROM bets WHERE result IS NULL")}

    def settle(self, game_id: int, final_total: int, src: str = "observed") -> int:
        rows = self.db.execute("SELECT id,side,line,price,stake FROM bets WHERE game_id=? AND result IS NULL", (game_id,)).fetchall()
        for bid, side, line, price, stake in rows:
            if final_total == line:
                res, pnl = "P", 0.0
            else:
                won = (final_total > line) if side == "over" else (final_total < line)
                res, pnl = ("W", stake * (price - 1)) if won else ("L", -stake)
            self.db.execute("UPDATE bets SET final_total=?, result=?, pnl=?, src=? WHERE id=?", (final_total, res, pnl, src, bid))
        self.db.commit()
        return len(rows)

    def settle_vanished(self, game_id: int, min_total: int) -> int:
        rows = self.db.execute("SELECT id,side,line,stake FROM bets WHERE game_id=? AND result IS NULL", (game_id,)).fetchall()
        for bid, side, line, stake in rows:
            if side == "under" and min_total >= line:
                self.db.execute("UPDATE bets SET final_total=?, result='L', pnl=?, src='partial' WHERE id=?", (min_total, -stake, bid))
            else:
                self.db.execute("UPDATE bets SET result='V', pnl=0, src='void' WHERE id=?", (bid,))
        self.db.commit()
        return len(rows)

    def stale_open_meta(self, now_s: float, grace_s: float = 7200.0) -> list[tuple[int, int | None, int | None]]:
        return self.db.execute("SELECT game_id, MAX(champ_id), MAX(start_ts) FROM bets WHERE result IS NULL AND ts_ms/1000.0 + ? < ? GROUP BY game_id",
                               (grace_s, now_s)).fetchall()

    def summary(self) -> dict[str, dict]:
        out = {}
        for rule in self.rules:
            n, pnl, w = self.db.execute("SELECT COUNT(*),COALESCE(SUM(pnl),0),COALESCE(SUM(result='W'),0) FROM bets WHERE rule=? AND result IN ('W','L')", (rule,)).fetchone()
            out[rule] = {"settled": n, "pnl_u": round(pnl, 2), "roi": round(pnl / n, 4) if n else 0.0, "wins": int(w)}
        return out
