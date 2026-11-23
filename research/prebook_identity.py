"""Post-hoc exact cross-feed identity attribution. Build links before loading scores."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from prematch_clock_audit import HEADER, TERMINALS_SHA, PreScan, examine, load_terminals
from tape_audit import DAY, HOUR, ROOT, DayScan, center_bias, load_record, records, tasks, utc_string, verify_asset
from vectiq.feed.schema import _best_of


@dataclass
class Identity:
    keys: set = field(default_factory=set)
    starts: set = field(default_factory=set)
    leagues: set = field(default_factory=set)
    homes: set = field(default_factory=set)
    aways: set = field(default_factory=set)
    home_ids: set = field(default_factory=set)
    away_ids: set = field(default_factory=set)
    formats: set = field(default_factory=set)
    key_first_ms: dict = field(default_factory=dict)

    def observe(self, data, t):
        # Intentionally never consult scores, status, finals, or market prices.
        start = data.get("S")
        if type(start) is not int or start <= 0:
            start = None
        league = data.get("L")
        home = data.get("O1E") or data.get("O1")
        away = data.get("O2E") or data.get("O2")
        if league and home and away and start:
            key = (league,home,away,start)
            self.keys.add(key)
            self.key_first_ms[key] = min(t, self.key_first_ms.get(key,t))
        for name, value in (("starts",start),("leagues",league),("homes",home),("aways",away),
                            ("home_ids",data.get("O1I")),("away_ids",data.get("O2I")),("formats",_best_of(data))):
            if value:
                getattr(self,name).add(value)

    def merge(self, other):
        for name in ("keys","starts","leagues","homes","aways","home_ids","away_ids","formats"):
            getattr(self,name).update(getattr(other,name))
        for key,t in other.key_first_ms.items():
            self.key_first_ms[key] = min(t,self.key_first_ms.get(key,t))

    def issue(self):
        if len(self.starts) != 1:
            return "missing_or_multiple_schedules"
        if len(self.keys) != 1 or len(self.homes) != 1 or len(self.aways) != 1 or len(self.leagues) != 1:
            return "incomplete_or_changed_names_league"
        if self.formats != {7}:
            return "missing_or_conflicting_bo7"
        if len(self.home_ids) > 1 or len(self.away_ids) > 1:
            return "changed_numeric_player_identity"
        return None


@dataclass
class Universe:
    pre: dict = field(default_factory=dict)
    live: dict = field(default_factory=dict)
    invalid_pre: set = field(default_factory=set)
    invalid_live: set = field(default_factory=set)
    catalog: PreScan = field(default_factory=PreScan)
    counts: Counter = field(default_factory=Counter)

    def merge(self, other):
        for name in ("pre","live"):
            combined = getattr(self,name)
            for gid, identity in getattr(other,name).items():
                if gid not in combined:
                    combined[gid] = identity
                else:
                    combined[gid].merge(identity)
        self.invalid_pre.update(other.invalid_pre)
        self.invalid_live.update(other.invalid_live)
        self.catalog.merge(other.catalog)
        self.counts.update(other.counts)


def scan_day(job):
    cache,hourly = job
    universe = Universe()
    for hour, assets in sorted(hourly.items()):
        seen = {}
        for asset in assets:
            path = cache / asset["name"]
            verify_asset(path,asset)
            for raw_line in records(path):
                header = HEADER.match(raw_line)
                if header is None:
                    raise ValueError(f"Unrecognized envelope in {path.name}")
                t, kind = int(header[1]), header[2]
                if not hour <= t < hour+HOUR:
                    raise ValueError(f"Record outside declared discovery hour in {path.name}")
                if kind not in (b"line",b"item",b"game"):
                    continue
                row = load_record(raw_line)
                d, gid = row["d"],row["id"]
                pre = kind == b"line"
                bad = universe.invalid_pre if pre else universe.invalid_live
                league = str(d.get("L") or "").lower().strip()
                if not league.startswith("masters") or "super league" in league or "china" in league:
                    if league:
                        # A reused global ID crossing leagues cannot authenticate a link.
                        bad.add(gid)
                    continue
                if gid != d.get("I"):
                    bad.add(gid)
                    continue
                identity = (universe.pre if pre else universe.live).setdefault(gid, Identity())
                identity.observe(d,t)
                key = (t, kind, gid)
                digest = hashlib.blake2b(raw_line,digest_size=16).digest()
                if key in seen:
                    if digest != seen[key]:
                        bad.add(gid)
                    continue
                seen[key] = digest
                if pre:
                    examine(universe.catalog,d,gid,t)
    return universe


def key_index(population):
    index = defaultdict(set)
    for gid, identity in population.items():
        for key in identity.keys:
            index[key].add(gid)
    return index


def infer(universe, lo, hi):
    # Index every candidate before selecting by quote or by observed terminal.
    pres, lives = key_index(universe.pre), key_index(universe.live)
    rows, reasons = [], Counter()
    for gid,(t,ct) in sorted(universe.catalog.first.items()):
        pre = universe.pre[gid]
        issue = pre.issue()
        if gid in universe.invalid_pre or gid in universe.catalog.conflicts:
            issue = "ambiguous_raw_pre_identity"
        if issue:
            reasons["pre_"+issue] += 1
            continue
        key = next(iter(pre.keys))
        if not lo <= key[3]*1000 < hi:
            reasons["scheduled_start_outside_discovery"] += 1
            continue
        if len(pres[key]) != 1:
            reasons["pre_key_maps_to_several_pre_ids"] += 1
            continue
        candidates = lives.get(key, ())
        if len(candidates) != 1:
            reasons["live_key_not_seen" if not candidates else "key_maps_to_several_live_ids"] += 1
            continue
        live_gid = next(iter(candidates))
        live = universe.live[live_gid]
        issue = live.issue()
        if live_gid in universe.invalid_live:
            issue = "ambiguous_raw_live_identity"
        if issue:
            reasons["live_"+issue] += 1
            continue
        shared = [(a,b) for a,b in [(pre.home_ids, live.home_ids),(pre.away_ids, live.away_ids)] if a and b]
        if any(a != b for a,b in shared):
            reasons["numeric_player_ids_disagree"] += 1
            continue
        rows.append((gid,live_gid,*key,t,ct,len(shared),live.key_first_ms[key]))
    if len({r[0] for r in rows}) != len(rows) or len({r[1] for r in rows}) != len(rows):
        raise AssertionError("cross-feed attribution is not one-to-one")
    return rows, reasons


def write_mapping(path, rows):
    with path.open("w", newline="") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(("pre_game_id","live_game_id","league","home","away","scheduled_start_s","pre_quote_ms",
                     "displayed_no_vig_center","numeric_player_ids_checked","live_identity_first_seen_ms"))
        wr.writerows(rows)
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache",type=Path,required=True)
    ap.add_argument("--terminals",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--mapping",type=Path,required=True)
    ap.add_argument("--split",type=Path,default=ROOT/"research/split.json")
    ap.add_argument("--manifest",type=Path,default=ROOT/"research/sources.json")
    ap.add_argument("--jobs",type=int,default=4)
    args=ap.parse_args()
    split=json.loads(args.split.read_text())
    lo,hi=split["discovery"]["start_ms"],split["discovery"]["end_ms"]
    groups,wanted,read=tasks(json.loads(args.manifest.read_text()),split,args.cache)
    universe=Universe()
    if args.jobs == 1:
        for day in groups:
            universe.merge(scan_day(day))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for part in pool.map(scan_day,groups):
                universe.merge(part)
    rows, exclusions=infer(universe,lo,hi)
    digest=write_mapping(args.mapping,rows)
    # Only now consult independently adjudicated match totals; absence of a
    # terminal is deliberately immaterial to the uniqueness of a candidate.
    finals=load_terminals(args.terminals,lo,hi)
    centers=DayScan()
    centers.centers={(row[1],0):(row[6],row[7]) for row in rows}
    days=(hi-lo)//DAY
    weights=np.random.default_rng(split["seed"]).multinomial(days,np.ones(days)/days,size=split["bootstrap_draws"])
    metric=center_bias(centers,finals,weights,lo)["k0"]
    earlier = DayScan()
    earlier.centers = {(row[1],0):(row[6],row[7]) for row in rows
                      if row[1] in finals and row[9] < finals[row[1]][1]}
    before_terminal = center_bias(earlier,finals,weights,lo)["k0"]
    output={
        "source_commit":split["source_commit"],"discovery":split["discovery"],"seed":split["seed"],
        "bootstrap_draws":split["bootstrap_draws"],"assets_verified":read,"assets_expected":wanted,
        "terminal_csv_sha256":TERMINALS_SHA,"identity_mapping_sha256":digest,
        "pre_master_ids_in_inventory":len(universe.pre),"livefeed_master_ids_in_inventory_even_without_terminal":len(universe.live),
        "pre_ids_with_no_vig_bracket":len(universe.catalog.first),"excluded_before_opening_terminals":dict(sorted(exclusions.items())),
        "exact_one_to_one_links":len(rows),"links_by_number_of_numeric_player_ids_cross_checked":dict(sorted(Counter(row[8] for row in rows).items())),
        "linked_match_without_legal_terminal":sum(row[1] not in finals for row in rows),
        "terminal_at_or_before_receipt":sum(row[1] in finals and finals[row[1]][1] <= row[6] for row in rows),
        "linked_live_key_seen_by_pre_quote":sum(row[9] <= row[6] for row in rows),
        "linked_with_live_key_first_seen_before_final":len(earlier.centers),
        "estimate_for_live_keys_first_seen_before_final":before_terminal,
        "estimate":metric,"estimand":"Post-hoc identity sensitivity, not an operator-issued crosswalk. Exact ordered player names, league, scheduled UTC second and one-to-one across every observed Masters pre/live identity. Known numeric player IDs must agree, changed identities, clock revisions and ambiguous keys are excluded. Pre-board quote was strictly before announced start. Probabilities/intervals are descriptive for matched discovery games only."
    }
    args.out.write_text(json.dumps(output,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
