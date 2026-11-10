import sqlite3

from vectiq.notify.session import Session, parse_amount
from vectiq.signals import st3


def test_parse_amount():
    assert parse_amount("103.5") == 103.5 and parse_amount(" $ 103,50 ") == 103.5
    assert parse_amount("abc") is None and parse_amount("") is None and parse_amount("0") is None
    assert parse_amount("10 3.5 x") is None


def test_start_and_stop_flow_keeps_the_real_balance(tmp_path):
    s = Session(tmp_path / "session.json")
    assert not s.active and "no iniciada" in s.footer()
    assert s.submit("103.5", 1000.0) is None                 # a number nobody asked for is ignored
    assert "saldo real" in s.press("start", 1000.0)
    assert "No entendí" in s.submit("mucho", 1010.0)
    assert "Sesión iniciada" in s.submit("103.5", 1020.0)
    assert s.active and s.bank == 103.5 and s.footer() == ""
    assert "saldo final" in s.press("stop", 2000.0)
    msg = s.submit("110", 2010.0)
    assert "Sesión cerrada" in msg and "+6.50" in msg and not s.active and s.bank == 110.0
    again = Session(tmp_path / "session.json")                 # survives the hand-off between collector runs
    assert again.bank == 110.0 and len(again.d["log"]) == 1


def test_answer_must_arrive_in_time_and_banca_command_corrects(tmp_path):
    s = Session(tmp_path / "s.json")
    s.press("start", 0.0)
    assert s.submit("100", 5000.0) is None                    # 15 minutes passed
    assert "Banca corregida" in s.submit("/banca 99,5", 5000.0) and s.bank == 99.5


def test_updates_are_handled_once(tmp_path):
    s = Session(tmp_path / "s.json")
    assert not s.seen(10) and s.seen(10) and s.seen(9) and not s.seen(11)


def _db(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(id INTEGER PRIMARY KEY, ts_ms INTEGER, result TEXT, price REAL, lead_min REAL)")
    db.executemany("INSERT INTO bets(ts_ms,result,price) VALUES(?,?,?)", [(i, r, p) for i, (r, p) in enumerate(rows)])
    return db


def _mix(wins, losses, reps=28):
    return ([("W", 2.1)] * wins + [("L", 2.1)] * losses) * reps


def test_evidence_fraction_shrinks_with_weak_evidence():
    good, marginal, flat = _mix(11, 9), _mix(10, 10), _mix(9, 11)           # hit 55 % / 50 % / 45 % at 2.10
    f_good, f_marg = st3.evidence_frac(_db(good), 0.015), st3.evidence_frac(_db(marginal), 0.015)
    assert 0.003 < f_good <= 0.015 and f_marg < f_good
    assert st3.evidence_frac(_db(flat), 0.015) == 0.0
    assert st3.evidence_frac(_db([("W", 2.1)] * 5), 0.015) == 0.005         # too little data: small default
    assert st3.evidence_frac(_db(good), 0.002) == 0.002                    # the cap binds


def test_cusum_alarms_when_the_edge_dies_not_when_it_holds():
    holds = [("W", 2.1), ("L", 2.1)] * 40 + [("W", 2.1)] * 20 + [("L", 2.1)] * 12
    dead = [("W", 2.1)] * 35 + [("L", 2.1)] * 45 + [("W", 2.1)] * 20 + [("L", 2.1)] * 45
    assert st3.cusum_stat(_db(dead)) >= st3.CUSUM_H
    assert st3.cusum_stat(_db([("W", 2.1)] * 60)) == 0.0
    assert st3.cusum_stat(_db(holds)) < st3.cusum_stat(_db(dead))


def test_betting_day_starts_at_6am_cuba():
    day = 1_800_000_000.0
    d0 = st3.day_start_ms(day) / 1000
    assert (d0 % 86400) == 11 * 3600 and d0 <= day < d0 + 86400


def test_panel_buttons_and_typed_balance_drive_the_alert_dollars(tmp_path, monkeypatch):
    from test_masters import NOW
    from test_st3 import _pre_col, _s

    monkeypatch.setattr(st3, "evidence_frac", lambda db, cap, where="": cap)
    sent, toasts = [], []
    ups = [[{"update_id": 1, "callback_query": {"id": "a", "data": "s:start", "message": {"message_id": 5}}}],
           [{"update_id": 2, "channel_post": {"message_id": 6, "text": "200"}}]]
    col, led, st = _pre_col(tmp_path, sent, poll_updates=lambda off: ups.pop(0) if ups else [],
                            ack_callback=lambda *a: toasts.append(a), session=Session(tmp_path / "session.json"))
    col._poll_exec(NOW)                                         # first poll also posts the panel
    assert any("PANEL" in m and b for m, b in sent) and col.session.d["panel_id"] == 7
    assert any("saldo real" in m for m, _ in sent) and toasts and toasts[0][2] is None      # toast only: panel buttons stay
    col._poll_exec(NOW + 10)
    assert col.session.active and col.session.bank == 200.0 and any("Sesión iniciada" in m for m, _ in sent)
    sent.clear()
    col._masters(_s([(11, 5)], start_in_min=-5, gid=333), NOW + 900)
    txt = " ".join(m for m, _ in sent)
    assert "$2.00" in txt and "no iniciada" not in txt           # 1 % of the typed $200, session running
