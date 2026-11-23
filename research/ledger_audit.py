"""Audit historical alert books on the discovery interval of a frozen split."""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import itertools
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DAY_MS = 86_400_000
BOOKS = ("ST3", "ST5", "ST0")  # panel order for simultaneous timestamps
PANEL_STAKES = {"ST3": 0.01, "ST5": 0.01, "ST0": 0.0045}
BOOTSTRAPS = 10_000
UNVERIFIED = "no verificado"


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int
    seed: int

    def __post_init__(self):
        if self.end_ms <= self.start_ms or self.start_ms % DAY_MS or self.end_ms % DAY_MS:
            raise ValueError("El tramo de descubrimiento requiere límites UTC a medianoche.")

    def days(self) -> list[int]:
        return list(range(self.start_ms // DAY_MS, self.end_ms // DAY_MS))


@dataclass(frozen=True)
class Entry:
    book: str
    row_id: int
    time_ms: int
    gid: int
    sets_done: int
    price: float
    line: float
    league: str
    stake: float | None
    players: str = ""
    reported_start_s: int | None = None

    @property
    def order(self):
        rank = BOOKS.index(self.book) if self.book in BOOKS else len(BOOKS)
        return self.time_ms, rank, self.book, self.row_id


@dataclass(frozen=True)
class Closed:
    entry: Entry
    net: float  # profit on one unit
    result: str  # W/L/P
    known_ms: int | None  # first terminal observation on tape, not bookmaker settlement


def quantile(sorted_sample: list[float], probability: float) -> float:
    position = probability * (len(sorted_sample) - 1)
    lo = math.floor(position)
    hi = math.ceil(position)
    return sorted_sample[lo] + (position - lo) * (sorted_sample[hi] - sorted_sample[lo])


def clustered_ratios(
    day_totals: dict[int, list[tuple[float, float]]],
    days: list[int],
    seed: int,
    draws: int = BOOTSTRAPS,
) -> list[dict]:
    """Ratio of pooled numerators/denominators; sample identical whole days for all columns."""
    if not day_totals:
        return []
    width = len(next(iter(day_totals.values())))
    totals = [day_totals.get(d, [(0.0, 0.0)] * width) for d in days]
    if not totals or width == 0 or draws <= 0:
        raise ValueError("Se necesitan días, métricas y repeticiones.")
    rng = random.Random(seed)
    samples: list[list[float]] = [[] for _ in range(width)]
    active = [sum(row[m][1] > 0 for row in totals) for m in range(width)]
    for _ in range(draws):
        counts = Counter(rng.choices(range(len(totals)), k=len(totals)))
        for m in range(width):
            if active[m] < 2:
                continue
            den = sum(totals[i][m][1] * c for i, c in counts.items())
            if den:
                num = sum(totals[i][m][0] * c for i, c in counts.items())
                samples[m].append(num / den)
    out = []
    for m, sample in enumerate(samples):
        den = sum(row[m][1] for row in totals)
        num = sum(row[m][0] for row in totals)
        sample.sort()
        available = den > 0 and active[m] >= 2 and bool(sample)
        out.append({
            "estimate": num / den if available else None,
            "ci95": [quantile(sample, .025), quantile(sample, .975)] if available else None,
            "n_days_contributing": active[m], "resamples_with_denominator": len(sample),
        })
    return out


def parse_window(split: dict) -> Window:
    discovery = split["discovery"]
    if str(split.get("timezone", "UTC")).upper() not in {"UTC", "ETC/UTC"} or split.get("day_start_hour", 0) != 0:
        raise ValueError("El registro admite días UTC completos.")
    return Window(int(discovery["start_ms"]), int(discovery["end_ms"]), int(split["seed"]))


def sha256_file(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            dig.update(block)
    return dig.hexdigest()


def valid_number(value) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(value)


def panel_filter(book: str, sets_done: int, price: float, ev) -> bool:
    if book == "ST3":
        return price >= 2.10 - 1e-9
    if book == "ST5":
        return valid_number(ev) and .15 < ev <= .30 + 1e-9 and sets_done <= 2
    return True


def read_book(directory: Path, book: str, spec: dict, window: Window, view: str,
              excluded_ids: set[int]) -> tuple[list[Entry], list[Closed], dict]:
    filename = spec.get("file", book.lower() + ".sqlite")
    if Path(filename).name != filename:
        raise ValueError("La ruta del libro debe pertenecer al directorio de entrada.")
    path = directory / filename
    if not path.is_file():
        return [], [], {"status": "missing", "file": filename}

    as_of = spec.get("as_of_ms")
    verified_asof = isinstance(as_of, int) and window.start_ms < as_of < window.end_ms
    if view == "ledger" and (not verified_asof or not spec.get("sha256")):
        return [], [], {"status": "blocked_without_dated_snapshot", "file": filename,
                        "as_of_ms": as_of, "outcomes_projected": False}
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_file() and sidecar.stat().st_size:
            raise ValueError(f"{filename}: journal no sellado.")
    digest = sha256_file(path)
    expected = spec.get("sha256")
    if expected and digest != expected.lower():
        raise ValueError(f"{filename}: sha256 distinto al manifiesto.")
    counters = Counter()
    entries = []
    closed = []
    sources = Counter()
    results = Counter()
    columns = "id,ts_ms,game_id,sets,side,line,price,ev,league,stake,players"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(bets)")}
        mandatory = set(columns.split(","))
        if view == "ledger":
            mandatory.update(("result", "pnl"))
        if not mandatory <= existing:
            raise ValueError(f"{filename}: faltan columnas {sorted(mandatory-existing)}.")
        columns += ",start_ts" if "start_ts" in existing else ",NULL AS start_ts"
        if view == "ledger":
            columns += ",result,pnl," + ("src" if "src" in existing else "NULL AS src")
        else:
            forbidden = {"result", "pnl", "src", "final_total", "closing_price"}
            def prevent_outcomes(action, table, col, database, trigger):
                if action == sqlite3.SQLITE_READ and table == "bets" and col in forbidden:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(prevent_outcomes)
        query = (f"SELECT {columns} FROM bets WHERE ts_ms >= ? AND ts_ms < ? "
                 "ORDER BY ts_ms,id")
        limit = min(window.end_ms, as_of + 1) if view == "ledger" else window.end_ms
        for row in conn.execute(query, (window.start_ms, limit)):
            counters["rows_in_window"] += 1
            ts, gid = row["ts_ms"], row["game_id"]
            if type(gid) is not int or gid <= 0 or type(ts) is not int:
                counters["invalid_id_or_clock"] += 1
                continue
            if gid in excluded_ids:
                counters["excluded_boundary_or_ambiguous_gid"] += 1
                continue
            start = row["start_ts"]
            if start is not None and (type(start) is not int or start <= 0):
                counters["invalid_reported_start"] += 1
                continue
            if start is not None and not window.start_ms <= start * 1000 < window.end_ms:
                counters["reported_start_outside_discovery"] += 1
                continue
            if row["side"] != "under":
                counters["other_or_missing_side"] += 1
                continue
            price, line = row["price"], row["line"]
            if not (valid_number(price) and price > 1 and valid_number(line) and line > 0):
                counters["invalid_price_or_line"] += 1
                continue
            sets_done = (row["sets"] or "").count("-")
            if not panel_filter(book, sets_done, price, row["ev"]):
                counters["outside_panel_filter"] += 1
                continue
            if book == "ST3" and price > 2.30 + 1e-9:
                counters["panel_ST3_above_owner_price_band_2_30"] += 1
            stake = row["stake"] if valid_number(row["stake"]) and row["stake"] > 0 else None
            entry = Entry(book, row["id"], ts, gid, sets_done, price, line,
                          row["league"] or "(desconocida)", stake, row["players"] or "", start)
            entries.append(entry)
            if view != "ledger":
                continue
            src = row["src"] or "(sin fuente)"
            sources[src] += 1
            result = row["result"]
            results[result or "(pendiente)"] += 1
            if result not in ("W", "L", "P"):
                continue
            if stake is None or not valid_number(row["pnl"]):
                counters["invalid_stake_or_pnl"] += 1
                continue
            net = round(row["pnl"] / stake, 4)
            contract = {"W": price - 1, "L": -1.0, "P": 0.0}[result]
            if abs(net - contract) > .00011:
                counters["net_disagrees_with_quote_and_result"] += 1
            closed.append(Closed(entry, net, result, None))

    diagnostic = {"status": "read", "file": filename, "sha256": digest,
                  "as_of_ms": as_of, "outcomes_projected": view == "ledger",
                  "counts": dict(counters), "eligible_openings": len(entries)}
    if view == "ledger":
        diagnostic.update(settlements_by_source=dict(sources), settlements_by_status=dict(results))
    return entries, closed, diagnostic


def participant_identity(names) -> tuple[str, ...]:
    return tuple(sorted(" ".join(x.casefold().split()) for x in names if x and x.strip()))


def terminal_map(path: Path, window: Window) -> tuple[dict, set[int]]:
    candidates = {}
    ambiguous = set()
    with path.open(newline="") as inp:
        reader = csv.DictReader(inp)
        if not {"game_id", "final_total", "terminal_ms", "league"} <= set(reader.fieldnames or ()):
            raise ValueError("La cinta necesita game_id,final_total,terminal_ms,league.")
        for row in reader:
            gid, total, ts = int(row["game_id"]), int(row["final_total"]), int(row["terminal_ms"])
            if not window.start_ms <= ts < window.end_ms or gid <= 0 or total < 44:
                raise ValueError("Terminal fuera de la ventana o no válido.")
            league = row["league"]
            names = participant_identity([row.get("home"), row.get("away")])
            if gid in candidates:
                prior_total, prior_ts, prior_league, prior_names = candidates[gid]
                if prior_total != total or prior_league != league or prior_names != names:
                    ambiguous.add(gid)
                ts = min(ts, prior_ts)
            candidates[gid] = (total, ts, league, names)
    for gid in ambiguous:
        candidates.pop(gid)
    return candidates, ambiguous


def reconcile_tape(entries: Iterable[Entry], terminals: dict, ambiguous: set[int]):
    closed, coverage = [], Counter()
    for entry in entries:
        terminal = terminals.get(entry.gid)
        if entry.gid in ambiguous:
            coverage["ambiguous_terminal"] += 1
            continue
        if terminal is None:
            coverage["no_eligible_terminal"] += 1
            continue
        total, ts, league, names = terminal
        if ts <= entry.time_ms:
            coverage["terminal_at_or_before_alert"] += 1
            continue
        if not league or entry.league != league:
            coverage["league_missing_or_mismatch"] += 1
            continue
        if names and (len(names) != 2 or participant_identity(entry.players.split(" vs ")) != names):
            coverage["player_pair_mismatch"] += 1
            continue
        if not names:
            coverage["matched_without_terminal_player_pair"] += 1
        result = "W" if total < entry.line else "L" if total > entry.line else "P"
        net = {"W": entry.price - 1, "L": -1.0, "P": 0.0}[result]
        closed.append(Closed(entry, net, result, ts))
        coverage["matched"] += 1
    return closed, dict(coverage)


def coverage_groups(entries: list[Entry], closed: list[Closed], key):
    available = Counter(key(e) for e in entries)
    complete = Counter(key(r.entry) for r in closed)
    return {group: {"openings": n, "with_eligible_terminal": complete[group]}
            for group, n in sorted(available.items())}


def calendar_day(key: int) -> str:
    return datetime.fromtimestamp(key * DAY_MS / 1000, timezone.utc).date().isoformat()


def panel_sim(rows: list[Closed]) -> float | None:
    if not rows or any(r.entry.book not in PANEL_STAKES for r in rows):
        return None
    bank = base = peak = 100.0
    mdd = 0.0
    last_recalculation = -10**15
    stakes = {}
    openings = [(r.entry.time_ms, 0, i) for i, r in enumerate(rows)]
    payouts = [(t + 600_000, 1, i) for t, _, i in openings]
    for time_ms, kind, i in heapq.merge(openings, payouts):
        if kind == 0:
            if time_ms - last_recalculation >= 3_600_000 or (abs(bank/base - 1) >= .05 if base else bank > 0):
                base, last_recalculation = bank, time_ms
            stakes[i] = min(3000.0, max(1.0, PANEL_STAKES[rows[i].entry.book] * base))
        else:
            bank = max(0., bank + stakes.pop(i) * rows[i].net)
            peak = max(peak, bank)
            mdd = max(mdd, 1 - bank / peak)
    return mdd


def observed_terminal_timing(rows: list[Closed]):
    by_time: dict[int, list[float]] = defaultdict(list)
    delays = []
    for row in rows:
        if row.known_ms is not None:
            delays.append((row.known_ms - row.entry.time_ms)/60_000)
            by_time[row.known_ms].append(row.net)
    if not delays:
        return None
    delays.sort()
    equity = peak = dd = 0.0
    for time_ms in sorted(by_time):
        equity += sum(by_time[time_ms])
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {"n_WL": len(delays), "minutes_entry_to_first_terminal_median": quantile(delays, .5),
            "n_observed_later_than_10min": sum(t > 10 for t in delays),
            "n_observed_earlier_than_10min": sum(t < 10 for t in delays),
            "n_observed_at_10min": sum(t == 10 for t in delays),
            "multi_entry_terminal_times": sum(len(b) > 1 for b in by_time.values()),
            "mdd_paper_units_netting_simultaneous_observations": dd}


def performance(rows: list[Closed], window: Window, draws: int):
    # The dashboard excludes pushes, void and unresolved alerts from all its KPIs.
    ordered = sorted((row for row in rows if row.result != "P"), key=lambda r: r.entry.order)
    day_rows = defaultdict(list)
    equity = peak = dd = 0.
    streak = longest = 0
    for row in ordered:
        day_rows[row.entry.time_ms // DAY_MS].append(row)
        equity += row.net
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        streak = streak + 1 if row.net <= 0 else 0
        longest = max(longest, streak)
    totals = {}
    daily_pnl = []
    for day, bets in sorted(day_rows.items()):
        n = len(bets)
        net = sum(b.net for b in bets)
        with_stake = [r for r in bets if r.entry.stake is not None]
        stake_sum = sum(r.entry.stake for r in with_stake)
        stake_net = sum(r.entry.stake * r.net for r in with_stake)
        totals[day] = [(sum(r.net > 0 for r in bets), n), (net, n),
                       (stake_net, stake_sum)]
        runs = itertools.groupby(bets, key=lambda r: r.net <= 0)
        day_longest = max((sum(1 for _ in group) for lost, group in runs if lost), default=0)
        daily_pnl.append({"utc_day_of_opening": calendar_day(day), "n": n, "net_units": net,
                          "longest_loss_streak_within_utc_opening_day": day_longest})
    ratios = clustered_ratios(totals, window.days(), window.seed, draws)
    empty = {"estimate": None, "ci95": None, "n_days_contributing": 0,
             "resamples_with_denominator": 0}
    wins, roi, roi_by_stake = ratios or [empty, empty, empty]
    return {
        "n_WL": len(ordered), "n_matches_WL": len({r.entry.gid for r in ordered}),
        "n_push_separate": sum(r.result == "P" for r in rows),
        "n_W": sum(r.result == "W" for r in ordered),
        "n_L": sum(r.result == "L" for r in ordered),
        "mean_decimal_price_WL": sum(r.entry.price for r in ordered)/len(ordered) if ordered else None,
        "wr_positive_net_per_WL": wins, "roi_flat_per_WL": roi,
        "roi_at_booked_paper_stakes_per_WL": roi_by_stake,
        "n_WL_with_paper_stake": sum(r.entry.stake is not None for r in ordered),
        "loss_streak_in_opening_order": longest if ordered else None,
        "mdd_flat_units_opening_order": dd if ordered else None,
        "panel_mdd_fraction_synthetic_t_plus_10min_initial_100": panel_sim(ordered),
        "actual_bank_mdd": UNVERIFIED, "actual_settlement_order_loss_streak": UNVERIFIED,
        "terminal_observation_timing": observed_terminal_timing(ordered),
        "days_of_opening_with_WL": len(daily_pnl),
        "positive_days_flat_units": sum(d["net_units"] > 1e-10 for d in daily_pnl),
        "negative_days_flat_units": sum(d["net_units"] < -1e-10 for d in daily_pnl),
        "flat_days_with_WL": sum(abs(d["net_units"]) <= 1e-10 for d in daily_pnl),
        "daily_flat_pnl_of_WL": daily_pnl,
    }


def missing_score_bounds(entries: list[Entry], closed: list[Closed], window: Window,
                         draws: int) -> dict:
    # Risk one unit per opening. A known push is a zero return on that unit.
    # Missing wins pay their own quoted price less one; missing losses return -1.
    observed_ids = {(r.entry.book, r.entry.row_id) for r in closed}
    missing = [e for e in entries if (e.book, e.row_id) not in observed_ids]
    totals = defaultdict(lambda: [(0.0, 0.0)])
    for row in closed:
        day = row.entry.time_ms // DAY_MS
        num, den = totals[day][0]
        totals[day][0] = (num + row.net, den + 1)
    estimates = clustered_ratios(totals, window.days(), window.seed, draws)
    estimate = estimates[0] if estimates else None
    out = {"unit": "one original unit per opening incl. refunded units at zero",
           "n_openings": len(entries), "n_with_final_including_push": len(closed),
           "n_without_valid_final": len(missing),
           "complete_case_roi_with_day_ci95": estimate, "identification_bounds": None}
    if entries and estimate and estimate["ci95"] is not None:
        missing_by_day = defaultdict(list)
        for e in missing:
            missing_by_day[e.time_ms // DAY_MS].append(e)
        daily = []
        for day in sorted(set(totals) | set(missing_by_day)):
            unknown = missing_by_day[day]
            known_net_day, known_count_day = totals[day][0]
            daily.append({"utc_day": calendar_day(day), "n_known": int(known_count_day),
                          "n_missing": len(unknown),
                          "worst_net_units": known_net_day - len(unknown),
                          "best_net_units": known_net_day + sum(e.price - 1 for e in unknown)})
        out["daily_net_units_under_missing_score_extremes"] = daily
        out["n_days_strictly_positive_even_if_all_missing_lose"] = sum(
            r["worst_net_units"] > 1e-10 for r in daily)
        known_net = sum(r.net for r in closed)
        out["identification_bounds"] = {
            "lower_all_missing_lose": (known_net - len(missing)) / len(entries),
            "upper_all_missing_win": (known_net + sum(e.price - 1 for e in missing)) / len(entries),
            "scope": "sample limits at quoted price; no sampling uncertainty or fill assumption verified",
        }
    return out


def opening_summary(entries):
    by_game = Counter(e.gid for e in entries)
    return {"n": len(entries), "n_unique_games": len(by_game),
            "n_without_reported_start_anchored_at_opening": sum(
                e.reported_start_s is None for e in entries),
            "n_duplicate_rows_beyond_first_per_game": sum(count-1 for count in by_game.values()),
            "stage_counts": dict(sorted(Counter(e.sets_done for e in entries).items())),
            "league_counts": dict(sorted(Counter(e.league for e in entries).items())),
            "opening_days": {calendar_day(k): n for k, n in sorted(Counter(
                e.time_ms // DAY_MS for e in entries).items())},
            "mean_decimal_price": sum(e.price for e in entries)/len(entries) if entries else None,
            "min_decimal_price": min((e.price for e in entries), default=None),
            "max_decimal_price": max((e.price for e in entries), default=None)}


def run(args):
    split = json.loads(args.split.read_text())
    window = parse_window(split)
    excluded = {int(gid) for gid in split.get("exclude_game_ids", [])}
    manifest = json.loads(args.manifest.read_text())["ledgers"] if args.manifest else {}
    if args.view == "ledger" and not args.manifest:
        raise ValueError("La auditoría de liquidaciones requiere manifiesto fechado.")
    if args.view == "tape" and not args.tape_terminals:
        raise ValueError("La comparación de cinta necesita el CSV de terminales certificados.")
    terminals, ambiguous = terminal_map(args.tape_terminals, window) if args.view == "tape" else ({}, set())
    if args.draws < 1:
        raise ValueError("Número de remuestreos no válido.")
    result = {"view": args.view, "window_ms_utc_start_inclusive_end_exclusive":
              [window.start_ms, window.end_ms], "seed": window.seed, "draws": args.draws,
              "unit": "filtered policy book row; a game can contribute to multiple books",
              "blocks": "whole UTC days of entry, sampled with replacement; empty days retained",
              "stage_mapping": {"PRE": "ST0", "SET1": "ST3", "ST5": "may include k=1 or k=2"},
              "books": {}}
    all_entries = {}
    all_open_rows = []
    all_closed = []
    available = []
    for book in BOOKS:
        entries, closed, audit = read_book(args.ledger_dir, book, manifest.get(book, {}),
                                           window, args.view, excluded)
        result["books"][book] = {"source": audit}
        if audit["status"] != "read":
            continue
        available.append(book)
        out = result["books"][book]
        all_entries[book] = {e.gid for e in entries}
        all_open_rows.extend(entries)
        out["openings"] = opening_summary(entries)
        if args.view == "tape":
            closed, out["terminal_coverage"] = reconcile_tape(entries, terminals, ambiguous)
            out["missing_outcome_bounds"] = missing_score_bounds(entries, closed, window, args.draws)
            out["coverage_by_league"] = coverage_groups(entries, closed, lambda e: e.league)
            out["coverage_by_utc_opening_day"] = coverage_groups(
                entries, closed, lambda e: calendar_day(e.time_ms // DAY_MS))
        if args.view != "openings":
            out["performance"] = performance(closed, window, args.draws)
            if book == "ST5":
                out["performance_by_sets_done"] = {
                    str(k): performance([r for r in closed if r.entry.sets_done == k], window, args.draws)
                    for k in sorted({e.sets_done for e in entries})
                }
            all_closed += closed
    common = {f"{a}+{b}": len(all_entries[a] & all_entries[b])
              for a, b in itertools.combinations(available, 2)}
    days_by_game = defaultdict(set)
    for entry in all_open_rows:
        days_by_game[entry.gid].add(entry.time_ms // DAY_MS)
    result["opening_overlap"] = {"books_available": available, "pairwise_shared_games": common,
                                 "n_book_rows_available": len(all_open_rows),
                                 "n_distinct_games_available": len(days_by_game),
                                 "n_games_spanning_multiple_utc_opening_days": sum(
                                     len(day_set) > 1 for day_set in days_by_game.values())}
    if len(available) == len(BOOKS):
        result["opening_overlap"]["all_three_shared_games"] = len(set.intersection(*all_entries.values()))
        if args.view != "openings":
            result["portfolio"] = performance(all_closed, window, args.draws)
            if args.view == "tape":
                result["portfolio"]["missing_outcome_bounds"] = missing_score_bounds(
                    all_open_rows, all_closed, window, args.draws)
    else:
        result["portfolio"] = {"status": "incompleto; no verificado"}
    if args.view == "tape":
        result["terminal_file_sha256"] = sha256_file(args.tape_terminals)
        result["settlement_basis"] = "observed complete score, at quoted price. Conditional on score coverage."
        result["n_unique_nonconflicting_terminals"] = len(terminals)
        result["n_conflicting_terminal_games"] = len(ambiguous)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger-dir", type=Path, required=True)
    ap.add_argument("--split", type=Path, required=True)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--view", choices=("openings", "ledger", "tape"), required=True)
    ap.add_argument("--tape-terminals", type=Path)
    ap.add_argument("--draws", type=int, default=BOOTSTRAPS)
    args = ap.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
