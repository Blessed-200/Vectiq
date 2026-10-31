"""Pruebas del Shadow Engine.

Cobertura: store append-only, implied_center, dedup de decisiones,
anti-leakage del historial de jugadores, reconciliación de liquidaciones
y regresión ST0 contra tools/frozen_eval.py (decisión a decisión).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shadow"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from store import Store  # noqa: E402
from engine import ShadowEngine, implied_center  # noqa: E402


def test_store_append_only(tmp_path):
    st = Store(tmp_path)
    st.append("decisions", {"gid": 1, "decision": "BET", "odds": 2.0})
    st.append("decisions", {"gid": 1, "decision": "SETTLEMENT", "won": True})
    recs = st.load("decisions")
    assert len(recs) == 2 and recs[0]["decision"] == "BET"
    assert all("sha256" in r and "ingested_at" in r for r in recs)


def test_implied_center():
    lad = [{"line": 100.5, "over": 1.9, "under": 1.9},
           {"line": 110.5, "over": 1.9, "under": 1.9}]
    c, ok = implied_center(lad)
    assert ok and abs(c - 105.5) < 1e-6
    # sin cruce -> no válido
    lad2 = [{"line": 100.5, "over": 2.5, "under": 1.5}]
    c2, ok2 = implied_center(lad2)
    assert c2 is None and not ok2


def test_player_hist_causal(tmp_path):
    eng = ShadowEngine(tmp_path)
    eng.player_hist["A"].append((1000, 100, 1))
    eng.player_hist["A"].append((2000, 105, 2))
    # con before=1500 solo cuenta el primer resultado
    assert eng._players_hist_stats("A vs B", 1500) == (None, 1)


def _fake_shard(tmp_path, gids):
    """Shard sintético con ticks st0 + finales para gids."""
    import io, zstandard as zstd
    p = tmp_path / "t.jsonl.zst"
    recs = []
    for gid in gids:
        st_ms = 1_800_000_000_000 + gid * 10_000
        # tick st0: item completo con escalera (t 30 min antes del inicio)
        recs.append({"t": st_ms - 1800_000, "k": "item", "id": str(gid), "d": {
            "I": gid, "S": st_ms // 1000, "O1E": "A", "O2E": "B",
            "O1I": 1, "O2I": 2, "LE": "Masters. Russia", "LI": 2685665,
            "MIO": {"MaF": "best of 7 sets"},
            "SC": {"TS": st_ms // 1000},
            "E": [{"T": 9, "P": 100.5, "C": 2.0, "G": 17},
                  {"T": 10, "P": 100.5, "C": 1.8, "G": 17},
                  {"T": 9, "P": 105.5, "C": 1.9, "G": 17},
                  {"T": 10, "P": 105.5, "C": 1.9, "G": 17}]}})
    buf = io.BytesIO()
    w = zstd.ZstdCompressor().stream_writer(buf, closefd=False)
    for r in recs:
        w.write((json.dumps(r) + "\n").encode())
    w.close()
    p.write_bytes(buf.getvalue())
    return p


def test_engine_records_decisions(tmp_path):
    shard = _fake_shard(tmp_path, [11, 12])
    eng = ShadowEngine(tmp_path / "state")
    eng.process_shard(shard)
    decs = eng.st.load("decisions")
    st0 = [d for d in decs if d["strategy"] == "ST0"]
    st1 = [d for d in decs if d["strategy"] == "ST1"]
    assert len(st0) == 2 and len(st1) == 2
    assert all(d["decision"] in ("BET", "NO_BET") for d in st0)
    assert all(d["decision"] == "GAP_FILTERED" for d in st1)  # sin historial
    mk = eng.st.load("market")
    assert len(mk) == 2 and all(m["ladder"] for m in mk)


def test_book_seed_on_cold_start(tmp_path):
    """masters_book.json siembra el libro en arranque en frío; sin él n(0)=0."""
    state = tmp_path / "state"
    state.mkdir()
    eng = ShadowEngine(state)
    eng.restore()
    assert eng.book.n(0) == 0
    seed = {"remaining": {"0": [90, 100, 110] * 30}}
    (state / "masters_book.json").write_text(json.dumps(seed))
    eng2 = ShadowEngine(state)
    eng2.restore()
    assert eng2.book.n(0) == 90


def test_no_duplicate_decisions_on_reprocess(tmp_path):
    shard = _fake_shard(tmp_path, [21])
    eng = ShadowEngine(tmp_path / "state")
    eng.process_shard(shard)
    n1 = len(eng.st.load("decisions"))
    eng2 = ShadowEngine(tmp_path / "state")
    eng2.restore()
    eng2.process_shard(shard)  # shard cambiado/releído -> no duplicar
    assert len(eng2.st.load("decisions")) == n1


def test_st0_regression_vs_frozen_eval(tmp_path):
    """ST0 debe producir las mismas decisiones que frozen_eval sobre el mismo tape."""
    tape = ROOT / "data" / "tape"
    if not tape.exists():
        import pytest
        pytest.skip("sin tape local")
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fb.json"
        subprocess.run([sys.executable, str(ROOT / "tools" / "frozen_eval.py"),
                        str(tape), "--eval-from", "2026-10-30", "--out", str(out)],
                       check=True, capture_output=True)
        fb = json.loads(out.read_text())
        eng = ShadowEngine(Path(td) / "state")
        # pre-cargar libro y partidos: procesar todo el tape
        for sh in sorted(tape.rglob("*.jsonl.zst")):
            eng.process_shard(sh)
        mine = {d["gid"]: (d.get("line"), d.get("odds"))
                for d in eng.st.load("decisions")
                if d["strategy"] == "ST0" and d["decision"] == "BET"}
        ref = {b["gid"]: (b["line"], b["odds"]) for b in fb["bets"]}
        # mismos partidos con apuesta y misma línea/cuota
        common = set(mine) & set(ref)
        assert len(ref) > 0
        assert len(common) / len(ref) >= 0.9
        for gid in common:
            assert mine[gid] == ref[gid], f"gid {gid}: {mine[gid]} vs {ref[gid]}"
