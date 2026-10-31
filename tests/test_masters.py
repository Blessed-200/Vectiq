import time
from pathlib import Path

import pytest

from vectiq.collector import Collector
from vectiq.feed.schema import MatchState, Quote
from vectiq.notify import telegram
from vectiq.signals import masters
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

NOW = 1_792_600_000.0


def ladder(lines):
    qs = []
    for ln, over, under in lines:
        qs += [Quote(1, "match", 17, "total", "over", ln, over, False, False),
               Quote(1, "match", 17, "total", "under", ln, under, False, False)]
    return qs


def state(sets=(), live=False, start_in_min=30, lines=((109.5, 1.848, 1.848), (104.5, 1.64, 2.208)), league="Masters. Polonia",
          best_of=7, gid=383584934):
    sets = list(sets)
    won = (sum(a > b for a, b in sets), sum(b > a for a, b in sets))
    return MatchState(gid, None, int(NOW + start_in_min * 60), None, int(NOW * 1000), league, 2685678,
                      "Norbert Plachta", "Piotr Stepinski", None, None, best_of, "Evento en curso" if live else "", live,
                      won, len(sets) + 1, sets, None, ladder(lines), "Masters. Poland")


@pytest.fixture(scope="module")
def book():
    return masters.MastersBook.from_seed()


def test_seed_reproduces_the_measured_bias(book):
    assert book.n(0) >= 1500 and len(book.set_pool) > 1000
    p = book.p_under(0, 0, 109.5)            # observed hit rate of the main-line Under: ~0.61
    assert 0.50 < p < 0.62
    assert book.p_under(0, 0, 104.5) < p     # monotone in the line
    assert book.p_under(0, 0, 90.5) < 0.4


def test_book_idempotent_and_retires_oldest():
    b = masters.MastersBook()
    sets = [(11, 5), (11, 6), (11, 7), (11, 8)]
    assert b.add_match(1, sets) and not b.add_match(1, sets) and b.n(0) == 1 and b.n(4) == 1
    assert not b.add_match(2, [(11, 5), (11, 6)])  # unfinished best-of-7 is never registered
    masters.MAX_KEEP, old = 3, masters.MAX_KEEP
    try:
        for i in range(10, 16):
            b.add_match(i, [(11, i - 10), (11, 6), (11, 7), (11, 8)])
        assert b.n(0) == 3 and list(b.order[0]) == sorted(b.rem[0], key=list(b.order[0]).index)
    finally:
        masters.MAX_KEEP = old


def test_url_matches_the_real_site_link():
    s = state()
    assert masters.match_url(s, "https://betwinner2.com") == \
        "https://betwinner2.com/es/line/table-tennis/2685678-masters-poland/383584934-norbert-plachta-piotr-stepinski"
    s.live = True
    assert "/es/live/table-tennis/" in masters.match_url(s, "https://betwinner2.com")


def test_prematch_pick_is_an_under_on_the_best_line(book):
    pick, why = masters.evaluate_masters(state(), book, now_s=NOW)
    assert why == "ok" and pick.signal.side == "under" and pick.k == 0
    assert pick.signal.line == 104.5 and pick.signal.ev >= 0.05
    assert 0 < pick.signal.stake_frac <= 0.02 and pick.min_odds < pick.signal.price
    assert pick.url.endswith("383584934-norbert-plachta-piotr-stepinski")


def test_gates(book):
    assert masters.evaluate_masters(state(league="Masters. China"), book, now_s=NOW)[1] == "not_masters"
    assert masters.evaluate_masters(state(best_of=5), book, now_s=NOW)[1] == "not_masters"
    assert masters.evaluate_masters(state(league="Liga Pro"), book, now_s=NOW)[1] == "not_masters"
    assert masters.evaluate_masters(state(start_in_min=300), book, now_s=NOW)[1] == "no_entry_window"
    assert masters.evaluate_masters(state(start_in_min=0.5), book, now_s=NOW)[1] == "no_entry_window"
    assert masters.evaluate_masters(state(), book, now_s=NOW, size_mult=0.0)[1] == "paused_drawdown"
    assert masters.evaluate_masters(state(), book, now_s=NOW, exposure_left=0.0)[1] == "exposure_cap"
    assert masters.evaluate_masters(state(lines=((109.5, 1.4, 2.9),)), masters.MastersBook(), now_s=NOW)[1] == "book_too_small"


