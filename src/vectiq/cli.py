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
from vectiq.notify import telegram
from vectiq.signals.masters import MastersBook
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
    if getattr(args, "line", False):
        out["line_feed"] = _probe_line(c)
    print(json.dumps(out, indent=2))
    return 0 if r.ok else 2


def _probe_line(c: BetwinnerClient) -> dict:
    """Does the pre-match board exist, and how far ahead are Masters totals visible?"""
    r = c.line_list()
    res: dict = {"ok": r.ok, "status": r.status, "bytes": r.size, "error": r.error}
    if r.ok and r.payload:
        vals = r.payload.get("Value") or []
        now = time.time()
        masters = [v for v in vals if str(v.get("L", "")).startswith("Masters")]
        ahead = sorted(round(((v.get("S") or now) - now) / 60) for v in masters)
        with_total = sum(1 for v in masters if any(e.get("G") == 17 for e in (v.get("E") or []) + [x for b in (v.get("AE") or []) for x in (b.get("ME") or [])]))
        res.update(matches=len(vals), masters=len(masters), masters_with_total_ladder=with_total,
                   masters_minutes_to_start=ahead[:25])
    return res


STATE_FILES = ("masters.sqlite", "masters_book.json", "cand.sqlite")


def cmd_rescue(args: argparse.Namespace) -> int:
    """Ask the detail endpoint for matches the live list never showed finishing: does it still know the final score?"""
    from vectiq.feed.schema import parse_game
    c = _client(args)
    ids = [int(x) for x in Path(args.ids).read_text().split() if x.strip()]
    out = []
    for gid in ids:
        r = c.game(gid)
        row: dict = {"id": gid, "ok": r.ok, "status": r.status, "bytes": r.size}
        try:
            if r.ok and r.payload and r.payload.get("Value"):
                st = parse_game(r.payload, r.fetched_ms).state
                row.update(sets=[list(x) for x in st.sets], won=list(st.sets_won), live=st.live, sls=st.status, bo=st.best_of)
            elif r.ok:
                row["empty"] = True
        except Exception as e:  # noqa: BLE001
            row["parse_error"] = str(e)[:80]
        out.append(row)
    Path(args.out).write_text(json.dumps(out))
    _release_upload([Path(args.out)])
    print(f"{sum(1 for o in out if o.get('sets') is not None)}/{len(out)} with state")
    return 0


def _release_upload(paths: list[Path]) -> None:
    subprocess.run(["gh", "release", "create", "ledger", "--title", "ledger", "--notes", "paper ledger",
                    "--latest=false"], check=False, capture_output=True)
    for p in paths:
        if p.exists():
            subprocess.run(["gh", "release", "upload", "ledger", str(p), "--clobber"], check=False)


def _handoff_in(col: Collector, out: Path) -> None:
    """Called when this run starts trading: take over the previous run's ledger and distribution book."""
    subprocess.run(["gh", "release", "download", "ledger", "--pattern", "masters.sqlite", "--pattern",
                    "masters_book.json", "--pattern", "cand.sqlite", "--dir", str(out), "--clobber"],
                   check=False, capture_output=True)
    col.mledger.reopen()
    if col.cand is not None:
        col.cand.reopen()
    loaded = MastersBook.load(out / "masters_book.json")
    for gid, (sets, _) in col.finals.items():  # finals this run saw before taking over
        loaded.add_match(gid, sets)
    col.book = loaded
    (out / "trading.flag").write_text("1")
    logging.getLogger("vectiq.cli").info("handoff in: bankroll=%.2f settled=%d", col.mledger.bankroll,
                                         len(col.mledger.settled_series()))


def _handoff_out(col: Collector, out: Path) -> None:
    """Called when this run stops trading: publish the state the next run takes over."""
    col.book.save(out / "masters_book.json")
    _release_upload([out / n for n in STATE_FILES])
    (out / "trading.flag").unlink(missing_ok=True)


def cmd_collect(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = Path(args.out)
    tape = TapeWriter(out / "tape")
    ledger = PaperLedger(out / "paper.sqlite", start_bankroll=args.bankroll)
    shadow = PaperLedger(out / "shadow.sqlite", start_bankroll=args.bankroll)
    cand = PaperLedger(out / "cand.sqlite", start_bankroll=1000.0)      # frozen offline candidate, flat ~$1, silent
    mledger = PaperLedger(out / "masters.sqlite", start_bankroll=args.bankroll)
    book = MastersBook.load(out / "masters_book.json")
    t0 = time.time()
    chat = telegram.shadow_chat()
    col = Collector(
        _client(args), tape, ledger, shadow=shadow, mledger=mledger, book=book, cand=cand,
        notify=lambda m: telegram.send(m, chat_id=chat), book_path=out / "masters_book.json",
        notify_doc=lambda path, cap: telegram.send_document(path, cap, chat_id=chat), dashboard_dir=out,
        trade_from_s=t0 + args.trade_from_min * 60 if args.trade_from_min else None,
        trade_until_s=t0 + args.trade_until_min * 60 if args.trade_until_min else None,
        on_trade_start=(lambda c: _handoff_in(c, out)) if args.trade_from_min else None,
        on_trade_stop=lambda c: _handoff_out(c, out))
    if not args.trade_from_min:
        (out / "trading.flag").write_text("1")  # chained runs get the flag in _handoff_in
    col.run(args.minutes * 60, args.interval)
    col.book.save(out / "masters_book.json")
    print(json.dumps({"alerted": ledger.summary(), "shadow": shadow.summary(), "masters": mledger.summary()}))
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
    base = Path(args.tape).parent
    if (base / "trading.flag").exists():  # only the run that currently owns the state may publish it
        _release_upload([base / n for n in ("paper.sqlite", "shadow.sqlite") + STATE_FILES])
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vectiq")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("probe", cmd_probe), ("collect", cmd_collect)):
        sp = sub.add_parser(name)
        sp.add_argument("--max-rps", type=float, default=4.0)
        if name == "probe":
            sp.add_argument("--line", action="store_true", help="also test the pre-match board")
        if name == "collect":
            sp.add_argument("--minutes", type=float, default=345)
            sp.add_argument("--interval", type=float, default=3.0)
            sp.add_argument("--out", default="data")
            sp.add_argument("--bankroll", type=float, default=100.0)
            sp.add_argument("--trade-from-min", type=float, default=0.0,
                            help="chained runs wait this long (passive capture) before taking over trading")
            sp.add_argument("--trade-until-min", type=float, default=335.0)
        sp.set_defaults(fn=fn)
    rs = sub.add_parser("rescue"); rs.add_argument("--ids", default="tools/rescue_ids.txt"); rs.add_argument("--out", default="rescue.json")
    rs.add_argument("--max-rps", type=float, default=3.0); rs.set_defaults(fn=cmd_rescue)
    t = sub.add_parser("tables"); t.add_argument("--tape", default="data/tape"); t.add_argument("--out", default="data/tables"); t.set_defaults(fn=cmd_tables)
    u = sub.add_parser("publish"); u.add_argument("--tape", default="data/tape"); u.set_defaults(fn=cmd_publish)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
