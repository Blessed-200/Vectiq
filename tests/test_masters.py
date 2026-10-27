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
    assert masters.drawdown_multiplier(0.30) == 0.0


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
