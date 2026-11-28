"""/estado like a sportsbook: stake leaves the balance while the alert is open and comes back with the result."""
import sqlite3

from vectiq.notify import bankroll
from vectiq.notify.users import Users

NOW = 1_800_000_000.0


def ledger(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(game_id INTEGER, result TEXT, pnl REAL, stake REAL, players TEXT, side TEXT, line REAL, price REAL)")
    db.executemany("INSERT INTO bets VALUES(?,?,?,?,?,?,?,?)", rows)
    return db


def test_open_stake_is_held_and_results_settle():
    st3 = ledger([(1, "W", 1.1, 1.0, "A vs B", "under", 100.5, 2.10),      # won at 2.10: +1.10 per $1
                  (2, None, None, 1.0, "C vs D", "under", 99.5, 1.95),     # still open
                  (3, "L", -1.0, 1.0, "E vs F", "under", 98.5, 1.90),
                  (4, "P", 0.0, 1.0, "G vs H", "under", 97.5, 1.90)])       # push: stake returned
    q = [(1, "bet", int((NOW - 3000) * 1000), 2.0), (2, "bet", int((NOW - 120) * 1000), 2.0), (3, "bet", int((NOW - 2000) * 1000), 2.0),
         (4, "bet", int((NOW - 1500) * 1000), 2.0)]
    s = bankroll.snapshot(100.0, NOW - 4000, q, {"bet": st3}, set(), NOW)
    assert s["realized"] == 100.0 + 2.2 - 2.0 and s["n_settled"] == 2
    assert s["open_n"] == 1 and s["in_play"] == 2.0
    assert abs(s["available"] - (100.2 - 2.0)) < 1e-9
    assert "C vs D" in bankroll.format_open(s["open"]) and "Under 99.5 @ 1.95" in bankroll.format_open(s["open"])


def test_no_llegue_is_neither_result_nor_in_play_and_old_unsettled_is_void():
    st3 = ledger([(1, None, None, 1.0, "A vs B", "under", 100.5, 2.0), (2, None, None, 1.0, "C vs D", "under", 99.5, 2.0)])
    q = [(1, "bet", int((NOW - 60) * 1000), 2.0), (2, "bet", int((NOW - 5 * 3600) * 1000), 2.0)]
    s = bankroll.snapshot(50.0, NOW - 7 * 3600, q, {"bet": st3}, {1}, NOW)
    assert s["open_n"] == 0 and s["excluded"] == 1 and s["available"] == 50.0       # skipped, and the 5 h old one is a vanished match


def test_quotes_before_the_typed_balance_are_ignored_and_kinds_use_their_own_ledger():
    st3, st5 = ledger([(1, None, None, 1.0, "A vs B", "under", 100.5, 2.0)]), ledger([(1, "W", 0.9, 1.0, "A vs B", "under", 101.5, 1.9)])
    q = [(1, "bet", int((NOW - 500) * 1000), 2.0), (1, "st5", int((NOW - 400) * 1000), 3.0), (9, "bet", int((NOW - 9999) * 1000), 5.0)]
    s = bankroll.snapshot(100.0, NOW - 600, q, {"bet": st3, "st5": st5}, set(), NOW)
    assert s["open_n"] == 1 and s["in_play"] == 2.0 and abs(s["realized"] - 102.7) < 1e-9


def test_user_estado_shows_available_and_keeps_the_stake_basis():
    st3 = ledger([(1, "W", 1.0, 1.0, "A vs B", "under", 100.5, 2.0), (2, None, None, 1.0, "C vs D", "under", 99.5, 1.95)])
    us = Users(None)
    u, _ = us.register("7", "Ana")
    us.approve("7", True)
    u.update(bank=100.0, bank_ts=NOW - 4000, q=[[1, "bet", int((NOW - 3000) * 1000), 2.0], [2, "bet", int((NOW - 100) * 1000), 2.0]])
    dbs = {"bet": st3, "st5": None, "st0": None}
    assert us.bank(u, dbs) == 102.0                                                # stake base unchanged: typed + settled
    txt, _ = us.handle("7", "Ana", "/estado", NOW, dbs, 0.01)
    assert "Disponible: $100.00" in txt and "En juego ahora: <b>1</b>" in txt and "C vs D" in txt


def test_alert_delivery_time_is_logged_next_to_the_alert():
    import time
    from vectiq.signals import st3
    db = sqlite3.connect(":memory:")
    al = st3.Alert(5, "A vs B", "Masters. Poland", (11, 8), (0, 0), 100.5, 2.0, [(100.5, 1.8, 2.0)], int(time.time() * 1000) - 1500)
    assert st3.record_alert(db, al, "bet", 77) is True
    d, s = db.execute("SELECT detect_ms, sent_ms FROM alert_timing WHERE game_id=5").fetchone()
    assert 1000 <= s - d <= 60_000
    assert st3.record_alert(db, al, "bet", 77) is False and db.execute("SELECT COUNT(*) FROM alert_timing").fetchone()[0] == 1


def test_alert_timing_is_logged_even_without_channel_message():
    """Alerts go to private chats only (channel message_id is None): the latency must still be recorded."""
    import time
    from vectiq.signals import st3
    db = sqlite3.connect(":memory:")
    al = st3.Alert(6, "A vs B", "Masters. Poland", (11, 8), (0, 0), 100.5, 2.0, [(100.5, 1.8, 2.0)], int(time.time() * 1000) - 2000)
    assert st3.record_alert(db, al, "st0", None) is True
    assert db.execute("SELECT COUNT(*) FROM alert_timing WHERE game_id=6 AND kind='st0'").fetchone()[0] == 1
