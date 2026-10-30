"""Extracción del mecanismo: para cada partido bo7 Masters finalizado,
volcar la escalera G17 completa (todas las líneas over/under con cuotas)
en cada ventana de entrada elegible (st0 pre-match y cada descanso k>=1),
junto con el total final real.

Salida: JSON lista de ventanas:
 {gid, day, league, stage, played (puntos jugados), sets_won, final_total,
  ladder: [{line, over, under}], market_probs}
Uso: python tools/mech_extract.py data/tape --test-from 2026-10-27 --out /tmp/mech.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vectiq.feed.schema import parse_item, _quotes, set_finished  # noqa: E402
from backtest import read_shard  # noqa: E402


def iter_tape(root: Path):
    for p in sorted(root.rglob("*.jsonl.zst")):
        yield from read_shard(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--test-from", default="2026-10-27")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--masters-only", action="store_true", default=True)
    args = ap.parse_args()
    test_from_ms = int(time.mktime(time.strptime(args.test_from, "%Y-%m-%d")) * 1000 - time.timezone * 1000)

    games: dict[int, dict] = {}
    out: list[dict] = []
    n_rec = 0
    t0 = time.time()

    for rec in iter_tape(args.root):
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
        g = games.setdefault(gid, {"done_stages": set(), "league": None, "bo": None, "finished": False})
        if g["finished"]:
            continue
        g["bo"] = state.best_of or g["bo"]
        g["league"] = state.league_en or state.league or g["league"]
        need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
        if need != 4:
            continue
        if "masters" not in (g["league"] or "").lower():
            continue

        done = state.completed_sets
        # ---- final
        if max(state.sets_won) >= need and state.in_play_set is None and len(done) == len(state.sets):
            total = sum(a + b for a, b in done)
            g["finished"] = True
            g["final_total"] = total
            # retro-rellena total final en ventanas registradas
            for w in out:
                if w["gid"] == gid:
                    w["final_total"] = total
                    w["final_sets"] = done
            continue
        if rec["t"] < test_from_ms or g["finished"]:
            continue

        now_s = rec["t"] / 1000.0
        stage = None
        if not state.live and not state.sets and state.start_ts and 60 <= state.start_ts - now_s <= 4 * 3600:
            stage = 0
        elif state.live and done and state.in_play_set is None and len(done) == len(state.sets) and max(state.sets_won) < need:
            stage = len(done)
        if stage is None or stage in g["done_stages"]:
            continue
        g["done_stages"].add(stage)

        # escalera completa en este tick
        by_line: dict[float, dict] = {}
        for q in state.quotes:
            if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
                by_line.setdefault(q.line, {})[q.selection] = q.price
        ladder = [
            {"line": ln, "over": s.get("over"), "under": s.get("under")}
            for ln, s in sorted(by_line.items())
            if s.get("over") and s.get("under")
        ]
        if not ladder:
            continue
        out.append({
            "gid": gid, "t": rec["t"],
            "day": time.strftime("%Y-%m-%d", time.gmtime(rec["t"] / 1000)),
            "league": g["league"], "stage": stage,
            "played": state.points_played, "sets_won": list(state.sets_won),
            "ladder": ladder, "final_total": None,
        })

    json.dump(out, open(args.out, "w"))
    n_fin = sum(1 for w in out if w.get("final_total") is not None)
    print(f"# {n_rec} recs, {len(out)} ventanas, {n_fin} con final ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
