# VECTIQ

Real-time odds capture, pricing and signal engine for table tennis.

The system has one job that everything else depends on: **record the bookmaker's
complete state — scores, every market, every price change — losslessly, from the
first day.** Pricing and alerts are layers on top of that record, and every future
hypothesis can be tested against it without re-collecting anything.

```
Betwinner LiveFeed ──► client ──► raw tape (lossless, delta-encoded, zstd)
                                   │
                                   ├─► tables (Parquet: states, quotes, stats)   reproducible, derived
                                   └─► engine ──► paper ledger ──► Telegram       set-break signals
```

## Layout

| Path | Responsibility |
|---|---|
| `src/vectiq/feed/client.py` | HTTP client: rate limit, jitter, `Retry-After`, circuit breaker, mirror failover |
| `src/vectiq/feed/schema.py` | Typed parsing of `Get1x2_VZip` and `GetGameZip` |
| `src/vectiq/feed/scheduler.py` | Adaptive detail polling (3 s at set breaks, 15 s mid-set, 60 s pre-match) |
| `src/vectiq/store/tape.py` | Raw tape: single source of truth |
| `src/vectiq/store/tables.py` | Parquet derivation from the tape |
| `src/vectiq/pricing/` | Exact set-level Markov pricer and hierarchical match pricer |
| `src/vectiq/signals/` | Guarded signal engine and SQLite paper ledger |
| `src/vectiq/notify/telegram.py` | Message formatting and delivery |
| `.github/workflows/` | `collector` (24/7), `probe` (reachability), `ci` |

## First steps (no terminal needed)

1. Add repository secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (already created).
2. Actions → **probe** → Run workflow. Read the JSON in the job summary: it tells you whether
   GitHub's network can reach the feed. If it cannot, see `docs/RUNBOOK.md` (VPS path, ~5 USD/month or free tier).
3. Actions → **collector** → Run workflow. From then on it self-queues every 15 minutes.
4. Raw shards appear as release assets (`tape-YYYY-MM-DD`); the paper ledger lives in release `ledger`.

## Local

```
pip install -e ".[dev]" && python -m pytest -q
python -m vectiq probe
python -m vectiq collect --minutes 30 --out data
python -m vectiq tables --tape data/tape --out data/tables
```

Documentation: `docs/ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, `docs/RUNBOOK.md`, `docs/HYPOTHESES.md`.