def test_never_bets_the_over(book):
    for ln in (95.5, 100.5, 104.5, 109.5, 112.5):
        st = state(lines=((ln, 3.5, 1.3),))   # absurdly generous Over, bad Under
        pick, _ = masters.evaluate_masters(st, book, now_s=NOW)
        assert pick is None or pick.signal.side == "under"


def test_set_break_stage_uses_points_already_played(book):
    st = state(sets=[(11, 6)], live=True, lines=((95.5, 1.7, 2.1), (99.5, 1.5, 2.6)))
    pick, why = masters.evaluate_masters(st, book, now_s=NOW)
    assert why in ("ok", "no_edge")
    if pick:
        assert pick.k == 1 and pick.signal.sets == [(11, 6)]
    # set 2 is switched off by default evidence (weak edge); an in-play set is never an entry
    assert masters.evaluate_masters(state(sets=[(11, 6), (3, 2)], live=True), book, now_s=NOW)[1] == "no_entry_window"
    assert masters.evaluate_masters(state(sets=[(11, 6), (11, 6)], live=True), book, now_s=NOW)[1] == "no_entry_window"


def test_sprt_switches_off_on_losses_and_back_on_on_wins():
    assert masters.sprt_state([(0.58, 1.9, False)] * 10)[0] == "on"        # patient: a few losses prove nothing
    bad = [(0.58, 1.9, False)] * 40
    assert masters.sprt_state(bad)[0] == "off"
    good = bad + [(0.58, 1.9, True)] * 50
    assert masters.sprt_state(good)[0] == "on"
    assert masters.sprt_state([])[0] == "on"


def test_drawdown_brake():
    assert masters.drawdown_multiplier(0.05) == 1.0
    assert masters.drawdown_multiplier(0.18) == 0.5
    assert masters.drawdown_multiplier(0.30) == 0.25       # paper never fully stops (deadlock on 2026-10-30)
    assert masters.drawdown_multiplier(0.30, floor=0.0) == 0.0


class FakeClient:
    pass


def test_collector_alerts_then_settles_and_learns(tmp_path, book):
    sent = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    notify=lambda m: sent.append(m) or True)
    st = state()
    col._masters(st, NOW)
    assert len(sent) == 1 and "betwinner2.com/es/line/table-tennis/2685678-masters-poland" in sent[0]
    assert "MENOS DE 104.5" in sent[0] and "mínima" in sent[0]
    col._masters(st, NOW + 3)                      # same game, same line: no duplicate alert
    assert len(sent) == 1
    n_before = col.book.n(0)
    fin = state(sets=[(11, 5), (11, 6), (9, 11), (11, 7), (11, 8)], live=True)
    fin.sets_won = (4, 1)
    fin.sets = [(11, 5), (11, 6), (9, 11), (11, 7), (11, 8)]
    fin.sets_won = (4, 1)
    col.last[st.game_id] = fin
    col._finished(st.game_id)
    assert led.summary()["settled"] == 1 and led.settled_series()[0][2] is True     # 90 pts < 104.5
    assert col.book.n(0) == n_before + 1 and col.finals[st.game_id][1] == 90


def test_trading_window_and_handoff(tmp_path, book):
    calls = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    notify=lambda m: True, trade_from_s=NOW + 600, trade_until_s=NOW + 3600,
                    on_trade_start=lambda c: calls.append("in"), on_trade_stop=lambda c: calls.append("out"))
    col._masters(state(), NOW)                    # before the window: passive, nothing opened
    assert led.summary()["settled"] == 0 and not led.open_games()
    col._trade_edges(NOW + 601)
    col._masters(state(), NOW + 601)
    assert calls == ["in"] and led.open_games() == {383584934}
    col._trade_edges(NOW + 3601)
    assert calls == ["in", "out"] and not col.trading(NOW + 3601)


def test_alert_format_has_everything_a_human_needs(book):
    pick, _ = masters.evaluate_masters(state(), book, now_s=NOW)
    txt = telegram.format_masters(pick, 100.0, sprt="on", drawdown=0.03)
    for needle in ("MASTERS UNDER", "MENOS DE 104.5", "mínima", "EV", "Apostar", "ON", "Abrir en Betwinner"):
        assert needle in txt


def test_workflow_files_parse():
    import yaml
    wf = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/collector.yml").read_text())
    assert wf["jobs"]["collect"]["timeout-minutes"] == 355
    assert "actions" in wf["permissions"] and wf["concurrency"]["cancel-in-progress"] is False


