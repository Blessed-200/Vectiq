"""Extracción en vivo: por cada partido en juego, muestrear ticks (1 por
bucket de ~45s) con escaleras de total (G17), ind1 (G15), ind2 (G62),
hándicap (G2) y estado real (puntos jugados por jugador, sets).
Al finalizar el partido, anexa resultado real a todos sus ticks.

Uso: python tools/cross_live_extract.py data/tape --out /tmp/cross_live.json
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

BUCKET_S = 45


def iter_tape(root: Path):
    for p in sorted(root.rglob("*.jsonl.zst")):
        yield from read_shard(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    games: dict[int, dict] = {}
    out: list[dict] = []
    by_gid: dict[int, list[int]] = {}
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
        g = games.setdefault(gid, {"done": False, "league": None, "bo": None, "last_bucket": -1})
        g["bo"] = state.best_of or g["bo"]
        g["league"] = state.league_en or state.league or g["league"]
        need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
        if need is None:
            continue

        done_sets = state.completed_sets
        finished = max(state.sets_won) >= need and state.in_play_set is None and len(done_sets) == len(state.sets)
        if finished:
            g["done"] = True
            x1 = sum(a for a, b in done_sets)
            x2 = sum(b for a, b in done_sets)
            for i in by_gid.get(gid, ()):
                out[i].update({"fx1": x1, "fx2": x2, "ftotal": x1 + x2, "fdiff": x1 - x2})
            continue
        if g["done"] or not state.live:
            continue
        bucket = int(rec["t"] / (BUCKET_S * 1000))
        if bucket == g["last_bucket"]:
            continue

        # puntos jugados por jugador hasta ahora
        x1p = sum(a for a, b in done_sets)
        x2p = sum(b for a, b in done_sets)
        if state.in_play_set:
            x1p += state.in_play_set[0]
            x2p += state.in_play_set[1]
        played = x1p + x2p
        if played < 8:
            continue

        tot: dict[float, dict] = {}
        i1: dict[float, dict] = {}
        i2: dict[float, dict] = {}
        hcp: dict[float, dict] = {}
        for q in state.quotes:
            if q.blocked or q.price is None or q.line is None:
                continue
            if q.market == "total":
                tot.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "team_total_home":
                i1.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "team_total_away":
                i2.setdefault(q.line, {})[q.selection] = q.price
            elif q.market == "handicap":
                hcp.setdefault(q.line, {})[q.selection] = q.price
        if not (tot and i1 and i2):
            continue
        g["last_bucket"] = bucket
        by_gid.setdefault(gid, []).append(len(out))
        out.append({
            "gid": gid, "t": rec["t"],
            "day": time.strftime("%Y-%m-%d", time.gmtime(rec["t"] / 1000)),
            "league": g["league"], "bo": g["bo"],
            "x1p": x1p, "x2p": x2p, "played": played,
            "nsets": len(done_sets), "sw": list(state.sets_won),
            "tot": [{"l": ln, **s} for ln, s in sorted(tot.items())],
            "i1": [{"l": ln, **s} for ln, s in sorted(i1.items())],
            "i2": [{"l": ln, **s} for ln, s in sorted(i2.items())],
            "hcp": [{"l": ln, **s} for ln, s in sorted(hcp.items())] or None,
            "fx1": None, "fx2": None, "ftotal": None, "fdiff": None,
        })

    args.out.write_text(json.dumps(out))
    fin = sum(1 for w in out if w["ftotal"] is not None)
    print(f"{len(out)} ticks ({len(games)} partidos) en {time.time()-t0:.0f}s -> {args.out}")
    print(f"  ticks con resultado: {fin}")


if __name__ == "__main__":
    main()
