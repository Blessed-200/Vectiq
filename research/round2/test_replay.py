import unittest
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from portfolio import trajectory,bootstrap,bet_list
from policies import read_rows
import numpy as np
class ReplayTests(unittest.TestCase):
    def test_future_predictor_refused(self):
        window={"start_ms":0,"end_ms":10**8}
        for name in ("price_prior_ts","player_features_known_ms"):
            with TemporaryDirectory() as path:
                p=Path(path)/"records";p.write_text(json.dumps({"gid":1,"day":"1970-01-01","ts":5000,"stage":"test",name:5001}))
                with self.assertRaises(ValueError):list(read_rows(p,window,"test"))
    def test_tied_bets_share_match_cap(self):
        b=[(0,1,7,"l",0,1.,float(i-1),"d","ST0") for i in range(3)]
        v,n,limited=trajectory(b,.01)
        self.assertEqual(n,3);self.assertEqual(limited,3);self.assertAlmostEqual(v[-1][1],1.)
    def test_actual_cross_midnight_prior_cash_still_reserved(self):
        b=[(0,10,7,"l",0,1.,-1.,"d","ST0"),(1,11,7,"l",0,1.,-1.,"d","ST3"),(2,12,7,"l",1,1.,-1.,"e","ST5")]
        v,n,limited=trajectory(b,.01)
        self.assertEqual(n,2);self.assertEqual(limited,1);self.assertAlmostEqual(v[-1][1],.98)
    def test_bootstrap_includes_first_drawdown_from_initial(self):
        b=[(0,1,7,"l",0,1.,-1.,"d","ST0")]
        s=bootstrap(b,.01,["d"],np.zeros((2,2),dtype=int))
        self.assertAlmostEqual(s["p95_dd"],1-.99**2)
if __name__=="__main__":unittest.main()
