from tests.test_st3 import _s
from vectiq.signals import st4


def test_two_zero_blowout_bets_lowest_rung():
    al = st4.evaluate(_s([(11, 5), (11, 4)]))            # same winner, margins 6+7 = 13
    assert al and al.line == 100.5 and al.price == 2.15 and al.sets == [(11, 5), (11, 4)]
    assert st4.signal_from(al, st4.STAKE_FRAC).side == "under"


def test_needs_same_winner_and_margin():
    assert st4.evaluate(_s([(11, 5), (4, 11)])) is None   # 1-1: no
    assert st4.evaluate(_s([(11, 8), (11, 8)])) is None   # margins 3+3 = 6 < 8
    assert st4.evaluate(_s([(11, 7), (11, 7)])) is not None  # 4+4 = 8 is the threshold
    assert st4.evaluate(_s([(5, 11), (3, 11)])) is not None  # away player winning both


def test_other_stages_and_window():
    assert st4.evaluate(_s([(11, 3)])) is None
    assert st4.evaluate(_s([(11, 3), (11, 4), (11, 5), (11, 3)])) is None
    assert st4.evaluate(_s([(11, 3), (11, 4), (2, 1)])) is not None   # 3 points of set 3: inside the window
    assert st4.evaluate(_s([(11, 3), (11, 4), (4, 2)])) is None       # 6 points: window closed


def test_stake_halved_and_capped_when_match_already_held():
    assert st4.stake_for(0.0, 0.0)[0] == 0.015
    assert st4.stake_for(0.0, 0.01)[0] == 0.0075
    assert abs(st4.stake_for(0.0, 0.0275)[0] - 0.0025) < 1e-9       # cap 3 % in total
    assert st4.stake_for(0.31, 0.0)[0] == 0.0            # paused after 30 % drawdown
