"""Individual shadow (signals/sombra.py): rules fire on the right rung, one bet per match and rule, settle, ratings, isolation."""
import dataclasses
import sqlite3

import pytest
from test_masters import NOW, FakeClient, state

from vectiq.collector import Collector
from vectiq.signals import masters, sombra
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

LAD = ((100.5, 1.62, 2.25), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))


def ru(**kw):
    s = state(league="Masters. Rusia", **kw)
    return dataclasses.replace(s, league_en="Masters. Russia")


def rated(sb, names=("Norbert Plachta", "Piotr Stepinski"), swr=-200.0):
    for n in names:
        sb.players[n] = [10.0, swr, NOW * 1000]            # ps = swr / (sw + 3) = -15.4 (a "low scorer")
    return sb


def test_r4_fires_on_the_lowest_rung_before_the_match():
    sb = rated(sombra.Sombra(":memory:", seed=None))
    opened = sb.observe(ru(lines=LAD), trading=True)
    got = {b["rule"]: b for b in opened}
    assert "R4" in got and got["R4"]["side"] == "under" and got["R4"]["line"] == 100.5 and got["R4"]["price"] == 2.25
    assert all(b["side"] in ("under", "over") for b in opened)      # some rules (R3, R5, R7, R10, R12, R13, R37) also bet Over, as in the research table


def test_not_russia_or_not_a_low_scorer_does_not_fire_r4():
    assert "R4" not in {b["rule"] for b in rated(sombra.Sombra(":memory:", seed=None)).observe(state(lines=LAD), trading=True)}
    assert sombra.Sombra(":memory:", seed=None).observe(ru(lines=LAD), trading=True) == []      # unknown players: rating 0


def test_one_bet_per_match_rule_and_stage():
    sb = rated(sombra.Sombra(":memory:", seed=None))
    first = sb.observe(ru(lines=LAD), trading=True)
    again = sb.observe(ru(lines=((100.5, 1.62, 2.40), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65))), trading=True)
    assert first and again == []
    n = sb.db.execute("SELECT COUNT(*) FROM bets WHERE rule='R4'").fetchone()[0]
    assert n == 1


def test_settlement_win_loss_push_and_void():
    sb = rated(sombra.Sombra(":memory:", seed=None))
    sb.observe(ru(lines=LAD), trading=True)
    assert sb.open_games() == {383584934}
    assert sb.settle(383584934, 99) >= 1
    res, pnl = sb.db.execute("SELECT result,pnl FROM bets WHERE rule='R4'").fetchone()
    assert res == "W" and abs(pnl - 1.25) < 1e-9
    sb2 = rated(sombra.Sombra(":memory:", seed=None))
    sb2.observe(ru(lines=LAD), trading=True)
    sb2.settle(383584934, 110)
    assert sb2.db.execute("SELECT result,pnl FROM bets WHERE rule='R4'").fetchone() == ("L", -1.0)
    sb3 = rated(sombra.Sombra(":memory:", seed=None))
    sb3.observe(ru(lines=LAD), trading=True)
    sb3.settle_vanished(383584934, 40)
    assert sb3.db.execute("SELECT result FROM bets WHERE rule='R4'").fetchone() == ("V",)


def test_final_updates_the_rating_once_and_only_after_the_first_snapshot():
    sb = sombra.Sombra(":memory:", seed=None)
    sb.observe(ru(lines=LAD), trading=True)
    assert sb.read("Norbert Plachta", NOW * 1000) == (0.0, 0.0)
    sb.final(383584934, 80, trading=True)
    sb.final(383584934, 80, trading=True)                      # idempotent
    m, n = sb.read("Norbert Plachta", sb.games[383584934]["last_ms"])
    assert n == pytest.approx(1.0) and m < 0                   # played well under its ladder: negative residual
    assert sb.players["Norbert Plachta"][0] == pytest.approx(1.0)


def test_disabled_is_a_noop():
    sb = rated(sombra.Sombra(":memory:", enabled=False, seed=None))
    assert sb.observe(ru(lines=LAD), trading=True) == []
    assert sb.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 0


