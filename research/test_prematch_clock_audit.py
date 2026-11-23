"""Verify the extra restrictions on the pre-match line sensitivity."""
import unittest

from prematch_clock_audit import PreScan, examine


class PreClockTests(unittest.TestCase):
    def quote(self, start, status="", points=(), fmt="7 sets"):
        return {"I":1,"L":"Masters. Test","S":start,"MIO":{"MaF":fmt},
                "SC":{"SLS":status,"FS":{"S1":0,"S2":0},
                      "PS":[{"Key":n+1,"Value":{"S1":a,"S2":b}} for n,(a,b) in enumerate(points)]},
                "E":[{"G":17,"T":9,"P":101.5,"C":1.67},{"G":17,"T":10,"P":101.5,"C":2.32},
                     {"G":17,"T":9,"P":107.5,"C":2.32},{"G":17,"T":10,"P":107.5,"C":1.67}]}

    def test_receipt_before_start_and_not_live(self):
        s=PreScan()
        for q,when in [(self.quote(10),10000),(self.quote(10,"En curso"),9999),
                       (self.quote(None),9999),(self.quote(10,fmt=""),9999),
                       (self.quote(10,points=[(1,0)]),9999)]:
            examine(s,q,1,when)
        self.assertFalse(s.first)
        examine(s,self.quote(10),1,9999)
        self.assertAlmostEqual(s.first[1][1],104.5)
        self.assertEqual(s.first[1][0],9999)

    def test_earliest_qualifying_board_wins(self):
        s=PreScan()
        for t in (3000, 2000, 4000):
            examine(s,self.quote(10),1,t)
        self.assertEqual(s.first[1][0],2000)


if __name__ == "__main__":
    unittest.main()
