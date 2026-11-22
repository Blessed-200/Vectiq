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


def test_collector_st0_alert_end_to_end_with_private_user(tmp_path):
    """The collector path (not just the rule): paper bet + channel alert + personalised private alert."""
    from test_masters import NOW, FakeClient

    from vectiq.collector import Collector
    from vectiq.notify.users import Users
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    sent, priv = [], []
    st3l, st0l = PaperLedger(tmp_path / "st3.sqlite", 100.0), PaperLedger(tmp_path / "st0.sqlite", 100.0)
    us = Users()
    us.register("9", "Ana")
    us.approve("9", True)
    us.get("9").update(bank=30.0, bank_ts=0.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=PaperLedger(tmp_path / "m.sqlite", 100.0),
                    book=masters.MastersBook.from_seed(), st3_ledger=st3l, st0_ledger=st0l, alerts="st0",
                    notify=lambda m: True, notify_alert=lambda m, b=None, silent=False: sent.append(m) or 7,
                    users=us, send_to=lambda cid, t, b=None, silent=False: priv.append((cid, t)) or 1, alert_chat="1")
    col._masters(_pre(), NOW)
    assert st0l.open_games() and len(sent) == 1 and "PREPARTIDO" in sent[0] and "MENOS DE 100.5" in sent[0]
    assert len(priv) == 1 and priv[0][0] == "9" and "$0.30" in priv[0][1] or "$0.50" in priv[0][1]
    col._masters(_pre(), NOW + 5)                                  # same match: no second alert
    assert len(sent) == 1 and len(priv) == 1
