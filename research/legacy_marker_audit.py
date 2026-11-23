"""Describe the historical PRE and ST4 markers found during attribution in phase zero."""
import argparse
import csv
import json
import math
from statistics import median
from collections import Counter
from dataclasses import replace
from pathlib import Path

from entry_trace import exact_snapshots
from ledger_audit import (Closed, Entry, parse_window, participant_identity, performance,
                          sha256_file, terminal_map)
from sources import ROOT
from tape_audit import opportunity_window
from vectiq.signals.masters import stage_of

STAGES = {"pre": 0, "st4": 2}


def load_markers(folder: Path, window):
    path = folder / "alert_markers.csv"
    if sha256_file(path) != json.loads((folder / "marker_origin.json").read_text())["projection_sha256"]:
        raise ValueError("El hash de los marcadores no coincide.")
    rows, seen = [], set()
    with path.open(newline="") as inp:
        for mark in csv.DictReader(inp):
            t = int(mark["ts_ms"])
            if not window.start_ms <= t < window.end_ms:
                raise ValueError("Marcador fuera de descubrimiento.")
            kind = mark["kind"]
            if kind not in STAGES:
                continue
            gid, line, price = int(mark["game_id"]), float(mark["line"]), float(mark["price"])
            if (kind, gid) in seen or gid <= 0 or not math.isfinite(line) or line <= 0 or not math.isfinite(price) or price <= 1:
                raise ValueError("Marcador ambiguo o con precio/línea inválidos.")
            seen.add((kind, gid))
            # gid is unique within each legacy marker family in this input.
            entry = Entry(kind, gid, t, gid, STAGES[kind], price, line, "", None)
            rows.append((entry, bool(int(mark["has_message_id"]))))
    return rows


def quote_identity(entry, snapshots, window):
    """Require an unambiguous match-stage-price tuple present at the marker time."""
    eligible = [(kind, s) for kind, s in snapshots if kind == "item" or
                (entry.book == "pre" and kind == "line")]
    if not eligible:
        return "no_exact_list_tick", None
    evidence = set()
    for _, state in eligible:
        names = participant_identity([state.home, state.away])
        market = [q for q in state.quotes if q.scope == "match" and q.market == "total" and
                  q.selection == "under" and q.line == entry.line]
        stage = opportunity_window(state)
        if state.game_id != entry.gid or len(names) != 2 or not state.league:
            flag = "ambiguous_or_missing_tick_identity"
        elif state.best_of != 7:
            flag = "tick_without_explicit_bo7"
        elif state.start_ts is not None and not window.start_ms <= state.start_ts * 1000 < window.end_ms:
            flag = "start_outside_discovery"
        elif not stage or stage[0] != STAGES[entry.book]:
            flag = "other_stage"
        elif entry.book == "pre" and stage_of(state, entry.time_ms / 1000) != (0, "prepartido"):
            flag = "outside_legacy_prematch_window"
        elif not any(not q.blocked and abs(q.price-entry.price) < 1e-8 for q in market):
            flag = "exact_line_price_not_shown_unblocked"
        else:
            flag = "exact_price_and_stage"
        evidence.add((flag, names, state.league))
    if len(evidence) != 1:
        return "inconsistent_simultaneous_ticks", None
    flag, names, league = evidence.pop()
    return flag, (names, league) if flag == "exact_price_and_stage" else None


def score(entry, terminal):
    total, time_ms, _, _ = terminal
    if time_ms <= entry.time_ms:
        raise ValueError("Terminal anterior o simultáneo al marcador.")
    grade = "W" if total < entry.line else "L" if total > entry.line else "P"
    unit = {"W": entry.price - 1, "L": -1.0, "P": 0.0}[grade]
    return Closed(entry, unit, grade, time_ms)


def run(cache, folder, split, terminals):
    window = parse_window(split)
    markers = load_markers(folder, window)
    observations, n_assets = exact_snapshots(cache, {(e.time_ms, e.gid) for e, _ in markers})
    finals, ambiguous = terminal_map(terminals, window)
    out = {"design": "Cohorts identified after marker inventory. Descriptive attribution only. "
           "ID-only cohort uses the marker provenance for stage, line, Under and quote. "
           "The strict cohort also requires a recorded message ID, an exact dated list quote, "
           "explicit BO7, a compatible stage and consistent player pair and league at the observed terminal. "
           "Participant names and league are taken from the tick; the marker itself lacks these fields. "
           "No independent bookmaker receipts, complete historical decision replay or account stakes.",
           "seed": window.seed, "bootstrap_draws": split["bootstrap_draws"],
           "assets_read": n_assets, "terminal_file_sha256": sha256_file(terminals),
           "marker_file_sha256": sha256_file(folder / "alert_markers.csv"), "cohorts": {}}
    for kind in STAGES:
        cohort = [(e, sent) for e, sent in markers if e.book == kind]
        all_scored, strict_scored, quotes, coverage = [], [], Counter(), Counter()
        for entry, has_msg in cohort:
            flag, identity = quote_identity(entry, observations[entry.time_ms, entry.gid], window)
            quotes[flag] += 1
            if entry.gid in ambiguous:
                coverage["ambiguous_terminal"] += 1
                continue
            final = finals.get(entry.gid)
            if final is None:
                coverage["no_eligible_terminal"] += 1
                continue
            if final[1] <= entry.time_ms:
                coverage["terminal_at_or_before_marker"] += 1
                continue
            weak = score(entry, final)
            all_scored.append(weak)
            if not has_msg:
                coverage["no_message_id"] += 1
            elif identity is None:
                coverage["no_exact_stage_quote_identity"] += 1
            elif identity != (final[3], final[2]):
                coverage["marker_tick_and_terminal_identity_differ_or_missing"] += 1
            else:
                strict_scored.append(replace(weak, entry=replace(entry, league=final[2])))
                coverage["strict_marker_quote_identity_terminal"] += 1
        out["cohorts"][kind] = {
            "recorded_markers": len(cohort), "marked_with_message_id": sum(sent for _,sent in cohort),
            "line_all_markers_mean": sum(e.line for e, _ in cohort) / len(cohort) if cohort else None,
            "line_strict_mean": sum(r.entry.line for r in strict_scored) / len(strict_scored) if strict_scored else None,
            "line_strict_median": median(r.entry.line for r in strict_scored) if strict_scored else None,
            "coverage_all_markers_mutually_exclusive": dict(coverage),
            "exact_quote_status_all_markers": dict(quotes),
            "marker_game_and_later_terminal": performance(all_scored, window, split["bootstrap_draws"]),
            "marker_message_same_tick_and_terminal": performance(strict_scored, window, split["bootstrap_draws"]),
        }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--terminals", type=Path, required=True)
    parser.add_argument("--split", type=Path, default=ROOT / "split.json")
    parser.add_argument("--frozen", type=Path, default=ROOT / "inputs")
    args = parser.parse_args()
    data = run(args.cache, args.frozen, json.loads(args.split.read_text()), args.terminals)
    print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
