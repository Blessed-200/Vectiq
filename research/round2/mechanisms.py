"""Seven fixed directional mechanism contrasts on one canonical offer per match-stage."""
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from policy_stats import divide, finite, holm, interval, price_p, ratio_metric, sign_p, y_win


def hour_bin(r):
    return int(r["ts"]//3_600_000%24)//6


def h5_fit(rows):
    levels=defaultdict(lambda:[0.,0])
    for r in rows:
        if r.get("total") is None or not finite(r.get("center")):
            continue
        val=r["center"]-r["total"]
        for key in (str(r["k"]), f'{r["k"]}|{r["league"]}|{hour_bin(r)}'):
            levels[key][0]+=val
            levels[key][1]+=1
    return dict(levels)


def points(r):
    sets=r.get("sets") or []
    partial=r.get("partial") or [0,0]
    return sum(sum(t) for t in sets)+sum(partial)


def build(rows,h5):
    items={f"H{i}":[] for i in range(1,8)}
    for r in rows:
        y=y_win(r)
        if y is None:
            continue
        k,lg,d=r["k"],r["league"],r["day"]
        fs=r.get("final_sets") or []
        pm=price_p(r)
        oddcent=int(np.floor(100*r["odds"]+.5000001))
        def put(key,group,x,target):
            if finite(x) and finite(target):
                items[key].append((d,group,float(x),float(target)))
        if k==1 and (r.get("sets") or []) and len(fs)>=3:
            a,b=r["sets"][0]
            sign=1 if a>b else -1
            margin=abs(a-b)
            second=sign*(fs[1][0]-fs[1][1])
            third=sign*(fs[2][0]-fs[2][1])
            if margin>=5:
                put("H1",(),1.,third-second)
            if y!=2 and finite(r.get("early_margin")):
                put("H2",(lg,margin),sign*r["early_margin"],y-pm)
            put("H3",(lg,margin),r.get("player_volatility"),-(second+third)/2)
        if len(fs)>k and finite(r.get("winner_home")) and finite(r.get("winner_away")):
            odds1,odds2=r["winner_home"],r["winner_away"]
            if odds1>1 and odds2>1:
                mismatch=int(min(.49999, abs(odds2/(odds1+odds2)-.5))*10)
                deuce=sum(min(a,b)>=10 for a,b in fs[k:])/(len(fs)-k)
                put("H4",(lg,k,mismatch),r.get("player_deuce"),deuce)
        if finite(r.get("center")) and str(k) in h5:
            total,n=h5[str(k)]; common=total/n
            local,count=h5.get(f"{k}|{lg}|{hour_bin(r)}",(0,0))
            adj=(local+20*common)/(count+20)
            target=r["center"]-r["total"]
            put("H5",(),1.,(target-common)**2-(target-adj)**2)
        if y!=2 and finite(r.get("prior_price")) and finite(r.get("price_prior_ts")):
            if r["price_prior_ts"]>=r["ts"]:
                raise ValueError("H6: la cuota anterior no precede a la alerta.")
            put("H6",(k,lg,r["line"],oddcent),int(r["odds"]<r["prior_price"]-1.e-10),pm-y)
        if y!=2:
            put("H7",(k,lg,oddcent),r["line"]-points(r),y-pm)
    return items


def within_slope(rows,day_index,W):
    # Only cells with covariate variation can identify a within-cell slope.
    values=defaultdict(set)
    for _,g,x,_ in rows:
        values[g].add(x)
    cells={g:i for i,g in enumerate(g for g,s in values.items() if len(s)>1)}
    good=[v for v in rows if v[1] in cells]
    if not good:
        return {"estimate":interval(np.nan,[],0,0),"n_raw":len(rows),"cells":0,"sign_p":1.}
    arr=np.zeros((5,len(day_index),len(cells)))
    for d,g,x,y in good:
        j=day_index[d];i=cells[g]
        arr[:,j,i]+=(1,x,y,x*x,x*y)
    def ratios(weights):
        out=[]
        for batch in np.array_split(weights,max(1,(len(weights)+63)//64)):
            n,x,y,xx,xy=np.einsum('rd,fdc->frc',batch,arr,optimize=False)
            n_safe=np.maximum(n,1)
            out.extend(divide((xy-x*y/n_safe).sum(axis=1),
                              np.where((xx-x*x/n_safe).sum(axis=1)>1.e-10,(xx-x*x/n_safe).sum(axis=1),0)))
        return np.array(out)
    mean_x=arr[1].sum(axis=0)/arr[0].sum(axis=0)
    mean_y=arr[2].sum(axis=0)/arr[0].sum(axis=0)
    scores=(arr[4]-mean_x*arr[2]-mean_y*arr[1]+mean_x*mean_y*arr[0]).sum(axis=1)
    days_used=sum(arr[0].sum(axis=1)>0)
    value=ratios(np.ones((1,len(day_index))))[0]
    return {"estimate":interval(value,ratios(W),len(good),days_used),"n_raw":len(rows),"cells":len(cells),
            "sign_p":sign_p(scores)}


def evaluate(rows,h5,days,W,stage):
    days_i={x:i for i,x in enumerate(days)}
    out={}
    for h,items in build(rows,h5).items():
        if h in ("H1","H5"):
            n=np.zeros(len(days));y=np.zeros(len(days))
            for d,_,_,val in items:
                n[days_i[d]]+=1;y[days_i[d]]+=val
            out[h]={"estimate":ratio_metric(y,n,W),"n_raw":len(items),"sign_p":sign_p(divide(y,n))}
        else:
            out[h]=within_slope(items,days_i,W)
    out["H5"]["same_data_optimism"]=stage=="discovery"
    adjusted=holm({h:v["sign_p"] for h,v in out.items()})
    for h in out:
        out[h]["holm_p_mechanism_family"]=adjusted[h]
    return {"n_game_stages":len(rows),"tests":out,"interpretation":"Directional descriptions; no filter promoted by this family."}
