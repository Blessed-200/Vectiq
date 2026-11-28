"""Book brake (signals/brake.py) and its wiring: stake weights 0.7 / 1.0 / 0.65 %, uniform multiplier on alerts."""
import random
import sqlite3
import time
from types import SimpleNamespace

from vectiq.signals import brake


def _events(n, roi, w=0.01, price=2.0, seed=1, t0=1_000_000.0, step=600.0):
    """n bets with win probability set so the expected unit result is `roi` at `price`."""
    r = random.Random(seed)
    p = (1 + roi) / price
    return [(t0 + i * step, w, (price - 1) if r.random() < p else -1.0) for i in range(n)]


def test_too_little_history_keeps_full_stake():
    ev = _events(50, 0.10)
    assert brake.multiplier(ev, ev[-1][0])[0] == 1.0


def test_healthy_edge_is_not_braked():
    ev = _events(3000, 0.13, w=0.007, seed=3)
    x, info = brake.multiplier(ev, ev[-1][0])
    assert x >= 0.95 and info["x_dd"] == 1.0


def test_vanished_edge_cuts_the_stake_but_never_below_the_floor():
    ev = _events(2500, 0.12, w=0.007, seed=4) + _events(2500, -0.05, w=0.007, seed=5, t0=1_000_000.0 + 2500 * 600.0)
    x, info = brake.multiplier(ev, ev[-1][0])
    assert brake.FLOOR <= x <= 0.6 and info["x_edge"] < 0.6 and "ROI reciente" in brake.reason(info)


def test_drawdown_brake_halves_and_releases_with_hysteresis():
    w = 0.02
    win = [(1000.0 + i * 600, w, 1.0) for i in range(300)]
    lose = [(1000.0 + (300 + i) * 600, w, -1.0) for i in range(14)]          # (0.98)^14 -> ~25 % under the peak
    x, info = brake.multiplier(win + lose, win[-1][0] + 14 * 600)
    assert info["dd"] >= brake.DD_ON and info["x_dd"] == brake.DD_X and x <= brake.DD_X
    # a long run of small gains takes the drawdown under DD_OFF again -> released
    rec = [(1000.0 + (314 + i) * 600, 0.02, 1.0) for i in range(60)]
    x2, info2 = brake.multiplier(win + lose + rec, rec[-1][0])
    assert info2["dd"] <= brake.DD_OFF and info2["x_dd"] == 1.0


def _ledger(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(id INTEGER PRIMARY KEY, ts_ms INTEGER, start_ts INTEGER, price REAL, result TEXT)")
    db.executemany("INSERT INTO bets(ts_ms, start_ts, price, result) VALUES(?,?,?,?)", rows)
    return db


def test_load_events_reads_settled_bets_of_the_last_30_days_and_skips_missing_books():
    now = time.time()
    rows = [(int((now - 3600) * 1000), int(now - 3000), 2.2, "W"), (int((now - 7200) * 1000), None, 2.0, "L"),
            (int((now - 40 * 86400) * 1000), None, 2.0, "W"), (int((now - 600) * 1000), None, 2.0, None)]
    ev = brake.load_events({"st0": _ledger(rows), "st3": None, "st5": _ledger([])}, {"st0": 0.0065, "st3": 0.007, "st5": 0.01}, now)
    assert [round(e[2], 2) for e in ev] == [-1.0, 1.2] and all(e[1] == 0.0065 for e in ev)
    assert all(e[0] <= now for e in ev)


def _collector(tmp_path):
    from tests.test_bankroll import _db_ledger
    from vectiq.collector import Collector
    from vectiq.notify.session import Session
    c = Collector.__new__(Collector)
    c.session, c.real_bank, c.flat_stake, c.stake_pct = Session(tmp_path / "s.json"), 1000.0, 2.0, 0.0175
    c.st3, c.st5 = _db_ledger(), None
    return c


def test_flat_applies_the_book_brake_to_the_dollar_stake(tmp_path):
    c = _collector(tmp_path)
    c._book_brake = lambda: (1.0, "")
    full, note_full = c._flat(0.01, "", "A vs B")
    c._book_brake = lambda: (0.5, "ROI reciente 4.0 % (esperado +10 %)")
    half, note_half = c._flat(0.01, "", "A vs B")
    assert abs(half * 2 - full) <= 0.003 and "Freno de cartera ×0.50" in note_half and "Freno" not in note_full


def test_book_brake_is_off_in_fixed_dollar_mode_and_with_the_env_switch(tmp_path, monkeypatch):
    c = _collector(tmp_path)
    c.stake_pct = 0.0
    assert c._book_brake() == (1.0, "")
    c.stake_pct = 0.01
    monkeypatch.setenv("VECTIQ_BOOK_BRAKE", "0")
    assert c._book_brake() == (1.0, "")


def test_book_brake_survives_a_broken_ledger(tmp_path):
    c = _collector(tmp_path)
    c.st5 = SimpleNamespace(db=None)
    assert c._book_brake()[0] == 1.0                      # no 'bets' table / no st0 -> neutral, never raises


def test_fan_out_stake_uses_the_brake_and_the_new_weights(tmp_path):
    from vectiq.notify.users import Users
    c = _collector(tmp_path)
    sent = []
    u = {"stake_base": 100.0, "stake_base_ts": time.time()}
    c.users = SimpleNamespace(active=lambda: [("1", u)], bank=lambda *_: 100.0, stake=Users.stake.__get__(SimpleNamespace()),
                              record=lambda *a: None, save=lambda: None)
    c.send_to = lambda uid, text, buttons, flag: sent.append(text) or 1
    c._tilt = lambda p: 1.0
    c._dbs = lambda: {}
    c._book_brake = lambda: (0.5, "caída de cartera 22 %")
    c._fan("bet", 1, 0, "A vs B", 0.05, lambda b, f, n: f"{f:.4f}|{n}")
    frac = float(sent[0].split("|")[0])
    assert abs(frac - 0.025) < 0.006 and "Freno de cartera ×0.50" in sent[0]


def test_default_stake_weights_are_the_risk_matched_ones():
    from vectiq.notify import bankroll, panel
    from vectiq.signals import st0
    assert abs(bankroll.STAKE_PCT * bankroll.REL_ST3 - 0.007) < 1e-9 and bankroll.STAKE_PCT == 0.01 and st0.STAKE_PCT == 0.0065
    assert panel.PCTS == (0.007, 0.01, 0.0065)
