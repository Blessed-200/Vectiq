"""Transparent tape statistics by UTC day. Coefficients are fitted once in discovery."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json
import math
import numpy as np
from portfolio import readrows, days_for,save,SEED,unit

def group_days(records, days, nums, dens):
    idx={d:i for i,d in enumerate(days)};s=np.zeros(len(days));n=s.copy()
    for r,num,den in zip(records,nums,dens):
        j=idx[r["day"]];s[j]+=num;n[j]+=den
    draws=np.random.default_rng(SEED).multinomial(len(days),[1/len(days)]*len(days),10000)
    nd=draws@n; good=nd>0
    return {"n":len(records),"active_days":int(np.sum(n>0)),
            "value":float(s.sum()/n.sum()) if n.sum() else None,
            "ci95":np.quantile((draws[good]@s)/nd[good],[.025,.975]).tolist() if np.sum(n>0)>=2 else None}
def average(records,days,values):
    return group_days(records,days,values,[1.]*len(values))
def numbers(rows,days,delayed=False):
    obs=[r for r in rows if unit(r) is not None and (not delayed or
         (r.get("exec_odds") and r["ts"]+3000<=r["exec_ts"]<=r["ts"]+15000 and r["exec_ts"]<r["terminal_ms"]))]
    op="exec_odds" if delayed else "odds"
    roi=[r[op]-1 if r["total"]<r["line"] else -1. if r["total"]>r["line"] else 0. for r in obs]
    wl=[r for r in obs if r["total"]!=r["line"]]
    return dict(n_open=len(rows),n_complete=len(obs),n_win=sum(r["total"]<r["line"] for r in obs),
        quote=average(obs,days,[r[op] for r in obs]),
        roi=average(obs,days,roi),
        win=average(wl,days,[r["total"]<r["line"] for r in wl]))
def design(row,leagues):
    k=row["k"]; scores=row["sets"]; part=row["partial"]; won=sum(a>b for a,b in scores)
    wh,wa=row["winner_home"],row["winner_away"]
    fav=abs((wa/(wh+wa))-.5) if wh and wa else 0.
    return [1.,k==1,k==2,sum(a+b for a,b in scores)/25,sum(part)/4,abs(won-(k-won)),
            fav,int(not bool(wh and wa))]+[int(row["league"]==s) for s in leagues[1:]]
def fit(rows):
    leagues=sorted(set(r["league"] for r in rows));p=8+max(0,len(leagues)-1)
    xt=np.zeros((p,p));xy=np.zeros(p);n=0
    for r in rows:
        if r["center"] is None:continue
        x=np.asarray(design(r,leagues))
        xt+=np.outer(x,x);xy+=x*r["center"];n+=1
    ridge=np.eye(p)*5;ridge[0,0]=1e-6
    return {"leagues":leagues,"fit_n":n,"ridge":5,"coeff":np.linalg.solve(xt+ridge,xy).tolist()}
def hazards(rows):
    out={}
    for grp,sub in [("todos",rows)]+[(x,[r for r in rows if r["league"]==x]) for x in sorted(set(r["league"] for r in rows))]:
        surv=1.;intervals=[]
        for lo,hi in [(0,5),(5,10),(10,20),(20,40),(40,60)]:
            # Risk set before event; censor the unobserved interval at its final tick, as disclosed.
            atrisk=sum(r["duration"]>lo for r in sub)
            moved=[r for r in sub if lo<r["duration"]<=hi and r["status"]=="move"]
            before=surv
            for t,n in sorted(Counter(r["duration"] for r in moved).items()):
                denominator=sum(r["duration"]>=t for r in sub)
                if denominator:surv*=1.-n/denominator
            intervals.append(dict(seconds=[lo,hi],at_risk=atrisk,changes=len(moved),
                down=sum(r["next_odds"]<r["odds"] for r in moved),conditional_change=1.-surv/before if before>0 and atrisk else None,
                cumulative_change=1.-surv if sub else None))
        out[grp]={"n":len(sub),"no_positive_followup":sum(r["duration"]<=0 for r in sub),"bins":intervals}
    return out
def evaluate(stage,folder,params):
    path=Path(folder);rr=readrows(path/"mechanism_rows.jsonl");ss=readrows(path/"signals.jsonl")
    days=days_for(stage);out={"coverage":{},"line":{},"alerts":{}}
    for k in (0,1,2):
        allr=[r for r in rr if r["k"]==k];a=[r for r in allr if r["center"] is not None]
        b=[r for r in a if r["total"] is not None]
        residual=[r["center"]-r["total"] for r in b]
        prices=[r for r in a if r["over"] and r["odds"]]
        out["line"][k]=dict(roots=len(allr),with_center=len(a),winner_prices=sum(bool(r["winner_home"] and r["winner_away"]) for r in a),
            centre_minus_points=average(b,days,residual),
            reconstruction_mae=average(a,days,[abs(np.dot(design(r,params["leagues"]),params["coeff"])-r["center"]) for r in a]),
            mean_overround=average(prices,days,[1/r["over"]+1/r["odds"]-1 for r in prices]))
    for leg in ("ST0","ST3","ST5","combined"):
        sub=[r for r in ss if leg=="combined" or r["leg"]==leg]
        offered=numbers(sub,days);late=numbers(sub,days,True)
        q=[r for r in sub if r.get("exec_odds") and (not r.get("terminal_ms") or r["exec_ts"]<r["terminal_ms"])]
        pairs=[r for r in q if r["total"] is not None]
        out["alerts"][leg]=dict(offered=offered,delayed=late,later_quotes=len(q),
           changed=average(q,days,[abs(r["odds"]-r["exec_odds"])>1e-7 for r in q]),
           price_delta=average(q,days,[r["exec_odds"]-r["odds"] for r in q]),
           matched_roi_change=average(pairs,days,[(r["exec_odds"]-r["odds"])*(r["total"]<r["line"]) for r in pairs]),
           missed=dict(Counter(r["exec_status"] for r in sub)))
    out["hazards"]=hazards(readrows(path/"hazards.jsonl"))
    return out
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("action",choices=("fit","evaluate"));p.add_argument("--stage",required=True)
    p.add_argument("--folder",type=Path,required=True);p.add_argument("--params",type=Path,required=True);p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.stage=="blind":p.error("Use final_once.py")
    if a.action=="fit":
        if a.stage!="discovery":p.error("Discovery only")
        save(a.params,fit(readrows(a.folder/"mechanism_rows.jsonl")))
    else:save(a.out,evaluate(a.stage,a.folder,json.loads(a.params.read_text())))
