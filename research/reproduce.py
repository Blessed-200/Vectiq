"""Reproduce Phase 0 in a separate directory and check each output against Git."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(work, cache, jobs):
    work = work.resolve()
    if work == HERE or HERE in work.parents:
        raise ValueError("Use a scratch output directory outside research/")
    work.mkdir(parents=True, exist_ok=True)
    outputs = work / "results"
    outputs.mkdir(exist_ok=True)
    books = work / "openings"
    terminal = work / "discovery_terminals.csv"
    split = HERE / "split.json"
    def script(name, *args, output=None):
        cmd = [sys.executable, str(HERE / name), *map(str, args)]
        if output:
            with output.open("w") as f:
                subprocess.run(cmd, cwd=HERE.parent, stdout=f, check=True)
        else:
            subprocess.run(cmd, cwd=HERE.parent, check=True)
    script("sources.py", "download-discovery", "--cache", cache)
    script("source_facts.py", "--out", outputs / "source_facts.json")
    script("opening_sources.py", "restore", "--ledger-dir", books)
    script("tape_audit.py", "--data-dir", cache, "--out", outputs/"tape_discovery.json",
           "--terminals", terminal, "--jobs", jobs)
    script("prematch_clock_audit.py", "--cache", cache, "--terminals", terminal,
           "--out", outputs/"prematch_clock.json", "--jobs", jobs)
    script("prebook_identity.py", "--cache", cache, "--terminals", terminal,
           "--out", outputs/"prebook_identity.json", "--mapping", outputs/"prebook_identity.csv", "--jobs", jobs)
    for view, name, extra in (("openings","ledger_openings.json",[]),
                              ("tape","ledger_tape_discovery.json",["--tape-terminals",terminal])):
        script("ledger_audit.py", "--ledger-dir", books, "--split", split, "--view", view,
               *extra, output=outputs/name)
    script("entry_trace.py", "audit", "--data-dir", cache, "--ledger-dir", books,
           "--terminals", terminal, "--out", outputs/"entry_trace.json")
    script("legacy_marker_audit.py", "--cache", cache, "--terminals", terminal,
           "--split", split, output=outputs/"legacy_marker_discovery.json")
    script("report_tables.py", "--results", outputs, "--out", work/"00_tablas.md")
    for item in [*sorted(outputs.glob("*.json")), outputs/"prebook_identity.csv", work/"00_tablas.md"]:
        frozen = HERE / item.relative_to(work)
        if digest(frozen) != digest(item):
            raise RuntimeError("Result differs from the archived output: "+item.name)
        print(f"Verified {item.name} {digest(item)}")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--work", type=Path, required=True)
    cli.add_argument("--cache", type=Path, required=True)
    cli.add_argument("--jobs", type=int, default=3)
    args = cli.parse_args()
    if args.jobs < 1:
        cli.error("--jobs must be at least one")
    run(args.work, args.cache.resolve(), args.jobs)
