"""Live check of the results service through the same client/proxy the collector uses (no secrets written)."""
import json, os, time
from vectiq.feed.client import BetwinnerClient
from vectiq.feed.results import day_window, parse_results

bases = [b for b in (os.environ.get("BW_BASE_URLS") or "https://betwinner2.com").split(",") if b]
c = BetwinnerClient(bases, max_rps=2, proxy=os.environ.get("BW_PROXY_URL") or None)
day = int(time.time()) - 86400
d0, d1 = day_window(day)
out = []
for champ in (2685660, 2685678, 109703, 270):      # Masters Spain / Poland, Germany Bundesliga, a TT cup
    r = c.results(champ, d0, d1)
    n = len(parse_results(r.payload)) if r.ok and r.payload else 0
    out.append(dict(champ=champ, day=d0, ok=r.ok, status=r.status, items=(r.payload or {}).get("count"), valid=n, error=r.error))
json.dump(out, open("results_check.json", "w"))
print(out)
