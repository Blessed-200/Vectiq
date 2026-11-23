"""Offline league/stage monitor and pre-specified joint false-alarm experiment."""
from __future__ import annotations

import argparse
import calendar
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import itertools
import json
import math
from pathlib import Path
from statistics import mean, median, stdev
from typing import Iterable

import numpy as np

LEAGUES = ("Masters. Argentina", "Masters. Belarús", "Masters. España", "Masters. España. Mujeres",
           "Masters. Mujeres", "Masters. Polonia", "Masters. Polonia. Mujeres", "Masters. Rusia",
           "Masters. Rusia. Mujeres")
STAGES = (0, 1, 2)
METRICS = ("center_minus_total", "log_clv", "log_loss")
DIRECTION = (-1., -1., 1.)
SHAPE = (len(LEAGUES), len(STAGES), len(METRICS))
N_STREAMS = math.prod(SHAPE)
MONTH_DAYS = (28, 29, 30, 31)
LAMBDA = .15
EWMA_SD = math.sqrt(LAMBDA / (2 - LAMBDA))
SPRT_LOWER = math.log(.20)
BASE_SEED = 2026112207
BURN_IN = 32
DAY_MS = 86_400_000
ROOT = Path(__file__).resolve().parent
SPLIT = ROOT.parent / "split.json"
KEYS = tuple(itertools.product(LEAGUES, STAGES, METRICS))
INDEX = {key: i for i, key in enumerate(KEYS)}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True,
                               allow_nan=False) + "\n")


def wilson(k, n):
    if not n:
        return None
    p, z = k / n, 1.959963984540054
    d = 1 + z * z / n
    c = (p + z*z/(2*n))/d
    e = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return [max(0., c-e), min(1., c+e)]


def utc_day(at_ms):
    return datetime.fromtimestamp(at_ms / 1000, timezone.utc).date().isoformat()


class Detectors:
    def __init__(self, shape):
        self.ewma = np.zeros(shape)
        self.cusum = np.zeros(shape)
        self.sprt = np.zeros(shape)

    def step(self, z):
        valid = np.isfinite(z)
        nz = np.where(valid, z, 0.)
        self.ewma = np.where(valid, (1-LAMBDA)*self.ewma + LAMBDA*nz, self.ewma)
        self.cusum = np.where(valid, np.maximum(0., self.cusum + nz-.25), self.cusum)
        trial = self.sprt + nz-.5
        self.sprt = np.where(valid, np.where(trial <= SPRT_LOWER, 0., trial), self.sprt)
        # Missing observations cannot trigger an alert, including a stale statistic.
        return np.where(valid, np.maximum(np.maximum(self.ewma/EWMA_SD, self.cusum/3.), self.sprt/2.), -np.inf)

    def methods(self, j, threshold):
        scores = {"ewma": self.ewma[0,j]/EWMA_SD, "cusum": self.cusum[0,j]/3.,
                  "sprt": self.sprt[0,j]/2.}
        return {name: float(v) for name,v in scores.items() if v >= threshold}


class NullProcess:
    def __init__(self, n, rng, rho=.2, df=7):
        self.rng, self.n, self.rho, self.df = rng, n, rho, df
        self.state = np.zeros((n, *SHAPE))
        for _ in range(BURN_IN):
            self.step()

    def step(self):
        rng, n = self.rng, self.n
        shared = rng.normal(size=(n,1,1,1))*math.sqrt(.15)
        league = rng.normal(size=(n,SHAPE[0],1,1))*math.sqrt(.15)
        stage = rng.normal(size=(n,1,SHAPE[1],1))*math.sqrt(.10)
        metric = rng.normal(size=(n,1,1,SHAPE[2]))*math.sqrt(.10)
        idiosyncratic = rng.normal(size=(n, *SHAPE))*math.sqrt(.50)
        vol = np.sqrt((self.df - 2.) / rng.chisquare(self.df, size=(n,1,1,1)))
        innovation = (shared+league+stage+metric+idiosyncratic)*vol
        self.state = self.rho*self.state + math.sqrt(1-self.rho**2)*innovation
        return self.state.reshape(n, N_STREAMS)


