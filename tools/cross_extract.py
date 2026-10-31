"""Extracción multi-mercado: para cada partido finalizado del tape (todas
las ligas), volcar en la ventana st0 elegible las escaleras de:
  total (G17), team_total_home (G15), team_total_away (G62), handicap (G2), winner (G1)
y los resultados reales: puntos jugador1 (X1), jugador2 (X2), total, diff, ganador.

Uso: python tools/cross_extract.py data/tape --out /tmp/cross.json [--bo 7]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vectiq.feed.schema import parse_item, _quotes  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from backtest import read_shard  # noqa: E402


def iter_tape(root: Path):
    for p in sorted(root.rglob("*.jsonl.zst")):
        yield from read_shard(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bo", type=int, default=0, help="0 = todos, 5 o 7")
    args = ap.parse_args()

    games: dict[int, dict] = {}
    out: list[dict] = []
    t0 = time.time()

    for rec in iter_tape(args.root):
        try:
            d = rec["d"]
            state = parse_item(d, rec["t"])
            if rec["k"] == "game":
                for ge in d.get("GE") or []:
                    for row in ge.get("E") or []:
                        state.quotes.extend(_quotes(state.game_id, "match", row))
        except Exception:
            continue
        gid = state.game_id
        g = games.setdefault(gid, {"done": False, "league": None, "bo": None, "window": None})
        g["bo"] = state.best_of or g["bo"]
        g["league"] = state.league_en or state.league or g["league"]
        need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
        if need is None or (args.bo and g["bo"] != args.bo):
            continue

        done = state.completed_sets
        if max(state.sets_won) >= need and state.in_play_set is None and len(done) == len(state.sets):
            g["done"] = True
            x1 = sum(a for a, b in done)
            x2 = sum(b for a, b in done)
            w = "home" if x1 > x2 else "away"
            for w_ in out:
                if w_["gid"] == gid:
                    w_.update({"x1": x1, "x2": x2, "total": x1 + x2,
                               "diff": x1 - x2, "winner": w, "n_sets": len(done)})
            # partido sin ventana capturada: registrar solo resultado
            if g["window"] is None:
                out.append({"gid": gid, "t": rec["t"],
                            "day": time.strftime("%Y-%m-%d", time.gmtime(rec["t"] / 1000)),
                            "league": g["league"], "bo": g["bo"],
                            "total_l": None, "i1_l": None, "i2_l": None,
                            "hcp_l": None, "w_l": None,
                            "x1": x1, "x2": x2, "total": x1 + x2,
                            "diff": x1 - x2, "winner": w, "n_sets": len(done)})
            continue
        if g["done"] or g["window"] is not None:
            continue
        # ventana st0: no live, sin sets, 60s..4h antes del inicio
        now_s = rec["t"] / 1000.0
        if state.live or state.sets or not state.start_ts:
            continue
        if not (60 <= state.start_ts - now_s <= 4 * 3600):
            continue

        tot: dict[float, dict] = {}
        i1: dict[float, dict] = {}
        i2: dict[float, dict] = {}
        hcp: dict[float, dict] = {}
        win: dict[str, float] = {}
        for q in state.quotes:
            if q.blocked or q.price is None:
                continue
            if q.market == "total" and q.line is not None:
                tot.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "team_total_home" and q.line is not None:
                i1.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "team_total_away" and q.line is not None:
                i2.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "handicap" and q.line is not None:
                hcp.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "winner":
                win[q.selection] = q.price
        if not (tot or i1 or i2 or hcp or win):
            continue
        g["window"] = True
        out.append({
            "gid": gid, "t": rec["t"],
            "day": time.strftime("%Y-%m-%d", time.gmtime(rec["t"] / 1000)),
            "league": g["league"], "bo": g["bo"],
            "total_l": [{"line": ln, **s} for ln, s in sorted(tot.items())] or None,
            "i1_l": [{"line": ln, **s} for ln, s in sorted(i1.items())] or None,
            "i2_l": [{"line": ln, **s} for ln, s in sorted(i2.items())] or None,
            "hcp_l": [{"line": ln, **s} for ln, s in sorted(hcp.items())] or None,
            "w_l": win or None,
            "x1": None, "x2": None, "total": None, "diff": None,
            "winner": None, "n_sets": None,
        })

    args.out.write_text(json.dumps(out))
    print(f"{len(out)} partidos en {time.time()-t0:.0f}s -> {args.out}")
    with_win = sum(1 for w in out if w["total"] is not None)
    with_lad = sum(1 for w in out if w["total_l"] or w["i1_l"] or w["hcp_l"])
    print(f"  con resultado: {with_win}   con escalera: {with_lad}")


if __name__ == "__main__":
    main()
