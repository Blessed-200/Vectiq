import sqlite3

from test_masters import state

from vectiq.notify import telegram
from vectiq.signals import st3
from vectiq.signals.paper import PaperLedger

# ladder: low rung 2.15 (>= 2.00), middle 1.90, top 1.65
LAD = ((100.5, 1.62, 2.15), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))


def _s(sets, **kw):
    return state(sets=sets, live=True, lines=kw.pop("lines", LAD), **kw)


def test_blowout_bets_the_lowest_rung_paying_two():
    al = st3.evaluate(_s([(11, 5)]))
    assert al and al.line == 100.5 and al.price == 2.15 and al.set1 == (11, 5) and al.inplay == (0, 0)
    sig = st3.signal_from(al, st3.STAKE_FRAC)
    assert sig.side == "under" and sig.line == 100.5 and sig.stake_frac == 0.01


def test_margin_below_five_is_no_bet():
    assert st3.evaluate(_s([(11, 7)])) is None            # margin 4
    assert st3.evaluate(_s([(11, 6)])) is not None        # margin 5 is the threshold
    assert st3.evaluate(_s([(5, 11)])) is not None        # the away player may be the blowout winner


def test_needs_a_rung_paying_two():
    assert st3.evaluate(_s([(11, 3)], lines=((103.5, 1.85, 1.95), (106.5, 2.2, 1.65)))) is None


def test_inplay_limit_and_other_filters():
    assert st3.evaluate(_s([(11, 3), (2, 1)])) is not None                 # 3 points of set 2: still inside the window
    assert st3.evaluate(_s([(11, 3), (4, 2)])) is None                     # 6 points: window closed
    assert st3.evaluate(_s([])) is None                                    # nothing completed yet
    assert st3.evaluate(_s([(11, 3), (11, 4)])) is None                    # k = 2 is never bet
    assert st3.evaluate(_s([(11, 3)], league="Masters. China")) is None
    assert st3.evaluate(_s([(11, 3)], league="Masters. Super League")) is None
    assert st3.evaluate(_s([(11, 3)], best_of=5)) is None
    assert st3.evaluate(state(sets=[(11, 3)], live=False, lines=LAD)) is None


def test_blocked_or_one_sided_lines_are_ignored():
    import dataclasses
    s = _s([(11, 3)])
    s.quotes = [dataclasses.replace(q, blocked=True) if q.line == 100.5 else q for q in s.quotes]
    assert st3.evaluate(s) is None                        # the only rung paying 2.00 is blocked: no bet
    lad = ((97.5, 1.45, 2.60),) + LAD
    s2 = _s([(11, 3)], lines=lad)
    s2.quotes = [dataclasses.replace(q, blocked=True) if q.line == 97.5 else q for q in s2.quotes]
    assert st3.evaluate(s2).line == 100.5                  # blocked 97.5 skipped, next rung paying 2.00


def test_stake_fraction_ladder():
    assert st3.stake_fraction(0.0) == 0.01
    assert abs(st3.stake_fraction(0.125) - 0.005) < 1e-9          # smooth brake: half at a 12.5 % drawdown
    assert abs(st3.stake_fraction(0.14) - 0.01 * 0.44) < 1e-9
    assert abs(st3.stake_fraction(0.29) - 0.01 * 0.25) < 1e-9       # never below a quarter until the stop
    assert st3.stake_fraction(0.30) == 0.0


def _bet(led, gid, won, ts):
    al = st3.evaluate(_s([(11, 3)], gid=gid))
    led.open_bet(st3.signal_from(al, 0.01), ts)
    led.settle(gid, 90 if won else 120)


def test_guard_rolling_roi_and_day_stop(tmp_path):
    led = PaperLedger(tmp_path / "st3.sqlite", start_bankroll=100.0)
    now_s = 1_800_000_000.0
    ok, _ = st3.guard(led.db, led.bankroll, led.drawdown(), now_s)
    assert ok
    for i in range(150):                       # 150 straight losses: rolling ROI -100 %
        _bet(led, 1000 + i, False, int((now_s - 3 * 86400) * 1000) + i)
    ok, why = st3.guard(led.db, led.bankroll, led.drawdown(), now_s)
    assert not ok and ("ROI" in why or "caída" in why)
    led2 = PaperLedger(tmp_path / "st3b.sqlite", start_bankroll=100.0)
    day0 = st3.day_start_ms(now_s)
    for i in range(17):                        # 17 losses of 1 % today: -15.7 % of the bankroll (stop at 15 %)
        _bet(led2, 2000 + i, False, day0 + 1000 + i)
    ok, why = st3.guard(led2.db, led2.bankroll, 0.0, now_s)
    assert not ok and "día" in why