def month_extrema(rng, n, rho=.2, df=7):
    result = {m: [] for m in MONTH_DAYS}
    for a in range(0, n, 160):
        b = min(160, n-a)
        process = NullProcess(b, rng, rho, df)
        detectors = Detectors((b, N_STREAMS))
        maxima = np.zeros(b)
        for day in range(1, 32):
            scores = detectors.step(process.step())
            maxima = np.maximum(maxima, scores.max(axis=1))
            if day in result:
                result[day].append(maxima.copy())
    return {m: np.concatenate(s) for m,s in result.items()}


def thresholds_from_oracle(null):
    return {m: float(np.quantile(v, 11/12, method="higher")) for m,v in null.items()}


def annual_false_alarms(null, thresholds):
    flags = {m: null[m] >= thresholds[m] for m in MONTH_DAYS}
    def annual(feb):
        # Sharing these 31-day sample paths between length estimates gives a paired
        # variance for the estimated mean, not a simulated annual count distribution.
        values = 7*flags[31].astype(float) + 4*flags[30] + flags[feb]
        m = float(values.mean())
        se = float(values.std(ddof=1)/math.sqrt(len(values)))
        return {"expected_count": m, "mc_ci95": [max(0., m-1.95996398454*se),m+1.95996398454*se]}
    return {"simulated_months_per_length":len(null[31]),
            "monthly_probability":{str(m):{"value":float(f.mean()),"ci95":wilson(int(f.sum()),len(f))}
                                   for m,f in flags.items()},
            "365_day_year":annual(28), "leap_year":annual(29)}


def detection_summary(first, horizon=90):
    first = np.asarray(first)
    n = len(first)
    times = first[first <= horizon]
    bounded = np.minimum(first, horizon)
    restricted = float(bounded.mean())
    se = float(bounded.std(ddof=1)/math.sqrt(n))
    return {"paths":n, "uncensored_by_day_90":len(times),
            "probability_by_day":{str(d):{"value":float(np.mean(first <= d)),
                                         "ci95":wilson(int(np.sum(first<=d)),n)} for d in (7,28,90)},
            "median_delay_if_detected_days":float(np.median(times)) if len(times) else None,
            "mean_days_capped_at_90":restricted,
            "mc_ci95_mean_days_capped_at_90":[max(0.,restricted-1.95996398454*se),min(90.,restricted+1.95996398454*se)]}


def power(thresholds, delta, local, rng, paths):
    affected = np.zeros((1, N_STREAMS),dtype=bool)
    if local:
        league, stage = "Masters. Rusia", 1
        affected[0,[INDEX[league,stage,m] for m in METRICS]] = True
    else:
        affected[:] = True
    any_times, true_times = [], []
    for a in range(0, paths, 160):
        b=min(160, paths-a)
        process=NullProcess(b,rng)
        first=np.full(b,91); attribution=np.full(b,91)
        day=0
        for length in (31,28,31):
            detectors=Detectors((b,N_STREAMS))
            silenced=np.zeros(b,dtype=bool)
            for _ in range(length):
                day+=1
                score=detectors.step(process.step()+delta*affected)
                hit=score >= thresholds[length]
                alert=np.any(hit,axis=1) & ~silenced
                relevant=alert & np.any(hit & affected,axis=1)
                first[(first==91)&alert]=day
                attribution[(attribution==91)&relevant]=day
                silenced|=alert
        any_times.extend(first.tolist());true_times.extend(attribution.tolist())
    return {"delta_daily_sd":delta,"scope":"one_league_stage_three_metrics" if local else "all_streams",
            "any_alert":detection_summary(any_times), "affected_stream_at_alert":detection_summary(true_times)}


