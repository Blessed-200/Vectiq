#!/usr/bin/env python3
"""Individual shadow (sombra.sqlite) -> site/data/sombra.json. Stdlib only. Missing file = honest empty document."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vectiq.signals.sombra_text import DESCR, ORDER, REF  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    db = Path(a.assets) / "sombra.sqlite"
    bets, void = [], 0
    if db.exists():
        con = sqlite3.connect(db)
        for ts, rule, gid, players, league, k, side, line, price, result, stake in con.execute(
                "SELECT ts_ms, rule, game_id, players, league, k, side, line, price, result, stake FROM bets ORDER BY ts_ms"):
            if rule not in ORDER:
                continue
            if result == "V":
                void += 1
                continue
            u = None if result is None else (round(price - 1, 4) if result == "W" else (0.0 if result == "P" else -1.0))
            bets.append([int(ts), ORDER.index(rule), u, round(price, 2), line, side, gid, players, (league or "").replace("Masters. ", ""), k])
        con.close()
    doc = {"schema_version": 1, "generated_ms": int(time.time() * 1000), "cuba_utc": float(os.environ.get("VECTIQ_CUBA_UTC", "-4")), "day_start_hour": 6,
           "rules": [{"id": r, "desc": DESCR.get(r, ""), "ref": REF.get(r)} for r in ORDER], "void": void,
           "cols": ["ts_ms", "rule", "u", "price", "line", "side", "game_id", "players", "league", "k"], "bets": bets}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sombra.json").write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print(f"sombra.json: {len(bets)} apuestas en sombra")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
