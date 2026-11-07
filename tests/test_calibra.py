import itertools
import random
import sqlite3

from test_masters import NOW, state

from vectiq.signals import calibra as cb
from vectiq.signals.calibra import Calibra
from vectiq.signals.paper import PaperLedger


_ids = itertools.count(1)


def _fill(c, day, k, n, mean, sd=20.0, league="Masters. Rusia", start=0):
    """n finished matches whose (centre - real) values are exactly mean +/- sd (SE = sd / sqrt(n))."""
    for i in range(n):
        x = mean + sd * (1 if i % 2 else -1)
        c.db.execute("INSERT INTO mkt(game_id,k,ts_ms,day,league,centre,ladder,final) VALUES(?,?,?,?,?,?,?,?)",
                     (next(_ids), k, 0, day, league, 100.0 + x, "[]", 100.0))
    c.db.commit()


def test_observe_keeps_last_prematch_then_first_live_snapshot(tmp_path):
    c = Calibra(tmp_path / "c.sqlite")
    s = state()
    c.observe(s)
    s.fetched_ms += 61_000
    c.observe(s)                                                              # later pre-match snapshot replaces the old
    assert c.db.execute("SELECT COUNT(*), MAX(ts_ms) FROM mkt WHERE k=0").fetchone() == (1, int(NOW * 1000) + 61_000)
    c.observe(state(sets=[(11, 5)], live=True))
    c.observe(state(sets=[(11, 5), (3, 1)], live=True))                       # k=1 already stored: no second row
    assert c.db.execute("SELECT COUNT(*) FROM mkt WHERE k=1").fetchone()[0] == 1
    c.observe(state(sets=[(11, 5), (6, 3)], live=True))                       # 9 points into set 2: too late for k=1
    assert c.db.execute("SELECT COUNT(*) FROM mkt").fetchone()[0] == 2
    c.observe(state(league="Masters. China"))
    assert c.db.execute("SELECT COUNT(*) FROM mkt WHERE league LIKE '%China%'").fetchone()[0] == 0
    c.set_final(383584934, 90)
    assert c.db.execute("SELECT final FROM mkt WHERE k=1").fetchone()[0] == 90


def test_sensor_learns_reference_then_stays_quiet_when_the_house_is_unchanged(tmp_path):
    c = Calibra(tmp_path / "c.sqlite")
    learning = []
    for d in range(1, 4):
        _fill(c, f"2026-11-0{d}", 0, 400, 5.5)
        learning.append(c.sensor_day(0, f"2026-11-0{d}")["learning"])
    assert learning == [True, True, False]                                     # reference exists after 3 full days
    assert abs(c._meta("base_0") - 5.5) < 1e-6
    for d in range(4, 10):
        _fill(c, f"2026-11-0{d}", 0, 400, 5.5)
        r = c.sensor_day(0, f"2026-11-0{d}")
        assert r["alarm"] == 0
    assert not c.braked(0)
    assert c.sensor_day(0, "2026-11-09") is None                             # idempotent


def test_sensor_alarms_within_days_when_the_edge_halves_but_ignores_a_growing_edge(tmp_path):
    c = Calibra(tmp_path / "c.sqlite")
    for d in range(1, 4):
        _fill(c, f"2026-11-0{d}", 1, 400, 5.5)
        c.sensor_day(1, f"2026-11-0{d}")
    alarm_day = None
    for i, d in enumerate(("2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"), 1):
        _fill(c, d, 1, 400, 2.5)                                              # the house recalibrated: bias 5.5 -> 2.5
        if c.sensor_day(1, d)["alarm"] and alarm_day is None:
            alarm_day = i
    assert alarm_day is not None and alarm_day <= 3 and c.braked(1)
    c2 = Calibra(tmp_path / "d.sqlite")
    for d in range(1, 4):
        _fill(c2, f"2026-11-0{d}", 1, 400, 5.5)
        c2.sensor_day(1, f"2026-11-0{d}")
    for d in range(4, 9):
        _fill(c2, f"2026-11-0{d}", 1, 400, 9.0)                               # edge GROWS: never a brake
        assert c2.sensor_day(1, f"2026-11-0{d}")["alarm"] == 0