def test_alert_and_exec_log_are_idempotent(tmp_path):
    db = sqlite3.connect(tmp_path / "x.sqlite")
    al = st3.evaluate(_s([(11, 3)]))
    assert st3.record_alert(db, al, "bet", 7) and not st3.record_alert(db, al, "bet", 8)
    assert st3.record_exec(db, 11, al.game_id, "aceptada") and not st3.record_exec(db, 11, al.game_id, "aceptada")
    assert st3.record_exec(db, 12, al.game_id, "no_llego")
    assert st3.exec_summary(db) == {"alerts": 1, "aceptada": 1, "no_llego": 1}


def test_cuba_clock_is_12_hour(monkeypatch):
    monkeypatch.delenv("VECTIQ_CUBA_UTC", raising=False)
    # 2026-11-04 01:14 UTC is 9:14 PM on 03/11 in Cuba (UTC-4, as the user's phone shows)
    assert telegram.cuba_clock(1793754840) == "9:14 PM"
    assert telegram.cuba_day(1793754840) == "03/11"
    # 12:11 UTC is 8:11 AM (the screenshot case); midnight and noon use 12
    assert telegram.cuba_clock(1793754840 + 11 * 3600 - 3 * 60) == "8:11 AM"
    monkeypatch.setenv("VECTIQ_CUBA_UTC", "-5")
    assert telegram.cuba_clock(1793754840) == "8:14 PM"


def test_alert_text_has_decision_first_and_cuba_time():
    al = st3.evaluate(_s([(11, 5)]))
    txt = telegram.format_st3(al, 100.0, 0.01, "https://betwinner2.com/es/live/x")
    lines = txt.split("\n")
    assert lines[0].startswith("🔴") and "MENOS DE 100.5" in lines[2] and "@ 2.15" in lines[2] and "$1.00" in lines[3]
    assert "Cuba" in txt and ("AM" in txt or "PM" in txt) and "$1.00" in txt and "betwinner2.com" in txt
    assert telegram.exec_buttons(5)[0][0][1] == "x:5:ok"


def test_collector_flow_arm_alert_button_settle_and_silenced_legacy(tmp_path):
    from test_masters import NOW, FakeClient

    from vectiq.collector import Collector
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    legacy, sent, acks = [], [], []
    gid = 383584934
    ups = [{"update_id": 5, "callback_query": {"id": "c1", "data": f"x:{gid}:ok", "from": {"first_name": "A"},
                                              "message": {"message_id": 42}}}]
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    st = PaperLedger(tmp_path / "st3.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    st3_ledger=st, alerts="st3", notify=lambda m: legacy.append(m) or True,
                    notify_alert=lambda m, b=None, silent=False: sent.append((m, b)) or 42,
                    poll_updates=lambda off: ups if off is None else [], ack_callback=lambda *a: acks.append(a),
                    alert_chat="1")
    col._masters(_s([(9, 4)], start_in_min=-5), NOW)                       # 9-4 is only 5 ahead: arms
    col._masters(_s([(9, 4)], start_in_min=-5), NOW + 3)                   # same match again: no second heads-up
    assert len(sent) == 1 and "PREPÁRATE" in sent[0][0]
    col._masters(_s([(11, 5)], start_in_min=-5), NOW + 10)                 # set 1 closes with a 6-point margin: bet alert
    assert len(sent) == 2 and "EN VIVO" in sent[1][0] and sent[1][1][0][0][1] == f"x:{gid}:ok"
    col._masters(_s([(11, 5), (1, 0)], start_in_min=-5), NOW + 13)         # later snapshots of the same match: nothing new
    assert len(sent) == 2 and st.summary()["settled"] == 0 and st.open_games() == {gid}
    col._poll_exec(NOW + 20)
    assert st3.exec_summary(st.db) == {"alerts": 1, "aceptada": 1} and acks and acks[0][4] == "Registrado: aceptada"
    col._poll_exec(NOW + 40)                                               # same update again: not counted twice
    assert st3.exec_summary(st.db)["aceptada"] == 1
    st.settle(gid, 90)                                                     # Under 100.5 wins at 2.15
    assert round(st.summary()["pnl"], 2) == 1.15 and legacy == []          # legacy Masters model stays silent


def test_paused_guard_records_paper_but_does_not_alert(tmp_path):
    from test_masters import NOW, FakeClient

    from vectiq.collector import Collector
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    sent = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    st = PaperLedger(tmp_path / "st3.sqlite", 100.0)
    for i in range(41):                                                    # 41 straight losses: drawdown > 30 %
        _bet(st, 5000 + i, False, int(NOW * 1000) - 5 * 86400_000 + i)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    st3_ledger=st, alerts="st3", notify=lambda m: True,
                    notify_alert=lambda m, b=None, silent=False: sent.append(m) or 1)
    col._masters(_s([(11, 3)], start_in_min=-5), NOW)
    assert any("EN PAUSA" in m for m in sent) and not any("EN VIVO" in m for m in sent)
    assert st.game_stake(383584934) > 0                                    # paper bet still recorded


