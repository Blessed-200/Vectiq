import json
import sqlite3

import numpy as np

from vectiq.signals import players
from vectiq.signals.players import Ratings


def _rows(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    names = [f"P{i}" for i in range(40)]
    true = {p: (-4.0 if i < 5 else 4.0 if i >= 35 else 0.0) for i, p in enumerate(names)}   # 5 low-scoring, 5 high-scoring
    rows = []
    for i in range(n):
        a, b = rng.choice(names, 2, replace=False)
        rows.append((str(a), str(b), true[a] + true[b] + rng.normal(0, 20), 20000.0 + i / 100.0))
    return rows


def test_ridge_finds_low_and_high_scoring_players():
    r = Ratings()
    assert r.fit(_rows(), now_days=20016.0)
    assert r.mult("P0 vs P1") > 1.15          # two low-scoring players: more stake
    assert r.mult("P38 vs P39") < 0.85        # two high-scoring players: less stake
    assert 0.9 < r.mult("P10 vs P11") < 1.1   # neutral pair stays near 1
    assert r.mult("Nuevo Jugador vs P1") == r.mult("Otro vs Otro2") == 1.0 / r.norm * np.clip(1.0, players.LO, players.HI)   # unseen: neutral z


def test_too_little_history_is_neutral_and_average_stake_is_preserved():
    r = Ratings()
    assert not r.fit(_rows(100), now_days=20016.0) and r.mult("P0 vs P1") == 1.0
    r.fit(_rows(), now_days=20016.0)
    ms = [r.mult(f"P{i} vs P{j}") for i in range(40) for j in range(40) if i != j]
    assert abs(np.mean(ms) - 1.0) < 0.08 and min(ms) >= players.LO / r.norm - 1e-9 and max(ms) <= players.HI / r.norm + 1e-9


def test_load_joins_obs_with_finals():
    st2, cal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    st2.execute("CREATE TABLE obs(game_id INTEGER, k INTEGER, ts_ms INTEGER, bet INTEGER, data TEXT)")
    cal.execute("CREATE TABLE mkt(game_id INTEGER, k INTEGER, final REAL)")
    st2.execute("INSERT INTO obs VALUES(1,1,86400000,0,?)", (json.dumps({"players": "A vs B", "centre": 100.0}),))
    st2.execute("INSERT INTO obs VALUES(2,1,86400000,0,?)", (json.dumps({"players": "C vs D", "centre": 100.0}),))   # no final yet
    cal.execute("INSERT INTO mkt VALUES(1,1,95.0)")
    assert Ratings.load(st2, cal) == [("A", "B", -5.0, 1.0)]


def test_collector_flat_tilts_stake_by_players(tmp_path):
    import time

    from vectiq.collector import Collector
    from vectiq.notify.session import Session
    from tests.test_bankroll import _db_ledger
    c = Collector.__new__(Collector)
    c.session, c.real_bank, c.flat_stake, c.stake_pct = Session(tmp_path / "s.json"), 1000.0, 2.0, 0.0175
    c.st3, c.st5 = _db_ledger(), None
    c.ratings = Ratings(); c.ratings.fit(_rows(), now_days=20016.0); c.ratings.fit_ts = time.time()    # skip the hourly refit
    from types import SimpleNamespace
    c.st2 = c.calibra = SimpleNamespace(db=None)
    base = c._flat(0.01, "", "P10 vs P11")[0] * 1000
    up, note_up = c._flat(0.01, "", "P0 vs P1"); down, note_dn = c._flat(0.01, "", "P38 vs P39")
    assert up * 1000 > base * 1.3 and "pocos puntos" in note_up
    assert down * 1000 < base * 0.8 and "más puntos" in note_dn
    assert abs(c._flat(0.01, "", "Desconocido vs Otro")[0] * 1000 - base) <= 1.0      # unseen players: neutral (within rounding)
