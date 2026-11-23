"""Both references on the same contract grid, with the control repriced in each UTC-day bootstrap."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import numpy as np
from policies import controls_index, read_rows, cell, LEGS, y_win, b0_estimator
from policy_stats import day_draws, divide, interval
from portfolio import days_for, SEED, save
from micro import numbers

def comp(rows,counts,days,W):
    good=[r for r in rows if y_win(r) is not None and (r["leg"],*cell(r)) in counts]
    keys=sorted({(r["leg"],*cell(r)) for r in good});C=len(keys);D=len(days)
    if not keys:return {"n":0}
    ix={k:i for i,k in enumerate(keys)};dx={d:i for i,d in enumerate(days)}
    ctrl=np.transpose(np.array([counts[k] for k in keys]),(1,2,0));ixrow=[ix[(r["leg"],*cell(r))] for r in good]
    d=np.array([dx[r["day"]] for r in good]);yy=np.array([y_win(r) for r in good]);odds=np.array([r["odds"] for r in good])
    def matrix(v):
        s=np.zeros((D,C));np.add.at(s,(d,ixrow),v);return s
    res={"n":len(good),"control_unique_games":len(set().union(*(counts.games[k] for k in keys))),
         "control_game_cells":int(ctrl.sum()),"cells":C}
    for name in ("roi","wr"):
        if name=="roi":
            win,loss,alloc,cc=odds-1,-np.ones(len(yy)),np.ones(len(yy)),ctrl
        else:
            win=(yy!=2).astype(float);loss=np.zeros(len(yy));alloc=win
            cc=ctrl.copy();cc[2]=0
        m=np.bincount(d,weights=alloc,minlength=D)
        s=np.bincount(d,weights=np.where(yy==1,win,np.where(yy==0,loss,0)),minlength=D)
        A,B,U=matrix(win),matrix(loss),matrix(alloc)
        diff=b0_estimator(s,np.ones((1,D)),cc,A,B,m,U)[0];boots=b0_estimator(s,W,cc,A,B,m,U)
        value=divide(s.sum(),m.sum());sb=divide(W@s,W@m)
        args=(int(m.sum()),np.count_nonzero(m))
        res[name]={"current":interval(value,sb,*args),"matched_under":interval(value-diff,sb-boots,*args),
                   "increment":interval(diff,boots,*args)}
    return res
def evaluate(stage,folder,split):
    days=days_for(stage);_,W=day_draws(days,SEED,10000);folder=Path(folder)
    roots=list(read_rows(folder/"signals.jsonl",split[stage],stage))
    counts,_,_=controls_index(read_rows(folder/"controls.jsonl",split[stage],stage),roots,days)
    return {key:comp([r for r in roots if key=="combined" or r["leg"]==key],counts,days,W) for key in (*LEGS,"combined")}
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=["discovery","confirmation"],required=True);p.add_argument("--folder",type=Path,required=True);p.add_argument("--out",type=Path,required=True);a=p.parse_args()
    save(a.out,evaluate(a.stage,a.folder,json.loads((Path(__file__).resolve().parents[1]/"split.json").read_text())))