def simulate():
    calibration_n, validation_n, power_n = 6000, 6000, 1200
    rng=np.random.default_rng(BASE_SEED)
    thresholds=thresholds_from_oracle(month_extrema(rng,calibration_n))
    nominal=annual_false_alarms(month_extrema(np.random.default_rng(BASE_SEED+1),validation_n),thresholds)
    stress=annual_false_alarms(month_extrema(np.random.default_rng(BASE_SEED+2),validation_n,.5,5),thresholds)
    scenarios=[]
    for i,(delta,local) in enumerate(itertools.product((.25,.5,1.),(False,True))):
        scenarios.append(power(thresholds,delta,local,np.random.default_rng(BASE_SEED+3+i),power_n))
    return {"type":"synthetic_operating_characteristics","historical_holdout_read":False,
            "seed":BASE_SEED,"calibration_months":calibration_n,"monthly_upper_thresholds":thresholds,
            "validation":nominal,"null_stress_ar_0_5_t5":stress,"power":scenarios,
            "channels":N_STREAMS,"methods_per_channel":3,"alarm_definition":"one joint alarm per calendar month max",
            "target_expected_null_alarms_per_365_days":1.0,"reference_null":"t7 common daily scale; AR(1)=0.20",
            "limitations":"Monte Carlo intervals conditional on a specified null with known reference and full coverage. Empirical false alarms and transaction execution are unverified."}


def number(value):
    if value is None or value == "":
        return None
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("Non-finite value")
    return v


def clock(value, label):
    if value is None or value=="":
        return None
    try:
        v=int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid timestamp {label}") from None
    if str(v) != str(value) or v < 0:
        raise ValueError(f"Invalid timestamp {label}")
    return v


TEMPLATE_FIELDS = ("opportunity_id","quoted_ms","league","k","line","offered_odds","center",
                   "model_probability","final_total","final_known_ms","closing_odds","closing_known_ms",
                   "closing_same_contract_state","attempt_status","execution_report_ms","accepted_ms",
                   "accepted_odds","accepted_stake","requested_stake")


def observations(rows: Iterable[dict], cutoff_ms):
    daily=defaultdict(list); weekly=defaultdict(lambda:defaultdict(list))
    excluded=Counter(); seen=set()
    for row in rows:
        quote = clock(row.get("quoted_ms"),"quoted_ms")
        if quote is None or quote >= cutoff_ms:
            raise ValueError("Input quote outside as-of cut")
        league,k=row.get("league"),int(row["k"])
        if league not in LEAGUES or k not in STAGES:
            excluded["unsupported_league_or_stage"]+=1; continue
        oid=row["opportunity_id"]
        if not oid or (oid,league,k) in seen:
            raise ValueError("Missing/repeated opportunity per league/stage")
        seen.add((oid,league,k))
        day=datetime.fromtimestamp(quote/1000,timezone.utc).date()
        iso=day.isocalendar(); week=f"{iso.year}-W{iso.week:02d}"
        group=weekly[(week,league,k)]
        group["opportunities"].append(1)
        def available(field):
            t=clock(row.get(field),field)
            if t is not None and t<quote:
                raise ValueError(f"{field} before its opportunity")
            return t if t is not None and t<cutoff_ms else None
        def record(metric, time, value):
            daily[(utc_day(time),league,k,metric)].append(value)
            group[metric].append(value)
            group[metric+"_cohort_days"].append(day.weekday())
        offered=number(row.get("offered_odds"))
        if offered is not None and offered<=1: raise ValueError("Decimal odds must exceed one")
        final_ms, close_ms, report_ms = (available(f) for f in ("final_known_ms","closing_known_ms","execution_report_ms"))
        if final_ms is not None:
            total,line,center,prob=(number(row.get(f)) for f in ("final_total","line","center","model_probability"))
            if total is None: raise ValueError("Final timestamp without a final")
            if total<0 or total!=int(total): raise ValueError("Invalid points")
            group["known_finals"].append(1)
            if center is not None:
                record(METRICS[0],final_ms,center-total)
                group["below_center"].append(float(total<center))
                group["below_center_cohort_days"].append(day.weekday())
            if line is not None and prob is not None and total!=line:
                if not 0<=prob<=1: raise ValueError("Invalid forecast probability")
                prob=min(1-1e-6,max(1e-6,prob))
                logloss=-math.log(prob) if total<line else -math.log1p(-prob)
                record(METRICS[2],final_ms,logloss)
        if close_ms is not None and str(row.get("closing_same_contract_state","")).lower() in ("true","1"):
            closing=number(row.get("closing_odds"))
            if closing is None or closing<=1 or offered is None:
                raise ValueError("Invalid comparable reference quote")
            record(METRICS[1],close_ms,math.log(offered/closing))
        if report_ms is not None:
            status=row.get("attempt_status")
            if status not in ("accepted","rejected","pending"):
                raise ValueError("Invalid attempt_status")
            group["attempts"].append(1)
            group[status].append(1)
            if status=="accepted":
                ac=available("accepted_ms")
                odds,stake,requested=(number(row.get(f)) for f in ("accepted_odds","accepted_stake","requested_stake"))
                if ac is None or ac>report_ms or odds is None or odds<=1 or stake is None or stake<=0:
                    raise ValueError("Incomplete bookmaker fill")
                if requested is not None and requested < stake: raise ValueError("Accepted stake exceeds requested")
                group["accepted_stake"].append(stake)
                group["delay_s"].append((ac-quote)/1000.)
                if offered is not None: group["log_slippage"].append(math.log(odds/offered))
    return daily,weekly,excluded


