"""Synthetic exact-quote and terminal checks for the historical alert markers."""
import unittest
from types import SimpleNamespace

from legacy_marker_audit import quote_identity, score
from ledger_audit import DAY_MS, Entry, Window


class HistoricalMarkers(unittest.TestCase):
    def setUp(self):
        self.window = Window(DAY_MS, 4 * DAY_MS, 1)
        self.entry = Entry("st4", 10, 2 * DAY_MS, 10, 2, 2.12, 100.5, "", None)
        quote = SimpleNamespace(scope="match", market="total", selection="under",
                                line=100.5, price=2.12, blocked=False)
        self.state = SimpleNamespace(home="Uno", away="Dos", game_id=10, league="Masters", best_of=7,
                                     start_ts=self.entry.time_ms // 1000 - 300,
                                     sets=[(11, 5), (11, 9)], sets_won=(2, 0), live=True,
                                     quotes=[quote])

    def test_packet_game_and_players_required(self):
        flag, identity = quote_identity(self.entry, [("item", self.state)], self.window)
        self.assertEqual(flag, "exact_price_and_stage")
        self.assertEqual(identity, (("dos", "uno"), "Masters"))
        self.state.game_id = 11
        self.assertIsNone(quote_identity(self.entry, [("item", self.state)], self.window)[1])

    def test_unconfirmed_best_of_and_blocked_quote_excluded(self):
        self.state.quotes[0].blocked = True
        self.assertIsNone(quote_identity(self.entry, [("item", self.state)], self.window)[1])
        self.state.quotes[0].blocked = False
        self.state.best_of = None
        self.assertIsNone(quote_identity(self.entry, [("item", self.state)], self.window)[1])

    def test_terminal_must_be_after_marker(self):
        with self.assertRaises(ValueError):
            score(self.entry, (100, self.entry.time_ms, "Masters", ("dos", "uno")))


if __name__ == "__main__":
    unittest.main()
