import sqlite3

from vectiq.notify import bankroll
from vectiq.signals import st3
from vectiq.signals.paper import SCHEMA


def _db():
    d = sqlite3.connect(":memory:")
    d.executescript(SCHEMA)
    st3.init_db(d)
    return d


def _bet(d, gid, stake, pnl, result="W"):
    d.execute("INSERT INTO bets(ts_ms,game_id,side,line,price,stake,result,pnl) VALUES(1,?,?,?,?,?,?,?)",
              (gid, "under", 100.5, 2.2, stake, result, pnl))


def test_tracked_bank_counts_taken_alerts_and_skips_no_llego():
    d3, d5 = _db(), _db()
    _bet(d3, 1, 1.0, 1.2)                       # win, quoted $10 -> +12
    _bet(d3, 2, 1.0, -1.0, "L")                 # loss, quoted $10 -> -10
    _bet(d3, 3, 1.0, 1.2)                       # win but user pressed "No llegué"
    _bet(d5, 4, 1.0, -1.0, "L")                 # st5 loss quoted $20 -> -20
    for gid, kind, stake in ((1, "bet", 10), (2, "bet", 10), (3, "bet", 10), (4, "st5", 20), (5, "bet", 10)):
        bankroll.record_quote(d3, gid, kind, 2000, stake)       # game 5 unsettled
    d3.execute("INSERT INTO exec VALUES(1,0,3,'no_llego','')")
    bank, n, ex = bankroll.tracked_bank(1000.0, 1.0, d3, d5)
    assert (round(bank, 2), n, ex) == (982.0, 3, 1)
    assert bankroll.tracked_bank(1000.0, 5.0, d3, d5)[0] == 1000.0      # alerts older than the typed balance are ignored


def test_stake_recalc_hourly_or_5pct_move():
    d = {}
    assert bankroll.stake_now(d, 1000.0, 0.0) == (17.5, True)
    assert bankroll.stake_now(d, 1030.0, 600.0) == (17.5, False)        # +3 %, 10 min: same stake
    assert bankroll.stake_now(d, 1060.0, 700.0) == (18.5, True)         # +6 %: recalculated
    assert bankroll.stake_now(d, 1060.0, 700.0 + 3600) == (18.5, True)  # an hour later
    assert bankroll.stake_now({}, 10.0, 0.0)[0] == 1.0                  # floor $1
    assert bankroll.stake_now({}, 1e7, 0.0)[0] == 3000.0                # cap $3,000


def test_collector_flat_uses_live_bank_and_announces_recalc(tmp_path):
    from vectiq.collector import Collector
    from vectiq.notify.session import Session
    c = Collector.__new__(Collector)
    c.session, c.real_bank, c.flat_stake, c.stake_pct = Session(tmp_path / "s.json"), 1000.0, 2.0, 0.0175
    c.st3, c.st5 = _db_ledger(), None
    frac, note = c._flat(0.01)
    assert round(frac * 1000, 2) == 17.5 and "recalculado" in note       # first call: base = bank
    frac, note = c._flat(0.01)
    assert round(frac * 1000, 2) == 17.5 and note == ""                  # same hour, no move: silent


def _db_ledger():
    class L:
        db = _db()
    return L()


def test_flat_drops_stale_reduced_stake_note(tmp_path):
    from vectiq.collector import Collector
    from vectiq.notify.session import Session
    c = Collector.__new__(Collector)
    c.session, c.real_bank, c.flat_stake, c.stake_pct = Session(tmp_path / "s.json"), 1000.0, 2.0, 0.0175
    c.st3, c.st5 = _db_ledger(), None
    c._flat(0.01)                                                   # first call recalculates
    assert c._flat(0.005, "Apuesta reducida por la evidencia reciente o por la caída.")[1] == ""
