import json

from test_masters import NOW, state

from vectiq.signals import st2
from vectiq.signals.paper import PaperLedger

# ladder whose centre (devig Under = 0.5) is ~109.5; central line price 1.80 < 1.85
LAD = ((104.5, 1.55, 2.35), (109.5, 2.00, 1.80), (114.5, 2.40, 1.55))


def _s(sets, **kw):
    return state(sets=sets, live=True, lines=kw.pop("lines", LAD), **kw)


def test_observe_k1_and_signal():
    o = st2.observe(_s([(11, 8)]))
    assert o and o["k"] == 1 and o["line"] == 109.5 and o["price"] == 1.80
    sig = st2.signal_from(o)
    assert sig and sig.side == "under" and sig.line == 109.5 and sig.stake_frac == st2.STAKE_FRAC


def test_price_cap_blocks_bet_but_still_observes():
    o = st2.observe(_s([(11, 8)], lines=((104.5, 1.50, 2.60), (109.5, 1.90, 1.90), (114.5, 2.60, 1.50))))
    assert o and st2.signal_from(o) is None


def test_k2_is_observed_never_bet():
    o = st2.observe(_s([(11, 8), (11, 9)]))
    assert o and o["k"] == 2 and st2.signal_from(o) is None


def test_filters():
    assert st2.observe(_s([])) is None                                   # k=0
    assert st2.observe(_s([(11, 8), (11, 9), (11, 7)])) is None           # k=3
    assert st2.observe(_s([(11, 8)], league="Masters. China")) is None
    assert st2.observe(_s([(11, 8)], best_of=5)) is None
    assert st2.observe(state(sets=[(11, 8)], live=False, lines=LAD)) is None
    assert st2.observe(_s([(11, 8)], lines=((109.5, 1.8, 1.8),))) is None  # ladder < 2 lines


def test_first_snapshot_only_and_ledger(tmp_path):
    led = PaperLedger(tmp_path / "st2.sqlite", start_bankroll=1000.0)
    o = st2.observe(_s([(11, 8)]))
    assert st2.log_obs(led.db, o) and not st2.log_obs(led.db, o)
    assert led.open_bet(st2.signal_from(o), o["ts_ms"]) and not led.open_bet(st2.signal_from(o), o["ts_ms"])
    st2.mark_bet(led.db, o["game_id"], 1)
    assert led.db.execute("SELECT bet, data FROM obs").fetchone()[0] == 1
    assert json.loads(led.db.execute("SELECT data FROM obs").fetchone()[0])["ladder"][1][0] == 109.5
    led.settle(o["game_id"], 100)
    assert led.summary()["settled"] == 1 and led.summary()["roi"] > 0
