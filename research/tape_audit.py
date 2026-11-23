"""Inventory/replay on SHA-verified discovery tape only; unit is a displayed price."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import zstandard as zstd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from asof import assert_asof
from vectiq.feed.schema import parse_game, parse_item
from vectiq.signals import st3
from vectiq.signals.st2 import _centre

try:
    from orjson import loads as load_record
except ImportError:
    load_record = json.loads

HOUR = 3_600_000
DAY = 24 * HOUR
# First commit containing both the implementation and these frozen prior weights.
PRIOR_MS = 1_794_102_401_000
PRIOR_COMMIT = "c342a99636dc3194a5790caf14ce9b72f6b15998"
NAME = re.compile(r"^(\d{4}-\d\d-\d\d)_(\d\d)(?:_r\d+)?\.jsonl\.zst$")
LEGS = ("ST0", "ST3", "ST5")


def utc_ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()) * 1000


def utc_string(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def cents(price):
    # Decimal feeds are non-negative. Half-cent ties round up.
    return math.floor(price * 100 + .5 + 1e-9)


def legal_set(a, b):
    hi, lo = max(a, b), min(a, b)
    return lo >= 0 and ((hi == 11 and lo <= 9) or (hi >= 12 and hi - lo == 2))


def legal_final(state):
    if state.best_of not in (None, 7) or max(state.sets_won) != 4:
        return None
    scores = list(state.sets)
    while scores and scores[-1] == (0, 0):
        scores.pop()
    if not 4 <= len(scores) <= 7:
        return None
    a = b = 0
    for home, away in scores:
        if max(a, b) >= 4 or not legal_set(home, away):
            return None
        a += int(home > away)
        b += int(away > home)
    return tuple(scores) if (a, b) == state.sets_won else None


def opportunity_window(state):
    scores = state.sets
    if not any(a or b for a, b in scores):
        return (0, ()) if state.sets_won == (0, 0) else None
    if not state.live:
        return None
    done = scores if legal_set(*scores[-1]) else scores[:-1]
    if not 1 <= len(done) <= 3 or not all(legal_set(a, b) for a, b in done):
        return None
    if len(done) != len(scores) and (min(scores[-1]) < 0 or sum(scores[-1]) > 4):
        return None
    if state.sets_won != (sum(a > b for a, b in done), sum(b > a for a, b in done)):
        return None
    return len(done), tuple(done)


@dataclass
class Match:
    first: int = 2**62
    last: int = -1
    starts: set = field(default_factory=set)
    ids: set = field(default_factory=set)
    names: set = field(default_factory=set)
    leagues: set = field(default_factory=set)
    formats: set = field(default_factory=set)
    terminal: dict = field(default_factory=dict)
    first_known_bo7: int = 2**62
    observed_fifth_win: bool = False

    def observe(self, s, kind, t):
        self.first = min(self.first, t)
        self.last = max(self.last, t)
        if isinstance(s.start_ts, (int, float)) and s.start_ts > 0:
            self.starts.add(int(s.start_ts) * 1000)
        if s.home_id and s.away_id:
            self.ids.add((s.home_id, s.away_id))
        if s.home and s.away:
            self.names.add((s.home, s.away))
        if s.league:
            self.leagues.add(s.league)
        if s.best_of is not None:
            self.formats.add(s.best_of)
        if s.best_of == 7:
            self.first_known_bo7 = min(t, self.first_known_bo7)
        self.observed_fifth_win |= max(s.sets_won) > 4
        seq = legal_final(s)
        if seq:
            self.terminal[seq] = min(self.terminal.get(seq, (2**62, "")), (t, kind))
        return seq

    def merge(self, other):
        self.first, self.last = min(self.first, other.first), max(self.last, other.last)
        self.first_known_bo7 = min(self.first_known_bo7, other.first_known_bo7)
        self.observed_fifth_win |= other.observed_fifth_win
        for name in ("starts", "ids", "names", "leagues", "formats"):
            getattr(self, name).update(getattr(other, name))
        for seq, term in other.terminal.items():
            self.terminal[seq] = min(self.terminal.get(seq, (2**62, "")), term)

    def quarantine(self, lo, hi):
        if len(self.ids) > 1 or len(self.leagues) != 1 or (not self.ids and len(self.names) > 1):
            return "ambiguous_identity"
        if self.observed_fifth_win or self.formats - {7}:
            return "other_or_conflicting_format"
        if 7 not in self.formats:
            return "unverified_best_of_7"
        if not self.starts:
            return "missing_scheduled_start"
        if min(self.starts) < lo or max(self.starts) >= hi:
            return "start_outside_discovery"
        if len(self.terminal) > 1:
            return "conflicting_terminal_scores"
        return None


@dataclass(frozen=True, order=True, slots=True)
class Pick:
    t: int
    k: int
    league: str
    line: float
    price: float
    status: str
    motor: str = ""

    def stratum(self):
        return self.motor, self.k, self.league, self.line, cents(self.price)


@dataclass
class DayScan:
    counts: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    markets: Counter = field(default_factory=Counter)
    leagues: Counter = field(default_factory=Counter)
    formats: Counter = field(default_factory=Counter)
    start_receipt_clocks: Counter = field(default_factory=Counter)
    age_ms: Counter = field(default_factory=Counter)
    gaps: dict = field(default_factory=lambda: {})
    edges: dict = field(default_factory=dict)
    matches: dict = field(default_factory=dict)
    controls: dict = field(default_factory=dict)
    centers: dict = field(default_factory=dict)
    candidates: dict = field(default_factory=dict)
    ambiguous: set = field(default_factory=set)
    hours: dict = field(default_factory=dict)

    def first_pick(self, leg, gid, pick):
        key = (leg, gid)
        previous = self.candidates.get(key)
        if previous is not None:
            if previous.t < pick.t or previous == pick:
                return
            if previous.t == pick.t:
                # item and line can coexist at the same receipt ms. No ordering
                # from a competing runner is assumed when their terms disagree.
                pick = replace(previous, status="ambiguous_simultaneous_first_quote")
        available = {"score_and_quote_received": pick.t}
        if leg == "ST5":
            available["frozen_prior"] = PRIOR_MS
        try:
            assert_asof(pick.t, 0, available)
        except ValueError as exc:
            raise RuntimeError("as-of failure on displayed-price replay") from exc
        self.candidates[key] = pick

    def window(self, s, t, win):
        k, done = win
        lad = st3.ladder(s)
        if not lad:
            return
        self.counts[f"book_changes_in_window_k{k}"] += 1
        if k in (0, 1) and len(lad) >= 2:
            ct = _centre([(ln, (1/u) / (1/u + 1/o)) for ln,o,u in lad])
            if ct is not None and math.isfinite(ct):
                pair = (s.game_id, k)
                quote = (t, ct)
                self.centers[pair] = min(quote, self.centers.get(pair, quote))
        if k <= 2 and (k != 0 or len(lad) >= 3):
            for line, _, under in lad:
                if not (math.isfinite(line) and math.isfinite(under) and line > 0):
                    continue
                cell = (s.game_id, k, s.league, line, cents(under))
                quote = (t, under)
                self.controls[cell] = min(quote, self.controls.get(cell, quote))
        if k == 0:
            if len(lad) >= 3:
                line, _, price = lad[0]
                if price >= 2.10:
                    gate = "shown" if price >= 2.20 else "PRE_rating_unknown"
                    self.first_pick("ST0", s.game_id, Pick(t, k, s.league, line, price, gate))
            return
        if k == 1 and abs(done[0][0] - done[0][1]) >= 5:
            paper = next((v for v in lad if v[2] >= 2), None)
            if paper:
                line, _, price = paper
                gate = "shown" if price >= 2.10 - 1e-9 else "first_paper_below_alert_price"
                self.first_pick("ST3", s.game_id, Pick(t, k, s.league, line, price, gate))
        if t >= PRIOR_MS:
            from vectiq.signals.markov import best_under
            paper = best_under(done, lad, 0.65, 0.05)
            if paper:
                line, price, _, ev = paper
                gate = "shown" if k in (1, 2) and 0.15 < ev <= 0.30 + 1e-9 else "first_paper_outside_alert_window"
                self.first_pick("ST5", s.game_id, Pick(t, k, s.league, line, price, gate))

    def intervals(self, times):
        for key, seen in times.items():
            seq = sorted(seen)
            hist = self.gaps.setdefault(key[0], Counter())
            hist.update(b - a for a, b in zip(seq, seq[1:]))
            if key in self.edges:
                first, last = self.edges[key]
                hist[seq[0] - last] += 1
            else:
                first = seq[0]
            self.edges[key] = (first, seq[-1])

    def merge(self, other):
        for attr in ("counts", "kinds", "markets", "leagues", "formats", "age_ms", "start_receipt_clocks"):
            getattr(self, attr).update(getattr(other, attr))
        self.hours.update(other.hours)
        self.ambiguous.update(other.ambiguous)
        for k, v in other.gaps.items():
            self.gaps.setdefault(k, Counter()).update(v)
        for key, (first, last) in other.edges.items():
            if key in self.edges:
                old_first, old_last = self.edges[key]
                self.gaps[key[0]][first - old_last] += 1
                first = old_first
            self.edges[key] = (first, last)
        for gid, match in other.matches.items():
            if gid in self.matches:
                self.matches[gid].merge(match)
            else:
                self.matches[gid] = match
        for key, q in other.centers.items():
            self.centers[key] = min(self.centers.get(key, q), q)
        for key, t in other.controls.items():
            self.controls[key] = min(self.controls.get(key, t), t)
        for (leg, gid), pick in other.candidates.items():
            self.first_pick(leg, gid, pick)


def verify_asset(path, asset):
    if path.stat().st_size != asset["size"]:
        raise ValueError(f"asset length mismatch: {path.name}")
    with path.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != asset["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {path.name}")


def records(path):
    with path.open("rb") as stream, zstd.ZstdDecompressor().stream_reader(stream, read_across_frames=True) as rd, io.BufferedReader(rd, 1 << 17) as buf:
        for line in buf:
            yield line


def observe_record(scan, raw, times):
    k, t, gid, data = raw["k"], raw["t"], raw["id"], raw["d"]
    scan.kinds[k] += 1
    if k not in {"item", "line", "game"}:
        return
    if type(gid) is not int or gid != data.get("I"):
        scan.counts["wrapper_game_id_mismatch"] += 1
        return
    times[(f"feed_changes_{k}", 0)].add(t)
    lg = str(data.get("L") or "").strip().lower()
    if not lg.startswith("masters") or "china" in lg or "super league" in lg:
        return
    if k == "game":
        obj = parse_game({"Value": data}, t)
        s = obj.state
        qs = (*s.quotes, *obj.set_quotes)
    else:
        s = parse_item(data, t)
        qs = s.quotes
    scan.counts["masters_family_records"] += 1
    scan.formats[str(s.best_of)] += 1
    scan.leagues[s.league] += 1
    if s.best_of not in (None, 7):
        scan.matches.setdefault(gid, Match()).observe(s, k, t)
        return
    # Only reception time is used to make decisions. U is inventoried separately.
    if isinstance(s.update_ts, (float, int)) and 946684800 <= s.update_ts <= 4102444800:
        scan.age_ms[t - int(s.update_ts * 1000)] += 1
    else:
        scan.counts["unknown_server_update_clock"] += 1
    times[(f"masters_per_match_{k}", gid)].add(t)
    m = scan.matches.setdefault(gid, Match())
    seq = m.observe(s, k, t)
    if max(s.sets_won) >= 4 and not seq:
        scan.counts["four_wins_but_invalid_or_incomplete_score_records"] += 1
    present = {(q.scope == "match", q.group, q.market, q.blocked) for q in qs}
    for match_scope, group, market, blocked in present:
        scan.markets[(k, "match" if match_scope else "set", group, market, "blocked" if blocked else "shown")] += 1
    if k == "game":
        return
    win = opportunity_window(s)
    if win is None:
        return
    if s.best_of != 7:
        scan.counts["window_changes_without_explicit_bo7"] += 1
        return
    scan.window(s, t, win)


def scan_day(task):
    data_dir, hourly = task
    scan = DayScan()
    for hour, assets in sorted(hourly.items()):
        seen, times = {}, defaultdict(set)
        raw_n = 0
        for asset in sorted(assets, key=lambda a: a["name"]):
            path = data_dir / asset["name"]
            verify_asset(path, asset)
            for line in records(path):
                raw_n += 1
                row = load_record(line)
                t, k, gid = row["t"], row["k"], row["id"]
                if type(t) is not int or not hour <= t < hour + HOUR:
                    raise ValueError(f"timestamp outside declared discovery hour in {path.name}")
                key = (t, k, gid)
                # Overlapping runs can write the same event. Never silently choose
                # contradictory payloads as tradable features or settlement truth.
                sig = hashlib.blake2b(line, digest_size=16).digest()
                if key in seen:
                    if seen[key] == sig:
                        scan.counts["repeated_exact_key"] += 1
                    else:
                        scan.counts["conflicting_same_tick_key"] += 1
                        scan.ambiguous.add(gid)
                    continue
                seen[key] = sig
                try:
                    observe_record(scan, row, times)
                except (KeyError, ValueError, TypeError, IndexError, OverflowError) as exc:
                    scan.counts[f"parser_error_{type(exc).__name__}"] += 1
                    scan.ambiguous.add(gid)
        scan.intervals(times)
        scan.hours[hour] = raw_n
        scan.counts["raw_records"] += raw_n
    return scan


def dist(hist):
    n = sum(hist.values())
    if not n:
        return {"n": 0}
    hit, result = 0, {"n": n}
    todo = [(max(1, math.ceil(q * n)), label) for q, label in ((.5, "p50_ms"), (.95, "p95_ms"), (1., "max_ms"))]
    for v, count in sorted(hist.items()):
        hit += count
        while todo and hit >= todo[0][0]:
            _, key = todo.pop(0)
            result[key] = v
    return result


def write_terminals(scan, lo, hi, path):
    reason, finals, rows = {}, {}, []
    for gid, m in scan.matches.items():
        why = "conflicting_raw_key_or_parser_error" if gid in scan.ambiguous else m.quarantine(lo, hi)
        if why is None and not m.terminal:
            why = "no_legal_observed_terminal"
        if why:
            reason[gid] = why
            continue
        seq, (t, source) = next(iter(m.terminal.items()))
        t = max(t, m.first_known_bo7)
        total = sum(a + b for a, b in seq)
        finals[gid] = (total, t)
        home, away = next(iter(m.names)) if len(m.names) == 1 else ("", "")
        rows.append((gid, total, t, source, next(iter(m.leagues)), min(m.starts) // 1000, home, away))
    if path:
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(("game_id", "final_total", "terminal_ms", "source", "league", "start_ts", "home", "away"))
            writer.writerows(sorted(rows))
    return reason, finals


def sums(pick, total):
    # Unit stake; integer pushes return the stake, counted in turnover.
    w, l = float(total < pick.line), float(total > pick.line)
    return (1., (pick.price - 1) * w - l, w + l, w)


def estimate(srows, crows, day_counts, lo):
    cells = sorted({p.stratum() for _, p, _ in srows})
    index = {key: i for i, key in enumerate(cells)}
    d = day_counts.shape[1]
    sel = np.zeros((d, len(cells), 4))
    ctrl = np.zeros_like(sel)
    sig_ids = {gid for gid, _, _ in srows}
    same_rows, ctrl_n, ctrl_games, ctrl_days = 0, 0, set(), set()
    ctrl_game_first_day, cross_day_games = {}, set()
    s_keys = {(gid, *p.stratum()) for gid, p, _ in srows}
    for group, rows in ((sel, srows), (ctrl, crows)):
        for gid, p, total in rows:
            key = p.stratum()
            if key not in index:
                continue
            day = (p.t - lo) // DAY
            group[day, index[key]] += sums(p, total)
            if group is ctrl:
                ctrl_n += 1
                ctrl_games.add(gid)
                ctrl_days.add(day)
                if ctrl_game_first_day.setdefault(gid, day) != day:
                    cross_day_games.add(gid)
                same_rows += (gid, *key) in s_keys
    out = {"shown_selected_n": len(srows), "selected_distinct_games":len(sig_ids), "selected_days": len({(p.t-lo)//DAY for _, p, _ in srows}),
           "exact_cells": len(cells), "control_game_cell_n": ctrl_n, "control_distinct_games": len(ctrl_games),
           "control_days": len(ctrl_days), "control_rows_also_signals": same_rows, "control_games_contributing_on_multiple_quote_days": len(cross_day_games),
           "control_games_also_selected": len(ctrl_games & sig_ids),
           "signal_mean_displayed_odds": float(np.mean([p.price for _,p,_ in srows])) if srows else None,
           "selected_pushes": sum(total == p.line for _,p,total in srows)}
    if not cells:
        return out
    # Repoststratify with the selected sample's cell counts in each *same-day draw*.
    # If any positive-weight cell is missing controls, skip that draw for that metric.
    for label, den, num in (("unit_roi", 0, 1), ("win_rate", 2, 3)):
        def values(weights):
            A, B = weights @ sel[:, :, den], weights @ sel[:, :, num]
            N, C = weights @ ctrl[:, :, den], weights @ ctrl[:, :, num]
            valid = (A.sum(1) > 0) & np.all((A == 0) | (N > 0), axis=1)
            sizes = A.sum(1)[valid]
            if not sizes.size:
                return np.empty((0, 3))
            rates = np.divide(C, N, out=np.zeros_like(C), where=N > 0)
            ctrl_m = np.sum((A * rates)[valid], axis=1) / sizes
            sel_m = B.sum(1)[valid] / sizes
            return np.column_stack((sel_m, ctrl_m, sel_m - ctrl_m))
        point = values(np.ones((1, d)))
        draws = np.concatenate([values(day_counts[i:i+64]) for i in range(0, len(day_counts), 64)])
        res = {"signal_n": int(sel[:, :, den].sum()), "control_n": int(ctrl[:, :, den].sum()), "paired_draws": len(draws)}
        for i, key in enumerate(("signal", "matched_blind_under", "increment")):
            ci = None
            if len(draws) and out["selected_days"] >= 2:
                ci = [float(x) for x in np.quantile(draws[:, i], [.025, .975])]
            res[key] = {"value": float(point[0, i]) if len(point) else None, "ci95_resampled_utc_days": ci}
            if ci is None:
                res[key]["value"] = None
        out[label] = res
    return out


def center_bias(scan, finals, weights, lo):
    output = {}
    for k in (0, 1):
        byday = np.zeros((weights.shape[1], 6))
        candidates = 0
        for (gid, stage), (t, center) in scan.centers.items():
            if stage != k:
                continue
            candidates += 1
            if gid not in finals or t >= finals[gid][1]:
                continue
            total = finals[gid][0]
            delta = center - total
            tie = abs(delta) < 1e-9
            byday[(t - lo) // DAY] += (1, center, total, delta, delta > 0 and not tie, tie)
        sums = byday.sum(0)
        n, days = int(sums[0]), int(np.count_nonzero(byday[:, 0]))
        weighted = weights @ byday
        good = weighted[:, 0] > 0
        estimator = {"games_with_bracket":candidates, "games_with_outcome":n, "days":days,
                     "valid_bootstrap":int(good.sum())}
        for j, name in enumerate(("mean_center","mean_observed_total","center_minus_total","fraction_below_center","fraction_equal_center"), 1):
            if n and days >= 2:
                draws = weighted[good, j] / weighted[good, 0]
                estimator[name] = {"value":float(sums[j]/n), "ci95":np.quantile(draws,[.025,.975]).tolist()}
            else:
                estimator[name] = {"value":None, "ci95":None}
        output[f"k{k}"] = estimator
    return output


def report(scan, split, terminal_path, actual, wanted):
    lo, hi = split["discovery"]["start_ms"], split["discovery"]["end_ms"]
    why, finals = write_terminals(scan, lo, hi, terminal_path)
    counts_by = defaultdict(Counter)
    samples = defaultdict(list)
    for (leg, gid), p in scan.candidates.items():
        counts_by[leg]["first_visible_paper_or_possible"] += 1
        status = p.status
        m = scan.matches[gid]
        if leg == "ST5" and (m.first < PRIOR_MS or not m.starts or min(m.starts) < PRIOR_MS):
            status = "match_before_frozen_prior"
        if status == "shown":
            counts_by[leg]["passed_price_and_score_before_risk_gates"] += 1
            status = why.get(gid, "usable")
            if status == "usable":
                terminal, t = finals[gid]
                if p.t >= t:
                    status = "quote_at_or_after_first_terminal"
                else:
                    samples[leg].append((gid, p, terminal))
        counts_by[leg][status] += 1
    controls, ccounts = [], Counter()
    for (gid, k, league, line, _), (t, price) in scan.controls.items():
        if gid not in finals:
            ccounts[why[gid]] += 1
        elif t >= finals[gid][1]:
            ccounts["quote_at_or_after_first_terminal"] += 1
        else:
            ccounts["usable"] += 1
            controls.append((gid, Pick(t, k, league, line, price, "shown"), finals[gid][0]))
    d = int((hi - lo) // DAY)
    rng = np.random.default_rng(split["seed"])
    weights = rng.multinomial(d, np.ones(d) / d, size=split["bootstrap_draws"])
    valid_hours = set(scan.hours)
    expected = set(range(lo, hi, HOUR))
    st5_controls = [r for r in controls if r[1].t >= PRIOR_MS and scan.matches[r[0]].first >= PRIOR_MS
                    and min(scan.matches[r[0]].starts) >= PRIOR_MS]
    comparisons = {leg:estimate(samples[leg], st5_controls if leg == "ST5" else controls, weights, lo) for leg in LEGS}
    for k in (1, 2):
        comparisons[f"ST5_after_set_{k}"] = estimate([r for r in samples["ST5"] if r[1].k == k], st5_controls, weights, lo)
    # Fixed aggregate: one unit per visible qualifying leg, same event may appear
    # in multiple legs. No risk weighting, no deployable equity simulation.
    by_leg_control = {"ST0":controls, "ST3":controls, "ST5":st5_controls}
    group_selected = [(gid, replace(p, motor=leg), total) for leg in LEGS for gid,p,total in samples[leg]]
    used = {leg:{p.stratum()[1:] for _,p,_ in samples[leg]} for leg in LEGS}
    group_control = ((gid, replace(p, motor=leg), total) for leg in LEGS for gid,p,total in by_leg_control[leg]
                     if p.stratum()[1:] in used[leg])
    pooled = estimate(group_selected, group_control, weights, lo)
    game_legs = defaultdict(set)
    for gid,p,_ in group_selected:
        game_legs[gid].add(p.motor)
    pooled["selected_games_by_legs"] = dict(sorted(Counter("+".join(sorted(v)) for v in game_legs.values()).items()))
    pooled["unit"] = "one unit per engine per match; a control may reappear in a different engine's stratum"
    comparisons["combined_flat_per_leg"] = pooled
    return {
        "split": "discovery", "source_commit": split["source_commit"], "lo_ms": lo, "hi_exclusive_ms": hi,
        "seed": split["seed"], "bootstrap": len(weights), "prior_commit_ST5": PRIOR_COMMIT,
        "design": "Displayed, unblocked quote only. Distinct match/stage/league/exact line/nearest price cent control; first visible quote per cell. Inclusive matched control. Stage 0 requires three rungs; stages 1–2 include 0–4 additional points. W/L win rate; W/L/push stake turnover ROI. Complete-case descriptive. No inferred fills, no imputation. Unknown format excluded from decisions.",
        "limits": "Current gate replay. Incomplete pre-sample history, run ownership, personal risk gates, ST0 point-in-time ratings, delivery, fills, official grading and historical rule changes are not reconstructed. As-of-time prior gate restricts ST5. Feed snapshots are changes; no heartbeat. Parsing sharp support and time cutoffs documented in the source.",
        "assets_requested": wanted, "assets_read": actual, "hours_in_window": len(expected), "hours_with_records": sum(bool(v) for v in scan.hours.values()),
        "hours_without_asset": [utc_string(h) for h in sorted(expected - valid_hours)], "empty_hours": [utc_string(h) for h,n in sorted(scan.hours.items()) if not n],
        "receipt_records_per_utc_day": {utc_string(ms)[:10]:sum(n for h,n in scan.hours.items() if ms <= h < ms+DAY) for ms in range(lo,hi,DAY)},
        "counts": dict(sorted(scan.counts.items())), "raw_kinds_after_dedupe": dict(sorted(scan.kinds.items())),
        "masters_family_by_declared_best_of": dict(sorted(scan.formats.items())), "masters_family_leagues_snapshot_counts": dict(sorted(scan.leagues.items())),
        "markets": [{"feed": k[0], "scope": k[1], "group": k[2], "market": k[3], "availability": k[4], "snapshots": n} for k,n in sorted(scan.markets.items())],
        "change_intervals": {k:dist(v) for k,v in sorted(scan.gaps.items())}, "receipt_minus_provider_update": dist(scan.age_ms),
        "negative_receipt_minus_provider_update_n":sum(v for k,v in scan.age_ms.items() if k<0),
        "masters_games_seen": len(scan.matches), "games_legal_terminal_in_discovery": len(finals),
        "game_exclusions": dict(sorted(Counter(why.values()).items())),
        "window_game_cell_counts": dict(sorted(ccounts.items())), "all_ex_ante_window_game_cells": len(scan.controls),
        "candidates": {leg:dict(sorted(counts_by[leg].items())) for leg in LEGS},
        "comparison": comparisons, "center_bias": center_bias(scan, finals, weights, lo)
    }


def tasks(sources, split, cache, allow_partial=False):
    lo, hi = split["discovery"]["start_ms"], split["discovery"]["end_ms"]
    group = defaultdict(lambda: defaultdict(list))
    declared, read, names = 0, 0, set()
    for a in sources["assets"]:
        if a["stage"] != "discovery":
            continue
        name = a["name"]
        match = NAME.fullmatch(name)
        if not match or match[1] != a["day"] or name in names:
            raise ValueError(f"invalid or duplicate discovery filename: {name}")
        t = utc_ms(match[1]) + int(match[2]) * HOUR
        if not lo <= t < hi or not 0 <= int(match[2]) < 24 or not re.fullmatch("[a-fA-F0-9]{64}", a["sha256"]):
            raise ValueError("discovery manifest disagrees with temporal/hash gate")
        names.add(name)
        declared += 1
        f = cache / name
        if allow_partial and not f.exists():
            continue
        if not f.is_file() or f.is_symlink():
            raise ValueError(f"missing or non-regular asset: {name}")
        group[match[1]][t].append(a)
        read += 1
    if not read:
        raise ValueError("no verified discovery input available")
    return [(cache, dict(hours)) for _,hours in sorted(group.items())], declared, read


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=ROOT / "research/sources.json")
    ap.add_argument("--split", type=Path, default=ROOT / "research/split.json")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--terminals", type=Path)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--available-only", action="store_true", help="coverage diagnostic on available manifest assets only")
    a = ap.parse_args()
    split = json.loads(a.split.read_text())
    groups, wanted, actual = tasks(json.loads(a.manifest.read_text()),split,a.data_dir,a.available_only)
    scan = DayScan()
    if a.jobs == 1:
        for task in groups:
            scan.merge(scan_day(task))
    else:
        with ProcessPoolExecutor(max_workers=a.jobs) as pool:
            for data in pool.map(scan_day, groups):
                scan.merge(data)
    out = report(scan, split, a.terminals, actual, wanted)
    a.out.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
