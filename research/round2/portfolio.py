"""Offline fractional-bankroll risk. One fitted allocation per risk constraint."""
import argparse
from collections import Counter, defaultdict
from datetime import date,timedelta
import hashlib
import heapq
import json
from pathlib import Path
import numpy as np

SEED=20261122
HOUR=3600000
DAY=24*HOUR
LEGS=("ST0","ST3","ST5")
GRID=(0.,.001,.0025,.005,.0075,.01,.015,.02,.025)
TARGETS=(.10,.15,.20)
DRAWS=5000
HORIZON=30
ROOT=Path(__file__).resolve().parents[1]
def save(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+"\n")
def readrows(path):
    with Path(path).open() as f:return [json.loads(s) for s in f]
def days_for(stage):
    pts=json.loads((ROOT/"split.json").read_text())[stage]
    a=date.fromisoformat(pts["start"])
    return [(a+timedelta(days=i)).isoformat() for i in range(pts["days"])]
def unit(r):
    if r["total"] is None:return None
    return r["odds"]-1 if r["total"]<r["line"] else (-1 if r["total"]>r["line"] else 0.)
def training(rows,days):
    rng=np.random.default_rng(SEED)
    ix=rng.integers(len(days),size=(10000,len(days)))
    factors={};hair={}
    for leg in LEGS:
        arr=np.array([[sum(v),len(v),sum(max(x,0) for x in v),sum(pr)] for day in days
               for picks in [[r for r in rows if r["day"]==day and r["leg"]==leg and unit(r) is not None]]
               for v in [[unit(r) for r in picks]] for pr in [[r["odds"]-1 for r in picks]]])
        x=arr[ix].sum(axis=1); valid=x[:,1]>0
        s=arr.sum(axis=0)
        lower=float(np.quantile(x[valid,0]/x[valid,1],.025)) if valid.any() else 0.
        half_kelly=max(0.,lower*s[1]/max(s[3],1e-12)/2)
        factors[leg]=min(1.,half_kelly/.025)
        hair[leg]=max(0.,min(1.,s[0]/s[2])) if s[2]>0 else 0.
    return {"kelly_scale":factors,"win_haircut_per_full_edge":hair}
