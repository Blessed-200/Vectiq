"""Extractor para vías 4/6/7/8: por cada partido Masters bo7, volcar la serie
temporal PREPARTIDO completa de la escalera G17 (línea -> (over,under)) en cada
tick con cambios, más metadatos y el total final.

Salida: {gid, day, hour_utc, league, players, start_ts, final_total|None,
         series: [[t_ms, {line: [over,under]}]]}   (solo ticks con cambio)
Uso: python tools/prematch_extract.py data/tape --out /tmp/prematch.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from backtest import read_shard  # noqa: E402
from vectiq.feed.schema import parse_item, _quotes  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    games: dict[int, dict] = {}
    n_rec = 0
    t0 = time.time()
    for p in sorted(args.root.rglob("*.jsonl.zst")):
        for rec in read_shard(p):
            n_rec += 1
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
            g = games.setdefault(gid, {"bo": None, "league": None, "finished": False,
                                       "series": [], "last_sig": None, "start_ts": None})
            if g["finished"]:
                continue
            g["bo"] = state.best_of or g["bo"]
            g["league"] = state.league_en or state.league or g["league"]
            g["start_ts"] = state.start_ts or g["start_ts"]
            if state.home:
                g["players"] = f"{state.home} vs {state.away}"
            need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
            done = state.completed_sets
            if need and max(state.sets_won) >= need and state.in_play_set is None and len(done) == len(state.sets):
                g["finished"] = True
                g["final_total"] = sum(a + b for a, b in done)
                continue
            if need != 4 or "masters" not in (g["league"] or "").lower():
                continue
            # solo ticks prepartido
            if state.live or state.sets or not state.start_ts or rec["t"] >= state.start_ts * 1000:
                continue
            lad = {}
            for q in state.quotes:
                if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
                    sides = lad.setdefault(q.line, {})
                    sides[q.selection] = q.price
            lad = {ln: [s["over"], s["under"]] for ln, s in lad.items() if "over" in s and "under" in s}
            if not lad:
                continue
            sig = json.dumps(lad, sort_keys=True)
            if sig != g["last_sig"]:
                g["series"].append([rec["t"], lad])
                g["last_sig"] = sig

    out = {}
    for gid, g in games.items():
        if g["bo"] != 7 or "masters" not in (g["league"] or "").lower():
            continue
        if not g["series"]:
            continue
        day = time.strftime("%Y-%m-%d %H", time.gmtime((g["series"][0][0]) / 1000))
        out[str(gid)] = {
            "day": day.split()[0], "hour_utc": int(day.split()[1]),
            "league": g["league"], "players": g.get("players", ""),
            "start_ts": g["start_ts"], "final_total": g.get("final_total"),
            "series": g["series"],
        }
    Path(args.out).write_text(json.dumps(out))
    print(f"# {n_rec} recs -> {len(out)} partidos masters con serie prepartido ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
