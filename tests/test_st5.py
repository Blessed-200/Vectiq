from tests.test_st3 import _s
from vectiq.signals import markov, st5

LAD2 = ((88.5, 1.40, 2.60), (90.5, 1.75, 2.00), (93.5, 1.95, 1.80), (96.5, 2.30, 1.60))


def test_model_reads_a_blowout_as_fewer_points():
    cdf_blow = markov.p_under([(11, 3), (11, 4)], 90.5)
    cdf_even = markov.p_under([(11, 9), (9, 11)], 90.5)
    assert cdf_blow > 0.6 > cdf_even + 0.2            # same line: far likelier Under after two blowouts


def test_model_is_a_distribution():
    cdf = markov.remaining_cdf(((11, 5),))
    assert abs(cdf[-1] - 1) < 1e-6 and (cdf[1:] >= cdf[:-1]).all()
    assert markov.p_under([(11, 5), (11, 4)], 10.5) == 0.0   # line already passed


def test_picks_the_high_probability_rung_by_best_ev():
    al = st5.evaluate(_s([(11, 3), (11, 4)], lines=LAD2))
    assert al and al.p >= st5.MIN_P and al.ev >= st5.MIN_EV and al.line == 90.5 and al.min_price > 1.0
    assert abs(al.min_price - (1 + st5.MIN_EV) / al.p) < 0.01


def test_no_alert_when_even_or_other_stages():
    assert st5.evaluate(_s([(11, 9), (9, 11)], lines=LAD2)) is None
    assert st5.evaluate(_s([], lines=LAD2)) is None
    assert st5.evaluate(_s([(11, 3), (11, 4), (11, 5), (11, 3)], lines=LAD2)) is None
    assert st5.evaluate(_s([(11, 3), (11, 4), (4, 2)], lines=LAD2)) is None     # 6 points of set 3 already


def test_stake_cap_and_drawdown():
    assert st5.stake_for(0.0, 0.0)[0] == st5.STAKE_FRAC
    assert abs(st5.stake_for(0.0, 0.025)[0] - 0.005) < 1e-9
    assert st5.stake_for(0.16, 0.0)[0] == st5.STAKE_FRAC / 2
    assert st5.stake_for(0.31, 0.0)[0] == 0.0