def _ledger(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(game_id INTEGER, league TEXT, price REAL, stake REAL, pnl REAL, result TEXT, lead_min REAL, ts_ms INTEGER)")
    for i, (lg, price, win) in enumerate(rows):
        db.execute("INSERT INTO bets VALUES(?,?,?,?,?,?,?,?)", (i, lg, price, 1.0, price - 1 if win else -1.0, "W" if win else "L", 5.0, i))
    return db


def test_belief_and_stake_rules():
    rng = random.Random(3)
    good = [("A", 2.0, rng.random() < 0.58) for _ in range(400)]              # real +16 % cell
    dead = [("B", 2.0, rng.random() < 0.46) for _ in range(80)]               # losing cell
    few = [("C", 2.0, True) for _ in range(10)]
    db = _ledger(good + dead + few)
    bg, bd, bf = cb.belief(db, "pre", "A"), cb.belief(db, "pre", "B"), cb.belief(db, "pre", "C")
    assert bg.media > 0.05 and 0 < cb.stake(bg) <= cb.TECHO
    assert cb.stake(bd) == 0.0
    assert cb.stake(bf) == cb.EXPLORA                                         # thin evidence: exploration only
    assert cb.stake(bg, braked=True) == round(cb.stake(bg) * cb.BRAKE, 6)
    assert cb.belief(db, "pre", "Z").n == 0 and cb.stake(cb.belief(db, "pre", "Z")) == cb.EXPLORA


def _scout_world(c, edge, days=10, per_day=300, seed=1):
    rng = random.Random(seed)
    under_low = 2.3 if edge else 1.9
    lad = f"[[100.5, 1.9, {under_low}], [110.5, 1.9, 1.9]]"
    for d in range(days):
        for i in range(per_day):
            final = 90.0 if rng.random() < 0.5 else 120.0
            c.db.execute("INSERT INTO mkt(game_id,k,ts_ms,day,league,centre,ladder,final) VALUES(?,?,?,?,?,?,?,?)",
                         (d * 10_000 + i, 1, 0, f"2026-11-{d + 1:02d}", "Masters. Rusia", 105.0, lad, final))
    c.db.commit()


def test_scout_finds_a_real_edge_out_of_sample_and_nothing_in_a_fair_world(tmp_path):
    c = Calibra(tmp_path / "e.sqlite")
    _scout_world(c, edge=True)
    r = c.scout()
    assert r["candidates"] and r["candidates"][0][0] == (1, "Masters. Rusia", "under", "baja")
    assert r["oos_n"] > 0 and r["oos_roi"] > 0.05
    n = Calibra(tmp_path / "n.sqlite")
    _scout_world(n, edge=False)
    r0 = n.scout()
    assert r0["candidates"] == [] and r0["oos_n"] == 0                        # overround world: nothing passes Bonferroni


def test_comparison_prefers_the_smaller_stake_on_a_losing_streak(tmp_path):
    c = Calibra(tmp_path / "c.sqlite")
    led = _ledger([("A", 2.0, False)] * 5 + [("A", 2.0, True)] * 5)
    for i in range(10):
        c.db.execute("INSERT INTO shadow VALUES(?,?,?,?,?,?,?,?,?,?,?)", (i, "pre", i, "A", 2.0, 0.01, 0.005, 0, 0.1, 0.05, 100))
    c.db.commit()
    r = cb.comparison(c.db, {"pre": led})
    assert r["n"] == 10 and r["calibra"]["max_drawdown"] < r["ancla"]["max_drawdown"]
    assert r["calibra"]["deployed"] < r["ancla"]["deployed"]


def test_collector_wiring_logs_shadow_stake_final_and_never_alters_alerts(tmp_path):
    from test_masters import FakeClient

    from vectiq.collector import Collector
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    sent, legacy = [], []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    st = PaperLedger(tmp_path / "st3.sqlite", 100.0)
    c = Calibra(tmp_path / "c.sqlite")
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    st3_ledger=st, calibra=c, alerts="st3,pre", notify=lambda m: legacy.append(m) or True,
                    notify_alert=lambda m, b=None, silent=False: sent.append(m) or 7)
    col._masters(state(), NOW)
    assert len(sent) == 1 and sent[0].startswith("🔵")                      # same alert as without CALIBRA
    row = c.db.execute("SELECT strat, ancla, calibra FROM shadow").fetchone()
    assert row[0] == "pre" and row[1] > 0 and 0 <= row[2] <= cb.TECHO
    assert c.db.execute("SELECT COUNT(*) FROM mkt WHERE k=0").fetchone()[0] == 1
    ghost = state(sets=[(11, 6), (6, 11), (11, 9), (8, 11), (11, 5), (9, 11), (11, 7)], live=True)
    ghost.sets_won = (4, 3)
    col.last[ghost.game_id] = ghost
    col._finished(ghost.game_id)
    assert c.db.execute("SELECT final FROM mkt WHERE k=0").fetchone()[0] == 11 + 6 + 6 + 11 + 11 + 9 + 8 + 11 + 11 + 5 + 9 + 11 + 11 + 7
    assert c.close_day("2026-11-05") is None                                  # too little data: no message
