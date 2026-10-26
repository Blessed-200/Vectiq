# Masters (best-of-7) — Under on total points

Status: **shadow test** (alerts to Telegram, paper ledger, no real money). Evidence window: tape 22-26 Oct 2026.

## The finding
Betwinner's total-points line for Masters matches sits ~6 points above what is played
(mean line ≈ 109.6, mean total ≈ 103). Over loses, Under wins. Not found in best-of-5 leagues (Liga Pro, Setka, TT Cup).

## What the evidence really says (read this before trusting any number)
* First pass (only matches whose final score was seen): Under ROI +12.8 %, z > 4. **This was survivorship bias.**
  ~16 % of matches leave the feed without a final score and they are mostly long ones (98 of 263 were last seen at 3-3).
* Corrected (censored matches imputed, book and outcomes both):
  main-line Under ROI **+4.6 % … +8.7 %**; engine (stages 0 and 1, model EV 5-15 %): **+9.9 %, 95 % CI +3.0 … +16.6 %**
  (cluster bootstrap by match), model probability calibrated (0.514 predicted vs 0.516 observed).
* Stage "after set 3" (ROI +3 %, CI crosses 0) and model EV > 15 % (over-optimistic ECDF tails) are switched off.
* Replay of the production code over 24-26 Oct (book warmed only with earlier matches): 1 314 alerts, hit 54.8 %,
  ROI +12.3 %, max drawdown 9.7 %. Optimistic: ~16 % of outcomes are imputed.
* CLV is only ≈ +0.4 %: the bias is a *static* mis-pricing, not a line that the market corrects before kick-off,
  so CLV cannot validate it. Only forward results can.

## How it prices
No player model. `MastersBook` keeps the empirical distribution of points still to be played after k completed sets
(only matches that already ended, plus imputed ones). Every ladder line is priced with it; the Under with the best EV
inside [5 %, 15 %] is the pick. Stake = ½ Kelly on a probability shrunk halfway to the market, capped at 1 % of bankroll,
≤ 2 % per match, scaled by a drawdown brake (½ at -15 %, flat at -25 %). A Wald SPRT mutes the engine when the evidence
says the edge is gone (patient by design; the brake is the fast protection).

## Kill criteria (decide in advance)
Stop if, after 300 settled forward bets, ROI < 0 or the 95 % CI excludes +3 %. Promote to real money only if forward ROI
stays above +5 % with the CI above 0 for 3 weeks. Do not retune thresholds on forward data.

## Execution facts (from live screenshots, 26 Oct)
Live limits ~1 800-2 600 USD, pre-match 150 (Spain) - 310 (Belarus) USD, minimum 0.3 USD. Expect the bookmaker to limit
winning accounts long before the limits matter.
