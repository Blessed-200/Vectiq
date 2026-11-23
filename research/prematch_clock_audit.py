"""Discovery-only sensitivity for a truly pre-start, pre-match board median."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from asof import assert_asof
from tape_audit import (
    DAY, HOUR, ROOT, DayScan, _centre, center_bias, load_record, parse_item,
    records, st3, tasks, utc_string, verify_asset
)

# Checked against the original terminal reconstruction and two independent reruns.
TERMINALS_SHA = "d857bc52c8092ca90f848daf7973fe05acdee636d13f7360cdf35d334f524270"
HEADER = re.compile(rb'^\{\s*"t"\s*:\s*(\d+)\s*,\s*"k"\s*:\s*"([^"]+)"\s*,')


@dataclass
class PreScan:
    counts: Counter = field(default_factory=Counter)
    games_seen: set = field(default_factory=set)
    valid_clock_games: set = field(default_factory=set)
    hours: set = field(default_factory=set)
    first: dict = field(default_factory=dict)
    conflicts: set = field(default_factory=set)

    def add_center(self, gid, t, center):
        if gid not in self.first or t < self.first[gid][0]:
            self.first[gid] = (t, center)
        elif t == self.first[gid][0] and center != self.first[gid][1]:
            self.conflicts.add(gid)

    def merge(self, other):
        self.counts.update(other.counts)
        self.games_seen.update(other.games_seen)
        self.valid_clock_games.update(other.valid_clock_games)
        self.hours.update(other.hours)
        self.conflicts.update(other.conflicts)
        for gid, (t, center) in other.first.items():
            self.add_center(gid, t, center)


def examine(scan, data, gid, t):
    if gid != data.get("I"):
        scan.counts["wrapper_game_id_mismatch"] += 1
        return
    lg = str(data.get("L") or "").lower().strip()
    if not lg.startswith("masters") or "china" in lg or "super league" in lg:
        return
    scan.games_seen.add(gid)
    scan.counts["masters_line_records"] += 1
    s = parse_item(data, t)
    if s.best_of != 7:
        scan.counts["unknown_or_non_bo7"] += 1
        return
    if s.live or s.sets_won != (0, 0) or any(a or b for a, b in s.sets):
        scan.counts["already_live_or_nonzero"] += 1
        return
    if type(s.start_ts) not in (float, int) or not math.isfinite(s.start_ts) or s.start_ts <= 0:
        scan.counts["missing_scheduled_start"] += 1
        return
    if t >= s.start_ts * 1000:
        scan.counts["receipt_at_or_after_announced_start"] += 1
        return
    scan.valid_clock_games.add(gid)
    scan.counts["declared_bo7_not_live_zero_strictly_before_start"] += 1
    lad = st3.ladder(s)
    if len(lad) < 2:
        scan.counts["fewer_than_two_complete_unblocked_lines"] += 1
        return
    ct = _centre([(line, (1/u) / (1/u + 1/o)) for line,o,u in lad])
    if ct is None or not math.isfinite(ct):
        scan.counts["no_no_vig_half_probability_crossing"] += 1
        return
    assert_asof(t, 0, {"prices_score_start_announcement_received": t})
    scan.counts["valid_bracket_records"] += 1
    scan.add_center(gid, t, ct)


def scan_day(job):
    folder, hourly = job
    s = PreScan()
    for hour, assets in sorted(hourly.items()):
        seen = {}
        for a in assets:
            path = folder / a["name"]
            verify_asset(path, a)
            for raw_line in records(path):
                head = HEADER.match(raw_line)
                if not head:
                    raise ValueError(f"Unrecognized tape envelope in {a['name']}")
                t = int(head[1])
                if not hour <= t < hour + HOUR:
                    raise ValueError(f"Record outside sealed discovery hour in {a['name']}")
                if head[2] != b"line":
                    continue
                s.hours.add(hour)
                s.counts["line_raw_records"] += 1
                row = load_record(raw_line)
                gid = row["id"]
                key = (gid,t)
                digest = hashlib.blake2b(raw_line,digest_size=16).digest()
                if key in seen:
                    if seen[key] != digest:
                        s.conflicts.add(gid)
                        s.counts["conflicting_raw_line_tick"] += 1
                    else:
                        s.counts["duplicate_raw_line_tick"] += 1
                    continue
                seen[key] = digest
                try:
                    examine(s,row["d"],gid,t)
                except (KeyError, TypeError, IndexError, OverflowError) as exc:
                    s.conflicts.add(gid)
                    s.counts[f"parser_error_{type(exc).__name__}"] += 1
    return s


def load_terminals(path, lo, hi):
    with path.open("rb") as f:
        if hashlib.file_digest(f,"sha256").hexdigest() != TERMINALS_SHA:
            raise ValueError("The terminal CSV differs from the frozen discovery-only score reconstruction")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    result = {}
    for row in rows:
        gid = int(row["game_id"])
        ts, start = int(row["terminal_ms"]), int(row["start_ts"]) * 1000
        if gid in result or not (lo <= ts < hi and lo <= start < hi):
            raise ValueError("Duplicate or out-of-partition terminal record")
        result[gid] = (int(row["final_total"]),ts)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--terminals", type=Path, required=True)
    ap.add_argument("--out", type=Path,required=True)
    ap.add_argument("--split", type=Path,default=ROOT/"research/split.json")
    ap.add_argument("--manifest", type=Path,default=ROOT/"research/sources.json")
    ap.add_argument("--jobs", type=int,default=4)
    args = ap.parse_args()
    split = json.loads(args.split.read_text())
    lo, hi = split["discovery"]["start_ms"], split["discovery"]["end_ms"]
    finals = load_terminals(args.terminals, lo, hi)
    groups, wanted, read = tasks(json.loads(args.manifest.read_text()), split, args.cache)
    scan = PreScan()
    if args.jobs == 1:
        for job in groups:
            scan.merge(scan_day(job))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for result in pool.map(scan_day, groups):
                scan.merge(result)
    adjudicated = DayScan()
    adjudicated.centers = {(gid,0):quote for gid,quote in scan.first.items() if gid not in scan.conflicts}
    d = (hi - lo) // DAY
    weights = np.random.default_rng(split["seed"]).multinomial(d, np.ones(d)/d,size=split["bootstrap_draws"])
    metric = center_bias(adjudicated,finals,weights,lo)["k0"]
    days_with_centers = Counter(utc_string(t)[:10] for t,_ in scan.first.values())
    days_with_labels = Counter(utc_string(t)[:10] for (gid,_),(t,_) in adjudicated.centers.items()
                               if gid in finals and finals[gid][1] > t)
    output = {
        "discovery":split["discovery"],"seed":split["seed"],"bootstrap_draws":split["bootstrap_draws"],
        "terminal_csv_sha256":TERMINALS_SHA,"source_commit":split["source_commit"],"assets_verified":read,
        "assets_expected":wanted,"hours_containing_preboard_records":len(scan.hours),"counts":dict(sorted(scan.counts.items())),
        "preboard_game_ids_also_in_terminal_csv":len(scan.games_seen & finals.keys()),
        "bracket_game_ids_also_in_terminal_csv":len(scan.first.keys() & finals.keys()),
        "unique_masters_games_on_preboard":len(scan.games_seen),"unique_matches_passed_prestart_clock":len(scan.valid_clock_games),
        "unique_matches_with_conflicting_raw_keys_any_sport":len(scan.conflicts),"games_with_center_by_utc_receipt_day":dict(sorted(days_with_centers.items())),
        "games_with_center_and_final_by_utc_receipt_day":dict(sorted(days_with_labels.items())),
        "estimate":metric,
        "scope":"Sensitivity specified after noting that the original k=0 can include live 0-0. First eligible line-feed quote only: explicit best_of 7, parser reports not live and zero points, scheduled start known on the same quote strictly later than receipt. At least two complete rungs bracket the no-vig 50% probability. Exact same frozen terminal labels and UTC day resampling. The scheduled clock is not the actual first serve. No bet, price acceptance or causal edge is inferred."
    }
    args.out.write_text(json.dumps(output,indent=2,sort_keys=True,allow_nan=False,ensure_ascii=False)+"\n")


if __name__ == "__main__":
    main()
