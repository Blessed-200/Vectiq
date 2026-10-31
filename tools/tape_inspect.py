"""Lector autónomo del raw tape (.jsonl.zst) + mapeo de la estructura JSON.

Uso:
    python tools/tape_inspect.py data/tape [--max-files N] [--out report.json]

Cada shard es una concatenación de frames zstd; cada frame es un lote de
líneas JSON ``{"t": ms_epoch, "k": "item"|"game", "id": game_id, "d": raw}``.
Se descomprime en memoria (stream, sin tocar disco) y se parsea línea a línea.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

import zstandard as zstd


def read_shard(path: Path) -> Iterator[dict[str, Any]]:
    """Descomprime un .zst multi-frame en memoria y cede un dict por línea JSON."""
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh, dctx.stream_reader(fh, read_across_frames=True) as rd:
        data = rd.read()
    for line in data.splitlines():
        if line:
            yield json.loads(line)


def iter_tape(root: Path, max_files: int | None = None) -> Iterator[dict[str, Any]]:
    files = sorted(root.rglob("*.jsonl.zst"))
    if max_files:
        files = files[:max_files]
    for p in files:
        yield from read_shard(p)


def keypaths(obj: Any, prefix: str = "", depth: int = 0) -> Iterator[str]:
    """Rutas de claves anidadas. Listas se marcan []. Profundidad tope 3."""
    if depth > 3 or not isinstance(obj, dict):
        return
    for k, v in obj.items():
        p = f"{prefix}.{k}" if prefix else k
        yield p
        if isinstance(v, dict):
            yield from keypaths(v, p, depth + 1)
        elif isinstance(v, list) and v:
            if isinstance(v[0], dict):
                yield from keypaths(v[0], p + "[]", depth + 1)
            else:
                yield p + "[]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--max-files", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    rec_count = 0
    kinds = Counter()
    games: dict[int, dict] = {}
    item_keys, game_keys = Counter(), Counter()
    # (G, T) -> stats de precio
    market_ctr: Counter = Counter()
    price_min: dict = defaultdict(lambda: float("inf"))
    price_max: dict = defaultdict(float)
    lines_seen: dict = defaultdict(set)
    first_ts = last_ts = None

    t0 = time.time()
    for rec in iter_tape(args.root, args.max_files):
        rec_count += 1
        k = rec["k"]
        kinds[k] += 1
        ts = rec["t"]
        first_ts = ts if first_ts is None else min(first_ts, ts)
        last_ts = ts if last_ts is None else max(last_ts, ts)
        gid = rec["id"]
        games.setdefault(gid, {"item": 0, "game": 0})[k] = \
            games.get(gid, {"item": 0, "game": 0}).get(k, 0) + 1
        d = rec["d"]
        census = item_keys if k == "item" else game_keys
        census.update(keypaths(d))
        # Mercados: E[] en item; en game también GE[].E[][] y SG[].GE[].E[][]
        def qscan(entries, src):
            for e in entries or []:
                key = (src, e.get("G"), e.get("T"))
                market_ctr[key] += 1
                c = e.get("C")
                if isinstance(c, (int, float)):
                    price_min[key] = min(price_min[key], c)
                    price_max[key] = max(price_max[key], c)
                if e.get("P") is not None:
                    lines_seen[key].add(e["P"])
        if k == "item":
            qscan(d.get("E"), "E")
            for blk in d.get("AE") or []:
                qscan(blk.get("ME"), "AE.ME")
        else:
            for ge in d.get("GE") or []:
                for row in ge.get("E") or []:
                    qscan(row, "GE.E")
            for sg in d.get("SG") or []:
                for ge in sg.get("GE") or []:
                    for row in ge.get("E") or []:
                        qscan(row, f"SG[{sg.get('PN') or sg.get('N')}].GE.E")

    report = {
        "records": rec_count,
        "kinds": dict(kinds),
        "unique_games": len(games),
        "span_utc": [time.strftime("%Y-%m-%d %H:%M", time.gmtime(first_ts / 1000)),
                     time.strftime("%Y-%m-%d %H:%M", time.gmtime(last_ts / 1000))],
        "elapsed_s": round(time.time() - t0, 1),
        "item_keypaths": item_keys.most_common(80),
        "game_keypaths": game_keys.most_common(120),
        "markets": [
            {"src": s, "G": g, "T": t, "n": n,
             "price_range": [price_min[(s, g, t)], price_max[(s, g, t)]],
             "lines": sorted(lines_seen[(s, g, t)])[:20]}
            for (s, g, t), n in market_ctr.most_common()
        ],
    }
    out = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(out)
    print(out)


if __name__ == "__main__":
    main()
