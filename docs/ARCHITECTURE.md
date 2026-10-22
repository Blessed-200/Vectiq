# Architecture

## Principles

1. **Raw first.** The tape stores the bookmaker's JSON exactly as received. Parquet tables are
   derived and can be rebuilt after any parser change. Nothing is thrown away at capture time.
2. **Delta-encoded.** A record is written only when its content changed (the keepalive field `U` is
   ignored), so volume tracks information, not poll count.
3. **The bookmaker's own score.** Signals use the score from the same payload as the prices. A
   third-party score lags by ~2 minutes and turns stale lines into phantom edges (see audit).
4. **Guards over optimism.** The engine refuses to price formats it was not calibrated on
   (best-of-7 Masters leagues), states without a clean pre-match anchor, and states where the model
   and the market median disagree by more than 6 points (almost always a state mismatch, not value).
5. **Paper and production are separate.** The paper ledger records the price available at signal
   time, settles from the bookmaker's final score and tracks closing-line value.

## Feed (from the real captures)

* `LiveFeed/Get1x2_VZip?sports=10&count=100&lng=es&mode=4` — every live and upcoming table-tennis
  match: score (`SC`), main markets (`E`) and three-line ladders (`AE`). One call, ~40 KB.
* `LiveFeed/GetGameZip?id=<I>&isSubGames=true&GroupEvents=true&countevents=250&grMode=2` — one match:
  full ladders (8+ lines), per-set sub-games (set winner, set total ladder), live stats (points on own
  serve, serve win %). Polled on a budget.
* `fatman-api/.../event.json` is the site's analytics beacon. It carries no sports data and is ignored.

Market group ids: `1` winner, `2` handicap, `17` match total, `15/62` team totals, `4` set total
(inside sub-games). Selection ids: totals `9` over / `10` under; handicap `7` home / `8` away.
`CE=1` marks the line the bookmaker is currently centred on; `B=true` marks a suspended quote.

## Scheduler

One list call every ~3 s (4 req/s ceiling shared with detail calls). Detail polls: 3 s at set
breaks, 6 s when a set is near its end, 15 s mid-set, 60 s for matches starting within 15 min,
300 s otherwise. Budget: 4 detail calls per cycle.

## 24/7 on GitHub Actions

`collector` runs `*/15` with `concurrency: collector` and `cancel-in-progress: false`: exactly one run
is active and one is pending, so when a 5h40m run ends the next starts immediately. Hourly shards are
uploaded every 20 minutes as release assets, and a workflow artifact is kept as a safety net.
Public repositories get unlimited Actions minutes. Scheduled workflows are disabled after 60 days of
repository inactivity; any push re-enables them.

## Pricing

`markov_pricer`: exact set-level DP (serve rotation, deuce rule). `hier_pricer`: match pricer with a
latent strength gap updated by every completed set; sets are not independent. Both are calibrated on
Liga Pro best-of-5 (4.1 M points). Prior strength comes from the **first pre-match winner price**
(devigged), because no rating source remains once the League-Pro API is dropped.
