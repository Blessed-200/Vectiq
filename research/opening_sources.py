"""Project discovery-only, pre-outcome columns from public mutable SQLite releases.

The retained CSVs are frozen research inputs. No aggregate, final, callback or
identity-of-user data is exposed by this extractor. No source DB is written to disk.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.request import Request, urlopen

from sources import API, ROOT, WEB, get_json, partition, save

BOOKS = ("st0", "st3", "st5")
COLUMNS = ("id", "ts_ms", "game_id", "sets", "side", "line", "price", "ev", "league",
           "stake", "players", "start_ts")
SQL_TYPES = ("INTEGER", "INTEGER", "INTEGER", "TEXT", "TEXT", "REAL", "REAL", "REAL",
             "TEXT", "REAL", "TEXT", "INTEGER")


def project(database, dest, stage):
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(database)
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        def limited_read(action, table, column, db, trigger):
            if action == sqlite3.SQLITE_READ and (table != "bets" or column not in COLUMNS or trigger):
                return sqlite3.SQLITE_DENY
            if action not in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(limited_read)
        rows = con.execute(
            f"SELECT {','.join(COLUMNS)} FROM bets WHERE ts_ms>=? AND ts_ms<? ORDER BY ts_ms,id",
            (stage["start_ms"], stage["end_ms"]))
        with dest.open("w", newline="") as file:
            out = csv.writer(file, lineterminator="\n")
            out.writerow(COLUMNS)
            for row in rows:
                if not stage["start_ms"] <= row[1] < stage["end_ms"]:
                    raise AssertionError("Date firewall error")
                out.writerow(row)
    finally:
        con.close()


def binary(url):
    req = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "Vectiq-offline-audit"})
    with urlopen(req, timeout=45) as result:
        return result.read()


def capture(dest):
    dest.mkdir(parents=True, exist_ok=True)
    meta = get_json(f"{API}/releases/tags/ledger")
    chosen = {a["name"]: a for a in meta["assets"] if a["name"] in {k+".sqlite" for k in BOOKS}}
    src = {}
    stage = partition()["discovery"]
    for name in BOOKS:
        asset = chosen[name+".sqlite"]
        digest = asset["digest"].removeprefix("sha256:")
        for url in (asset["url"], WEB+"/ledger/"+asset["name"]):
            try:
                raw = binary(url)
            except OSError:
                continue
            if hashlib.sha256(raw).hexdigest() == digest:
                break
        else:
            raise RuntimeError("Source changed or unavailable: "+asset["name"])
        output = dest / (name + "_openings.csv")
        project(raw, output, stage)
        src[name] = {"id": asset["id"], "updated_at": asset["updated_at"], "raw_sha256": digest,
                     "projection_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                     "retained_columns": COLUMNS}
    save(dest / "origins.json", src)


def restore(frozen, dest):
    dest.mkdir(parents=True, exist_ok=True)
    origins = json.loads((frozen / "origins.json").read_text())
    discovery = partition()["discovery"]
    for book in BOOKS:
        csv_path = frozen / (book + "_openings.csv")
        if hashlib.sha256(csv_path.read_bytes()).hexdigest() != origins[book]["projection_sha256"]:
            raise ValueError("Projection digest mismatch")
        path = dest / (book + ".sqlite")
        temp = dest / (book + ".tmp")
        if temp.exists():
            raise FileExistsError(temp)
        with sqlite3.connect(temp) as con, csv_path.open(newline="") as file:
            con.execute("CREATE TABLE bets (" + ",".join(k+" "+t for k, t in zip(COLUMNS, SQL_TYPES)) + ")")
            rdr = csv.DictReader(file)
            if tuple(rdr.fieldnames or ()) != COLUMNS:
                raise ValueError("Projection schema mismatch")
            for row in rdr:
                if not discovery["start_ms"] <= int(row["ts_ms"]) < discovery["end_ms"]:
                    raise ValueError("Projection outside discovery")
                con.execute("INSERT INTO bets VALUES ("+",".join("?" for _ in COLUMNS)+")",
                            tuple(None if row[k]=="" else row[k] for k in COLUMNS))
        temp.replace(path)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=["capture", "restore"])
    cli.add_argument("--frozen", type=Path, default=ROOT/"inputs")
    cli.add_argument("--ledger-dir", type=Path, default=Path("/tmp/vectiq_openings"))
    args = cli.parse_args()
    if args.action == "capture":
        capture(args.frozen)
    else:
        restore(args.frozen, args.ledger_dir)