def test_passive_run_remembers_the_match_but_never_bets():
    sb = rated(sombra.Sombra(":memory:", seed=None))
    assert sb.observe(ru(lines=LAD), trading=False) == []
    assert sb.games[383584934]["centre"] is not None and sb.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 0


def test_state_survives_a_restart(tmp_path):
    p = tmp_path / "sombra.sqlite"
    a = sombra.Sombra(p, seed=None)
    a.observe(ru(lines=LAD), trading=True)
    a.final(383584934, 80, trading=True)
    a.flush(force=True)
    b = sombra.Sombra(p, seed=None)
    assert "Norbert Plachta" in b.players and b.games[383584934]["rated"]


def test_seed_loads_ratings_once(tmp_path):
    a = sombra.Sombra(tmp_path / "s.sqlite")
    assert len(a.players) > 500
    n = len(a.players)
    a.db.commit()
    b = sombra.Sombra(tmp_path / "s.sqlite")
    assert len(b.players) == n


def test_every_rule_atom_references_a_known_feature():
    ok = {"hour", "ps_min", "ps_n", "s1_margin", "league", "country", "rem_line", "sl_margin", "since_start", "k", "sl_tot", "pdev", "sp_up", "tt_diff"}
    for name, atoms in sombra.RULES.items():
        for f, op, v in atoms:
            base = f if not f.startswith("d:") else None
            if base:
                assert base in ok, (name, f)
            else:
                for part in f[2:].replace("-", " ").replace("*", " ").replace("/", " ").split():
                    assert part in {"pdev", "mk_edge", "mk_p", "mk_ev", "price_opp"}, (name, f)


class Boom:
    def observe(self, *a, **k):
        raise RuntimeError("boom")

    def flush(self, *a, **k):
        raise RuntimeError("boom")

    def final(self, *a, **k):
        raise RuntimeError("boom")

    def open_games(self):
        raise RuntimeError("boom")

    def settle(self, *a, **k):
        raise RuntimeError("boom")

    def settle_vanished(self, *a, **k):
        raise RuntimeError("boom")

    def stale_open_meta(self, *a, **k):
        raise RuntimeError("boom")


def _run_st0(tmp_path, sb, tag):
    sent = []
    d = tmp_path / tag
    d.mkdir()
    st3l, st0l = PaperLedger(d / "st3.sqlite", 100.0), PaperLedger(d / "st0.sqlite", 100.0)
    col = Collector(FakeClient(), TapeWriter(d / "t"), None, mledger=PaperLedger(d / "m.sqlite", 100.0), book=masters.MastersBook.from_seed(),
                    st3_ledger=st3l, st0_ledger=st0l, alerts="st0", notify=lambda m: True,
                    notify_alert=lambda m, b=None, silent=False: sent.append(m) or 7, alert_chat="1", sombra=sb)
    s = state(lines=((100.5, 1.62, 2.25), (103.5, 1.85, 1.95), (106.5, 2.20, 1.65)))
    col._masters(s, NOW)
    col._sombra_obs(s, "item")
    col._masters(s, NOW + 5)
    rows = sqlite3.connect(d / "st0.sqlite").execute("SELECT game_id,side,line,price,stake FROM bets ORDER BY id").fetchall()
    return sent, rows


def test_production_is_identical_with_shadow_on_off_or_broken(tmp_path):
    base_sent, base_rows = _run_st0(tmp_path, None, "off")
    on_sent, on_rows = _run_st0(tmp_path, sombra.Sombra(":memory:", seed=None), "on")
    bad_sent, bad_rows = _run_st0(tmp_path, Boom(), "bad")
    assert base_sent and base_rows
    assert on_sent == base_sent == bad_sent and on_rows == base_rows == bad_rows


def test_the_shadow_never_sends_anything(tmp_path):
    sent, _ = _run_st0(tmp_path, rated(sombra.Sombra(":memory:", seed=None)), "x")
    assert all("R4" not in m and "SOMBRA" not in m.upper() for m in sent)
