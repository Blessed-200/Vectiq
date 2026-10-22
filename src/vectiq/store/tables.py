"""Derive columnar tables (Parquet) from the raw tape. Fully reproducible."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from vectiq.feed.schema import parse_game, parse_item
from vectiq.store.tape import iter_tape

STATE_COLS = ["t", "game_id", "league", "home", "away", "best_of", "status", "live", "sets_won_h", "sets_won_a",
              "current_set", "sets", "points_played", "server", "update_ts", "start_ts"]
QUOTE_COLS = ["t", "game_id", "scope", "group", "market", "selection", "line", "price", "main", "blocked"]


def build(tape_root: Path, out_dir: Path) -> dict[str, int]:
    states: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    for rec in iter_tape(tape_root):
        t = rec["t"]
        try:
            if rec["k"] == "item":
                st, qs, extra = parse_item(rec["d"], t), None, []
            elif rec["k"] == "game":
                det = parse_game({"Success": True, "Value": rec["d"]}, t)
                st, extra = det.state, det.set_quotes
                for k, (h, a) in det.stats.items():
                    stats.append({"t": t, "game_id": st.game_id, "stat": k, "home": h, "away": a})
            else:
                continue
        except (KeyError, ValueError):
            continue
        states.append({
            "t": t, "game_id": st.game_id, "league": st.league, "home": st.home, "away": st.away,
            "best_of": st.best_of, "status": st.status, "live": st.live,
            "sets_won_h": st.sets_won[0], "sets_won_a": st.sets_won[1], "current_set": st.current_set,
            "sets": ";".join(f"{a}-{b}" for a, b in st.sets), "points_played": st.points_played,
            "server": st.server, "update_ts": st.update_ts, "start_ts": st.start_ts,
        })
        for q in [*st.quotes, *extra]:
            quotes.append({"t": t, "game_id": q.game_id, "scope": q.scope, "group": q.group, "market": q.market,
                           "selection": q.selection, "line": q.line, "price": q.price, "main": q.main,
                           "blocked": q.blocked})
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows, cols in (("states", states, STATE_COLS), ("quotes", quotes, QUOTE_COLS),
                             ("stats", stats, ["t", "game_id", "stat", "home", "away"])):
        tbl = pa.Table.from_pylist(rows) if rows else pa.table({c: [] for c in cols})
        pq.write_table(tbl, out_dir / f"{name}.parquet", compression="zstd")
    return {"states": len(states), "quotes": len(quotes), "stats": len(stats)}
