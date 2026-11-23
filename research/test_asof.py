import unittest
from asof import assert_asof, assert_quote_execution
from sources import FILENAME, discovery_assets, partition


class PointInTimeTest(unittest.TestCase):
    def test_exact_boundary(self):
        self.assertEqual(assert_asof(1000, 30, {"last_point": 970}, [(500, 950)]), 970)

    def test_future_fields_rejected_including_latency_window(self):
        for future in (971, 1001, None):
            with self.subTest(future=future), self.assertRaises(ValueError):
                assert_asof(1000, 30, {"quote": 900, "player_stat": future})

    def test_past_match_with_future_or_unknown_final(self):
        for known in (995, 1020, None):
            with self.subTest(known=known), self.assertRaises(ValueError):
                assert_asof(1000, 30, {"score": 950}, [(100, known)])

    def test_unusable_clocks(self):
        for latency in (-1, None, float("nan"), True):
            with self.subTest(latency=latency), self.assertRaises(ValueError):
                assert_asof(1000, latency, {"score": 800})
        with self.assertRaises(ValueError):
            assert_asof(1000, 0, {})

    def test_executable_quote(self):
        assert_quote_execution(1000, 1010, 980, 1010, 2.13, 1.0)
        for accepted, end, price, stake in ((1020, 1010, 2.13, 1), (990, 1100, 2.13, 1),
                                            (1010, 1100, None, 1), (1010, 1100, 2.13, 0)):
            with self.subTest(accepted=accepted, price=price, stake=stake), self.assertRaises(ValueError):
                assert_quote_execution(1000, accepted, 980, end, price, stake)

    def test_partition_and_download_gate(self):
        window = partition()
        self.assertEqual(window["discovery"]["end_ms"], window["confirmation"]["start_ms"])
        self.assertEqual(window["confirmation"]["end_ms"], window["blind"]["start_ms"])
        for a in discovery_assets():
            self.assertTrue(window["discovery"]["start"] <= FILENAME.fullmatch(a["name"])[1]
                            < window["discovery"]["end_exclusive"])
            self.assertEqual(a["stage"], "discovery")


if __name__ == "__main__":
    unittest.main()
