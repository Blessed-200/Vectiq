#!/usr/bin/env python3
"""Panel data: ONLY what is alerted on Telegram (the definitive set of 19-21 Nov), applied to the whole history.

ST3: Under lowest rung after set 1 with margin >= 5 and price >= 2.10   (st3.sqlite)
ST5: Markov Under with 0.15 < EV <= 0.30, at most 2 sets played         (st5.sqlite)
ST0: pre-match Under on the lowest rung (rule inside the bot)           (st0.sqlite)
Writes site/data/panel.json: one compact row per alert, the page does all the maths in the browser.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

ST3_MIN_PRICE = float(os.environ.get("VECTIQ_ST3_ALERT_MIN_PRICE", "2.10"))
ST5_EV_MIN, ST5_EV_MAX = float(os.environ.get("VECTIQ_ST5_ALERT_EV_MIN", "0.15")), float(os.environ.get("VECTIQ_ST5_ALERT_EV_MAX", "0.30"))


def rows(db: Path, leg: int) -> list[list]:
    con = sqlite3.connect(db)
    out = []
    for ts, gid, players, league, line, price, ev, sets, stake, pnl, result in con.execute(
            "SELECT ts_ms, game_id, players, league, line, price, ev, sets, stake, pnl, result FROM bets"):
        k = (sets or "").count("-")
        if leg == 0 and price < ST3_MIN_PRICE - 1e-9:
            continue
        if leg == 1 and not (ST5_EV_MIN < (ev or 0) <= ST5_EV_MAX + 1e-9 and k <= 2):
            continue
        u = round(pnl / stake, 4) if result in ("W", "L") and stake else None
        out.append([int(ts), leg, u, round(price, 2), line, gid, players, (league or "").replace("Masters. ", "")])
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    assets, out = Path(a.assets), Path(a.out)
    bets = sorted(rows(assets / "st3.sqlite", 0) + rows(assets / "st5.sqlite", 1)
                  + (rows(assets / "st0.sqlite", 2) if (assets / "st0.sqlite").exists() else []), key=lambda r: r[0])
    out.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 1, "generated_ms": int(time.time() * 1000), "cuba_utc": float(os.environ.get("VECTIQ_CUBA_UTC", "-4")),
           "day_start_hour": 6, "stake_pct": 0.01, "stake_pcts": [0.01, 0.01, 0.0045], "legs": ["ST3", "ST5", "ST0"],
           "filters": {"st3_min_price": ST3_MIN_PRICE, "st5_ev": [ST5_EV_MIN, ST5_EV_MAX]},
           "cols": ["ts_ms", "leg", "u", "price", "line", "game_id", "players", "league"], "bets": bets}
    (out / "panel.json").write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print(f"panel.json: {len(bets)} alertas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
