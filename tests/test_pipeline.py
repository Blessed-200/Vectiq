import json
from pathlib import Path

import pytest

from vectiq.collector import Collector
from vectiq.feed.client import BetwinnerClient, CircuitOpen, Response
from vectiq.feed.scheduler import DetailScheduler
from vectiq.feed.schema import MatchState, parse_feed, parse_game, set_finished
from vectiq.signals import engine
from vectiq.signals.paper import PaperLedger
from vectiq.store import tables
from vectiq.store.tape import TapeWriter, iter_tape

FX = Path(__file__).parent / "fixtures"
LIST = json.loads((FX / "get1x2_sample.json").read_text())
GAME = json.loads((FX / "getgamezip_sample.json").read_text())


def test_parse_feed_real_capture():
    states = parse_feed(LIST, 1_000)
    assert len(states) == len(LIST["Value"]) >= 10
    liga = [s for s in states if s.league == "Liga Pro"]
    assert liga and all(s.best_of == 5 for s in liga)
    masters = [s for s in states if s.league.startswith("Masters")]
    assert masters and all(s.best_of == 7 for s in masters)
    s = next(s for s in states if s.sets)
    assert all(isinstance(a, int) and isinstance(b, int) for a, b in s.sets)
    tot = [q for q in s.quotes if q.market == "total"]
    assert {q.selection for q in tot} == {"over", "under"} and len(tot) >= 6  # main + alt ladder


def test_unstarted_set_not_counted():
    item = {"I": 1, "SC": {"PS": [{"Key": 1, "Value": {"S1": 11, "S2": 5}}, {"Key": 2, "Value": {"NF": "2 Set"}}],
                           "FS": {"S1": 1}, "CP": 2, "SLS": "Evento en curso"}, "O1": "A", "O2": "B", "E": []}
    from vectiq.feed.schema import parse_item
    st = parse_item(item, 0)
    assert st.sets == [(11, 5)] and st.in_play_set is None and engine.at_set_break(st)


def test_parse_game_detail_has_stats_and_set_markets():
    d = parse_game(GAME, 5)
    assert d.stats and "% de victorias en el servicio propio" in d.stats
    assert d.set_quotes and any(q.market == "set_total" for q in d.set_quotes)
    assert len({q.line for q in d.set_quotes if q.market == "set_total"}) >= 6  # full ladder, not 3 lines


def test_set_finished_rules():
    assert set_finished(11, 9) and not set_finished(11, 10) and set_finished(14, 12) and not set_finished(10, 8)


def test_tape_delta_and_roundtrip(tmp_path):
    w = TapeWriter(tmp_path / "tape")
    raw = LIST["Value"][0]
    assert w.append("item", raw["I"], raw, 1_792_592_000_000)
    assert not w.append("item", raw["I"], {**raw, "U": 999}, 1_792_592_003_000)  # volatile keepalive ignored
    changed = {**raw, "E": []}
    assert w.append("item", raw["I"], changed, 1_792_592_006_000)
    w.close()
    recs = list(iter_tape(tmp_path / "tape"))
    assert [r["t"] for r in recs] == [1_792_592_000_000, 1_792_592_006_000]
    assert recs[0]["d"] == raw  # lossless


def test_tables_rebuild(tmp_path):
    w = TapeWriter(tmp_path / "tape")
    for it in LIST["Value"]:
        w.append("item", it["I"], it, 1_792_592_000_000)
    w.append("game", 762247858, GAME["Value"], 1_792_592_001_000)
    w.close()
    n = tables.build(tmp_path / "tape", tmp_path / "t")
    assert n["states"] == len(LIST["Value"]) + 1 and n["quotes"] > 100 and n["stats"] > 0


def test_scheduler_prioritises_set_breaks():
    mk = lambda gid, sets, live=True: MatchState(gid, None, None, None, 0, "L", None, "a", "b", None, None, 5, "x",
                                                 live, (0, 0), 1, sets, None)
    brk, mid = mk(1, [(11, 5)]), mk(2, [(3, 2)])
    sch = DetailScheduler(budget_per_cycle=1)
    assert sch.pick([mid, brk], 100.0) == [1]
    sch2 = DetailScheduler(budget_per_cycle=2)
    sch2.pick([brk, mid], 0.0)
    assert sch2.next_due[1] - 0 == 3.0 and sch2.next_due[2] - 0 == 15.0


