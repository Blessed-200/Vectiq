import unittest
import tempfile
import json
from pathlib import Path
import numpy as np

import mechanisms
from policies import (read_rows, fit_policies, evaluate_policies, rate_for, bounds_for_scores,
                      controls_index, run_policy)
from policy_stats import day_draws, holm, forecasts


class PolicyComparisons(unittest.TestCase):
    def setUp(self):
        self.days=["1970-01-02","1970-01-03","1970-01-04"]
        self.window={"start_ms":86400000,"end_ms":4*86400000}
        self.rows=[]
        for i in range(12):
            t=(i%3+1)*86400000 + i*50000
            self.rows.append({"gid":100+i,"ts":t,"day":self.days[i%3],"stage":"discovery","terminal_ms":t+600000,
                              "total":98 if i%2 else 105,"line":100.5,"odds":2.10,"over":1.85,
                              "leg":"ST3","k":1,"league":"Liga","sets":[[11,6]],"partial":[0,0],
                              "final_sets":[[11,6],[11,7],[11,3],[11,5]],"p_cal":.54,"p_struct":.53,
                              "p_market":.47,"p_current":.49,"p_push":0.,"player_deuce":.06+i/1000,
                              "player_volatility":3.2+i/1000,"early_margin":(i%5)-2,"price_prior_ts":t-1000,
                              "prior_price":2.15 if i%2 else 2.05,"center":103.,"winner_home":1.45,"winner_away":2.5,
                              "exec_odds":2.09,"exec_ts":t+3000,"st5_eligible":True})
        self.frozen=fit_policies(self.rows,self.rows)
        self.ids,self.W=day_draws(self.days,1,40)

    def test_holdout_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"one";p.write_text(json.dumps({**self.rows[0],"terminal_ms":4*86400000}))
            with self.assertRaises(ValueError):list(read_rows(p,self.window,"discovery"))

    def test_zero_same_cell_effect(self):
        counts,_,_=controls_index(self.rows,self.rows,self.days)
        perf=run_policy(self.rows,counts,self.frozen,"P1",self.days,self.ids,self.W,True)
        self.assertAlmostEqual(perf["growth_increment"]["blind_under"]["value"],0.,places=9)
        self.assertEqual(perf["growth_increment"]["current"]["value"],0.)
        self.assertEqual(perf["n_control_game_cells_known"],12)

    def test_missing_score_bounds_and_no_imputed_fills(self):
        rows=[*self.rows,{**self.rows[0],"total":None,"terminal_ms":None},
              {**self.rows[0],"exec_ts":None,"exec_odds":None}]
        b=bounds_for_scores(rows,self.frozen,"P1",self.days,self.W,True)
        self.assertEqual(b["n_offered_without_score"],1)
        self.assertEqual(b["n_missing_quote_or_no_valid_later_tick"],1)
        self.assertLess(b["identification_bounds_at_observed_prices"][0],b["complete_case_roi"]["value"])

    def test_no_oracle_push_imputation(self):
        self.assertEqual(rate_for({**self.rows[0],"line":100,"p_push":None},self.frozen,"P2"),0.)
        self.assertGreater(rate_for({**self.rows[0],"line":100.5,"p_push":None},self.frozen,"P2"),0.)
        self.assertEqual(holm({"a":.01,"b":.10}),{"a":.02,"b":.10})

    def test_auc_ties_and_forecast_common_rows(self):
        r=forecasts(self.rows,self.days,self.W)
        self.assertEqual(r["n_common_nonpush"],12)
        self.assertAlmostEqual(r["metrics"]["p_market"]["auc"]["value"],.5)
        self.assertEqual(forecasts([{**self.rows[0],"p_current":None}],self.days,self.W)["n_common_nonpush"],0)

    def test_mechanism_h1_exchangeable_reference(self):
        h=mechanisms.evaluate(self.rows,self.frozen["h5"],self.days,self.W,"discovery")
        # Signed margins are 8 and 4 respectively in S3 and S2.
        self.assertAlmostEqual(h["tests"]["H1"]["estimate"]["value"],4.)
        self.assertEqual(h["tests"]["H3"]["n_raw"],12)


if __name__=="__main__":
    unittest.main()