def test_vanished_match_is_certain_loss_or_void(tmp_path):
    from vectiq.signals.engine import Signal
    led = PaperLedger(tmp_path / "v.sqlite", 100.0)
    for gid, line in ((1, 95.5), (2, 120.5)):
        led.open_bet(Signal(gid, "A vs B", "Masters. Rusia", [], "under", line, 2.0, 0.6, 0.2, 1.6, 0.02, 100.0, 109.5))
    led.settle_vanished(1, 98)    # 98 points already played > 95.5: the Under can no longer win
    led.settle_vanished(2, 98)    # unknown ending: voided, stake returned
    sm = led.summary()
    assert sm["settled"] == 1 and sm["void"] == 1 and sm["pnl"] == pytest.approx(-2.0) and led.drawdown() > 0
    assert led.bankroll == pytest.approx(98.0)


def test_one_poll_hiccup_does_not_close_the_match(tmp_path):
    from vectiq.feed.schema import parse_feed
    class C:
        def __init__(self): self.items = []
        def live_list(self):
            from vectiq.feed.client import Response
            return Response(True, 200, {"Success": True, "Value": self.items}, 1, 1, int(NOW * 1000))
        def game(self, gid):
            from vectiq.feed.client import Response
            return Response(False, 500, None, 1, 1, int(NOW * 1000))
    import json
    raw = json.loads((Path(__file__).parent / "fixtures" / "get1x2_sample.json").read_text())["Value"][:3]
    c = C(); c.items = raw
    col = Collector(c, TapeWriter(tmp_path / "t"), None)
    col.step(now_s=NOW)
    assert len(col.last) == 3
    c.items = raw[:2]
    col.step(now_s=NOW + 3); col.step(now_s=NOW + 6)
    assert len(col.last) == 3          # missing for 2 polls: still tracked
    col.step(now_s=NOW + 9)
    assert len(col.last) == 2          # 3 polls: declared over


def test_censored_match_is_imputed_not_dropped(tmp_path):
    b = masters.MastersBook.from_seed()
    n0 = b.n(0)
    st = state(sets=[(11, 6), (6, 11), (11, 9), (8, 11), (11, 5), (9, 11), (10, 7)], live=True)
    st.sets_won = (3, 3)                       # deciding set in progress, last seen at 10-7
    t = b.add_censored(st.game_id, st)
    assert b.n(0) == n0 + 1
    so_far = sum(a + c for a, c in st.sets)
    assert t >= so_far + 1                     # the set still has to be finished
    assert b.impute_total(st) == t             # deterministic per game id


def test_collector_settles_vanished_masters_with_imputed_total(tmp_path):
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    notify=lambda m: True)
    col._masters(state(), NOW)
    assert led.open_games()
    ghost = state(sets=[(11, 6), (6, 11), (11, 9), (8, 11), (11, 5), (9, 11), (10, 7)], live=True)
    ghost.sets_won = (3, 3)
    col.last[ghost.game_id] = ghost            # vanished mid deciding set
    col._finished(ghost.game_id)
    assert not led.open_games() and led.summary()["settled"] == 1 and led.summary()["void"] == 0


def test_one_bet_per_match_and_stage(tmp_path):
    sent = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    notify=lambda m: sent.append(m) or True)
    col._masters(state(), NOW)
    col._masters(state(lines=((109.5, 1.848, 1.848), (103.5, 1.6, 2.30), (104.5, 1.64, 2.208))), NOW + 20)  # new line appears
    assert len(sent) == 1 and led.db.execute("select count(*) from bets").fetchone()[0] == 1
    assert led.has_stage(383584934, 0) and not led.has_stage(383584934, 1)
    st1 = state(sets=[(11, 6)], live=True, lines=((95.5, 1.5, 2.6), (99.5, 1.4, 3.0)))
    col._masters(st1, NOW + 900)               # set-1 break is a different stage: allowed (if it has edge)
    assert led.db.execute("select count(*) from bets where game_id=383584934").fetchone()[0] in (1, 2)


