"""Synthetic coverage, timing and censoring tests for the offline deterioration monitor."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from monitor import (Detectors, INDEX, N_STREAMS, annual_false_alarms, detection_summary,
                     fit_daily, observations, run_observed, summarize_weeks, evaluate_registered_stage)

L="Masters. Rusia"
def energy_row(**more):
    base=dict(opportunity_id="g1",k=1,league=L,quoted_ms=1000,line=100.5,center=104.,
              offered_odds=2.,model_probability=.5,final_total=90,final_known_ms=2000,
              closing_odds=1.9,closing_known_ms=1800,closing_same_contract_state=True)
    base.update(more)
    return base


class MonitorTests(unittest.TestCase):
    def test_report_fails_closed_on_future_or_wrong_market(self):
        daily,weekly,_=observations([energy_row(closing_same_contract_state=False)],1500)
        row=summarize_weeks(weekly)[0]
        self.assertFalse(daily);self.assertEqual(row["known_final_n"],0)
        self.assertIsNone(row["accepted_over_resolved"])
        self.assertEqual(row["opportunities_without_execution_report"],1)
        daily,weekly,_=observations([energy_row(closing_same_contract_state=False)],3000)
        self.assertEqual(summarize_weeks(weekly)[0]["log_clv"]["n"],0)
        self.assertEqual(summarize_weeks(weekly)[0]["center_minus_total"]["mean"],14.)

    def test_release_day_and_account_provenance(self):
        end=2*86400000
        fields=dict(final_known_ms=end+200,execution_report_ms=7000,accepted_ms=5000,
                    attempt_status="accepted",accepted_odds=1.8,accepted_stake=3,requested_stake=4)
        daily,weekly,_=observations([energy_row(**fields)],end+1000)
        self.assertIn(("1970-01-03",L,1,"center_minus_total"),daily)
        v=summarize_weeks(weekly)[0]
        self.assertEqual((v["accepted_receipts"],v["median_quote_to_accept_seconds"]),(1,4.))
        self.assertLess(v["median_log_slippage"],0)
        with self.assertRaises(ValueError): observations([energy_row(attempt_status="accepted",execution_report_ms=2000)],3000)
        with self.assertRaises(ValueError): observations([energy_row(final_known_ms=999)],3000)
        with self.assertRaises(ValueError): observations([energy_row(),energy_row()],3000)

    def test_push_and_missing_day_do_not_create_loss_signal(self):
        _,weekly,_=observations([energy_row(final_total=100,line=100)],3000)
        self.assertEqual(summarize_weeks(weekly)[0]["log_loss"]["n"],0)
        detector=Detectors((1,N_STREAMS))
        a=detector.step(np.ones((1,N_STREAMS)))
        self.assertTrue(np.all(a>0))
        empty=detector.step(np.full((1,N_STREAMS),np.nan))
        self.assertTrue(np.all(empty==-np.inf))
        self.assertTrue(np.allclose(detector.cusum,.75))

    def test_monthly_lock_and_rearm(self):
        thresholds={i:.01 for i in (28,29,30,31)}
        ref={(L,1,"center_minus_total"):(1.,1.,10)}
        daily={(d,L,1,"center_minus_total"):[-3]*5 for d in ("2026-11-05","2026-11-06","2026-12-01")}
        self.assertEqual([r["day_utc"] for r in run_observed(daily,ref,thresholds)["alarms"]],["2026-11-05","2026-12-01"])
        self.assertFalse(fit_daily(daily))

    def test_confirmation_state_survives_month_and_blind_refuses_before_io(self):
        thresholds={m:.1 for m in (28,29,30,31)}
        ref={(L,1,"center_minus_total"):(1.,1.,10)}
        d1={("2026-11-16",L,1,"center_minus_total"):[-4]*5}
        c=run_observed(d1,ref,thresholds)
        d2={("2026-11-17",L,1,"center_minus_total"):[-5]*5}
        b=run_observed(d2,ref,thresholds,c["state_end"])
        self.assertEqual(len(c["alarms"]),1); self.assertFalse(b["alarms"])
        with self.assertRaisesRegex(ValueError,"Overlapping"):
            run_observed(d1,ref,thresholds,c["state_end"])
        with TemporaryDirectory() as folder:
            bogus=Path(folder)/"never_read"
            with self.assertRaisesRegex(ValueError,"single final"):
                evaluate_registered_stage("blind",bogus,bogus,bogus)
            with self.assertRaisesRegex(ValueError,"Confirmation state"):
                evaluate_registered_stage("blind",bogus,bogus,bogus,final_authorized=True)

    def test_joint_count_and_horizon(self):
        a={m:np.array([0.,2.,0.,2.]) for m in (28,29,30,31)}
        result=annual_false_alarms(a,{m:1. for m in a})
        self.assertEqual(result["365_day_year"]["expected_count"],6.)
        summary=detection_summary([1,91,91,90])
        self.assertEqual(summary["probability_by_day"]["90"]["value"],.5)
        self.assertEqual(summary["mean_days_capped_at_90"],67.75)


if __name__=="__main__":
    unittest.main()
