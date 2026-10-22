# Runbook

## Is the feed reachable from GitHub?
Run the **probe** workflow. Expected: `"ok": true, "status": 200`, a match count and a detail block.
* `403`/`429`/timeouts: the host refuses datacenter networks. Options, in order:
  1. Run the collector on a small VPS (`Dockerfile` included; `docker run -d --restart=always -v vectiq:/app/data vectiq`).
  2. Set secret `BW_PROXY_URL` to a proxy you are entitled to use.
  3. Set repository variable `BW_BASE_URLS` to a comma-separated list of mirrors (client fails over after a cooldown).
* The client never retries aggressively: a 403/429 opens a circuit for 90 s × attempts.

## Terms of use
The feed is an undocumented first-party API. Keep the request rate low (default ≤ 4 req/s, jittered),
identify nothing as someone else, and expect the host to be able to restrict access. Nothing in this
repository places bets; the ledger is simulation only.

## Operations
* Health: a `cycle=…` log line every ~5 min with matches, records written/skipped and HTTP counters.
* Data loss: at most the last ~200 unflushed records on a hard crash; shards are published every 20 min.
* Parser change: edit `schema.py`, run `python -m vectiq tables` on the archived tape; no re-collection needed.
* Tokens: never commit them; they live in repository secrets only.

## Gates before any real-money use (agreed metrics)
Positive CLV over ≥ 1,500 signals; 95 % lower bound of ROI > 0 with N ≥ 2,000; max drawdown < 25 % at quarter-Kelly;
bet acceptance ≥ 90 %. Until the paper ledger passes these, alerts are information, not instructions.
