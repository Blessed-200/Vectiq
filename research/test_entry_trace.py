import unittest
from types import SimpleNamespace as NS
from entry_trace import classify


class QuoteIdentityTest(unittest.TestCase):
    def setUp(self):
        self.bet = NS(book="ST3", gid=4, line=104.5, price=2.11, sets_done=1, league="Masters. España",
                      players="A vs B")
        self.state = NS(game_id=4, home="A", away="B", league=self.bet.league,
                        sets=[(11, 5), (0, 0)], sets_won=(1, 0), live=True,
                        quotes=[NS(market="total", scope="match", selection="under", line=104.5,
                                   price=2.11, blocked=False)])

    def test_matching_ledger_and_packet(self):
        self.assertEqual(classify(self.bet, [("item", self.state)]), "same_visible_under_line_price_stage")

    def test_same_price_wrong_participants(self):
        self.state.home = "C"
        self.assertEqual(classify(self.bet, [("item", self.state)]), "participant_identity_differs")

    def test_same_price_wrong_id(self):
        self.state.game_id = 7
        self.assertEqual(classify(self.bet, [("item", self.state)]), "packet_and_ledger_identity_differ")

    def test_closed_market(self):
        self.state.quotes[0].blocked = True
        self.assertEqual(classify(self.bet, [("item", self.state)]), "different_or_blocked_price")


if __name__ == "__main__":
    unittest.main()