def summarize_weeks(weekly, bounds=None):
    output=[]
    if bounds:
        monday=date.fromisoformat(utc_day(bounds[0]))
        monday-=timedelta(days=monday.weekday())
        last=date.fromisoformat(utc_day(bounds[1]-1))
        while monday<=last:
            y,w,_=monday.isocalendar()
            week=f"{y}-W{w:02d}"
            for league,k in itertools.product(LEAGUES,STAGES):
                weekly[(week,league,k)]
            monday+=timedelta(days=7)
    weights=np.random.default_rng(BASE_SEED).multinomial(7,np.ones(7)/7,size=10000)
    for (week,league,stage),v in sorted(weekly.items()):
        n=len(v["opportunities"]); attempts=len(v["attempts"]); accepted=len(v["accepted"])
        resolved=accepted+len(v["rejected"])
        out={"week_of_quote_utc":week,"league":league,"stage":stage,"opportunities":n,
             "known_final_n":len(v["known_finals"]),"opportunities_without_known_final":n-len(v["known_finals"]),
             "attempt_reports":attempts,"resolved_attempts":resolved,"pending_attempt_reports":len(v["pending"]),
             "accepted_receipts":accepted,"accepted_over_resolved":accepted/resolved if resolved else None,
             "opportunities_without_execution_report":n-attempts,
             "observed_max_accepted_stake":max(v["accepted_stake"],default=None),
             "median_quote_to_accept_seconds":median(v["delay_s"]) if v["delay_s"] else None,
             "median_log_slippage":median(v["log_slippage"]) if v["log_slippage"] else None}
        for key in (*METRICS,"below_center"):
            a=v[key]
            dayids=v[key+"_cohort_days"]
            cnt=np.bincount(dayids, minlength=7) if dayids else np.zeros(7,dtype=int)
            sums=np.bincount(dayids, weights=a,minlength=7) if dayids else np.zeros(7)
            den=weights@cnt
            usable=den>0
            active=int(np.count_nonzero(cnt))
            ci=None
            if active>=2:
                vals=(weights[usable]@sums)/den[usable]
                ci=np.quantile(vals,[.025,.975]).tolist()
            out[key]={"n":len(a),"mean":mean(a) if a else None,"nonempty_days":active,
                      "ci95_cohort_day_bootstrap":ci,"valid_draws":int(usable.sum()) if ci else 0}
        out["coverage_status"]={
            "market":"observed" if n else "no_eligible_opportunities",
            "terminals":"some_known" if v["known_finals"] else "none_observed",
            "clv":"comparable_reference_present" if v["log_clv"] else "no_comparable_quote",
            "execution":"reports_present" if attempts else "no_account_reports"}
        output.append(out)
    return output


