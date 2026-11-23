"""Secondary H6 diagnostic registered after the discovery canonical cohort proved empty."""
import argparse
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from mechanisms import within_slope
from policies import read_rows
from policy_stats import block_days,day_draws,finite,price_p,y_win


def evaluate(stage,folder,days=None,seed=20261122,draws=10000,split=None):
    reg=split if isinstance(split,dict) else json.loads(Path(split or Path(__file__).resolve().parents[1]/"split.json").read_text())
    win=reg[stage]
    days=days or block_days(win)
    counts=Counter(); chosen={}
    for r in read_rows(Path(folder)/"controls.jsonl",win,stage):
        counts["control_offers"]+=1
        if not finite(r.get("prior_price")) or not finite(r.get("price_prior_ts")):
            continue
        counts["offers_with_previous"]+=1
        if r["price_prior_ts"]>=r["ts"]:
            raise ValueError("H6-E necesita una cuota anterior causal.")
        key=(r["gid"],r["k"])
        rank=(r["ts"],abs(price_p(r)-.5),r["line"],r["odds"])
        if key not in chosen or rank<chosen[key][0]:
            chosen[key]=(rank,r)
    counts["first_per_game_and_stage_with_previous"]=len(chosen)
    rows=[]
    for _,r in chosen.values():
        delta=r["odds"]-r["prior_price"]
        flag="down" if delta < -1e-10 else "up" if delta>1e-10 else "same"
        counts["first_"+flag]+=1
        y=y_win(r)
        if y not in (0,1):
            counts["missing_final_or_push"]+=1;continue
        counts["scored_"+flag]+=1
        g=(r["k"],r["league"],r["line"],math.floor(100*r["odds"]+.5000001))
        rows.append((r["day"],g,int(flag=="down"),price_p(r)-y))
    _,W=day_draws(days,seed,draws)
    res=within_slope(rows,{x:i for i,x in enumerate(days)},W)
    return {"id":"H6-E","stage":stage,"registered_after_discovery_primary_results":True,"exploratory_only":True,
            "counts":dict(counts),"n_exact_cells_with_price_variation":res["cells"],
            "slope_with_day_ci95":res["estimate"],"n_scored_before_cell_support":res["n_raw"],
            "seed":seed,"bootstrap_draws":draws}


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--stage",required=True,choices=("discovery","confirmation","blind"))
    ap.add_argument("--folder",type=Path,required=True);ap.add_argument("--split",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args();reg=json.loads(a.split.read_text())
    out=evaluate(a.stage,a.folder,split=reg,seed=reg["seed"],draws=reg["bootstrap_draws"])
    a.out.write_text(json.dumps(out,indent=2,sort_keys=True,allow_nan=False)+"\n")
