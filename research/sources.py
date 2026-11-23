"""Freeze GitHub release metadata and fetch only the registered discovery shards."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com/repos/Blessed-200/Vectiq"
WEB = "https://github.com/Blessed-200/Vectiq/releases/download"
FIRST = date(2026, 10, 23)
STOP = date(2026, 11, 23)
BASE = "b706336b3c093daf3338ec501e20c8f27657b185"
STAGES = ("discovery", "confirmation", "blind")
FILENAME = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})(?:_r\d+)?\.jsonl\.zst")


def midnight(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) * 1000


def partition():
    n = (STOP - FIRST).days
    # Nearest integer cumulative cuts, halves rounded upward.
    offsets = (0, (6*n + 5)//10, (8*n + 5)//10, n)
    s = {"timezone": "UTC", "day_start_hour": 0, "seed": 20261122,
         "source_commit": BASE, "bootstrap_draws": 10000}
    for stage, a, b in zip(STAGES, offsets, offsets[1:]):
        start, end = FIRST + timedelta(days=a), FIRST + timedelta(days=b)
        s[stage] = dict(start_ms=midnight(start), end_ms=midnight(end),
                        start=start.isoformat(), end_exclusive=end.isoformat(), days=b-a)
    return s


def get_json(url):
    with urlopen(Request(url, headers={"User-Agent": "Vectiq-offline-audit"}), timeout=60) as response:
        return json.load(response)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def index():
    split = partition()
    assets = []
    page = 1
    while True:
        releases = get_json(f"{API}/releases?per_page=100&page={page}")
        for release in releases:
            tag = release["tag_name"]
            if not tag.startswith("tape-"):
                continue
            day = date.fromisoformat(tag[5:])
            if not FIRST <= day < STOP:
                continue
            stage = next(k for k in STAGES if split[k]["start_ms"] <= midnight(day) < split[k]["end_ms"])
            for asset in release["assets"]:
                match = FILENAME.fullmatch(asset["name"])
                if not match or match[1] != day.isoformat() or int(match[2]) > 23:
                    raise ValueError(f"Unexpected shard name in {tag}")
                digest = asset.get("digest") or ""
                if not re.fullmatch("sha256:[a-f0-9]{64}", digest):
                    raise ValueError(f"Missing digest for {asset['name']}")
                assets.append(dict(day=day.isoformat(), stage=stage, name=asset["name"], id=asset["id"],
                                   size=asset["size"], sha256=digest.split(":")[1]))
        if len(releases) < 100:
            break
        page += 1
    assets.sort(key=lambda a: a["name"])
    if len({a["name"] for a in assets}) != len(assets):
        raise ValueError("Duplicate release shard")
    save(ROOT / "split.json", split)
    save(ROOT / "sources.json", {"repository": "Blessed-200/Vectiq", "assets": assets})


def discovery_assets():
    assets = json.loads((ROOT / "sources.json").read_text())["assets"]
    split = partition()["discovery"]
    for asset in assets:
        m = FILENAME.fullmatch(asset["name"])
        if (asset["stage"] == "discovery" and m and m[1] == asset["day"]
                and split["start"] <= m[1] < split["end_exclusive"]):
            yield asset
        elif asset["stage"] == "discovery":
            raise ValueError("Shard outside discovery")


def fetch_one(asset, folder):
    path = folder / asset["name"]
    def verified(p):
        if not p.is_file() or p.stat().st_size != asset["size"]:
            return False
        with p.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest() == asset["sha256"]
    if verified(path):
        return
    tmp = path.with_suffix(".part")
    urls = [f"{WEB}/tape-{asset['day']}/{asset['name']}", f"{API}/releases/assets/{asset['id']}"]
    for attempt in range(5):
        try:
            req = Request(urls[attempt % 2], headers={"Accept":"application/octet-stream","User-Agent":"Vectiq-offline-audit"})
            with urlopen(req, timeout=100) as response, tmp.open("wb") as out:
                shutil.copyfileobj(response, out)
            if not verified(tmp):
                raise OSError("Digest mismatch")
            tmp.replace(path)
            return
        except (OSError, TimeoutError) as exc:
            tmp.unlink(missing_ok=True)
            if attempt == 4:
                # Do not expose signed redirect URLs in exception messages.
                raise RuntimeError(f"Unavailable or hash mismatch: {asset['name']}") from None
            time.sleep(0.25 * 2**attempt)


def catalog():
    assets = json.loads((ROOT / "sources.json").read_text())["assets"]
    out = {}
    for name in STAGES:
        rows = [a for a in assets if a["stage"] == name]
        hours = {a["name"][:13] for a in rows}
        expected = 24 * partition()[name]["days"]
        out[name] = dict(shards=len(rows), total_bytes=sum(a["size"] for a in rows), utc_hour_slots=len(hours),
                         expected_hours=expected, utc_days_with_assets=len({a["day"] for a in rows}),
                         utc_days_in_split=partition()[name]["days"], absent_hour_slots=expected-len(hours))
    return out


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=["index", "download-discovery", "catalog"])
    cli.add_argument("--cache", type=Path)
    cli.add_argument("--workers", type=int, default=12)
    args = cli.parse_args()
    if args.action == "index":
        index()
    elif args.action == "catalog":
        print(json.dumps(catalog(), indent=2))
    else:
        if args.cache is None:
            cli.error("--cache required")
        args.cache.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(lambda a: fetch_one(a, args.cache), discovery_assets()))
        print("SHA-256 verified for every discovery shard.")