def bet_list(rows, factors, hair, erosion=0., unknown_loss=False, omit=None):
    bets=[]
    for r in rows:
        if r["leg"]==omit:continue
        u=unit(r); end=r["terminal_ms"]
        if u is None:
            if not unknown_loss:continue
            u=-1.;end=r["ts"]+DAY
        if end<=r["ts"]:raise ValueError("Invalid opening/settlement")
        if u>0:u*=1.-erosion*hair[r["leg"]]
        bets.append((r["ts"],end,r["gid"],r["league"],r["ts"]//(6*HOUR),factors[r["leg"]],u,r["day"],r["leg"]))
    return sorted(bets)
def trajectory(bets,scale,brake=False):
    # One currency unit, scaling homogeneously with equity. Settle ties together.
    equity=1.;peak=1.; reserved=0.; capital=[];heap=[]
    match=defaultdict(float);league=defaultdict(float);session=defaultdict(float)
    executed=0;limited=0
    def settle(t):
        nonlocal reserved,equity,peak
        while heap and heap[0][0]<=t:
            time=heap[0][0]
            while heap and heap[0][0]==time:
                _,_,gid,lg,band,stake,ret=heapq.heappop(heap)
                reserved-=stake;match[gid]-=stake;league[lg]-=stake;session[band]-=stake
                equity+=stake*ret
            peak=max(peak,equity)
            capital.append((time,equity))
    i=0
    while i<len(bets):
        t=bets[i][0];settle(t);j=i
        while j<len(bets) and bets[j][0]==t:j+=1
        base=equity*scale
        requested=[base*r[5]*(.5 if brake and equity<=peak*.90 else 1.) for r in bets[i:j]]
        # Scale simultaneously in each overlapping cap, independent of order.
        bunch=bets[i:j]; caps=[]
        for p,cap,used in ((2,2*base,match),(3,5*base,league),(4,8*base,session)):
            total=defaultdict(float)
            for r,w in zip(bunch,requested):total[r[p]]+=w
            caps.append({k:max(0.,min(1.,(cap-used[k])/v)) if v else 0. for k,v in total.items()})
        whole=min(12*base-reserved,equity-reserved)
        global_ratio=max(0.,min(1.,whole/sum(requested))) if sum(requested) else 0.
        for n,(r,w) in enumerate(zip(bunch,requested),i):
            gid,lg,band=r[2:5]
            stake=w*min(global_ratio,caps[0][gid],caps[1][lg],caps[2][band])
            if w and stake<w-1e-12:limited+=1
            if stake<=1e-12:continue
            reserved+=stake;match[gid]+=stake;league[lg]+=stake;session[band]+=stake;executed+=1
            heapq.heappush(heap,(r[1],n,gid,lg,band,stake,r[6]))
        i=j
    settle(float("inf"))
    return capital,executed,limited
def original(bets,scale,brake=False):
    v,n,l=trajectory(bets,scale,brake)
    ts=np.array([t for t,_ in v]);eq=np.array([1.]+[x for _,x in v]);dd=1-eq/np.maximum.accumulate(eq)
    if len(ts):
        first=min(x[0] for x in bets); times=np.r_[first,ts,ts[-1]]
        weights=np.diff(times); total=max(1,weights.sum())
        ulcer=float(np.sqrt(np.sum(weights*dd**2)/total))
        uw=float(np.sum(weights*(dd>1e-12))/total); longest=0;start=None
        for t,x in zip(times[:-1],dd):
            if x>1e-12 and start is None:start=t
            elif x<=1e-12 and start is not None:longest=max(longest,t-start);start=None
        if start is not None:longest=max(longest,ts[-1]-start)
    else:ulcer=0.;uw=0.;longest=0
    return dict(end_per_100=100*float(eq[-1]),max_dd=float(max(dd)),ulcer=ulcer,
                underwater_time_fraction=uw,longest_underwater_hours=float(longest/HOUR),executed=n,limited=l)
def bootstrap(bets,scale,days,ids,brake=False):
    # Every sampled opening-day carries its own contracts through final settlement.
    # Day batches are sequenced only once those bets clear. Continuous real-time path is reported separately.
    daily=defaultdict(list)
    for b in bets:daily[b[7]].append(b)
    summaries=[]
    for day in days:
        curve,_,_=trajectory(daily[day],scale,brake)
        eq=np.r_[1.,[v for _,v in curve]]
        summaries.append((eq[-1],max(eq),min(eq),max(1-eq/np.maximum.accumulate(eq))))
    a=np.array(summaries);last=np.ones(len(ids));peak=last.copy();worst=last*0
    water=np.zeros(len(ids));ulcer=water.copy()
    for choices in ids.T:
        m=a[choices]
        worst=np.maximum.reduce([worst,m[:,3],1-last*m[:,2]/peak])
        peak=np.maximum(peak,last*m[:,1]);last*=m[:,0]
        dd=np.maximum(0.,1-last/peak);water+=dd>1e-12;ulcer+=dd*dd
    logs=np.log(np.maximum(last,1e-15))
    return dict(p95_dd=float(np.quantile(worst,.95)),mean_log_30=float(logs.mean()),
                p025_p975_log_30=np.quantile(logs,[.025,.975]).tolist(),
                median_end_per_100=float(100*np.median(last)),p05_end_per_100=float(100*np.quantile(last,.05)),
                mean_ulcer_at_daily_close=float(np.sqrt(ulcer/ids.shape[1]).mean()),
                mean_days_underwater_at_close=float(water.mean()))
def schedules(days):
    r=np.random.default_rng(SEED)
    return r.integers(len(days),size=(DRAWS,HORIZON))
def factors(mode,params):
    return {l:1. for l in LEGS} if mode=="igual" else params["kelly_scale"]
def fit(rows,days):
    params=training(rows,days); ids=schedules(days); table=[]
    for mode in ("igual","medio_kelly_descontado"):
        bets=bet_list(rows,factors(mode,params),params["win_haircut_per_full_edge"])
        for scale in GRID:
            row=bootstrap(bets,scale,days,ids);row.update(mode=mode,scale=scale);table.append(row)
    params["policies"]={}
    for target in TARGETS:
        ok=[r for r in table if r["p95_dd"]<=target]
        best=max(ok,key=lambda r:(r["mean_log_30"],-r["mean_ulcer_at_daily_close"],-r["mean_days_underwater_at_close"],-r["scale"]))
        params["policies"][str(target)]={k:best[k] for k in ("mode","scale")}
    params["selection"]=table
    return params
def statistics(rows,days):
    result={};by=defaultdict(set)
    for r in rows:by[r["leg"]].add(r["gid"])
    pnl={l:np.array([sum(unit(r) for r in rows if r["day"]==d and r["leg"]==l and unit(r) is not None) for d in days]) for l in LEGS}
    for j,a in enumerate(LEGS):
        for b in LEGS[j+1:]:
            x,y=pnl[a],pnl[b];active=[i for i,d in enumerate(days) if any(r["day"]==d and r["leg"]==a and unit(r) is not None for r in rows) and any(r["day"]==d and r["leg"]==b and unit(r) is not None for r in rows)]
            xx=x[active];yy=y[active]
            corr=float(np.corrcoef(xx,yy)[0,1]) if len(active)>=3 and np.std(xx)*np.std(yy)>0 else None
            low=int(np.sum((xx<=np.quantile(xx,.2))&(yy<=np.quantile(yy,.2)))) if len(active) else 0
            result[f"{a}/{b}"]=dict(shared_games=len(by[a]&by[b]),union_games=len(by[a]|by[b]),both_active_days=len(active),
                                      daily_pnl_pearson=corr,days_joint_bottom_quintile=low)
    return result
def slip(rows,days):
    idx=np.random.default_rng(SEED).integers(len(days),size=(10000,len(days)))
    out={}
    for every in (30,20):
        for decrement in (.10,.20):
            arr=[]
            for day in days:
                group=[r for r in rows if r["day"]==day and unit(r) is not None];vals=[]
                for r in group:
                    raw=f"{SEED}|{r['gid']}|{r['leg']}".encode()
                    take=int.from_bytes(hashlib.sha256(raw).digest()[:8],"big")/2**64<1/every
                    vals.append(unit(r)-(decrement if take and unit(r)>0 else 0.))
                arr.append([sum(vals),len(vals)])
            v=np.array(arr);d=v[idx].sum(axis=1);good=d[:,1]>0;n=int(v[:,1].sum())
            out[f"1/{every};-{decrement:.2f}"]=dict(n=n,roi=float(v[:,0].sum()/n) if n else None,
                    ci95=np.quantile(d[good,0]/d[good,1],[.025,.975]).tolist() if sum(v[:,1]>0)>=2 else None)
    return out
def evaluate(rows,days,params):
    ids=schedules(days); ret={"pairs":statistics(rows,days),"risk":{},"scenario_slippage":slip(rows,days)}
    ret["opened"]=len(rows);ret["labelled"]=sum(unit(r) is not None for r in rows)
    ret["cross_utc_midnight"]=sum(r["terminal_ms"]//DAY!=r["ts"]//DAY for r in rows if unit(r) is not None)
    for target,policy in params["policies"].items():
        fs=factors(policy["mode"],params); hair=params["win_haircut_per_full_edge"];s=policy["scale"]
        bets=bet_list(rows,fs,hair)
        baseline=bootstrap(bets,s,days,ids)
        report=dict(policy=policy,mc=baseline,observed=original(bets,s),missing_all_lost=original(bet_list(rows,fs,hair,unknown_loss=True),s),
                    brake_original=original(bets,s,True),stress={},contribution={})
        for rho in (.25,.50,.75):
            report["stress"][str(rho)]=bootstrap(bet_list(rows,fs,hair,erosion=rho),s,days,ids)
        for l in LEGS:
            other=bootstrap(bet_list(rows,fs,hair,omit=l),s,days,ids)
            report["contribution"][l]=dict(delta_mean_log_30=baseline["mean_log_30"]-other["mean_log_30"],delta_p95_dd=baseline["p95_dd"]-other["p95_dd"])
        ret["risk"][target]=report
    return ret
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("action",choices=("fit","evaluate"));p.add_argument("--stage",required=True)
    p.add_argument("--rows",type=Path,required=True);p.add_argument("--params",type=Path,required=True);p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.stage=="blind":p.error("Use final_once.py")
    rows=readrows(a.rows);days=days_for(a.stage)
    if a.action=="fit":
        if a.stage!="discovery":p.error("Fit only on discovery")
        save(a.params,fit(rows,days))
    else:save(a.out,evaluate(rows,days,json.loads(a.params.read_text())))