def fit_daily(daily):
    values=defaultdict(list)
    for (_,league,k,metric),rows in daily.items():
        if len(rows)>=5:
            values[(league,k,metric)].append(mean(rows))
    ref={}
    for key,v in values.items():
        if len(v)>=10 and stdev(v)>0:
            ref[key]=(mean(v),stdev(v),len(v))
    return ref


def run_observed(daily,ref,thresholds,previous_state=None):
    days=defaultdict(dict)
    for (day,league,k,metric),v in sorted(daily.items()):
        key=(league,k,metric)
        if key not in ref or len(v)<5: continue
        base,sd,_=ref[key]
        days[day][INDEX[key]]=DIRECTION[METRICS.index(metric)]*(mean(v)-base)/sd
    report=[]; current=None; last_day=None; detectors=None; silenced=False
    if previous_state and previous_state.get("calendar_month"):
        current=tuple(previous_state["calendar_month"]);last_day=previous_state["last_processed_day"]
        detectors=Detectors((1,N_STREAMS))
        for attr in ("ewma","cusum","sprt"):
            arr=np.asarray(previous_state[attr],dtype=float)
            if arr.shape!=(N_STREAMS,) or not np.all(np.isfinite(arr)):
                raise ValueError("Invalid previous monitor state")
            setattr(detectors,attr,arr.reshape(1,N_STREAMS))
        silenced=bool(previous_state["silenced"])
    for day,observed in sorted(days.items()):
        if last_day and day<=last_day: raise ValueError("Overlapping daily monitor input")
        last_day=day
        dt=date.fromisoformat(day)
        if (dt.year,dt.month)!=current:
            current=dt.year,dt.month
            detectors=Detectors((1,N_STREAMS)); silenced=False
        z=np.full((1,N_STREAMS),np.nan)
        for j,value in observed.items(): z[0,j]=value
        scores=detectors.step(z)[0]
        threshold=thresholds[calendar.monthrange(*current)[1]]
        mask=np.where(scores>=threshold)[0]
        if len(mask) and not silenced:
            report.append({"day_utc":day,"triggered":[{"league":KEYS[j][0],"k":KEYS[j][1],"metric":KEYS[j][2],
                           "methods":detectors.methods(j,threshold)} for j in mask]})
            silenced=True
    endstate={"calendar_month":current,"last_processed_day":last_day,"silenced":silenced}
    if detectors is not None:
        endstate.update({a:getattr(detectors,a)[0].tolist() for a in ("ewma","cusum","sprt")})
    return {"eligible_days":len(days),"frozen_reference_series":len(ref),"alarms":report,"state_end":endstate}


def load_events(path):
    with path.open(newline="") as f:
        rd=csv.DictReader(f)
        if not {"quoted_ms","opportunity_id","league","k"} <= set(rd.fieldnames or ()):
            raise ValueError("Missing event columns")
        return list(rd)


