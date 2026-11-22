import sqlite3

from vectiq.notify.users import Users


def _dbs():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(game_id INTEGER, pnl REAL, stake REAL, result TEXT)")
    return {"bet": db, "st5": None, "st0": None}


def test_pending_then_approved_then_bank():
    us, dbs = Users(), _dbs()
    r, note = us.handle("7", "Ana", "/start", 0.0, dbs, 0.01)
    assert "pendiente" in r and "Ana" in note and not us.active()
    assert us.handle("7", "Ana", "/banca 30", 0.0, dbs, 0.01)[0].startswith("Hola")      # still pending: ignored
    us.approve("7", True)
    r, _ = us.handle("7", "Ana", "30", 1.0, dbs, 0.01)                                      # plain number after approval
    assert "$30.00" in r and len(us.active()) == 1
    u = us.get("7")
    s, redo = us.stake(u, 30.0, 2.0, 0.01, 1.0)
    assert s == 0.3 and not redo


def test_bank_follows_results_and_skips():
    us, dbs = Users(), _dbs()
    us.register("1", "B")
    us.approve("1", True)
    u = us.get("1")
    u.update(bank=100.0, bank_ts=0.0)
    dbs["bet"].executemany("INSERT INTO bets VALUES(?,?,?,?)", [(1, 1.2, 1.0, "W"), (2, -1.0, 1.0, "L"), (3, 1.2, 1.0, "W")])
    for gid in (1, 2, 3):
        us.record(u, gid, "bet", 10, 1.0)
    us.mark_skip(u, 3)
    assert abs(us.bank(u, dbs) - 100.2) < 1e-9
