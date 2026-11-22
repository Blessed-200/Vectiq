from test_masters import state

from vectiq.notify import bankroll, telegram
from vectiq.signals import st0
from vectiq.signals.paper import PaperLedger
import sqlite3

LAD = ((100.5, 1.62, 2.25), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))
LAD_LOW = ((100.5, 1.62, 2.12), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))
LAD_CHEAP = ((100.5, 1.62, 2.05), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))


def _pre(lines=LAD, sets=(), live=False, **kw):
    return state(sets=list(sets), live=live, lines=lines, **kw)


def test_prematch_bets_the_lowest_rung_when_it_pays_220():
    al = st0.evaluate(_pre())
    assert al and al.line == 100.5 and al.price == 2.25 and al.min_price == 2.20
    sig = st0.signal_from(al, st0.STAKE_FRAC)
    assert sig.side == "under" and sig.line == 100.5 and sig.sets == []


def test_between_210_and_220_only_low_scorers():
    assert st0.evaluate(_pre(LAD_LOW)) is None
    assert st0.evaluate(_pre(LAD_LOW), z=-0.1) is None
    al = st0.evaluate(_pre(LAD_LOW), z=-0.5)
    assert al and al.price == 2.12 and al.min_price == 2.10
    assert st0.evaluate(_pre(LAD_CHEAP), z=-2.0) is None          # below 2.10 never


def test_only_before_the_first_point_and_with_three_rungs():
    assert st0.evaluate(_pre(sets=[(11, 5)], live=True)) is None
    assert st0.evaluate(_pre(sets=[(2, 1)], live=True)) is None
    assert st0.evaluate(_pre(sets=[(0, 0)], live=True)) is not None      # live but 0-0
    assert st0.evaluate(_pre(LAD[:2])) is None


def test_tracked_bank_counts_st0_results():
    db3, db0 = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    for db in (db3, db0):
        db.execute("CREATE TABLE bets(game_id INTEGER, result TEXT, pnl REAL, stake REAL)")
    db0.execute("INSERT INTO bets VALUES(7,'W',2.5,2.0)")
    bankroll.record_quote(db3, 7, "st0", 2_000_000, 10.0)
    bank, n, _ = bankroll.tracked_bank(100.0, 1.0, db3, None, db0)
    assert n == 1 and abs(bank - 112.5) < 1e-9


def test_message_head():
    al = st0.evaluate(_pre(start_ts=1_000_000_000 + 150) if False else _pre())
    txt = telegram.format_st0(al, 100.0, 0.01, "https://x")
    assert "PREPARTIDO" in txt and "MENOS DE 100.5 @ 2.25" in txt and "(mín 2.20)" in txt
