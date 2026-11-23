"""Run the frozen round-two policy, mechanism and probability comparisons on one partition."""
import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import mechanisms
from policy_stats import (block_days, day_draws, divide, finite, forecasts, holm, interval,
                          price_p, ratio_metric, sign_p, y_win)

LEGS=("ST0","ST3","ST5")
POLICIES=("P1","P2","P3","P4","P5","P6")
STAKE=.01


def read_rows(path, window, stage):
    with Path(path).open() as stream:
        for line in stream:
            r=json.loads(line)
            if r.get("stage",stage)!=stage or not window["start_ms"]<=r["ts"]<window["end_ms"]:
                raise ValueError("La fila no pertenece al tramo autorizado.")
            if r["day"]!=datetime.fromtimestamp(r["ts"]/1000,timezone.utc).date().isoformat():
                raise ValueError("El bloque de día UTC no coincide con la apertura.")
            end=r.get("terminal_ms")
            if end is not None and not r["ts"]<end<window["end_ms"]:
                raise ValueError("Hora del final fuera de la ventana causal.")
            if r.get("total") is not None and end is None:
                raise ValueError("Total sin hora de conocimiento.")
            for name in ("player_features_known_ms","price_prior_ts"):
                z=r.get(name)
                if finite(z) and z>=r["ts"]:
                    raise ValueError(f"{name} no precede a la decisión.")
            yield r


def cell(r):
    return (r["k"],r["league"],float(r["line"]),math.floor(float(r["odds"])*100+.5000001))


def rate_for(r,frozen,policy):
    if policy=="current":
        return STAKE
    if policy=="P1":
        return STAKE*int(r["leg"]!="ST3" or r["odds"]<=2.30+1e-9)
    if policy in ("P2","P3","P6"):
        p=r.get("p_cal"); push=r.get("p_push")
        if not finite(push) and float(r["line"]).is_integer():
            return 0.
        if not finite(p):
            return 0.
        edge=(1-(push if finite(push) else 0.))*(p*r["odds"]-1.)
        return STAKE*(np.clip(edge/.10,0,1) if policy=="P3" else int(edge>=.05-1.e-12))
    vals=frozen["deuce"].get(r["leg"]); deu=r.get("player_deuce")
    if not vals or not finite(deu):
        return STAKE
    if policy=="P4":
        return STAKE*int(deu<=vals["q75"])
    if policy=="P5":
        z=(deu-vals["mean"])/vals["sd"] if vals["sd"] else 0.
        return STAKE*min(1.,max(.25,1-max(0,z)/2))
    raise ValueError(policy)


def fit_policies(signals, mechanism_rows=()):
    d={}
    for leg in LEGS:
        x=[r["player_deuce"] for r in signals if r["leg"]==leg and finite(r.get("player_deuce"))]
        if x:
            d[leg]={"n":len(x),"mean":float(np.mean(x)),"sd":float(np.std(x)),"q75":float(np.quantile(x,.75))}
    return {"deuce":d,"h5":mechanisms.h5_fit(mechanism_rows),"policy_ids":list(POLICIES),
            "stake_for_paper_comparison":STAKE,"hypotheses":7}


class ControlCounts(defaultdict):
    def __init__(self, n_days):
        super().__init__(lambda:np.zeros((3,n_days)))
        self.games=defaultdict(set)


def controls_index(stream, roots, days):
    targets=defaultdict(set)
    for r in roots:
        targets[cell(r)].add(r["leg"])
    counts=ControlCounts(len(days))
    ids={s:i for i,s in enumerate(days)}
    best={}; nr=0; unique_games=set()
    for r in stream:
        nr+=1
        key=cell(r);gk=(r["gid"],r["k"]);unique_games.add(r["gid"])
        choice=(r["ts"],abs(price_p(r)-.5),r["line"],r["odds"])
        if gk not in best or choice<best[gk][0]:
            best[gk]=(choice,r)
        y=y_win(r)
        if y is None:
            continue
        for leg in targets.get(key,()):
            if leg=="ST5" and not r.get("st5_eligible"):
                continue
            counts[(leg,*key)][y,ids[r["day"]]]+=1
            counts.games[(leg,*key)].add(r["gid"])
    return counts,[r for _,r in best.values()],{"control_rows_all":nr,"distinct_games_all":len(unique_games)}


