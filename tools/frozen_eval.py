"""Validación prospectiva de la estrategia CONGELADA — "Devin B".

Reglas congeladas (post-orden del 2026-10-30):
  * Solo partidos Masters BO7 (league contiene 'masters', best_of==7).
  * Mercado G17 total de puntos, lado UNDER (T10).
  * Solo st0: tick prepartido, 60 s <= t<=4 h antes de start_ts, sin sets jugados.
  * UNA apuesta por partido (la primera ventana st0 elegible).
  * Selección de línea determinista: argmax_EV( EV=p_under*cuota_under-1 ) sobre
    todas las líneas de la escalera disponibles en ese tick; requiere EV>=5 %.
    SIN techo de EV, SIN filtro de línea, SIN calibración.
  * p_under = CDF empírica del libro bo7 (EmpBook(4)), que se actualiza
    causalmente solo cuando un partido finaliza (misma mecánica que el motor A).

Registra TODAS las oportunidades elegibles (incluidas las rechazadas y el motivo).

Uso: python tools/frozen_eval.py data/tape --train-days 8 --eval-from 2026-10-30 --out /tmp/frozenB.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from backtest import read_shard, EmpBook, MIN_EV, MIN_BOOK  # noqa: E402
from vectiq.feed.schema import parse_item, _quotes  # noqa: E402


def iter_tape(root: Path):
    for p in sorted(root.rglob("*.jsonl.zst")):
        yield p, read_shard(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--eval-from", default="2026-10-30")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    eval_ms = int(time.mktime(time.strptime(args.eval_from, "%Y-%m-%d")) * 1000 - time.timezone * 1000)

    book = EmpBook(4)
    games: dict[int, dict] = {}
    opps: list[dict] = []
    bets: list[dict] = []
    n_rec = 0
    t0 = time.time()

    for shard, recs in iter_tape(args.root):
        for rec in recs:
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
                                       "bet_placed": False, "opp_logged": False, "last_price": {}})
            if g["finished"]:
                continue
            g["bo"] = state.best_of or g["bo"]
            g["league"] = state.league_en or state.league or g["league"]

            # CLV tracking: últimas cuotas vistas por (market,sel,line)
            for q in state.quotes:
                if q.scope == "match" and q.market == "total" and not q.blocked:
                    g["last_price"][(q.selection, q.line)] = q.price

            need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
            done = state.completed_sets

            # ---- final observado -> libro causal (train y eval)
            if need and max(state.sets_won) >= need and state.in_play_set is None and len(done) == len(state.sets):
                total = sum(a + b for a, b in done)
                g["finished"] = True
                g["final_total"] = total
                # settle apuestas de este gid
                for b in bets:
                    if b["gid"] == gid and b["won"] is None:
                        b["won"] = total < b["line"]
                        b["close_odds"] = g["last_price"].get(("under", b["line"]))
                book.add(gid, done, total, 0, True)
                continue

            if rec["t"] < eval_ms or need != 4 or "masters" not in (g["league"] or "").lower():
                continue

            now_s = rec["t"] / 1000.0
            is_st0 = (not state.live and not state.sets and state.start_ts
                      and 60 <= state.start_ts - now_s <= 4 * 3600)
            if not is_st0 or g["opp_logged"]:
                continue
            g["opp_logged"] = True   # primera ventana st0 elegible solamente

            # escalera under disponible en este tick
            by_line: dict[float, dict] = {}
            for q in state.quotes:
                if q.scope == "match" and q.market == "total" and q.line is not None and not q.blocked:
                    by_line.setdefault(q.line, {})[q.selection] = q.price

            opp = {"gid": gid, "t": rec["t"], "league": g["league"],
                   "players": f"{state.home} vs {state.away}",
                   "book_n": book.n(0), "ladder": [], "decision": None, "reject": None}
            best = None
            for ln, sides in sorted(by_line.items()):
                u_odds, o_odds = sides.get("under"), sides.get("over")
                if not u_odds or not o_odds:
                    continue
                pu = book.p_under(0, 0, ln)
                ev = pu * u_odds - 1.0 if pu is not None else None
                opp["ladder"].append({"line": ln, "under": u_odds, "over": o_odds,
                                      "p_model": pu, "ev": ev})
                if ev is not None and ev >= MIN_EV and ev > (best["ev"] if best else -9):
                    best = {"line": ln, "odds": u_odds, "p": pu, "ev": ev}
            if book.n(0) < MIN_BOOK:
                opp["reject"] = f"book_insuficiente n={book.n(0)}"
            elif not opp["ladder"]:
                opp["reject"] = "sin_escalera_over_under"
            elif best is None:
                opp["reject"] = "ninguna_linea_EV>=5%"
            if best:
                opp["decision"] = best
                bets.append({"gid": gid, "t": rec["t"], "league": g["league"],
                             "players": opp["players"], "line": best["line"],
                             "odds": best["odds"], "p": best["p"], "ev": best["ev"],
                             "won": None, "close_odds": None, "final_total": None})
                g["bet_placed"] = True
            opps.append(opp)

    # finales vistos en tape -> rellenar bets
    finmap = {gid: g.get("final_total") for gid, g in games.items() if g.get("finished")}
    for b in bets:
        if b["gid"] in finmap and b["final_total"] is None:
            b["final_total"] = finmap[b["gid"]]
            if b["won"] is None:
                b["won"] = b["final_total"] < b["line"]

    out = {"eval_from": args.eval_from, "records": n_rec, "elapsed_s": round(time.time() - t0, 1),
           "book_final_n0": book.n(0), "n_opportunities": len(opps), "n_bets": len(bets),
           "opportunities": opps, "bets": bets}
    args.out.write_text(json.dumps(out, ensure_ascii=False))
    w = [b for b in bets if b["won"] is not None]
    print(f"# opps={len(opps)} bets={len(bets)} settled={len(w)} "
          f"WR={sum(b['won'] for b in w)/max(1,len(w)):.3f} "
          f"ROI={sum((b['odds']-1) if b['won'] else -1 for b in w)/max(1,len(w)):+.3f}")


if __name__ == "__main__":
    main()