def stage_daily(directory,stage):
    """Read one caller-authorized partition, validating dates before using a quote."""
    if stage not in ("discovery","confirmation","blind") or directory.name != stage or directory.is_symlink():
        raise ValueError("Invalid partition directory")
    metadata = json.loads((directory / "extract.json").read_text())
    if metadata["stage"] != stage:
        raise ValueError("Extract and partition disagree")
    window=json.loads(SPLIT.read_text())[stage]
    start,end=window["start_ms"],window["end_ms"]
    first, centers = {}, {}
    source = directory / "controls.jsonl"
    with source.open() as f:
        for text in f:
            r = json.loads(text)
            t = r["ts"]
            if r["stage"] != stage or not start <= t < end:
                raise ValueError("Quote outside registered partition")
            if r["league"] not in LEAGUES or r["k"] not in STAGES:
                continue
            key = (r["gid"], r["k"])
            prob = r["over"] / (r["over"] + r["odds"])
            rank = (t, abs(prob-.5), r["line"])
            if key not in first or rank < first[key][0]:
                first[key] = (rank, r, prob)
            if r["center"] is not None and (key not in centers or (t, r["center"]) < centers[key]):
                centers[key] = (t, r["center"])
    out = []
    for key, (_, r, prob) in first.items():
        term, ref_t = r["terminal_ms"], r["close_ts"]
        if term is not None and not r["ts"] < term < end:
            raise ValueError("Invalid terminal clock")
        if ref_t is not None and not r["ts"] < ref_t < end:
            raise ValueError("Invalid reference quote clock")
        # At the last quote itself one cannot know it will be the last one in its
        # state. Waiting for a known terminal prevents backdating that reference.
        valid_close = r["close_price"] is not None and ref_t is not None and term is not None and ref_t <= term
        center_t, center = centers.get(key, (None,None))
        if center_t is not None and term is not None and center_t >= term:
            center = None
        out.append(dict(opportunity_id=str(key[0]),quoted_ms=r["ts"],league=r["league"],k=r["k"],
                        line=r["line"],offered_odds=r["odds"],center=center,model_probability=prob,
                        final_total=r["total"],final_known_ms=term,
                        closing_odds=r["close_price"] if valid_close else None,
                        closing_known_ms=term if valid_close else None,
                        closing_same_contract_state=valid_close))
    daily, weeks, excluded = observations(out,end)
    return daily,weeks,excluded,len(out),window


def discovery_from_controls(directory):
    daily,weeks,excluded,n,window=stage_daily(directory,"discovery")
    # Freeze scales on discovery only.
    return {"source":"round2 discovery controls, earliest opportunity per gid/stage and first usable center",
            "forecast":"frozen no-vig bookmaker Under probability from both offered sides; no candidate model",
            "clv":"last observed later offer at exactly the same scoring state/source/line, acknowledged no earlier than terminal; proxy only",
            "execution":"no account receipts in this source",
            "discovery":window,"opportunities":n,"baseline":reference_snapshot(fit_daily(daily)),
            "weekly":summarize_weeks(weeks,(window["start_ms"],window["end_ms"])),"excluded":dict(excluded)}


def reference_snapshot(ref):
    return [{"league":a,"k":b,"metric":c,"discovery_mean_daily":m,"discovery_sd_daily":s,
             "discovery_days":n} for (a,b,c),(m,s,n) in sorted(ref.items())]