def day_paths(rows,stakes,days):
    payouts=defaultdict(lambda:defaultdict(float))
    for r,f in zip(rows,stakes):
        y=y_win(r)
        val=r["odds"]-1 if y==1 else -1 if y==0 else 0
        payouts[r["day"]][r["terminal_ms"]]+=f*val
    features=[]
    for day in days:
        logs=[np.log1p(x) if x> -1 else -np.inf for _,x in sorted(payouts[day].items())]
        path=np.r_[0.,np.cumsum(logs)]
        peak=np.maximum.accumulate(path)
        features.append((path[-1],path.min(),path.max(),np.max(peak-path)))
    return np.array(features)


def dd_p95(path,orders):
    equity=np.zeros(len(orders)); peak=equity.copy(); dd=equity.copy()
    for j in range(orders.shape[1]):
        total,low,hi,intra=path[orders[:,j]].T
        dd=np.maximum(dd,np.maximum(peak-(equity+low),intra))
        peak=np.maximum(peak,equity+hi)
        equity+=total
    return float(np.quantile(-np.expm1(-dd),.95))


def b0_estimator(score,w,ctrl,win_if,loss_if,m,U):
    # Resample each complete day jointly in the signal and comparison markets.
    estimates=[]
    for batch in np.array_split(w,max(1,(len(w)+127)//128)):
        cw=batch@ctrl[1];cl=batch@ctrl[0]; cn=cw+cl+batch@ctrl[2]
        units=batch@U
        valid=np.all((units<=1.e-12)|(cn>0),axis=1)
        wpay=batch@win_if;lpay=batch@loss_if
        b=(wpay*divide(cw,np.maximum(cn,1))+lpay*divide(cl,np.maximum(cn,1))).sum(axis=1)
        vals=divide(batch@score-b,batch@m)
        vals[~valid]=np.nan
        estimates.extend(vals)
    return np.array(estimates)


def bounds_for_scores(roots, frozen, policy, days, W, delayed):
    day_index={d:i for i,d in enumerate(days)}
    num=np.zeros(len(days)); den=num.copy(); lower=upper=all_risk=0.
    n_selected=n_offered=n_known=0
    for r in roots:
        f=rate_for(r,frozen,policy)
        if not f:continue
        n_selected+=1
        odds=r.get("exec_odds") if delayed else r["odds"]
        if not finite(odds):continue
        if delayed:
            et=r.get("exec_ts")
            if not finite(et) or not 3000<=et-r["ts"]<=15000:continue
            if r.get("terminal_ms") is not None and et>=r["terminal_ms"]:continue
        n_offered+=1;all_risk+=f
        y=y_win(r)
        if y is None:
            lower-=f;upper+=f*(odds-1)
            continue
        j=day_index[r["day"]]
        pnl=f*(odds-1 if y==1 else -1 if y==0 else 0.)
        lower+=pnl;upper+=pnl;num[j]+=pnl;den[j]+=f;n_known+=1
    observed=ratio_metric(num,den,W,n=n_known)
    return {"selected_all":n_selected,"n_offered":n_offered,
            "n_missing_quote_or_no_valid_later_tick":n_selected-n_offered,
            "n_scored":n_known,"n_offered_without_score":n_offered-n_known,
            "complete_case_roi":observed,
            "identification_bounds_at_observed_prices":[lower/all_risk,upper/all_risk]
                if all_risk>0 and observed["ci95"] is not None else None,
            "bounds_exclude_unknown_prices_and_actual_fills":True}


def run_policy(roots,ctrl_counts,frozen,policy,days,orders,W,breakdowns=False):
    ids={d:i for i,d in enumerate(days)}
    focus=[r for r in roots if (r["leg"],*cell(r)) in ctrl_counts and y_win(r) is not None]
    keys=sorted({(r["leg"],*cell(r)) for r in focus})
    idx={k:i for i,k in enumerate(keys)}
    C=len(keys);D=len(days); n=len(focus)
    if not n:
        return {"n_openings":len(roots),"n_common":0,"intersection_sign_p":1.,"status":"sin soporte"}
    ctrl=np.transpose(np.array([ctrl_counts[k] for k in keys]),(1,2,0))
    f=np.array([rate_for(r,frozen,policy) for r in focus])
    base=np.array([STAKE if policy!="P6" or r["odds"]>=2.10-1e-9 else 0. for r in focus])
    refs={"current":base}
    if policy=="P6":
        refs["price_band"]=np.array([STAKE if 2.1-1.e-9<=r["odds"]<=2.3+1.e-9 else 0. for r in focus])
    y=np.array([y_win(r) for r in focus])
    odds=np.array([r["odds"] for r in focus])
    ret=np.where(y==1,odds-1,np.where(y==0,-1.,0.))
    d=np.array([ids[r["day"]] for r in focus])
    ix=np.array([idx[(r["leg"],*cell(r))] for r in focus])
    m=np.bincount(d,minlength=D).astype(float)
    pay=lambda z:np.bincount(d,weights=z,minlength=D)
    def mat(z):
        a=np.zeros((D,C));np.add.at(a,(d,ix),z);return a
    score=pay(np.log1p(f*ret))
    A,B,U=mat(np.log1p(f*(odds-1))),mat(np.log1p(-f)),mat(f>0)
    s0=b0_estimator(score,np.ones((1,D)),ctrl,A,B,m,U)[0]
    bdraw=b0_estimator(score,W,ctrl,A,B,m,U)
    bdays=b0_estimator(score,np.eye(D),ctrl,A,B,m,U)
    deltas={"blind_under":interval(s0,bdraw,n,np.count_nonzero(m))}
    pvals={"blind_under":sign_p(bdays)}
    group_daily={"blind_under":bdays}
    for ref,sz in refs.items():
        amt=score-pay(np.log1p(sz*ret))
        deltas[ref]=ratio_metric(amt,m,W)
        dref=divide(amt,m)
        pvals[ref]=sign_p(dref);group_daily[ref]=dref
    invested=pay(f)
    n_wl=pay((f>0)&(y!=2))
    wr=ratio_metric(pay((f>0)&(y==1)),n_wl,W)
    roi=ratio_metric(pay(f*ret),invested,W,n=int(sum(f>0)))
    active=int(sum(f>0)); base_active=int(sum(base>0))
    # Exposure/volume is also counted before outcome and common-support selection.
    taken_all=sum(rate_for(r,frozen,policy)>0 for r in roots)
    base_all=sum(policy!="P6" or r["odds"]>=2.10-1.e-9 for r in roots)
    perf={"n_openings":len(roots),"n_with_terminal":sum(y_win(r) is not None for r in roots),"n_common":n,
          "openings_selected_before_terminal":taken_all,"baseline_openings_before_terminal":base_all,
          "selected_known_common":active,"baseline_selected_known_common":base_active,
          "volume_retained":taken_all/base_all if base_all else None,"selected_price_mean":float(odds[f>0].mean()) if active else None,
          "n_distinct_control_games_known":len(set().union(*(ctrl_counts.games[k] for k in keys))),
          "n_control_game_cells_known":int(ctrl.sum()),"n_exact_common_cells":C,
          "wr":wr,"roi_at_policy_stake":roi,"growth_per_opportunity":ratio_metric(score,m,W),
          "growth_increment":deltas,"daily_sign_p":pvals,"intersection_sign_p":max(pvals.values()),
          "p95_drawdown_candidate_observed_price_proxy":dd_p95(day_paths(focus,f,days),orders),
          "p95_drawdown_current_observed_price_proxy":dd_p95(day_paths(focus,base,days),orders)}
    # Secondary changes in WR and ROI use the same day draws for candidate and controls.
    secondary={}
    for metric,sc,deno,winpay,losspay,used,reference_pool in [
        ("roi",pay(f*ret),invested,mat(f*(odds-1)),mat(-f),U,ctrl),
        ("wr",pay((f>0)&(y==1)),n_wl,mat((f>0)&(y!=2)),mat(np.zeros(n)),mat((f>0)&(y!=2)),
         np.array([ctrl[0],ctrl[1],np.zeros_like(ctrl[2])]))]:
        den_count=int((f>0).sum()) if metric=="roi" else int(n_wl.sum())
        point=divide(sc.sum(),deno.sum())
        sboot=divide(W@sc,W@deno)
        gpoint=b0_estimator(sc,np.ones((1,D)),reference_pool,winpay,losspay,deno,used)[0]
        gboot=b0_estimator(sc,W,reference_pool,winpay,losspay,deno,used)
        positive_days=int(np.count_nonzero(deno))
        results={"blind_under":interval(point-gpoint,sboot-gboot,den_count,positive_days),
                 "difference_to_blind_under":interval(gpoint,gboot,den_count,positive_days)}
        for reference,sz in refs.items():
            if metric=="roi":
                sn=pay(sz*ret);sd=pay(sz);num=int(np.count_nonzero(sz))
            else:
                sn=pay((sz>0)&(y==1));sd=pay((sz>0)&(y!=2));num=int(sd.sum())
            rv=divide(sn.sum(),sd.sum());rr=divide(W@sn,W@sd)
            results[reference]=ratio_metric(sn,sd,W,n=num)
            results["difference_to_"+reference]=interval(point-rv,sboot-rr,den_count,positive_days)
        secondary[metric]=results
    perf["secondary_same_support"]=secondary
    perf["current_mean_price"]=float(odds[base>0].mean()) if np.any(base>0) else None
    # Recompute matched means within each month and league (no selection among groups).
    months=sorted({z[:7] for z in days})
    groups={}
    for month in months:
        wm=np.array([[float(z[:7]==month) for z in days]])
        b=b0_estimator(score,wm,ctrl,A,B,m,U)[0]
        g=[b]+[float(divide(wm@(score-pay(np.log1p(sz*ret))),wm@m)[0]) for sz in refs.values()]
        groups[month]=bool(all(np.isfinite(g)) and min(g)>0) if np.isfinite(g).all() else None
    lgs={}
    for lg in sorted({r["league"] for r in focus}):
        mask=np.array([r["league"]==lg for r in focus]); cms=np.array([k[2]==lg for k in keys])
        lscore=np.log1p(f[mask]*ret[mask]).sum()
        ctrlx=ctrl[:,:,cms].sum(axis=1)
        lc=float((A[:,cms].sum(axis=0)*ctrlx[1]/ctrlx.sum(axis=0)+B[:,cms].sum(axis=0)*ctrlx[0]/ctrlx.sum(axis=0)).sum())
        gains=[lscore-lc]+[lscore-np.log1p(sz[mask]*ret[mask]).sum() for sz in refs.values()]
        lgs[lg]=bool(min(gains)>0)
    perf["months_all_comparators_positive"]=groups;perf["leagues_all_comparators_positive"]=lgs
    perf["age_and_missing"]={"missing_p_cal":sum(not finite(r.get("p_cal")) for r in roots),
                             "missing_player_deuce":sum(not finite(r.get("player_deuce")) for r in roots)}
    perf["missing_scores_at_initial_quote"]=bounds_for_scores(roots,frozen,policy,days,W,False)
    perf["missing_scores_at_delayed_quote"]=bounds_for_scores(roots,frozen,policy,days,W,True)
    accepted=[]
    for r,sz in zip(focus,f):
        if sz<=0:continue
        eo,et=r.get("exec_odds"),r.get("exec_ts")
        if not finite(eo) or not finite(et):continue
        if not 3000<=et-r["ts"]<=15000:
            raise ValueError("Reloj de recotización incompatible.")
        if et>=r["terminal_ms"]:continue
        accepted.append((ids[r["day"]],sz,eo,y_win(r)))
    eg=np.zeros(D); ev=np.zeros(D)
    for j,sz,eo,yy in accepted:
        eg[j]+=sz*(eo-1 if yy==1 else -1 if yy==0 else 0);ev[j]+=sz
    perf["delayed_quote_sensitivity"]={"n_requoted":len(accepted),"n_selected":active,
        "roi_at_delayed_visible_price":ratio_metric(eg,ev,W,n=len(accepted))}
    if breakdowns:
        perf["branch_descriptive"]={}
        for leg in LEGS:
            mask=np.array([r["leg"]==leg for r in focus]);use=mask&(f>0)
            perf["branch_descriptive"][leg]={"n_common":int(mask.sum()),"n_enter":int(use.sum()),
                "roi_at_policy_stake":ratio_metric(pay(mask*f*ret),pay(mask*f),W,n=int(use.sum())),
                "wr":ratio_metric(pay(use&(y==1)),pay(use&(y!=2)),W)}
    return perf


def permutation_streaks(rows,seed,n_draws=2000):
    rng=np.random.default_rng(seed)
    out={}
    for leg in (*LEGS,"portfolio"):
        per=defaultdict(lambda:defaultdict(list))
        for r in rows:
            if (leg=="portfolio" or r["leg"]==leg) and y_win(r) in (0,1):
                per[r["day"]][r["gid"]].append((r["ts"],y_win(r)))
        # Match blocks ordered by first signal; within each block use alert order.
        blocks=[[tuple(v for _,v in sorted(g)) for g in sorted(per[d].values(),key=min)] for d in sorted(per)]
        def lengths(seq):
            cur=mx=0
            for block in seq:
                for y in block:
                    cur=cur+1 if y==0 else 0;mx=max(mx,cur)
            return mx
        if not blocks:
            out[leg]={"n":0};continue
        obs=lengths(b for day in blocks for b in day)
        sim=[lengths(day[i] for day in blocks for i in rng.permutation(len(day))) for _ in range(n_draws)]
        out[leg]={"n":sum(len(b) for day in blocks for b in day),"days":len(blocks),"observed_match_block_streak":obs,
                  "permuted_95_range":np.quantile(sim,[.025,.975]).tolist(),"fraction_at_least_observed":(1+sum(s>=obs for s in sim))/(1+n_draws)}
    return out


def evaluate_policies(signals,alternatives,controls,frozen,days,seed,draws,stage,mechanism_rows=None):
    orders,W=day_draws(days,seed,draws)
    counts,canonical,meta=controls_index(controls,[*signals,*alternatives],days)
    if mechanism_rows is not None:
        canonical=list(mechanism_rows)
    policies={p:run_policy(alternatives if p=="P6" else signals,counts,frozen,p,days,orders,W,True) for p in POLICIES}
    adj=holm({k:v["intersection_sign_p"] for k,v in policies.items()})
    for p,v in policies.items():
        v["holm_p_policy_family"]=adj[p]
        if v.get("n_common"):
            months=[x for x in v["months_all_comparators_positive"].values() if x is not None]
            v["stage_gates"]={
                "lower_ci_positive_against_every_comparator":all(
                    u["ci95"] is not None and u["ci95"][0]>0 for u in v["growth_increment"].values()),
                "holm_below_05":adj[p]<.05,
                "at_least_70pct_opening_volume":(v["volume_retained"] or 0)>=.7,
                "at_least_two_positive_leagues":sum(v["leagues_all_comparators_positive"].values())>=2,
                "at_least_80pct_of_months_and_two_months":len(months)>=2 and sum(months)/len(months)>=.8,
                "paper_drawdown_p95_no_increase":v["p95_drawdown_candidate_observed_price_proxy"]<=v["p95_drawdown_current_observed_price_proxy"]+1e-12,
            }
    pairs={}
    for left,right in (("P5","P4"),("P3","P2")):
        nums=np.zeros(len(days));den=nums.copy();ix={d:i for i,d in enumerate(days)}
        for r in signals:
            if y_win(r) is None or (r["leg"],*cell(r)) not in counts:continue
            yy=y_win(r); ret=r["odds"]-1 if yy==1 else -1 if yy==0 else 0
            i=ix[r["day"]];den[i]+=1
            nums[i]+=np.log1p(rate_for(r,frozen,left)*ret)-np.log1p(rate_for(r,frozen,right)*ret)
        pairs[left+"_minus_"+right]=ratio_metric(nums,den,W)
    return {"stage":stage,"seed":seed,"draws":draws,"data":meta,"policies":policies,
            "paired_soft_vs_hard_growth_descriptive":pairs,
            "mechanisms":mechanisms.evaluate(canonical,frozen["h5"],days,W,stage),
            "forecast":forecasts(signals,days,W),
            "forecast_by_leg":{leg:forecasts([r for r in signals if r["leg"]==leg],days,W) for leg in LEGS},
            "loss_streak_permutation":permutation_streaks(signals,seed)}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action",choices=("fit","evaluate"));ap.add_argument("--stage",choices=("discovery","confirmation"),required=True)
    ap.add_argument("--signals",type=Path,required=True);ap.add_argument("--controls",type=Path,required=True)
    ap.add_argument("--alternatives",type=Path);ap.add_argument("--mechanism-rows",type=Path)
    ap.add_argument("--split",type=Path,required=True);ap.add_argument("--frozen",type=Path,required=True)
    ap.add_argument("--out",type=Path);args=ap.parse_args()
    split=json.loads(args.split.read_text());window=split[args.stage];days=block_days(window)
    load=lambda p:read_rows(p,window,args.stage)
    signals=list(load(args.signals))
    if args.action=="fit":
        if args.stage!="discovery":raise ValueError("Sólo se ajusta en descubrimiento.")
        rows=load(args.mechanism_rows) if args.mechanism_rows else controls_index(load(args.controls),signals,days)[1]
        value=fit_policies(signals,rows); dest=args.frozen
    else:
        if not args.out:raise ValueError("--out obligatorio")
        alt=list(load(args.alternatives)) if args.alternatives else []
        canon=load(args.mechanism_rows) if args.mechanism_rows else None
        value=evaluate_policies(signals,alt,load(args.controls),json.loads(args.frozen.read_text()),
                                days,split["seed"],split["bootstrap_draws"],args.stage,canon)
        dest=args.out
    def scalar(x):
        if isinstance(x,np.generic):
            return x.item()
        raise TypeError(f"Tipo JSON no permitido: {type(x)}")
    dest.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False,default=scalar)+"\n")


if __name__=="__main__":
    main()
