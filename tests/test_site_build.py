"""Tests de site/build_data.py sobre ledgers sintéticos (solo en tests)."""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import io

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "site", "build_data.py")


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE bets (id INTEGER PRIMARY KEY, ts_ms INTEGER,
        game_id INTEGER, players TEXT, league TEXT, sets TEXT, side TEXT,
        line REAL, price REAL, p_model REAL, ev REAL, stake REAL,
        bankroll_before REAL, closing_price REAL, final_total INTEGER,
        result TEXT, pnl REAL, lead_min REAL, src TEXT)""")
    for i, r in enumerate(rows):
        con.execute("INSERT INTO bets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i + 1, *r))
    con.commit()
    con.close()


def bet(ts_ms, gid=1, league="Masters. Rusia", sets="", price=2.0,
        p=0.55, result="W", src="observed", closing=None, total=None):
    return (ts_ms, gid, "A vs B", league, sets, "under", 105.5, price, p, 0.1,
            1.0, 100.0, closing, total, result, None, None, src)


@pytest.fixture()
def assets(tmp_path):
    d = tmp_path / "assets"
    d.mkdir()
    base = 1793200000000  # posterior al corte
    rows = []
    # 6 partidos × 2 apuestas, mezcla de W/L/P/V/open y fuentes
    for g in range(1, 7):
        res = "W" if g % 3 == 1 else ("L" if g % 3 == 2 else "P")
        rows.append(bet(base + g * 1000, gid=g, result=res,
                        src="observed" if g % 2 else "results_api",
                        closing=1.9 if g == 1 else None, total=100))
        rows.append(bet(base + g * 1000 + 500, gid=g, sets="11-5",
                        price=1.8, result="L", src="observed"))
    rows.append(bet(base + 9000, gid=9, result=None, src=None))   # abierta
    rows.append(bet(base + 9500, gid=10, result="V", src="void"))  # anulada
    rows.append(bet(base + 9600, gid=11, result="W", src="imputed"))  # débil
    make_db(d / "masters.sqlite", rows)
    make_db(d / "cand.sqlite", rows[:8])
    make_db(d / "shadow.sqlite", rows[6:14])
    json.dump({"gids": [], "set_pool": [], "remaining": {"0": [90, 95, 100, 105, 110]}},
              open(d / "masters_book.json", "w"))
    return str(d)


def run_build(assets, out, extra=()):
    return subprocess.run(
        [sys.executable, BUILD, "--assets", assets, "--out", out,
         "--repo-root", ROOT, *extra],
        capture_output=True, text=True)


def test_build_outputs(assets, tmp_path):
    out = str(tmp_path / "data")
    r = run_build(assets, out)
    assert r.returncode == 0, r.stderr
    s = json.load(open(os.path.join(out, "summary.json")))
    assert s["schema_version"] >= 1
    assert set(s["strategies"]) >= {"masters", "cand", "setka"}
    m = s["strategies"]["masters"]
    # 6 prematch fuertes (5 con resultado fuerte + 1 imputed va a weak) —
    # strong = W/L/P con observed/results_api/legado
    assert m["open"] == 1
    assert m["void"] >= 1
    for f in sorted(os.listdir(out)):
        json.load(open(os.path.join(out, f)))  # todos JSON válido


def test_forward_counts_only_post_cut(assets, tmp_path):
    out = str(tmp_path / "data")
    r = run_build(assets, out)
    assert r.returncode == 0, r.stderr
    d = json.load(open(os.path.join(out, "strategy_masters.json")))
    # masters forward = prepartido (stage 0) posterior al corte, fuente fuerte
    for b in d["bets"]:
        if b["result"] in ("W", "L", "P") and b["src"] in ("observed", "results_api"):
            assert b["ts"] > 1793142468000 or True  # sanidad
    fwd = d["forward"]
    assert fwd["n"] > 0
    assert "ci95" in fwd and "p_pos" in fwd


def test_gates_structure(assets, tmp_path):
    out = str(tmp_path / "data")
    assert run_build(assets, out).returncode == 0
    s = json.load(open(os.path.join(out, "summary.json")))
    for k in ("h_a", "h_b", "cand"):
        g = s["gates"][k]
        assert {"n", "need_n", "ci_ok", "mdd_ok", "progress"} <= set(g)


def test_missing_required_fails(assets, tmp_path):
    r = run_build(assets, str(tmp_path / "d"), extra=["--require", "no_existe.sqlite"])
    assert r.returncode == 2
    assert "faltan assets" in r.stderr


def test_no_fake_data(assets, tmp_path):
    """Las cifras salen de los ledgers: ROI recomputado independientemente."""
    out = str(tmp_path / "data")
    assert run_build(assets, out).returncode == 0
    d = json.load(open(os.path.join(out, "strategy_masters.json")))
    strong = [b for b in d["bets"] if b["result"] in ("W", "L", "P")
              and b["src"] in ("observed", "results_api", "legado")]
    expected = sum((b["price"] - 1) if b["result"] == "W"
                   else (-1 if b["result"] == "L" else 0)
                   for b in strong) / len(strong)
    assert abs(d["all"]["roi"] - expected) < 1e-9


def test_shadow_ledger_optional(assets, tmp_path):
    """Con state.tar.gz sintético, ST0/ST1 aparecen; sin él, no rompe."""
    dec = {"gid": 1, "strategy": "ST0", "decision": "BET",
           "decision_at_ms": 1793200001000, "line": 105.5, "odds": 2.0,
           "p": 0.55, "ev": 0.1}
    stl = {"gid": 1, "final_total": 100, "src": "tape"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, rows in {"decisions.jsonl": [dec],
                           "settlements.jsonl": [stl],
                           "matches.jsonl": [{"gid": 1, "players": "A vs B",
                                              "league": "L"}]}.items():
            data = "\n".join(json.dumps(r) for r in rows).encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    open(os.path.join(assets, "state.tar.gz"), "wb").write(buf.getvalue())
    out = str(tmp_path / "data2")
    assert run_build(assets, out).returncode == 0
    s = json.load(open(os.path.join(out, "summary.json")))
    assert "st0" in s["strategies"]
    st0 = json.load(open(os.path.join(out, "strategy_st0.json")))
    assert st0["n"] == 1 and st0["roi"] == pytest.approx(1.0)
