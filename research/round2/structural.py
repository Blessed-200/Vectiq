"""Prequential hierarchical point/set model and finite-coverage latent-state oracle.

All public probabilities used for binary scoring condition on a non-push. p_struct_win
and p_push instead are unconditional probabilities, as required for unit P&L.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import heapq
import json
from math import ceil, comb, exp, floor, isfinite, log, sqrt
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"src"))
from vectiq.signals import markov

DAY = 86_400_000
ST5_KNOWN = 1_794_102_401_000
LEGS = ("ST0","ST3","ST5")
GRID = np.linspace(-1.2,1.2,33)
P = expit(GRID)
Q = 1-P
DMAX = 400
SMAX = 80
DGRID = np.linspace(-.9,.9,7)
EPS = 1e-6
SEED = 20261122
CUTS = {"discovery":(1792713600000,1794355200000), "confirmation":(1794355200000,1794873600000),
        "blind":(1794873600000,1795392000000)}
PRIOR_VAR = .25 ** 2
RESIDUAL_VAR = .22 ** 2
INFO_POINTS = 24.
PLAYER_HALF_DAYS = 14.
COEFF_PRIOR = (.25,.30)


def clip(p):
    return np.clip(p,EPS,1-EPS)


def numeric(x):
    try:
        v=float(x)
        return v if isfinite(v) else None
    except (TypeError,ValueError):
        return None


def pair_devig(home,away):
    h,a=numeric(home),numeric(away)
    return (1/h)/(1/h+1/a) if h and a and min(h,a)>1 else None


def fair(row):
    u,o=numeric(row.get("odds")),numeric(row.get("over"))
    p=pair_devig(u,o)
    if p is not None:
        return p,"paired"
    return ((1/u,"under_only") if u and u>1 else (None,"unavailable"))


def player(row,side):
    val=row.get("player_"+side+"_id")
    if val is not None and str(val).strip() not in ("","0","None"):
        return "id:"+str(val)
    name=row.get(side)
    return "name:"+str(row.get("league"))+"|"+str(name).strip().casefold() if name else None


def legal(s):
    h,a=s
    return min(h,a)>=0 and (max(h,a)==11 and min(h,a)<=9 or max(h,a)>=12 and abs(h-a)==2)


def safe_scores(raw):
    if raw is None:
        return ()
    return tuple((int(v[0]),int(v[1])) for v in raw)


def checked_final(sets):
    if not 4<=len(sets)<=7:
        raise ValueError("invalid number of sets in terminal event")
    h=a=0
    for x,y in sets:
        if max(h,a)>=4 or not legal((x,y)):
            raise ValueError("invalid terminal set sequence")
        h+=int(x>y); a+=int(y>x)
    if max(h,a)!=4:
        raise ValueError("terminal event has no fourth set win")


def deuce_base(p):
    return comb(20,10)*(p*(1-p))**10


@dataclass
class Skill:
    mean:float=0.
    var:float=PRIOR_VAR
    touched:int=0
    n:int=0

    def at(self,ms):
        if not self.touched:
            return self.mean,self.var
        keep=2.**(-max(0,ms-self.touched)/DAY/PLAYER_HALF_DAYS)
        return self.mean*keep,PRIOR_VAR+(self.var-PRIOR_VAR)*keep*keep


@dataclass
class Deuces:
    n:int=0
    observed:float=0.
    expected:float=0.

    def shift(self,prior_n):
        baseline=deuce_base(.5)
        p=(self.observed+prior_n*baseline)/(self.n+prior_n)
        e=(self.expected+prior_n*baseline)/(self.n+prior_n)
        return float(logit(clip(p))-logit(clip(e)))

    def add(self,n,o,e):
        self.n+=n; self.observed+=o; self.expected+=e


@dataclass
class League:
    n:int=0
    ss:float=0.
    noise:float=0.
    deuce:Deuces=field(default_factory=Deuces)

    def residual_variance(self):
        return float(np.clip((self.ss-self.noise+50*RESIDUAL_VAR)/(self.n+50),.08**2,.50**2))


class Book:
    def __init__(self,state=None):
        s=state or {}
        self.skills={k:Skill(**x) for k,x in s.get("skills",{}).items()}
        self.players_deuce={k:Deuces(**x) for k,x in s.get("players_deuce",{}).items()}
        self.leagues={k:League(**{**x,"deuce":Deuces(**x["deuce"])}) for k,x in s.get("leagues",{}).items()}
        self.seen=set(s.get("seen",[]))
        self.clock=int(s.get("clock",0))

    def state(self):
        return {"skills":{k:asdict(v) for k,v in self.skills.items()},
                "players_deuce":{k:asdict(v) for k,v in self.players_deuce.items()},
                "leagues":{k:asdict(v) for k,v in self.leagues.items()},
                "seen":sorted(self.seen),"clock":self.clock}

    def components(self,row,t):
        a,b=player(row,"home"),player(row,"away")
        ma,va=self.skills.get(a,Skill()).at(t)
        mb,vb=self.skills.get(b,Skill()).at(t)
        league=self.leagues.get(str(row["league"]),League())
        deuce=league.deuce.shift(100)
        for z in (a,b):
            if z in self.players_deuce:
                deuce+=.5*self.players_deuce[z].shift(50)
        return ma-mb,va+vb+league.residual_variance(),int(np.argmin(abs(DGRID-np.clip(deuce,-.9,.9))))

    def observe(self,row):
        gid=str(row["gid"])
        if gid in self.seen:
            return
        t=int(row["terminal_ms"])
        if t<self.clock:
            raise ValueError("out of order historical match")
        sets=safe_scores(row["final_sets"])
        checked_final(sets)
        h=sum(a for a,b in sets); away=sum(b for a,b in sets)
        if row.get("total") is not None and h+away!=int(row["total"]):
            raise ValueError("terminal total disagrees with all sets")
        self.seen.add(gid)
        self.clock=t
        a,b=player(row,"home"),player(row,"away")
        league=self.leagues.setdefault(str(row["league"]),League())
        sa,sb=self.skills.get(a,Skill()),self.skills.get(b,Skill())
        ma,va=sa.at(t); mb,vb=sb.at(t)
        mu=ma-mb
        prior_league=league.residual_variance()
        q=float(expit(mu)); actual=(h+.5)/(h+away+1)
        info=min(INFO_POINTS,h+away)*q*(1-q)
        denom=1+info*(va+vb+prior_league)
        correction=min(INFO_POINTS,h+away)*(actual-q)/denom
        if a and b and a!=b:
            self.skills[a]=Skill(ma+va*correction,max(.001,va-va*va*info/denom),t,sa.n+1)
            self.skills[b]=Skill(mb-vb*correction,max(.001,vb-vb*vb*info/denom),t,sb.n+1)
        league.ss+=(float(logit(actual))-mu)**2
        league.noise+=1/((h+away)*max(.04,actual*(1-actual)))+va+vb
        before=league.deuce.shift(100)
        n=len(sets); observed=sum(min(x,y)>=10 for x,y in sets)
        raw=float(deuce_base(actual))
        expected=float(expit(logit(clip(raw))+before))
        league.deuce.add(n,observed,n*raw)
        league.n+=1
        for who in {a,b}-{None}:
            self.players_deuce.setdefault(who,Deuces()).add(n,observed,n*expected)


@lru_cache(maxsize=160)
def set_pmf(d,partial):
    """Joint future total set length and winning side, conditional on current score."""
    a,b=partial
    if min(a,b)<0 or max(a,b)>9:
        raise ValueError("Only early next-set states are in the declared universe")
    wa=np.zeros((len(P),SMAX+1)); wb=np.zeros_like(wa)
    for j in range(b,10):
        wa[:,11+j]=comb(10-a+j-b,j-b)*P**(11-a)*Q**(j-b)
    for i in range(a,10):
        wb[:,11+i]=comb(10-b+i-a,i-a)*Q**(11-b)*P**(i-a)
    rd=comb(20-a-b,10-a)*P**(10-a)*Q**(10-b)
    scale=1-rd+rd*exp(float(DGRID[d]))
    wa/=scale[:,None]; wb/=scale[:,None]
    # Probability of reaching 10-10 is tilted, then the two-point blocks
    # retain the Bernoulli win-by-two transition.
    reach=rd*exp(float(DGRID[d]))/scale
    for j in range(1,(SMAX-20)//2+1):
        wa[:,20+2*j]=reach*(2*P*Q)**(j-1)*P**2
        wb[:,20+2*j]=reach*(2*P*Q)**(j-1)*Q**2
    norm=(wa+wb).sum(axis=1)
    wa/=norm[:,None]; wb/=norm[:,None]
    return wa,wb


@lru_cache(maxsize=7)
def kernel(d):
    wa,wb=set_pmf(d,(0,0))
    n=len(P)
    mass=np.zeros((5,5,n,DMAX+1))
    awin=np.zeros((5,5,n))
    mass[4,:,:,0]=1; mass[:,4,:,0]=1; awin[4,:,:]=1
    for i in range(3,-1,-1):
        for j in range(3,-1,-1):
            for z in range(n):
                mass[i,j,z]=np.convolve(wa[z],mass[i+1,j,z])[:DMAX+1]+np.convolve(wb[z],mass[i,j+1,z])[:DMAX+1]
            awin[i,j]=wa.sum(axis=1)*awin[i+1,j]+wb.sum(axis=1)*awin[i,j+1]
    return mass,awin


@lru_cache(maxsize=192)
def outcome_kernel(d,home_won,away_won,partial):
    mass,awin=kernel(d)
    if partial==(0,0):
        output=mass[home_won,away_won]
        win=awin[home_won,away_won]
    else:
        wa,wb=set_pmf(d,partial)
        output=np.empty((len(P),DMAX+1))
        for z in range(len(P)):
            output[z]=np.convolve(wa[z],mass[home_won+1,away_won,z])[:DMAX+1]+np.convolve(wb[z],mass[home_won,away_won+1,z])[:DMAX+1]
        win=wa.sum(axis=1)*awin[home_won+1,away_won]+wb.sum(axis=1)*awin[home_won,away_won+1]
    cdf=np.cumsum(output,axis=1)
    cdf/=cdf[:,-1,None]
    return cdf,win


@lru_cache(maxsize=1024)
def score_loglik(d,score):
    a,b=score
    if not legal(score):
        raise ValueError("illegal completed set at prediction")
    hi,lo=max(a,b),min(a,b)
    fav,other=(P,Q) if a>b else (Q,P)
    tilt=float(DGRID[d]); normal=1-deuce_base(P)+deuce_base(P)*exp(tilt)
    if lo<10:
        return log(comb(10+lo,lo))+11*np.log(fav)+lo*np.log(other)-np.log(normal)
    return np.log(deuce_base(P))+tilt-np.log(normal)+(hi-12)*np.log(2*P*Q)+2*np.log(fav)


def posterior(row,book,t):
    sets=safe_scores(row.get("sets"))
    if int(row["k"])!=len(sets) or any(not legal(s) for s in sets):
        raise ValueError("score at decision disagrees with k")
    part=tuple(row.get("partial") or (0,0))
    if len(part)!=2 or any(type(x) is not int or x<0 for x in part) or sum(part)>4:
        raise ValueError("unknown or late next-set score")
    if not sets and part!=(0,0):
        raise ValueError("ST0 requires a score of zero")
    home_won=sum(a>b for a,b in sets); away_won=len(sets)-home_won
    if max(home_won,away_won)>=4:
        raise ValueError("decision after match end")
    mu,var,d=book.components(row,t)
    lw=-(GRID-mu)**2/(2*var)
    for score in sets:
        lw+=score_loglik(d,score)
    if part!=(0,0):
        a,b=part
        r=comb(20-a-b,10-a)*P**(10-a)*Q**(10-b)
        fac=exp(float(DGRID[d]))-1
        lw+=a*np.log(P)+b*np.log(Q)+np.log1p(r*fac)-np.log1p(deuce_base(P)*fac)
    cdf,pwin=outcome_kernel(d,home_won,away_won,part)
    quoted=pair_devig(row.get("winner_home"),row.get("winner_away"))
    if quoted is not None:
        lw-=.5*((logit(clip(pwin))-logit(clip(quoted)))/.70)**2
    w=np.exp(lw-lw.max()); w/=w.sum()
    played=sum(x+y for x,y in sets)
    thresh=float(row["line"])-played
    if not isfinite(thresh) or thresh>DMAX:
        raise ValueError("invalid or unsupported total line")
    ix=ceil(thresh)-1
    pu=np.zeros(len(P)) if ix<0 else cdf[:,ix]
    push=np.zeros(len(P))
    if thresh.is_integer() and thresh>=0:
        j=int(thresh)
        push=cdf[:,j]-(cdf[:,j-1] if j else 0)
    return w,pu,push,(d,home_won,away_won,part),played


def quantiles(x,w,levels=(.025,.975)):
    ix=np.argsort(x)
    wc=w[ix].cumsum()
    return [float(x[ix[min(np.searchsorted(wc,l),len(ix)-1)]]) for l in levels]


def predict_one(row,book,cutoff,calibrator=None,keep_latent=False):
    if book.clock>=cutoff and book.clock:
        raise ValueError("historical final at or after the decision cutoff")
    if int(row["ts"])>cutoff:
        raise ValueError("book/score observed after allowed execution information cutoff")
    w,pu,pp,key,played=posterior(row,book,cutoff)
    win,push=float(w@pu),float(w@pp)
    raw_p=float(clip(win/max(EPS,1-push)))
    low,high=quantiles(pu/np.maximum(EPS,1-pp),w)
    leg=row["leg"]
    p_market,source=fair(row)
    if leg=="ST0":
        current=.52
    elif leg=="ST3":
        current=1/row["odds"]
    elif leg=="ST5":
        current=markov.p_under(list(safe_scores(row["sets"])),float(row["line"])) if cutoff>=ST5_KNOWN else None
    else:
        raise ValueError(f"unknown leg {leg!r}")
    params=(calibrator or {}).get(leg,[1.,0.])
    def mapped(v):
        return float(expit(params[0]*logit(clip(v))+params[1]))
    out={"p_market":p_market,"market_probability_source":source,"p_current":current,
         "p_struct":raw_p,"p_struct_raw":raw_p,"p_struct_win":win,"p_push":push,
         "p_struct_low":low,"p_struct_high":high,"p_cal":mapped(raw_p),
         "p_cal_low":mapped(low),"p_cal_high":mapped(high),
         "structural_history_n":len(book.seen),"structural_last_final_ms":book.clock,
         "winner_market_used":pair_devig(row.get("winner_home"),row.get("winner_away")) is not None}
    latent=(w,pu,pp,key,played) if keep_latent else None
    return out,latent


def fit_beta(history):
    out={}
    for leg in LEGS:
        records=history.get(leg,[])
        if len(records)<50 or len({r[1] for r in records})<2:
            out[leg]=[1.,0.]
            continue
        v=np.asarray(records,dtype=float)
        x=logit(clip(v[:,0])); y=v[:,1]
        def objective(ab):
            a,b=ab; z=a*x+b; p=expit(z)
            penalty=.5*((a-1)/COEFF_PRIOR[0])**2+.5*(b/COEFF_PRIOR[1])**2
            loss=np.sum(np.logaddexp(0,z)-y*z)+penalty
            grad=[np.sum((p-y)*x)+(a-1)/COEFF_PRIOR[0]**2,np.sum(p-y)+b/COEFF_PRIOR[1]**2]
            return float(loss),np.array(grad)
        result=minimize(objective,[1.,0.],jac=True,bounds=((.1,3),(-2,2)),method="L-BFGS-B")
        if not result.success:
            raise RuntimeError("beta calibration optimizer did not converge")
        out[leg]=result.x.tolist()
    return out


def predict_rows(rows,*,history=(),calibrator=None,latency_ms=0):
    """Rows are returned in their original order. History is one scored event per match."""
    if latency_ms<0:
        raise ValueError("negative latency")
    out=[None]*len(rows); book=Book()
    events=sorted(history,key=lambda r:(int(r["terminal_ms"]),str(r["gid"])))
    i=0
    for j in sorted(range(len(rows)),key=lambda j:int(rows[j].get("alert_ms",rows[j]["ts"]))):
        r=rows[j]; cutoff=int(r.get("alert_ms",r["ts"]))-latency_ms
        while i<len(events) and int(events[i]["terminal_ms"])<cutoff:
            book.observe(events[i]); i+=1
        if str(r["gid"]) in book.seen:
            raise ValueError("predicting an already settled match")
        out[j]={**r,**predict_one(r,book,cutoff,calibrator)[0]}
    return out


def read_jsonl(path):
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path,rows):
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")


def run_stage(stage,signals,alternatives,games,carry=None,keep_latent=False,warmup=()):
    lo,hi=CUTS[stage]
    carry=carry or {}
    if stage!="discovery" and carry.get("through_ms")!=lo:
        raise ValueError("prior checkpoint does not end at this stage boundary")
    book=Book(carry.get("book"))
    if warmup:
        if stage!="discovery" or carry:
            raise ValueError("warm-up history is only allowed before discovery starts")
        for event in sorted(warmup,key=lambda g:(int(g["terminal_ms"]),str(g["gid"]))):
            if not lo-3*3_600_000<=int(event["terminal_ms"])<lo:
                raise ValueError("warm-up event lies outside the sealed three-hour window")
            book.observe(event)
    ready={leg:list(carry.get("beta_history",{}).get(leg,[])) for leg in LEGS}
    history_ids=set(carry.get("beta_alerts",[]))
    events=sorted(games,key=lambda r:(int(r["terminal_ms"]),str(r["gid"])))
    event_by_id={}
    for g in events:
        gid=str(g["gid"]); t=int(g["terminal_ms"])
        if not lo<=t<hi or gid in event_by_id or gid in book.seen:
            raise ValueError("duplicate or cross-partition terminal game")
        event_by_id[gid]=g
    pred=[None]*len(signals); alt=[None]*len(alternatives); lat=[None]*len(signals)
    records=[(r,True,i) for i,r in enumerate(signals)]+[(r,False,i) for i,r in enumerate(alternatives)]
    records.sort(key=lambda e:(int(e[0]["ts"]),str(e[0]["gid"]),e[0]["leg"],not e[1],e[2]))
    idx=0; pending=[]; serial=0; current_day=None; ab=carry.get("frozen_beta",{})
    n_cal={leg:len(v) for leg,v in ready.items()}

    def release_cal(before):
        while pending and pending[0][0]<before:
            _,_,leg,p,gid,line=heapq.heappop(pending)
            total=int(event_by_id[gid]["total"])
            if total!=line:
                ready[leg].append([p,int(total<line)])

    for row,official,j in records:
        t=int(row["ts"]); gid=str(row["gid"]); leg=row["leg"]
        if not lo<=t<hi or (row.get("stage") and row["stage"]!=stage):
            raise ValueError("row outside authorized stage")
        day=t//DAY
        if day!=current_day:
            if stage!="blind":
                release_cal(day*DAY)
                ab=fit_beta(ready)
                n_cal={k:len(v) for k,v in ready.items()}
            current_day=day
        while idx<len(events) and int(events[idx]["terminal_ms"])<t:
            book.observe(events[idx]); idx+=1
        if gid in book.seen:
            raise ValueError("already finished game passed as decision")
        features,latent=predict_one(row,book,t,ab,keep_latent and official)
        features["beta_n"]=n_cal[leg]
        features["beta_cutoff_exclusive_ms"]=lo if stage=="blind" else day*DAY
        enriched={**row,**features}
        if official:
            pred[j]=enriched; lat[j]=latent
            gid_leg=leg+"|"+gid
            if stage!="blind" and row.get("signal") and gid_leg not in history_ids and gid in event_by_id:
                known=int(event_by_id[gid]["terminal_ms"])
                if known<=t:
                    raise ValueError("label already known for a supposed alert")
                history_ids.add(gid_leg)
                heapq.heappush(pending,(known,serial,leg,features["p_struct"],gid,float(row["line"])))
                serial+=1
        else:
            alt[j]=enriched
    while idx<len(events):
        book.observe(events[idx]); idx+=1
    if stage!="blind":
        release_cal(hi); ab=fit_beta(ready)
    state={"through_ms":hi,"stage":stage,"book":book.state(),"beta_history":ready,
           "beta_alerts":sorted(history_ids),"frozen_beta":ab}
    return pred,alt,state,lat


def select_fixed(dates,criterion,push=None,half=False):
    mask=np.ones(len(dates),dtype=bool)
    if not half:
        return mask
    mask[:]=False
    for day in np.unique(dates):
        ix=np.flatnonzero(dates==day); m=max(1,len(ix)//2)
        if push is None:
            chosen=np.argsort(-np.round(criterion[ix],12),kind="stable")[:m]
        else:
            # Fractional programming: maximize sum(Pwin)/sum(1-Ppush)
            # at the same daily count, even when integer-line pushes vary.
            ratio=.5
            for _ in range(25):
                order=np.argsort(-(criterion[ix]-ratio*(1-push[ix])),kind="stable")[:m]
                nxt=criterion[ix[order]].sum()/(1-push[ix[order]]).sum()
                if abs(nxt-ratio)<1e-12:
                    break
                ratio=nxt
            chosen=order
        mask[ix[chosen]]=True
    return mask


def day_ratio(days,num,den,weights,n_days):
    n=np.bincount(days,weights=num,minlength=n_days)
    d=np.bincount(days,weights=den,minlength=n_days)
    nb=weights@n; db=weights@d
    good=db>0
    active=int(np.sum(d>0))
    est=float(n.sum()/d.sum()) if d.sum()>0 else None
    ci=np.quantile(nb[good]/db[good],[.025,.975]).tolist() if active>=2 and good.any() else None
    return {"value":est,"ci95":ci,"days":active,"valid_draws":int(good.sum())}


def simulate_leg(rows,latents,stage,rng,nsim,weights):
    if not rows:
        return {"designs":0,"scenarios":0}
    order=rng.integers(len(rows),size=nsim)
    zorder=np.argsort(order,kind="stable")
    cuts=np.flatnonzero(np.diff(order[zorder]))+1
    response=np.empty(nsim); true_win=np.empty(nsim); true_push=np.empty(nsim)
    for indices in np.split(zorder,cuts):
        ix=order[indices[0]]
        w,pu,pp,key,played=latents[ix]
        z=rng.choice(len(P),size=len(indices),p=w)
        true_win[indices]=pu[z]; true_push[indices]=pp[z]
        cdf,_=outcome_kernel(*key)
        for latent in np.unique(z):
            positions=indices[z==latent]
            response[positions]=played+np.searchsorted(cdf[latent],rng.random(len(positions)),side="left")
    odds=np.array([r["odds"] for r in rows])[order]
    line=np.array([r["line"] for r in rows])[order]
    days=np.array([(r["ts"]-CUTS[stage][0])//DAY for r in rows],dtype=int)[order]
    conditional=clip(true_win/np.maximum(EPS,1-true_push))
    marginal=np.array([r["p_struct"] for r in rows])[order]
    marginal_push=np.array([r["p_push"] for r in rows])[order]
    current=np.array([r["p_current"] if r["p_current"] is not None else np.nan for r in rows])[order]
    y=(response<line).astype(float); l=(response>line).astype(float); resolved=y+l
    ret=(odds-1)*y-l
    ev=odds*true_win+true_push-1
    entropy=-(true_win*np.log(conditional)+(1-true_win-true_push)*np.log1p(-conditional))
    n_days=weights.shape[1]

    def measure(mask,p):
        documented=mask & np.isfinite(p)
        x=clip(np.where(np.isfinite(p),p,.5))
        true_ce=-(true_win*np.log(x)+(1-true_win-true_push)*np.log1p(-x))
        ll=-(y*np.log(x)+l*np.log1p(-x))
        def ratio(num,den,keep=mask):
            return day_ratio(days,num*keep,den*keep,weights,n_days)
        return {"scenarios":int(mask.sum()),"unique_source_designs":len(np.unique(order[mask])),
                "nonpush_outcomes":int(resolved[mask].sum()),"scored_nonpush":int(resolved[documented].sum()),
                "simulated_roi":ratio(ret,np.ones(nsim)),"simulated_wr":ratio(y,resolved),
                "conditional_expected_roi":ratio(ev,np.ones(nsim)),
                "conditional_expected_wr":ratio(true_win,1-true_push),
                "simulated_logloss":ratio(ll,resolved,documented),
                "conditional_expected_logloss":ratio(true_ce,1-true_push,documented),
                "expected_logloss_gap_to_latent":ratio(true_ce-entropy,1-true_push,documented)}

    out={"designs":len(rows),"source_days":len(set((r["ts"]//DAY) for r in rows)),"scenarios":nsim,
         "current_available_scenarios":int(np.isfinite(current).sum()),"policies":{}}
    mask=np.ones(nsim,dtype=bool)
    out["policies"]["all_current"]=measure(mask,current)
    out["policies"]["all_structural"]=measure(mask,marginal)
    out["policies"]["all_latent_oracle"]=measure(mask,conditional)
    choices={}
    for name,p,push in (("latent_oracle",conditional,true_push),("structural",marginal,marginal_push),
                        ("current",current,marginal_push)):
        avail=np.isfinite(p)
        if not avail.all():
            # No price/forecast filling for the deployed reference.
            if not avail.any():
                continue
            # An incomplete reference is compared only on its labelled all-volume row.
            continue
        pu=p*(1-push)
        by_roi=select_fixed(days,odds*pu+push-1,half=True)
        by_wr=select_fixed(days,pu,push=push,half=True)
        choices[name]=(by_roi,by_wr)
        out["policies"]["half_roi_"+name]=measure(by_roi,p)
        out["policies"]["half_wr_"+name]=measure(by_wr,p)
    if "current" in choices:
        deltas={}
        for label,mask_idx,amount,den in (("roi",0,ev,np.ones(nsim)),("wr",1,true_win,1-true_push)):
            a,b=choices["latent_oracle"][mask_idx],choices["current"][mask_idx]
            def totals(mask,measure):
                return np.bincount(days,weights=mask*measure,minlength=n_days)
            ax,ad=totals(a,amount),totals(a,den); bx,bd=totals(b,amount),totals(b,den)
            ca,cb=weights@ad,weights@bd
            valid=(ca>0)&(cb>0)
            diff=(weights@ax)[valid]/ca[valid]-(weights@bx)[valid]/cb[valid]
            deltas[label]={"value":float(ax.sum()/ad.sum()-bx.sum()/bd.sum()),
                           "ci95":np.quantile(diff,[.025,.975]).tolist() if sum((ad+bd)>0)>=2 else None,
                           "days":int(sum((ad+bd)>0)),"valid_draws":int(valid.sum())}
        out["half_oracle_gain_vs_current"]=deltas
    return out


def simulate_oracle(rows,latents,stage,nsim=50000,seed=SEED):
    if nsim<50000:
        raise ValueError("the preregistration requires at least 50000 per strategy and joint mixture")
    rng=np.random.default_rng(seed)
    nd=(CUTS[stage][1]-CUTS[stage][0])//DAY
    weights=rng.multinomial(nd,np.ones(nd)/nd,size=10000)
    result={"stage":stage,"seed":seed,"scenarios_per_population":nsim,"day_resamples":len(weights),
            "interpretation":"Known latent rally probability conditional on the fitted set law and visible design; future scores drawn from its full score distribution. 100% or half per source day; ROI and WR ranked separately; flat stake. Combined is a mixture of alert designs, not a joint bankroll simulator. Simulation intervals resample UTC opportunity days, conditional on model, hyperparameters and realized finite scenario pool."}
    for label in (*LEGS,"combined"):
        ix=[i for i,r in enumerate(rows) if r.get("signal") and (label=="combined" or r["leg"]==label)]
        result[label]=simulate_leg([rows[i] for i in ix],[latents[i] for i in ix],stage,rng,nsim,weights)
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",choices=["predict"])
    ap.add_argument("--stage",required=True,choices=CUTS)
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--out-dir",type=Path,required=True)
    ap.add_argument("--state-in",type=Path)
    ap.add_argument("--oracle-out",type=Path)
    ap.add_argument("--warmup-games",type=Path)
    a=ap.parse_args()
    if (a.stage=="discovery")!=(a.state_in is None):
        raise ValueError("only discovery starts without a prior checkpoint")
    src=a.data_root/a.stage
    s,alt,gs=[read_jsonl(src/name) for name in ("signals.jsonl","alternatives.jsonl","games.jsonl")]
    carry=json.loads(a.state_in.read_text()) if a.state_in else None
    warmup=read_jsonl(a.warmup_games) if a.warmup_games is not None else ()
    resolved,other,state,latent=run_stage(a.stage,s,alt,gs,carry,bool(a.oracle_out),warmup)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    write_jsonl(a.out_dir/"predictions.jsonl",resolved)
    write_jsonl(a.out_dir/"alternative_predictions.jsonl",other)
    (a.out_dir/"structural_state.json").write_text(json.dumps(state,ensure_ascii=False,allow_nan=False,sort_keys=True))
    if a.oracle_out:
        out=simulate_oracle(resolved,latent,a.stage)
        a.oracle_out.write_text(json.dumps(out,indent=2,sort_keys=True,allow_nan=False)+"\n")


if __name__=="__main__":
    main()
