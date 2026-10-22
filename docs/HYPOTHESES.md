# Hypotheses this dataset makes testable (none assumed true)

1. **Score lag / stale lines.** Time from a set ending (score change) to ladder re-centring, per league. Needs: `states` + `quotes` at 3 s.
2. **Overround dynamics.** Margin by time-to-start, league and set state; does it widen when a match is hot?
3. **Cross-market consistency.** Winner price vs handicap vs total vs set markets for the same game: implied-strength disagreement (arbitrage-free check).
4. **Set-total ladder vs exact pricer.** Per-set ladders (8+ lines) against `markov_pricer`; mid-set pricing at any score.
5. **Pre-match movement.** Opening → closing line drift for matches in the 15-minute window; closing line as the benchmark for CLV.
6. **Serve statistics as signal.** Live "points on own serve" and serve win % (in `stats`) vs the 52.86 % i.i.d. baseline.
7. **League-specific calibration.** Best-of-7 Masters leagues (Russia, Belarus, Poland, Spain, Argentina): fit κ, τ from the tape itself.
8. **Suspension behaviour.** When quotes are blocked (`B`), for how long, and what the line does on reopening.
9. **Liquidity proxy.** Which markets keep the tightest margins and widest ladders (stake capacity before the line moves).
