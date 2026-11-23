"""Whole-day uncertainty, finite families and prognostic scores."""
import math
from collections import Counter
from datetime import datetime, timezone, timedelta

import numpy as np


def finite(v):
    return isinstance(v, (float, int, np.number)) and math.isfinite(v)


def block_days(window):
    start = datetime.fromtimestamp(window["start_ms"]/1000, timezone.utc).date()
    stop = datetime.fromtimestamp(window["end_ms"]/1000, timezone.utc).date()
    return [(start + timedelta(days=i)).isoformat() for i in range((stop-start).days)]


def sign_p(deltas):
    a = [x for x in deltas if finite(x) and abs(x) > 1.e-13]
    if not a:
        return 1.
    n, positive = len(a), sum(x > 0 for x in a)
    return sum(math.comb(n, k) for k in range(positive, n + 1)) / 2**n


def holm(raw):
    if not raw:
        return {}
    sorted_p = sorted(raw, key=lambda key: raw[key])
    out, high = {}, 0.
    for i, key in enumerate(sorted_p):
        high = max(high, min(1., (len(raw)-i)*raw[key]))
        out[key] = high
    return out


def interval(estimate, replicates, n, days):
    x = np.asarray(replicates, dtype=float)
    x = x[np.isfinite(x)]
    good = bool(n and days >= 2 and np.isfinite(estimate) and len(x))
    return {"value": float(estimate) if good else None,
            "ci95": np.quantile(x,[.025,.975]).tolist() if good else None, "n": int(n),
            "active_days": int(days), "valid_draws": int(len(x))}


def divide(a,b):
    a,b=np.broadcast_arrays(np.asarray(a,dtype=float),np.asarray(b,dtype=float))
    return np.divide(a,b,out=np.full(a.shape,np.nan),where=b!=0)


def day_draws(days, seed, draws):
    ids=np.random.default_rng(seed).integers(len(days),size=(draws,len(days)))
    w=np.zeros((draws,len(days)),dtype=float)
    np.add.at(w,(np.arange(draws)[:,None],ids),1.)
    return ids,w


def y_win(r):
    if r.get("total") is None or r.get("terminal_ms") is None:
        return None
    return int(r["total"]<r["line"]) if r["total"]!=r["line"] else 2


def price_p(r):
    if finite(r.get("p_market")):
        return r["p_market"]
    o,u=r.get("over"),r.get("odds")
    return o/(o+u) if finite(o) and o>1 and finite(u) and u>1 else 1/u


def ratio_metric(num,den,W,n=None):
    num,den=np.asarray(num),np.asarray(den)
    return interval(divide(num.sum(),den.sum()),divide(W@num,W@den),
                    den.sum() if n is None else n,np.count_nonzero(den))


def forecasts(rows,days,W):
    index={d:i for i,d in enumerate(days)}
    models=("p_cal","p_current","p_market")
    keep=[r for r in rows if y_win(r) in (0,1) and all(finite(r.get(k)) and 0<=r[k]<=1 for k in models)]
    d=np.array([index[r["day"]] for r in keep],dtype=int)
    y=np.array([y_win(r) for r in keep])
    count=np.bincount(d,minlength=len(days))
    result={"n_rows":len(rows),"n_common_nonpush":len(keep),"metrics":{}, "comparison":{}}
    ls={}; ms={}
    for key in models:
        p=np.clip(np.array([r[key] for r in keep]),1.e-9,1.-1.e-9)
        loss=-np.where(y==1,np.log(p),np.log1p(-p))
        sums=np.bincount(d,weights=loss,minlength=len(days))
        sq=np.bincount(d,weights=(y-p)**2,minlength=len(days))
        ls[key]=sums;ms[key]=sq
        # AUC in each complete-day resample: positive-day vs negative-day pair counts.
        conc=np.zeros((len(days),len(days))); pairs=conc.copy()
        for i in range(len(days)):
            positives=p[(d==i)&(y==1)]
            for j in range(len(days)):
                negatives=np.sort(p[(d==j)&(y==0)])
                pairs[i,j]=len(positives)*len(negatives)
                if len(negatives):
                    a=np.searchsorted(negatives,positives,"left")
                    b=np.searchsorted(negatives,positives,"right")
                    conc[i,j]=((a+b)/2).sum()
        den=np.einsum('ij,ij->i',W@pairs,W)
        auc=divide(np.einsum('ij,ij->i',W@conc,W),den)
        calibration=[]
        edges=[0,.4,.45,.50,.55,.60,.65,.70,1.]
        for lo,hi in zip(edges,edges[1:]):
            mask=(p>=lo)&(p<hi if hi<1 else p<=hi)
            counts=np.bincount(d[mask],minlength=len(days))
            wins=np.bincount(d[mask],weights=y[mask],minlength=len(days))
            calibration.append({"lo":lo,"hi":hi,"mean_forecast":float(p[mask].mean()) if mask.any() else None,
                                "wr":ratio_metric(wins,counts,W)})
        result["metrics"][key]={"log_loss":ratio_metric(sums,count,W),"brier":ratio_metric(sq,count,W),
                               "auc":interval(divide(conc.sum(),pairs.sum()),auc,len(keep),np.count_nonzero(count)),
                               "calibration_bins":calibration}
    for key in models[1:]:
        delta=ls[key]-ls["p_cal"]
        result["comparison"][key]={"logloss_gain":ratio_metric(delta,count,W),
            "brier_gain":ratio_metric(ms[key]-ms["p_cal"],count,W),
            "sign_p_over_margin":sign_p(divide(delta,count)-.005)}
    p_cal=np.clip(np.array([r["p_cal"] for r in keep]),1e-9,1-1e-9)
    truth=np.where(y==1,p_cal,1-p_cal)
    model_groups={}
    for name,label in (("month",lambda r:r["day"][:7]),("league",lambda r:r["league"])):
        groups={}
        for group in sorted({label(r) for r in keep}):
            mask=np.array([label(r)==group for r in keep])
            gains=[]
            for baseline in models[1:]:
                p=np.clip(np.array([r[baseline] for r in keep]),1e-9,1-1e-9)
                reftruth=np.where(y==1,p,1-p)
                gains.append(float(np.mean(np.log(truth[mask])-np.log(reftruth[mask]))))
            groups[group]={"n":int(mask.sum()),"both_logloss_gains_positive":min(gains)>0}
        model_groups[name]=groups
    result["consistency"]=model_groups
    result["intersection_sign_p_over_margin"]=max(v["sign_p_over_margin"] for v in result["comparison"].values())
    mg=list(model_groups["month"].values())
    result["stage_gates"]={
        "both_logloss_gain_lower_bounds_over_margin":all(v["logloss_gain"]["ci95"] is not None and
             v["logloss_gain"]["ci95"][0]>.005 for v in result["comparison"].values()),
        "directional_p_over_margin_below_05":result["intersection_sign_p_over_margin"]<.05,
        "two_positive_leagues":sum(v["both_logloss_gains_positive"] for v in model_groups["league"].values())>=2,
        "two_months_and_80pct_positive":len(mg)>=2 and sum(v["both_logloss_gains_positive"] for v in mg)/len(mg)>=.8}
    return result
