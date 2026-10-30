"""Panel cuantitativo diario ST0 vs ST1 — lee el store append-only.

Uso: python -m shadow.report --state shadow/state [--day YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Store  # noqa: E402


def devig(over, under):
    return (1 / under) / ((1 / under) + (1 / over))


def metrics(state: Path, day: str | None):
    st = Store(state)
    decs = st.load("decisions")
    bets = {s: {} for s in ("ST0", "ST1")}
    settled = {s: {} for s in ("ST0", "ST1")}
    for d in decs:
        gid = str(d.get("gid"))
        if day:
            t = d.get("event_time_ms") or d.get("decision_at_ms") or 0
            if time.strftime("%Y-%m-%d", time.gmtime(t / 1000)) != day:
                continue
        if d.get("decision") == "BET":
            bets[d["strategy"]][gid] = d
        elif d.get("decision") == "SETTLEMENT":
            settled[d["strategy"]][gid] = d
    out = {}
    for s in ("ST0", "ST1"):
        rows = []
        for gid, b in bets[s].items():
            stl = settled[s].get(gid)
            if stl is None:
                continue
            won = stl["won"]
            pnl = stl["pnl_u"]
            close = stl.get("close_odds")
            clv = (b["odds"] / close - 1) if close else None
            rows.append({"won": won, "pnl": pnl, "odds": b["odds"],
                         "p": b.get("p"), "clv": clv, "league": b.get("league")})
        n = len(rows)
        w = sum(r["won"] for r in rows)
        eq = 0.0; pk = 0.0; mdd = 0.0; streak = mx = 0
        for r in rows:
            eq += r["pnl"]; pk = max(pk, eq); mdd = max(mdd, pk - eq)
            streak = streak + 1 if r["pnl"] < 0 else 0; mx = max(mx, streak)
        clvs = [r["clv"] for r in rows if r["clv"] is not None]
        ps = [(r["p"], r["won"]) for r in rows if r.get("p") is not None]
        brier = sum((p - wn) ** 2 for p, wn in ps) / len(ps) if ps else None
        ll = -sum(wn * math.log(max(p, 1e-9)) + (1 - wn) * math.log(max(1 - p, 1e-9))
                  for p, wn in ps) / len(ps) if ps else None
        out[s] = {
            "bets": len(bets[s]), "settled": n, "pending": len(bets[s]) - n,
            "wins": w, "losses": n - w, "wr": w / n if n else None,
            "roi_u": sum(r["pnl"] for r in rows) / n if n else None,
            "pnl_u": sum(r["pnl"] for r in rows),
            "odds_mean": sum(r["odds"] for r in rows) / n if n else None,
            "clv_mean": sum(clvs) / len(clvs) if clvs else None,
            "clv_beat": sum(c > 0 for c in clvs) / len(clvs) if clvs else None,
            "brier": brier, "logloss": ll,
            "mdd_u": mdd, "max_loss_streak": mx,
        }
    both = set(bets["ST0"]) & set(bets["ST1"])
    out["paired"] = {"both_bet": len(both),
                     "only_ST0": len(set(bets["ST0"]) - set(bets["ST1"])),
                     "only_ST1": len(set(bets["ST1"]) - set(bets["ST0"]))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--day", default=None)
    args = ap.parse_args()
    print(json.dumps(metrics(args.state, args.day), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
