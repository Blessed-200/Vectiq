"""Regressions for holdout isolation and causal terminal matching; synthetic inputs only."""
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from . import ledger_audit as audit
except ImportError:
    import ledger_audit as audit


class DiscoverySafety(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.directory = Path(self.work.name)
        self.window = audit.Window(audit.DAY_MS, 4*audit.DAY_MS, 20261122)
        self.sqlite = self.directory / "st3.sqlite"
        with sqlite3.connect(self.sqlite) as conn:
            # An openings projection has no outcome columns. Admitting them breaks this test.
            conn.execute("""CREATE TABLE bets(
                id INTEGER PRIMARY KEY, ts_ms INTEGER, game_id INTEGER, sets TEXT, side TEXT,
                line REAL, price REAL, ev REAL, league TEXT, stake REAL, players TEXT)""")
            rows = [(1, self.window.start_ms, 2.12),
                    (2, self.window.end_ms - 1, 2.20),
                    (3, self.window.end_ms, 2.25),
                    (4, self.window.start_ms + 600, 2.09)]
            conn.executemany("""INSERT INTO bets VALUES(?,?,?,'11-6','under',100.5,?,.17,
                                'Masters. Rusia',2,'Uno vs Dos')""",
                             [(i, t, i, p) for i, t, p in rows])

    def read(self, view="openings", spec=None):
        return audit.read_book(self.directory, "ST3", spec or {}, self.window, view, set())

    def test_projection_excludes_next_stage_and_has_no_outcome_fields(self):
        entries, outcomes, diag = self.read()
        self.assertEqual([r.gid for r in entries], [1, 2])
        self.assertEqual(outcomes, [])
        self.assertFalse(diag["outcomes_projected"])
        self.assertEqual(diag["counts"]["outside_panel_filter"], 1)

    def test_outcomes_blocked_if_snapshot_postdates_cut(self):
        spec = {"as_of_ms": self.window.end_ms, "sha256": audit.sha256_file(self.sqlite)}
        entries, outcomes, diag = self.read("ledger", spec)
        self.assertEqual(entries + outcomes, [])
        self.assertEqual(diag["status"], "blocked_without_dated_snapshot")

    def test_terminal_at_or_before_quote_and_conflict_excluded(self):
        entries, _, _ = self.read()
        names = ("dos", "uno")
        terminals = {1: (92, entries[0].time_ms, entries[0].league, names),
                     2: (98, entries[1].time_ms, entries[1].league, names)}
        closed, reasons = audit.reconcile_tape(entries, terminals, {2})
        self.assertEqual(closed, [])
        self.assertEqual(reasons, {"terminal_at_or_before_alert": 1, "ambiguous_terminal": 1})

    def test_player_identity_swapped_order_and_mismatch(self):
        entries, _, _ = self.read()
        entry = entries[0]
        common = (95, entry.time_ms + 1, entry.league)
        closed, reasons = audit.reconcile_tape([entry], {1: common + (("dos", "uno"),)}, set())
        self.assertEqual([r.result for r in closed], ["W"])
        closed, reasons = audit.reconcile_tape([entry], {1: common + (("otra", "uno"),)}, set())
        self.assertEqual(reasons, {"player_pair_mismatch": 1})

    def test_terminal_at_holdout_boundary_is_rejected(self):
        path = self.directory / "synthetic.csv"
        with path.open("w", newline="") as stream:
            w = csv.writer(stream)
            w.writerow(["game_id", "final_total", "terminal_ms", "league"])
            w.writerow([1, 88, self.window.end_ms, "Liga"])
        with self.assertRaises(ValueError):
            audit.terminal_map(path, self.window)

    def test_missing_outcome_bounds_pay_each_missing_price_and_include_pushes(self):
        entries, _, _ = self.read()
        first, second = entries
        extra = audit.Entry("ST5", 20, first.time_ms + 2, 35, 2, 3., 105., "Liga", 2.)
        outcomes = [audit.Closed(first, first.price-1, "W", None),
                    audit.Closed(second, 0, "P", None)]
        result = audit.missing_score_bounds(entries + [extra], outcomes, self.window, 100)
        self.assertEqual(result["n_without_valid_final"], 1)
        expected = first.price - 1
        self.assertAlmostEqual(result["identification_bounds"]["lower_all_missing_lose"],
                               (expected - 1) / 3)
        self.assertAlmostEqual(result["identification_bounds"]["upper_all_missing_win"],
                               (expected + 2) / 3)
        self.assertAlmostEqual(result["complete_case_roi_with_day_ci95"]["estimate"], expected / 2)

    def test_day_ratio_uses_joint_denominators(self):
        # Changing n within a day must change the pooled estimate.
        result = audit.clustered_ratios({1: [(1., 1.)], 2: [(0., 9.)]}, [1, 2], 1, draws=10)
        self.assertEqual(result[0]["estimate"], .1)
        self.assertEqual(audit.clustered_ratios({1: [(1., 1.)]}, [1, 2], 1, draws=10)[0]["ci95"], None)


if __name__ == "__main__":
    unittest.main()
