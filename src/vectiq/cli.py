"""Command line entry point: ``python -m vectiq <command>``."""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from vectiq.collector import Collector
from vectiq.feed.client import BetwinnerClient
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

DEFAULT_BASES = ["https://betwinner2.com"]


def _client(args: argparse.Namespace) -> BetwinnerClient:
    bases = [b for b in (os.environ.get("BW_BASE_URLS") or ",".join(DEFAULT_BASES)).split(",") if b]
    return BetwinnerClient(bases, max_rps=args.max_rps, proxy=os.environ.get("BW_PROXY_URL") or None)


def cmd_probe(args: argparse.Namespace) -> int:
    """Check reachability from this network: status, latency, size, match count."""
    c = _client(args)
    r = c.live_list()
    out = {"ok": r.ok, "status": r.status, "latency_ms": round(r.latency_ms), "bytes": r.size, "error": r.error}
    if r.ok and r.payload:
        vals = r.payload.get("Value") or []
        out["matches"] = len(vals)
        out["live"] = sum(1 for v in vals if "curso" in str((v.get("SC") or {}).get("SLS", "")).lower())
        if vals:
            g = c.game(int(vals[0]["I"]))
            out["detail"] = {"ok": g.ok, "status": g.status, "bytes": g.size, "latency_ms": round(g.latency_ms)}
    print(json.dumps(out, indent=2))
    return 0 if r.ok else 2


def cmd_collect(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tape = TapeWriter(Path(args.out) / "tape")
    ledger = PaperLedger(Path(args.out) / "paper.sqlite", start_bankroll=args.bankroll)
    Collector(_client(args), tape, ledger).run(args.minutes * 60, args.interval)
    print(json.dumps(ledger.summary()))
    return 0


def cmd_tables(args: argparse.Namespace) -> int:
    from vectiq.store import tables
    print(json.dumps(tables.build(Path(args.tape), Path(args.out))))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Upload recently written shards as release assets (one tag per shard's UTC day).

    Shards touched in the last 3 h are uploaded regardless of today's date, so the last
    minutes before midnight are never stranded. Asset names carry the run id: a new
    runner can never overwrite what a previous runner already published.
    """
    import shutil
    import tempfile

    run = os.environ.get("GITHUB_RUN_ID", "local")
    created: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for p in sorted(Path(args.tape).rglob("*.jsonl.zst")):
            if time.time() - p.stat().st_mtime > 3 * 3600:
                continue
            y, m, d = p.parent.parts[-3:]
            tag = f"tape-{y}-{m}-{d}"
            if tag not in created:
                subprocess.run(["gh", "release", "create", tag, "--title", tag, "--notes", "raw tape shards",
                                "--latest=false"], check=False, capture_output=True)
                created.add(tag)
            asset = Path(tmp) / f"{y}-{m}-{d}_{p.name[:2]}_r{run}.jsonl.zst"
            shutil.copyfile(p, asset)
            subprocess.run(["gh", "release", "upload", tag, str(asset), "--clobber"], check=False)
    ledger = Path(args.tape).parent / "paper.sqlite"
    if ledger.exists():  # persistent paper-trading state across ephemeral runners
        subprocess.run(["gh", "release", "create", "ledger", "--title", "ledger", "--notes", "paper ledger", "--latest=false"],
                       check=False, capture_output=True)
        subprocess.run(["gh", "release", "upload", "ledger", str(ledger), "--clobber"], check=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vectiq")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("probe", cmd_probe), ("collect", cmd_collect)):
        sp = sub.add_parser(name)
        sp.add_argument("--max-rps", type=float, default=4.0)
        if name == "collect":
            sp.add_argument("--minutes", type=float, default=345)
            sp.add_argument("--interval", type=float, default=3.0)
            sp.add_argument("--out", default="data")
            sp.add_argument("--bankroll", type=float, default=100.0)
        sp.set_defaults(fn=fn)
    t = sub.add_parser("tables"); t.add_argument("--tape", default="data/tape"); t.add_argument("--out", default="data/tables"); t.set_defaults(fn=cmd_tables)
    u = sub.add_parser("publish"); u.add_argument("--tape", default="data/tape"); u.set_defaults(fn=cmd_publish)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