def test_prematch_board_gives_hours_of_lead_and_is_archived(tmp_path):
    from vectiq.feed.client import Response
    import json
    raw = json.loads((Path(__file__).parent / "fixtures" / "get1x2_sample.json").read_text())["Value"][0]
    raw = {**raw, "L": "Masters. Polonia", "LE": "Masters. Poland", "LI": 2685678, "S": int(NOW + 100 * 60), "SC": {},
           "MIO": {}, "E": [{"G": 17, "T": 9, "P": 109.5, "C": 1.85}, {"G": 17, "T": 10, "P": 109.5, "C": 1.85},
                            {"G": 17, "T": 9, "P": 104.5, "C": 1.64}, {"G": 17, "T": 10, "P": 104.5, "C": 2.208}]}

    class C:
        def live_list(self): return Response(True, 200, {"Success": True, "Value": []}, 1, 1, int(NOW * 1000))
        def line_list(self): return Response(True, 200, {"Success": True, "Value": [raw]}, 1, 1, int(NOW * 1000))
        def game(self, gid): return Response(False, 500, None, 1, 1, int(NOW * 1000))

    sent = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    tape = TapeWriter(tmp_path / "t")
    col = Collector(C(), tape, None, mledger=led, book=masters.MastersBook.from_seed(), notify=lambda m: sent.append(m) or True)
    col.step(now_s=NOW)
    tape.close()
    alerts = [m for m in sent if "resumen diario" not in m]
    assert len(alerts) == 1 and "empieza en 100 min" in alerts[0]
    assert led.db.execute("select lead_min from bets").fetchone()[0] == pytest.approx(100.0)
    from vectiq.store.tape import iter_tape
    assert any(r["k"] == "line" for r in iter_tape(tmp_path / "t"))
    assert not col.last                  # board matches never enter the live tracker