def test_arm_trigger_thresholds():
    assert st3.arm(_s([(9, 4)])) == (9, 4)
    assert st3.arm(_s([(4, 10)])) == (4, 10)               # works for either player
    assert st3.arm(_s([(9, 5)])) is None                   # lead 4 is below the calibrated 5
    assert st3.arm(_s([(8, 2)])) is None                   # leader below 9 points
    assert st3.arm(_s([(11, 5), (9, 4)])) is None          # only while set 1 is in progress
    assert st3.arm(_s([(9, 4)], league="Masters. China")) is None


def _pre_col(tmp_path, sent, **kw):
    from test_masters import FakeClient

    from vectiq.collector import Collector
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    st = PaperLedger(tmp_path / "st3.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    st3_ledger=st, alerts=kw.pop("alerts", "st3,pre"), notify=lambda m: True,
                    notify_alert=lambda m, b=None, silent=False: sent.append((m, b)) or 7, **kw)
    return col, led, st


def test_prematch_alert_uses_the_same_channel_with_buttons_and_minimum_odds(tmp_path):
    from test_masters import NOW
    sent = []
    col, led, st = _pre_col(tmp_path, sent)
    col._masters(state(), NOW)
    assert len(sent) == 1
    txt, buttons = sent[0]
    assert txt.startswith("🔵") and "PREPARTIDO" in txt and "MENOS DE" in txt and "mín" in txt and "Cuba" in txt and "betwinner2.com" in txt
    assert buttons == telegram.exec_buttons(383584934)
    assert st3.already_alerted(st.db, 383584934, "pre") and led.game_stake(383584934) > 0
    col._masters(state(), NOW + 20)                                         # same match again: still one alert
    assert len(sent) == 1


def test_old_model_live_entries_are_off_by_default(tmp_path):
    from test_masters import NOW
    sent = []
    col, led, st = _pre_col(tmp_path, sent)
    col._masters(state(sets=[(11, 9)], live=True, lines=((95.5, 1.5, 2.6), (99.5, 1.4, 3.0))), NOW)
    assert led.db.execute("select count(*) from bets").fetchone()[0] == 0 and sent == []


def test_pre_and_st3_on_one_match_share_a_2_percent_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(st3, "evidence_frac", lambda db, cap, where="": cap)   # these tests are about the cap, not the evidence
    from test_masters import NOW
    sent = []
    col, led, st = _pre_col(tmp_path, sent)
    col._masters(state(), NOW)                                              # PRE bet (alerts stake at most 0.5 %)
    col._masters(_s([(11, 5)], start_in_min=-5), NOW + 900)                 # then ST3 on the same match
    st3_msg = [m for m, _ in sent if m.startswith("🔴")]
    assert st3_msg and "1.0 %" in st3_msg[0] and "tope" not in st3_msg[0]    # PRE 0.5 % + ST3 1.0 % stay under the 2 % cap


def test_combined_day_loss_counts_both_books(tmp_path):
    from test_masters import NOW
    col, led, st = _pre_col(tmp_path, [])
    for i in range(6):
        _bet(st, 7000 + i, False, int(NOW * 1000))
    assert col._combined_day_loss(NOW) > 0.05


def test_alerts_quote_the_real_bank_not_the_compounded_paper_bank(tmp_path, monkeypatch):
    monkeypatch.setattr(st3, "evidence_frac", lambda db, cap, where="": cap)
    from test_masters import NOW
    sent = []
    col, led, st = _pre_col(tmp_path, sent)
    st.db.execute("UPDATE meta SET v='250' WHERE k='start'")               # paper ledger drifted far from the real $100
    st.db.commit()
    col._masters(state(), NOW)
    col._masters(_s([(11, 5)], start_in_min=-5, gid=111), NOW + 900)
    txt = " ".join(m for m, _ in sent)
    assert "$1.00" in txt and "$2.50" not in txt


def test_alert_head_fits_the_lock_screen_and_names_copy_with_one_tap():
    from vectiq.notify.telegram import _liga, _names
    assert _liga("Masters. Rusia. Mujeres") == "Rusia · Mujeres" and _liga("Masters. Belarús") == "Belarús"
    assert _names("Marina Dobryanskaya vs Veronika Kan") == "<code>Marina Dobryanskaya</code> vs <code>Veronika Kan</code>"
    txt = telegram.format_st3(st3.evaluate(_s([(11, 5)])), 100.0, 0.01, "https://x")
    head = txt.split("\n")[:4]
    assert head[0].startswith("🔴") and "<code>" in head[1] and "MENOS DE" in head[2] and "$1.00" in head[3]
