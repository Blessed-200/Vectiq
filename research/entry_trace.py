"""Match discovery ledger entries to their raw quote and the server-side alert marker."""
import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from ledger_audit import (BOOKS, parse_window, participant_identity, performance, read_book, reconcile_tape,
                          terminal_map)
from opening_sources import binary
from sources import API, ROOT, discovery_assets, get_json, partition, save
from tape_audit import HOUR, opportunity_window, parse_item, records, utc_ms, verify_asset

TICK_HEADER = re.compile(rb'^\{"t":(\d+),"k":"([^"]+)","id":(\d+),')
KINDS = {"bet":"ST3", "st5":"ST5", "st0":"ST0", "pre":"PRE_legacy", "st4":"ST4"}


def capture_markers(folder):
    asset = next(a for a in get_json(API + "/releases/tags/ledger")["assets"] if a["name"] == "st3.sqlite")
    db = binary(asset["url"])
    sha = hashlib.sha256(db).hexdigest()
    if asset["digest"] != "sha256:" + sha:
        raise RuntimeError("Release changed during download")
    allowed = {"game_id", "kind", "ts_ms", "message_id", "line", "price"}
    with sqlite3.connect(":memory:") as con:
        con.deserialize(db)
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        def allow(action, table, col, db, trig):
            if action == sqlite3.SQLITE_READ and table == "alerts" and col in allowed and not trig:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_DENY
        con.set_authorizer(allow)
        d = partition()["discovery"]
        rows = con.execute("SELECT game_id, kind, ts_ms, line, price, message_id IS NOT NULL FROM alerts "
                           "WHERE ts_ms>=? AND ts_ms<? AND kind IN ('bet','st0','st5','pre','st4') ORDER BY ts_ms, game_id, kind",
                           (d["start_ms"], d["end_ms"]))
        path = folder / "alert_markers.csv"
        with path.open("w",newline="") as f:
            writer = csv.writer(f,lineterminator="\n")
            writer.writerow(("game_id","kind","ts_ms","line","price","has_message_id"))
            writer.writerows(rows)
    save(folder/"marker_origin.json",dict(id=asset["id"],updated_at=asset["updated_at"],
         raw_sha256=sha,projection_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def entries_in_window(directory):
    win = parse_window(partition())
    by_key = defaultdict(list)
    for book in BOOKS:
        bets, _, _ = read_book(directory, book, {}, win, "openings", set())
        for bet in bets:
            by_key[(bet.time_ms, bet.gid)].append(bet)
    return by_key


def classify(bet, snapshots):
    eligible = [s for kind, s in snapshots if kind == "item" or (bet.book == "ST0" and kind == "line")]
    if not eligible:
        return "no_exact_list_tick"
    observed = set()
    for state in eligible:
        quotes = [q for q in state.quotes if q.market == "total" and q.scope == "match" and
                  q.selection == "under" and q.line == bet.line]
        prices = {(q.price,q.blocked) for q in quotes}
        window = opportunity_window(state)
        if state.game_id != bet.gid:
            flag = "packet_and_ledger_identity_differ"
        elif state.home and state.away and participant_identity([state.home, state.away]) != participant_identity(bet.players.split(" vs ")):
            flag = "participant_identity_differs"
        elif state.league != bet.league:
            flag = "league_differs"
        elif not quotes:
            flag = "under_line_missing"
        elif not any(abs(p-bet.price)<1e-8 and not blocked for p,blocked in prices):
            flag = "different_or_blocked_price"
        elif not window or window[0] != bet.sets_done:
            flag = "price_matches_stage_unconfirmed"
        else:
            flag = "same_visible_under_line_price_stage"
        observed.add(flag)
    return next(iter(observed)) if len(observed)==1 else "ambiguous_simultaneous_tick"


def exact_snapshots(cache, by_key):
    """Return only discovery list ticks matching the requested reception time and game ID."""
    needed_hours = {t//HOUR for t,_ in by_key}
    observations = defaultdict(list)
    assets_read = 0
    for asset in discovery_assets():
        hour = (utc_ms(asset["day"])//HOUR) + int(asset["name"][11:13])
        if hour not in needed_hours:
            continue
        path = cache/asset["name"]
        verify_asset(path,asset)
        assets_read += 1
        for raw in records(path):
            header = TICK_HEADER.match(raw)
            if header:
                t, gid = int(header[1]), int(header[3])
                kind = header[2].decode()
                if t//HOUR != hour:
                    raise ValueError("Wrong source clock")
                if (t,gid) not in by_key or kind not in ("item","line"):
                    continue
            row = json.loads(raw)
            t, gid, kind = row["t"], row["id"], row["k"]
            if t//HOUR != hour:
                raise ValueError("Wrong source clock")
            if (t,gid) in by_key and kind in ("item","line"):
                observations[t,gid].append((kind,parse_item(row["d"],t)))
    return observations, assets_read


def audit(cache, ledger_dir, frozen, terminal_file):
    by_key = entries_in_window(ledger_dir)
    observations, assets_read = exact_snapshots(cache, by_key)
    markerfile = frozen/"alert_markers.csv"
    if hashlib.sha256(markerfile.read_bytes()).hexdigest() != json.loads((frozen/"marker_origin.json").read_text())["projection_sha256"]:
        raise ValueError("Marker digest mismatch")
    markers = {}
    with markerfile.open() as source:
        for m in csv.DictReader(source):
            key = (KINDS[m["kind"]],int(m["game_id"]))
            if key in markers:
                raise ValueError("Ambiguous alert marker")
            markers[key] = m
    results = {book:{"recorded":0,"quote":Counter(),"alert":Counter(),"both_quote_and_exact_alert":0} for book in BOOKS}
    used = set()
    chain = defaultdict(list)
    for (t,gid), entries in by_key.items():
        for bet in entries:
            out = results[bet.book]
            out["recorded"] += 1
            flag = classify(bet,observations[t,gid])
            out["quote"][flag] += 1
            key = (bet.book,gid)
            marker = markers.get(key)
            if marker:
                used.add(key)
            if not marker:
                match = "no_matching_game_and_leg_marker"
            elif (int(marker["ts_ms"]) != t or float(marker["line"]) != bet.line
                  or abs(float(marker["price"])-bet.price)>1e-8):
                match = "different_time_line_or_price"
            else:
                match = "same_marker_with_message_id" if int(marker["has_message_id"]) else "same_marker_without_message_id"
                if flag == "same_visible_under_line_price_stage":
                    out["both_quote_and_exact_alert"] += 1
                    if int(marker["has_message_id"]):
                        chain[bet.book].append(bet)
            out["alert"][match] += 1
    window = parse_window(partition())
    terminals, ambiguities = terminal_map(terminal_file, window)
    retention = {}
    for book, entries in [*( (k, chain[k]) for k in BOOKS), ("portfolio",sum((chain[k] for k in BOOKS), []))]:
        closed, coverage = reconcile_tape(entries, terminals, ambiguities)
        retention[book] = {"exact_quote_marker_with_id_n":len(entries), "terminal_coverage":coverage,
                           "performance_at_displayed_odds":performance(closed, window, partition()["bootstrap_draws"])}
    return dict(discovery_only=True,seed=partition()["seed"],books=results,shards_read=assets_read,
                linked_quote_marker_terminal=retention,
                other_registered_markers_by_leg=dict(Counter(k for k,_ in set(markers)-used)),
                definition="Equality at local feed receipt and alert marker. Message id is evidence of recording; account fills and actual end-user delivery unavailable.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action",choices=["capture-markers","audit"])
    cli.add_argument("--frozen",type=Path,default=ROOT/"inputs")
    cli.add_argument("--data-dir",type=Path,default=Path("/tmp/vectiq_discovery_tape"))
    cli.add_argument("--ledger-dir",type=Path,default=Path("/tmp/vectiq_openings"))
    cli.add_argument("--out",type=Path,default=ROOT/"results"/"entry_trace.json")
    cli.add_argument("--terminals",type=Path,default=Path("/tmp/vectiq_discovery_tape_terminals.csv"))
    a=cli.parse_args()
    if a.action=="capture-markers":
        capture_markers(a.frozen)
    else:
        save(a.out,audit(a.data_dir,a.ledger_dir,a.frozen,a.terminals))