def evaluate_registered_stage(stage, directory, baseline_path, calibration_path, previous_report=None, *, final_authorized=False):
    """Blind caller is the frozen single-access driver. This function never fits or selects policies."""
    if stage not in ("confirmation","blind") or (stage=="blind" and not final_authorized):
        raise ValueError("Blind evaluation is reserved to the single final entry point")
    if stage=="blind" and previous_report is None:
        raise ValueError("Confirmation state is required for a continuous monitor")
    if stage=="confirmation" and previous_report is not None:
        raise ValueError("Confirmation starts after reference freezing")
    import hashlib
    frozen=Path(baseline_path).read_bytes()
    cal_raw=Path(calibration_path).read_bytes()
    b=json.loads(frozen); cal=json.loads(cal_raw)
    split=json.loads(SPLIT.read_text())
    if b["discovery"]!=split["discovery"] or cal.get("type")!="synthetic_operating_characteristics":
        raise ValueError("Reference or calibration does not match")
    hashes={"baseline_sha256":hashlib.sha256(frozen).hexdigest(),"calibration_sha256":hashlib.sha256(cal_raw).hexdigest()}
    ref={(x["league"],x["k"],x["metric"]):(x["discovery_mean_daily"],x["discovery_sd_daily"],x["discovery_days"])
         for x in b["baseline"]}
    h={int(k):float(v) for k,v in cal["monthly_upper_thresholds"].items()}
    previous=None
    if previous_report:
        aux=json.loads(Path(previous_report).read_bytes())
        if aux["stage"]!="confirmation" or any(aux[k]!=v for k,v in hashes.items()):
            raise ValueError("Confirmation monitor state or frozen references differ")
        previous=aux["state_end"]
    daily,weeks,excluded,n,window=stage_daily(Path(directory),stage)
    return {"stage":stage,**hashes,"window":window,"opportunities":n,"excluded":dict(excluded),
            "weekly":summarize_weeks(weeks,(window["start_ms"],window["end_ms"])),
            **run_observed(daily,ref,h,previous),
            "limits":"One-of-81 any-series monthly joint trigger, carried from confirmation; comparable-state CLV proxy, feed terminal timing, no bookmaker fills. Held-out slice too short to estimate an annual false-alarm rate."}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="action",required=True)
    ss=sub.add_parser("simulate"); ss.add_argument("--out",type=Path,required=True)
    empirical=sub.add_parser("discovery-controls"); empirical.add_argument("--directory",type=Path,required=True)
    empirical.add_argument("--out",type=Path,required=True)
    week=sub.add_parser("weekly"); week.add_argument("--events",type=Path,required=True)
    week.add_argument("--cutoff-ms",type=int,required=True); week.add_argument("--out",type=Path,required=True)
    conf=sub.add_parser("evaluate-confirmation");conf.add_argument("--directory",type=Path,required=True)
    conf.add_argument("--baseline",type=Path,required=True);conf.add_argument("--calibration",type=Path,required=True)
    conf.add_argument("--out",type=Path,required=True)
    train=sub.add_parser("reference-discovery"); train.add_argument("--events",type=Path,required=True)
    train.add_argument("--out",type=Path,required=True)
    run=sub.add_parser("monitor");run.add_argument("--events",type=Path,required=True)
    run.add_argument("--cutoff-ms",type=int,required=True);run.add_argument("--baseline",type=Path,required=True)
    run.add_argument("--calibration",type=Path,required=True);run.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    if a.action=="simulate":
        write_json(a.out,simulate()); return
    if a.action=="evaluate-confirmation":
        write_json(a.out,evaluate_registered_stage("confirmation",a.directory,a.baseline,a.calibration)); return
    if a.action=="discovery-controls":
        write_json(a.out,discovery_from_controls(a.directory)); return
    window=json.loads(SPLIT.read_text())["discovery"]
    cutoff=window["end_ms"] if a.action=="reference-discovery" else a.cutoff_ms
    rows=load_events(a.events)
    if a.action=="reference-discovery" and any(int(r["quoted_ms"])<window["start_ms"] for r in rows):
        raise ValueError("Quote before registered discovery")
    if a.action=="monitor" and any(int(r["quoted_ms"])<window["end_ms"] for r in rows):
        raise ValueError("Evaluation and reference must be temporally disjoint")
    daily,weekly,excluded=observations(rows,cutoff)
    if a.action=="reference-discovery":
        write_json(a.out,{"discovery":window,"baseline":reference_snapshot(fit_daily(daily)),
                          "weekly":summarize_weeks(weekly),"excluded":dict(excluded)})
    elif a.action=="weekly":
        write_json(a.out,{"cutoff_ms":cutoff,"weekly":summarize_weeks(weekly),"excluded":dict(excluded)})
    else:
        b=json.loads(a.baseline.read_text())
        if b["discovery"]!=window: raise ValueError("Baseline not frozen on this discovery interval")
        ref={(x["league"],x["k"],x["metric"]):(x["discovery_mean_daily"],x["discovery_sd_daily"],x["discovery_days"])
             for x in b["baseline"]}
        h={int(k):float(v) for k,v in json.loads(a.calibration.read_text())["monthly_upper_thresholds"].items()}
        write_json(a.out,{"cutoff_ms":cutoff,"weekly":summarize_weeks(weekly),**run_observed(daily,ref,h)})


if __name__=="__main__":
    main()