def test_orphan_open_bets_are_voided_and_free_the_cap(tmp_path):
    from vectiq.feed.client import Response
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    led.db.execute("INSERT INTO bets(ts_ms,game_id,players,league,sets,side,line,price,p_model,ev,stake,bankroll_before,lead_min) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (int((NOW - 5 * 3600) * 1000), 1, "a vs b", "Masters", "", "under", 105.5, 2.0, .5, .1, 1.0, 100.0, 10.0))
    led.db.commit()
    assert led.open_stake == 1.0

    class C:
        def live_list(self): return Response(True, 200, {"Success": True, "Value": []}, 1, 1, int(NOW * 1000))
        def game(self, gid): return Response(False, 500, None, 1, 1, int(NOW * 1000))

    col = Collector(C(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(), notify=lambda m: True)
    col.step(now_s=NOW)
    assert led.open_stake == 0.0
    assert led.db.execute("select result from bets").fetchone()[0] == "V"


def test_push_and_settlement_source(tmp_path):
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    ins = ("INSERT INTO bets(ts_ms,game_id,players,league,sets,side,line,price,p_model,ev,stake,bankroll_before) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
    led.db.execute(ins, (1, 1, "a", "Masters", "", "under", 105.0, 2.0, .5, .1, 1.0, 100.0))
    led.db.execute(ins, (1, 2, "b", "Masters", "", "under", 105.5, 2.0, .5, .1, 1.0, 100.0))
    led.db.commit()
    led.settle(1, 105)                      # exact hit on an integer line: push, stake back
    led.settle(2, 100, src="imputed")
    rows = {r[0]: r[1:] for r in led.db.execute("select game_id,result,pnl,src from bets")}
    assert rows[1] == ("P", 0.0, "observed")
    assert rows[2] == ("W", 1.0, "imputed")
    assert led.summary()["settled"] == 1    # pushes are not counted as settled W/L


def test_parse_results_rejects_inconsistent_scores():
    from vectiq.feed.results import parse_results, parse_score
    assert parse_score("3:1 (11:6,9:11,11:5,11:8)") == [(11, 6), (9, 11), (11, 5), (11, 8)]
    assert parse_score("3:1 (11:6,9:11,11:5)") is None            # set count != games won
    assert parse_score("3:0 (11:6,9:11,11:5)") is None            # a set lost is not a set won
    assert parse_score(None) is None and parse_score("") is None
    body = {"count": 2, "items": [{"id": 7, "score": "3:0 (11:6,11:5,11:9)"}, {"id": 8, "score": "bad"}]}
    assert parse_results(body) == {7: [(11, 6), (11, 5), (11, 9)]}
    assert parse_results({"count": 3, "items": body["items"]}) == {}   # count mismatch: reject the whole body
    assert parse_results({"count": 0}) == {}


def _ghost():
    g = state(sets=[(11, 6), (6, 11), (11, 9), (8, 11), (11, 5), (9, 11), (10, 7)], live=True)
    g.sets_won = (3, 3)
    g.league_id, g.start_ts = 2685660, int(NOW) - 3000
    return g


def _with_results(rows, tmp_path):
    from vectiq.feed.client import Response

    class C(FakeClient):
        calls = 0
        def results(self, champ, d0, d1):
            C.calls += 1
            return Response(True, 200, {"count": len(rows), "items": rows}, 1, 1, int(NOW * 1000))
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    col = Collector(C(), TapeWriter(tmp_path / "t"), None, mledger=led, book=masters.MastersBook.from_seed(),
                    notify=lambda m: True)
    col._masters(state(), NOW)
    return col, led, C


def test_vanished_match_is_settled_from_the_results_service(tmp_path):
    g = _ghost()
    rows = [{"id": g.game_id, "score": "4:3 (11:6,6:11,11:9,8:11,11:5,9:11,11:7)"}]
    col, led, C = _with_results(rows, tmp_path)
    col.last[g.game_id] = g
    col._finished(g.game_id)
    assert not led.open_games() and not col.pending and C.calls == 1
    src, tot = led.db.execute("select src, final_total from bets").fetchone()
    assert src == "results_api" and tot == 11 + 6 + 6 + 11 + 11 + 9 + 8 + 11 + 11 + 5 + 9 + 11 + 11 + 7


def test_pending_result_retries_then_falls_back_to_imputed(tmp_path, monkeypatch):
    g = _ghost()
    col, led, C = _with_results([], tmp_path)          # service has nothing yet
    col.last[g.game_id] = g
    col._finished(g.game_id)
    assert g.game_id in col.pending and led.open_games()          # bets stay open, nothing invented
    col._poll_pending(NOW)
    assert g.game_id in col.pending                                # not due yet: no extra request
    col.pending[g.game_id].update(next=0.0, deadline=0.0)          # deadline reached
    col._res_cache.clear()
    col._poll_pending(NOW)
    assert not col.pending and not led.open_games()
    assert led.db.execute("select src from bets").fetchone()[0] == "imputed"


def test_drawdown_brake_does_not_starve_the_book_with_a_minimum_stake(tmp_path):
    """Regression: bankroll $98 x 1 % x 0.5 brake = $0.49 was rejected by a $0.50 floor, silencing every alert."""
    from vectiq.signals.engine import Signal
    led = PaperLedger(tmp_path / "m.sqlite", 98.16)
    sig = Signal(game_id=9, players="a v b", league="Masters. Rusia", sets=[], side="under", line=107.5, price=2.0,
                 p_model=0.55, ev=0.1, fair_odds=1.8, stake_frac=0.005, model_total=105.0, market_median=108.0)
    assert led.open_bet(sig, int(NOW * 1000)) and led.open_stake == 0.49


def _cand_state(sets=((11, 8), (7, 6)), lines=((99.5, 1.84, 1.84), (95.5, 1.55, 2.3)), **kw):
    return state(sets=sets, live=True, lines=lines, best_of=7, **kw)


def test_frozen_candidate_matches_the_manus_spec():
    from vectiq.signals.candidate import evaluate_candidate as ev
    sig = ev(_cand_state())                                   # points 19+13=32, line 99.5 -> floor(6.75)=6
    assert sig and sig.side == "under" and sig.line == 99.5 and sig.price == 1.84
    assert ev(_cand_state(sets=((11, 8), (4, 5)))) is None    # PB12: only 9 points in the set in progress
    assert ev(_cand_state(sets=((11, 8),))) is None           # no set in progress
    assert ev(_cand_state(sets=((11, 8), (11, 5), (6, 7)))) is None   # two sets completed
    assert ev(_cand_state(lines=((109.5, 1.84, 1.84),))) is None      # budget 7, not 6
    assert ev(_cand_state(league="Masters. China")) is None
    assert ev(state(sets=[(11, 8), (7, 6)], live=True, best_of=5, lines=((99.5, 1.84, 1.84),))) is None
    # picks the most balanced line (min |over-under|), not the cheapest Under
    sig2 = ev(_cand_state(lines=((99.5, 1.80, 1.90), (95.5, 1.88, 1.89), (101.5, 1.70, 2.1))))
    assert sig2 is None or sig2.line == 95.5


def test_candidate_is_recorded_once_per_game_and_never_notified(tmp_path):
    sent = []
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    cand = PaperLedger(tmp_path / "c.sqlite", 1000.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, cand=cand,
                    book=masters.MastersBook.from_seed(), notify=lambda m: sent.append(m) or True)
    col._masters(_cand_state(), NOW)
    col._masters(_cand_state(sets=((11, 8), (8, 7))), NOW + 5)      # later snapshot of the same game
    assert cand.db.execute("select count(*) from bets").fetchone()[0] == 1 and cand.open_stake == 1.0
    assert not any("99.5" in m for m in sent)
    col._settle_final(383584934, _cand_state(), [(11, 8), (7, 11), (11, 5), (11, 9), (11, 7)], "results_api")
    assert cand.db.execute("select result, src from bets").fetchone() == ("W", "results_api")


def test_candidate_bet_settles_when_the_final_is_observed_in_the_feed(tmp_path):
    led = PaperLedger(tmp_path / "m.sqlite", 100.0)
    cand = PaperLedger(tmp_path / "c.sqlite", 1000.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=led, cand=cand,
                    book=masters.MastersBook.from_seed(), notify=lambda m: True)
    col._masters(_cand_state(), NOW)
    assert cand.open_games()
    fin = _cand_state(sets=((11, 8), (7, 11), (11, 5), (11, 9), (11, 7)))
    fin.sets_won = (3, 2) if False else (4, 1)
    fin.sets = [(11, 8), (7, 11), (11, 5), (11, 9), (11, 7)]
    fin.sets_won = (4, 1)
    col.last[fin.game_id] = fin
    col._finished(fin.game_id)
    assert not cand.open_games() and cand.db.execute("select result from bets").fetchone()[0] == "W"


def test_stale_bets_in_every_ledger_are_settled_from_the_results_service(tmp_path):
    from vectiq.feed.client import Response
    from vectiq.signals.engine import Signal

    class C(FakeClient):
        def results(self, champ, d0, d1):
            rows = [{"id": 9, "score": "4:1 (11:8,7:11,11:5,11:9,11:7)"}]
            return Response(True, 200, {"count": 1, "items": rows}, 1, 1, int(NOW * 1000))
    cand = PaperLedger(tmp_path / "c.sqlite", 1000.0)
    sig = Signal(9, "a v b", "Masters. Rusia", [], "under", 99.5, 1.84, 0.55, 0.1, 1.8, 0.001, 90.0, 99.5)
    assert cand.open_bet(sig, int((NOW - 4 * 3600) * 1000), champ_id=2685660, start_ts=int(NOW - 5 * 3600))
    legacy = Signal(10, "c v d", "Masters. Rusia", [], "under", 99.5, 1.84, 0.55, 0.1, 1.8, 0.001, 90.0, 99.5)
    assert cand.open_bet(legacy, int((NOW - 4 * 3600) * 1000))          # old row: no champ/start stored
    col = Collector(C(), TapeWriter(tmp_path / "t"), None, cand=cand, notify=lambda m: True)
    col._sweep_orphans(NOW)
    rows = dict(cand.db.execute("select game_id, result||':'||src from bets").fetchall())
    assert rows[9] == "W:results_api" and rows[10] == "V:void" and not cand.open_games()


def test_dashboard_counts_only_real_results_and_renders(tmp_path):
    from vectiq.notify import dashboard
    from vectiq.signals.engine import Signal
    led = PaperLedger(tmp_path / "l.sqlite", 100.0)
    for g, tot in ((1, 90), (2, 120), (3, 90)):
        sg = Signal(g, "a v b", "Masters. Rusia", [], "under", 100.5, 2.0, 0.55, 0.1, 1.8, 0.01, 90.0, 100.5)
        led.open_bet(sg, 1_792_000_000_000 + g)
        led.settle(g, tot)
    sg = Signal(4, "a v b", "Masters. Rusia", [], "under", 100.5, 2.0, 0.55, 0.1, 1.8, 0.01, 90.0, 100.5)
    led.open_bet(sg, 1_792_000_000_010)                                   # still open: must not count
    page, books = dashboard.build({"X": tmp_path / "l.sqlite", "Missing": tmp_path / "none.sqlite"}, now_s=1_792_100_000.0)
    b = books[0]
    assert len(books) == 1 and b["n"] == 3 and b["open"] == 1 and abs(b["roi"] - 1 / 3) < 1e-9
    assert "<svg" in page and "VECTIQ" in page and "X" in dashboard.caption(books)