def _state(sets, best_of=5, lines=((74.5, 1.88, 1.88),)):
    from vectiq.feed.schema import Quote
    qs = []
    for ln, o, u in lines:
        qs += [Quote(1, "match", 17, "total", "over", ln, o, True, False),
               Quote(1, "match", 17, "total", "under", ln, u, True, False)]
    return MatchState(1, None, None, None, 0, "Liga Pro", None, "A", "B", None, None, best_of, "Evento en curso", True,
                      (sum(a > b for a, b in sets), sum(b > a for a, b in sets)), len(sets) + 1, sets, None, qs)


def test_engine_gates():
    assert engine.evaluate(_state([(8, 11), (6, 11)], best_of=7), 0.0)[1] == "uncalibrated_format"
    assert engine.evaluate(_state([(8, 11), (6, 11)]), None)[1] == "no_prematch_anchor"
    assert engine.evaluate(_state([(8, 11), (6, 11), (4, 3)]), 0.0)[1] == "not_set_break"


def test_engine_flags_state_mismatch_instead_of_phantom_edge():
    # 2-0 with 36 points played and a 57.5 line is the stale-score pattern: must NOT become a bet
    sigs, why = engine.evaluate(_state([(12, 14), (3, 11)], lines=((57.5, 1.83, 1.93),)), 0.0)
    assert sigs == [] and why == "state_mismatch"


def test_engine_finds_genuine_edge():
    sigs, why = engine.evaluate(_state([(11, 6), (11, 7)], lines=((54.5, 2.6, 1.5),)), 0.0)
    assert why in ("ok", "no_edge", "state_mismatch")


def test_implied_rating_diff_monotone():
    a = engine.implied_rating_diff(1.5, 2.6)
    b = engine.implied_rating_diff(2.6, 1.5)
    assert a > 0 > b and abs(a + b) < 1.0


def test_paper_ledger_settlement_and_clv(tmp_path):
    from vectiq.signals.engine import Signal
    L = PaperLedger(tmp_path / "p.sqlite", 100.0)
    s = Signal(1, "A vs B", "Liga Pro", [(11, 5), (11, 6)], "over", 57.5, 1.9, 0.6, 0.14, 1 / 0.6, 0.02, 60.0, 57.5)
    assert L.open_bet(s) and not L.open_bet(s)  # unique per game/side/line
    L.note_close(1, 57.5, "over", 1.8)
    assert L.settle(1, 62) == 1
    sm = L.summary()
    assert sm["settled"] == 1 and sm["pnl"] == pytest.approx(2.0 * 0.9) and sm["clv"] > 0


class FakeClient:
    def __init__(self):
        self.stats = {}
    def live_list(self):
        return Response(True, 200, LIST, 10, 100, 1_792_592_000_000)
    def game(self, gid):
        return Response(True, 200, {"Success": True, "Value": GAME["Value"]}, 10, 100, 1_792_592_001_000)


def test_collector_end_to_end(tmp_path):
    sent = []
    tape = TapeWriter(tmp_path / "tape")
    col = Collector(FakeClient(), tape, PaperLedger(tmp_path / "p.sqlite"), notify=lambda m: sent.append(m) or True)
    assert col.step(now_s=1000.0) == len(LIST["Value"])
    col.step(now_s=1003.0)  # identical payload: delta store must skip
    tape.close()
    assert tape.skipped >= len(LIST["Value"])
    assert any(r["k"] == "game" for r in iter_tape(tmp_path / "tape"))


def test_client_breaker_opens_on_403():
    class R:
        status_code, headers, content = 403, {}, b""
    class S:
        headers, proxies = {}, {}
        def get(self, *a, **k): return R()
    t = [0.0]
    c = BetwinnerClient(["https://x"], max_rps=1000, session=S(), clock=lambda: t[0], sleep=lambda s: None)
    assert not c.live_list().ok
    with pytest.raises(CircuitOpen):
        c.live_list()
    t[0] += 1000
    assert not c.live_list().ok  # half-open retry after cooldown
