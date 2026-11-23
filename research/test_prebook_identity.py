import unittest

from prebook_identity import DAY, Identity, Universe, infer


def identity(scheduled=12345, home_id=10, away_id=20, league="Masters. Test", home="Alice", away="Bea"):
    item={"S":scheduled,"L":league,"O1":home,"O2":away,"O1I":home_id,"O2I":away_id,"MIO":{"MaF":"7 sets"}}
    result=Identity()
    result.observe(item,4000)
    return result


class IdentityTests(unittest.TestCase):
    def test_competing_live_id_blocks_even_without_any_terminal_or_bracket(self):
        u=Universe()
        u.pre[1],u.live[2],u.live[3]=identity(),identity(),identity()
        u.catalog.add_center(1,5000,104.5)
        rows,reasons=infer(u,0,DAY)
        self.assertEqual(rows,[])
        self.assertEqual(reasons["key_maps_to_several_live_ids"],1)
        del u.live[3]
        rows,_=infer(u,0,DAY)
        self.assertEqual(rows[0][0:2],(1,2))
        self.assertEqual(rows[0][8],2)
        self.assertEqual(rows[0][9],4000)

    def test_identity_requires_exact_order_and_unchanged_schedule_and_consistent_numeric_ids(self):
        for other, reason in [(identity(home="Bea",away="Alice"),"live_key_not_seen"),
                              (identity(scheduled=12346),"live_key_not_seen"),
                              (identity(home_id=11),"numeric_player_ids_disagree")]:
            u=Universe()
            u.pre[1],u.live[2]=identity(),other
            u.catalog.add_center(1,5000,104.5)
            self.assertEqual(infer(u,0,DAY)[1][reason],1)
        u.live[2]=identity()
        u.live[2].merge(identity(scheduled=12346))
        self.assertEqual(infer(u,0,DAY)[1]["live_missing_or_multiple_schedules"],1)

    def test_pre_identity_without_signal_can_block_other_pre_identity(self):
        u=Universe()
        u.pre[1],u.pre[2],u.live[3]=identity(),identity(),identity()
        u.catalog.add_center(1,5000,104.5)
        self.assertEqual(infer(u,0,DAY)[1]["pre_key_maps_to_several_pre_ids"],1)


if __name__ == "__main__":
    unittest.main()
