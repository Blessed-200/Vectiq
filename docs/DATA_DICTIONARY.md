# Data dictionary

## Raw tape — `tape/YYYY/MM/DD/HH.jsonl.zst`
Concatenated zstd frames of JSON lines: `{"t": fetch time (ms, UTC), "k": "item"|"game", "id": game id, "d": raw payload}`.
`item` = one element of `Get1x2_VZip.Value`; `game` = `GetGameZip.Value`. Read with `vectiq.store.tape.iter_tape`.

## `states.parquet`
`t, game_id, league, home, away, best_of, status, live, sets_won_h, sets_won_a, current_set, sets ("11-5;9-11;3-2"),
points_played, server, update_ts (bookmaker U), start_ts`.

## `quotes.parquet` (long format, one row per price per observation)
`t, game_id, scope ("match" | "setN"), group, market, selection, line, price, main, blocked`.
Join to states on `(game_id, t)`. Price movement = successive rows of the same `(game_id, scope, group, selection, line)`.

## `stats.parquet`
`t, game_id, stat, home, away` — live statistics from `GetGameZip` (points on own serve / opponent's serve, serve win %, points won).

## Paper ledger — `paper.sqlite`, table `bets`
`ts_ms, game_id, players, league, sets, side, line, price, p_model, ev, stake, bankroll_before, closing_price, final_total, result, pnl`.
