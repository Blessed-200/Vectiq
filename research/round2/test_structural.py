import math
import unittest
from copy import deepcopy

import numpy as np

from structural import (Book, CUTS, DAY, LEGS, P, checked_final, fit_beta, kernel, outcome_kernel, posterior, predict_one,
                        predict_rows, run_stage, select_fixed, set_pmf, simulate_leg)


def opportunity(ts,gid=10,leg="ST3",sets=None,partial=(0,0),line=102.5):
    if sets is None:
        sets=() if leg=="ST0" else ((11,5),)
    return dict(gid=gid,ts=ts,league="Masters. Test",home="Alice",away="Bea",k=len(sets),sets=list(sets),
                partial=list(partial),line=line,odds=2.2,over=1.78,leg=leg,signal=True,
                player_home_id=1,player_away_id=2,winner_home=1.6,winner_away=2.3)


def historical(t,gid=11,scores=((11,3),)*4):
    return dict(gid=gid,terminal_ms=t,league="Masters. Test",home="Alice",away="Bea",
                player_home_id=1,player_away_id=2,final_sets=list(scores),total=sum(a+b for a,b in scores))


class Structural(unittest.TestCase):
    def test_probability_law_and_push(self):
        wa,wb=set_pmf(3,(0,0))
        np.testing.assert_allclose((wa+wb).sum(1),1,rtol=1e-12)
        np.testing.assert_allclose(wa,wb[::-1],atol=1e-12)
        for score in ((0,0),(3,1)):
            cdf,win=outcome_kernel(3,1,0,score)
            self.assertTrue(np.all(np.diff(cdf,axis=1)>=-1e-14))
            np.testing.assert_allclose(cdf[:,-1],1)
        row=opportunity(50000,line=100)
        f,_=predict_one(row,Book(),50000)
        upper,_=predict_one({**row,"line":100.5},Book(),50000)
        lower,_=predict_one({**row,"line":99.5},Book(),50000)
        self.assertAlmostEqual(f["p_push"],upper["p_struct"]-lower["p_struct"],places=8)
        self.assertAlmostEqual(f["p_struct_win"],lower["p_struct"],places=8)

    def test_future_perturbation_and_exact_cutoff(self):
        row=opportunity(100000)
        a,b=historical(100000),historical(100000,scores=((2,11),)*4)
        first=predict_rows([row],history=[a])[0]
        second=predict_rows([row],history=[b])[0]
        self.assertEqual(first["p_struct"],second["p_struct"])
        self.assertEqual(first["structural_history_n"],0)
        later=predict_rows([{**row,"ts":100001}],history=[a])[0]
        self.assertEqual(later["structural_history_n"],1)
        with self.assertRaisesRegex(ValueError,"cutoff"):
            predict_rows([row],latency_ms=1)

    def test_partial_price_and_references(self):
        r=opportunity(200000,partial=(0,0))
        f,_=predict_one(r,Book(),200000)
        z,_=predict_one({**r,"partial":[3,0]},Book(),200000)
        self.assertNotEqual(f["p_struct"],z["p_struct"])
        self.assertAlmostEqual(f["p_current"],1/2.2)
        self.assertAlmostEqual(f["p_market"],(1/2.2)/(1/2.2+1/1.78))
        self.assertIsNone(predict_one({**r,"leg":"ST5"},Book(),200000)[0]["p_current"])

    def test_monotone_beta_and_duplicate_output(self):
        lo,hi=CUTS["discovery"]
        r=opportunity(lo+1)
        p,a,checkpoint,_=run_stage("discovery",[r],[{**r,"signal":False}],[historical(lo+100,10)])
        for key in ("p_current","p_market","p_struct","p_cal","p_push"):
            self.assertEqual(p[0][key],a[0][key])
        self.assertEqual(len(checkpoint["beta_history"]["ST3"]),1)
        history={"ST3":[[.2,0],[.8,1]]*30}
        self.assertGreater(fit_beta(history)["ST3"][0],0)
        with self.assertRaisesRegex(ValueError,"invalid"):
            checked_final(((13,8),)*4)

    def test_half_volume_and_oracle(self):
        expected=np.array([.4,.1,.6,.2])
        ix=select_fixed(np.array([0,0,1,1]),expected,half=True)
        np.testing.assert_equal(ix,[True,False,True,False])
        lo,hi=CUTS["discovery"]; rng=np.random.default_rng(42)
        rr=[opportunity(lo+1),opportunity(lo+DAY+1,gid=55)]
        pred=[predict_one(r,Book(),r["ts"],keep_latent=True) for r in rr]
        weights=rng.multinomial(19,np.ones(19)/19,size=200)
        o=simulate_leg([{**r,**a[0]} for r,a in zip(rr,pred)],[a[1] for a in pred],"discovery",rng,2048,weights)
        self.assertEqual(o["policies"]["all_latent_oracle"]["scenarios"],2048)
        self.assertGreater(o["policies"]["all_structural"]["expected_logloss_gap_to_latent"]["value"],0)
        self.assertLess(abs(o["policies"]["all_latent_oracle"]["expected_logloss_gap_to_latent"]["value"]),1e-10)
        self.assertGreaterEqual(o["policies"]["half_roi_latent_oracle"]["conditional_expected_roi"]["value"],
                                o["policies"]["half_roi_structural"]["conditional_expected_roi"]["value"]-1e-10)


if __name__=="__main__":
    unittest.main()
