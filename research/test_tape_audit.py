"""Adversarial checks for the discovery-only displayed-price audit."""
import unittest
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from tape_audit import (
    DAY, HOUR, PRIOR_MS, DayScan, Pick, center_bias, estimate, legal_final, observe_record,
    opportunity_window, tasks, write_terminals
)
from vectiq.feed.schema import parse_item


def raw(t, gid=1, points=(), wins=(0, 0), price=2.20, bo7=True):
    item = {
        "I":gid, "L":"Masters. Test", "S":(t // DAY * DAY + HOUR) // 1000,
        "O1":"A", "O2":"B", "O1I":1, "O2I":2,
        "SC":{"SLS":"En curso","PS":[{"Key":i + 1,"Value":{"S1":a,"S2":b}} for i,(a,b) in enumerate(points)],
              "FS":{"S1":wins[0],"S2":wins[1]}},
        "MIO":{"MaF":"7 sets" if bo7 else ""}, "E":[]
    }
    for line in (102.5, 104.5, 106.5):
        item["E"].extend([{"G":17,"T":9,"P":line,"C":1.7},{"G":17,"T":10,"P":line,"C":price}])
    return {"k":"item","id":gid,"t":t,"d":item}


class TestTape(unittest.TestCase):
    def test_first_paper_precedes_channel_filter_and_previous_rating_ambiguity(self):
        d = DayScan()
        now = DAY * 20000
        times = defaultdict(set)
        # Reverse receipt order on purpose to emulate overlapping collectors.
        for t, score, wins, p in [(now+2,((11,4),),(1,0),2.25),(now+1,((11,4),),(1,0),2.04),
                                  (now+2,(),(0,0),2.25),(now+1,(),(0,0),2.15)]:
            observe_record(d,raw(t,points=score,wins=wins,price=p),times)
        self.assertEqual(d.candidates["ST3",1].status,"first_paper_below_alert_price")
        self.assertEqual(d.candidates["ST0",1].status,"PRE_rating_unknown")

    def test_concurrent_boards_without_canonical_order(self):
        d=DayScan()
        p=Pick(DAY,1,"league",105.5,2.15,"shown")
        d.first_pick("ST3",1,p)
        d.first_pick("ST3",1,p)
        self.assertEqual(d.candidates["ST3",1].status,"shown")
        d.first_pick("ST3",1,Pick(DAY,1,"league",104.5,2.17,"shown"))
        self.assertEqual(d.candidates["ST3",1].status,"ambiguous_simultaneous_first_quote")
        earlier=Pick(DAY-1,1,"league",101.5,2.21,"shown")
        d.first_pick("ST3",1,earlier)
        self.assertEqual(d.candidates["ST3",1],earlier)

    def test_no_accidentally_completed_match(self):
        t = DAY * 20000
        def state(sets, fs):
            return parse_item(raw(t,points=sets,wins=fs)["d"],t)
        correct = ((11,0),(9,11),(11,6),(12,10),(11,2))
        self.assertEqual(legal_final(state(correct,(4,1))), correct)
        self.assertIsNone(legal_final(state(((11,4),)*4 + ((4,11),),(4,1))))
        self.assertIsNone(legal_final(state(((13,9),)*4,(4,0))))
        self.assertIsNone(legal_final(state(correct,(3,2))))
        self.assertIsNone(opportunity_window(state(((11,3),(4,1)),(1,0))))
        self.assertIsNone(opportunity_window(state(((11,3),),(0,0))))

    def test_conflicts_and_preceding_period_never_become_terminal(self):
        lo=DAY*20000
        d=DayScan()
        for gid,starts,seqs in [(1,lo+3,[(11,1)]),(2,lo+4,[(11,2),(11,3)]),(3,lo-2,[(11,1)])]:
            for pair in seqs:
                r=raw(lo+HOUR,gid,(pair,)*4,(4,0))
                r["d"]["S"]=starts//1000
                observe_record(d,r,defaultdict(set))
        with TemporaryDirectory() as tmp:
            reason, final = write_terminals(d,lo,lo+DAY,Path(tmp)/"term.csv")
        self.assertEqual(set(final),{1})
        self.assertEqual(reason[2],"conflicting_terminal_scores")
        self.assertEqual(reason[3],"start_outside_discovery")

    def test_market_center_and_unknown_format(self):
        t = DAY * 20000
        d = DayScan()
        for gid, tick in [(11,t + HOUR),(12,t + DAY + HOUR)]:
            r = raw(tick,gid)
            # Balanced probability at the middle rung, flanked by both sides.
            for q in r["d"]["E"]:
                if q["P"] == 104.5:
                    q["C"] = 2
                elif q["P"] == 106.5:
                    q["C"] = 1.5 if q["T"] == 10 else 2.4
            observe_record(d,r,defaultdict(set))
        out = center_bias(d,{11:(100,t+HOUR+10),12:(109,t+DAY+HOUR+10)},np.array([[2,0],[1,1],[0,2]]),t)
        self.assertAlmostEqual(out["k0"]["mean_center"]["value"],104.5)
        self.assertAlmostEqual(out["k0"]["center_minus_total"]["value"],0.0)
        self.assertEqual(out["k0"]["fraction_below_center"]["value"],.5)
        d2=DayScan()
        observe_record(d2,raw(t+HOUR,6,((11,3),)*4,(4,0),bo7=False),defaultdict(set))
        reason,_ = write_terminals(d2,t,t+DAY,None)
        self.assertEqual(reason[6],"unverified_best_of_7")

    def test_blind_input_not_opened(self):
        split={"discovery":{"start_ms":0,"end_ms":DAY}}
        source={"assets":[{"stage":"blind","name":"/never-read"}]}
        with self.assertRaisesRegex(ValueError,"no verified discovery input available"):
            tasks(source,split,Path("/does-not-exist"))
        source["assets"].append({"stage":"discovery","name":"1970-01-02_00.jsonl.zst","day":"1970-01-02","sha256":"0"*64})
        with self.assertRaisesRegex(ValueError,"temporal/hash"):
            tasks(source,split,Path("/does-not-exist"))

    def test_asof_failure_propagates(self):
        with self.assertRaisesRegex(RuntimeError,"as-of"):
            DayScan().first_pick("ST5",1,Pick(PRIOR_MS-1,1,"x",106.5,2.2,"shown"))

    def test_pooled_control_keeps_engine_separate(self):
        s0=Pick(1,1,"league",100.5,2.2,"shown","ST3")
        s1=Pick(DAY+1,1,"league",100.5,2.2,"shown","ST5")
        selected=[(1,s0,98),(2,s1,103)]
        controls=[(1,s0,98),(2,s1,103),(3,s1,106),(4,s1,112)]
        result=estimate(selected,controls,np.array([[2,0],[1,1],[0,2]]),0)
        self.assertEqual(result["exact_cells"],2)
        self.assertAlmostEqual(result["win_rate"]["matched_blind_under"]["value"],.5)
        self.assertAlmostEqual(result["win_rate"]["increment"]["value"],0)

    def test_inclusive_matched_control_does_not_erase_real_denominators(self):
        p1,p2=Pick(1,1,"x",100.5,2.10,"shown"),Pick(DAY+1,1,"x",100.5,2.10,"shown")
        sig=[(1,p1,99),(2,p2,102)]
        ctrl=sig+[(3,p1,102),(4,p2,102)]
        result=estimate(sig,ctrl,np.array([[2,0],[1,1],[0,2]]),0)
        self.assertEqual(result["control_rows_also_signals"],2)
        self.assertEqual(result["unit_roi"]["control_n"],4)
        self.assertAlmostEqual(result["win_rate"]["increment"]["value"],.25)
        self.assertAlmostEqual(result["unit_roi"]["increment"]["value"],.525)


if __name__ == "__main__":
    unittest.main()
