"""Construct stage-isolated, receipt-time offers and separately labelled follow-ups."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys
from itertools import groupby

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import sources
from tape_audit import (DAY,HOUR,PRIOR_MS,Match,cents,legal_final,legal_set,opportunity_window,
                        parse_item,records,verify_asset)
from asof import assert_asof
from vectiq.signals import st3,markov
from vectiq.signals.players import Ratings
from vectiq.signals.st2 import _centre

STAGES = ("discovery","confirmation","blind")
def daystr(ts):
    return datetime.fromtimestamp(ts/1000, timezone.utc).date().isoformat()
def jsave(p,x):
    p.write_text(json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n")
def jsonlines(p):
    with Path(p).open() as f:
        return [json.loads(x) for x in f if x.strip()]
def write_rows(path,rows):
    with path.open("w") as out:
        for r in rows:
            out.write(json.dumps(r,ensure_ascii=False,separators=(",",":"),allow_nan=False)+"\n")
def split_for(stage):
    if stage=="warmup":
        start=sources.midnight(sources.FIRST)
        return {"start_ms":start-DAY,"end_ms":start}
    return sources.partition()[stage]
def assets(stage):
    arr=(json.loads((HERE/"warmup.json").read_text()) if stage=="warmup" else
         json.loads((HERE.parent/"sources.json").read_text())["assets"])
    s=split_for(stage)
    selected=[a for a in arr if a["stage"]==stage]
    for a in selected:
        m=sources.FILENAME.fullmatch(a["name"])
        if not m or a["day"]!=m[1] or not s["start_ms"]<=sources.midnight(sources.date.fromisoformat(m[1]))<s["end_ms"]:
            raise ValueError("Illegal file at partition boundary")
    return selected
def fetch(stage,cache):
    cache.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(lambda a:sources.fetch_one(a,cache),assets(stage)))


def slim_job(args):
    hour, grouped, cache, work=args
    by_key={}
    clashes=set()
    for a in grouped:
        path=cache/a["name"]
        verify_asset(path,a)
        for line in records(path):
            row=json.loads(line)
            t,kind,g,raw=row["t"],row["k"],row["id"],row["d"]
            if not hour<=t<hour+HOUR: raise ValueError("Receipt outside registered hour")
            if kind not in ("game","item","line"):continue
            league=(raw.get("L") or "").lower()
            if not league.startswith("masters") or "super league" in league or "china" in league:continue
            key=(t,kind,g)
            if raw.get("I")!=g:
                clashes.add(g);continue
            if key in by_key:
                if by_key[key][0]!=hashlib.blake2b(line,digest_size=16).digest():clashes.add(g)
                continue
            try:
                s=parse_item(raw,t)
                win=opportunity_window(s)
                if s.best_of==7 and kind in ("item","line") and win is not None:
                    lad=st3.ladder(s)
                    winners={q.selection:q.price for q in s.quotes if q.market=="winner" and q.scope=="match" and not q.blocked and q.price>1}
                else: lad=[];winners={}
                v=[t,g,kind,s.league,s.home,s.away,s.home_id,s.away_id,s.start_ts,s.best_of,s.sets,s.sets_won,
                   s.live,win[0] if win is not None else -1,lad,winners.get("home"),winners.get("away")]
                by_key[key]=(hashlib.blake2b(line,digest_size=16).digest(),v)
            except (TypeError,KeyError,ValueError,IndexError,OverflowError):
                clashes.add(g)
    path=work/(str(hour)+".gz")
    with gzip.open(path,"wt",compresslevel=1) as f:
        for key,(_,v) in sorted(by_key.items()):
            f.write(json.dumps(v,ensure_ascii=False,separators=(",",":"))+"\n")
    return list(clashes)


def match_rows(gid, ev, ambiguous, lo, hi):
    # Only complete native identity/format can supply a label. Features never use these exclusions.
    starts={int(v[8])*1000 for v in ev if v[8] and isinstance(v[8],(int,float))}
    ids={(v[6],v[7]) for v in ev if v[6] and v[7]}
    names={(v[4],v[5]) for v in ev if v[4] and v[5]}
    lgs={v[3] for v in ev if v[3]}
    formats={v[9] for v in ev if v[9] is not None}
    scheduled_outside=not starts or min(starts)<lo or max(starts)>=hi
    bad=ambiguous or scheduled_outside or len(lgs)!=1 or len(ids)>1 or (not ids and len(names)>1) or formats!={7}
    terminals={}
    for v in ev:
        if max(v[11])>4:bad=True
        if max(v[11])!=4:continue
        scores=list(v[10])
        while scores and scores[-1]==[0,0]:scores.pop()
        won=[0,0]
        if not 4<=len(scores)<=7:continue
        for a,b in scores:
            if max(won)>=4 or not legal_set(a,b):break
            won[int(b>a)]+=1
        else:
            if won==v[11]:
                key=tuple(tuple(p) for p in scores)
                terminals[key]=min(terminals.get(key,2**62),v[0])
    terminal,total,final=None,None,None
    if len(terminals)==1 and not bad:
        final,terminal=next(iter(terminals.items()))
        total=sum(sum(p) for p in final)
        explicit=min(v[0] for v in ev if v[9]==7)
        terminal=max(explicit,terminal)
    game={"gid":gid,"league":ev[0][3],"home":ev[0][4],"away":ev[0][5],
          "player_home_id":ev[0][6],"player_away_id":ev[0][7],"terminal_ms":terminal,
          "day":daystr(terminal) if terminal else None,"total":total,"final_sets":final}
    return game,scheduled_outside,bad


def analyze_game(gid, ev, ambiguous, lo, hi, stage):
    ev.sort(key=lambda v:(v[0],v[2]))
    game,crossed,bad=match_rows(gid,ev,ambiguous,lo,hi)
    offers,signals,cells,centers,hazards=[],[],set(),{},[]
    first_stage={}
    early=None
    first_early=None
    trailing={}
    used=set()
    pre_candidates=[]
    pending=[]
    last_by_quote={}
    fixed=[]
    def finalize_probe(e,reason):
        if e["exec_status"]=="pending":e["exec_status"]=reason
    for t, same_iter in groupby(ev,key=lambda r:r[0]):
        same=list(same_iter)
        # A future disagreement cannot retroactively select an old offer; only the outcome is quarantined.
        source=[v for v in same if v[2] in ("item","line")]
        if not source:continue
        if len(source)>1 and any(v[10:17]!=source[0][10:17] for v in source[1:]):
            for o in pending:finalize_probe(o,"ambiguous")
            pending=[];continue
        v=source[0]
        _,_,kind,league,home,away,hid,aid,start,bo,scores,wins,live,k,lad,wh,wa=v
        if not start or not lo<=int(start)*1000<hi or bo!=7:continue
        if scores and not sum(wins) and early is None and 6<=sum(scores[0])<=8:
            early=scores[0][0]-scores[0][1];first_early=t
        qmap={ln:(over,under) for ln,over,under in lad if over>1 and under>1}
        partial=scores[k] if k>=0 and len(scores)>k else [0,0]
        done=scores[:k] if k>=0 else []
        for o in pending:
            if t<=o["ts"]:continue
            elapsed=t-o["ts"]
            if elapsed>15000:finalize_probe(o,"no_tick_by_15s")
            elif kind!=o["source"]:continue
            elif k!=o["k"] or list(done)!=o["sets"] or o["line"] not in qmap:
                finalize_probe(o,"market_or_stage_changed")
            elif elapsed>=3000:
                if o["exec_status"]=="pending":
                    o.update(exec_odds=qmap[o["line"]][1],exec_ts=t,exec_status="reobserved")
        pending=[o for o in pending if o["exec_status"]=="pending"]
        for hz in fixed[:]:
            if hz["src"]!=kind:continue
            dt=(t-hz["ts"])/1000.
            if dt<=0:continue
            if dt>60 or k!=hz["k"] or scores!=hz["score"] or hz["line"] not in qmap:
                hz["duration"]=min(60.,dt);hz["status"]="censored";fixed.remove(hz)
            elif abs(qmap[hz["line"]][1]-hz["odds"])>1e-8:
                hz["duration"]=dt;hz["status"]="move";hz["next_odds"]=qmap[hz["line"]][1];fixed.remove(hz)
            else:hz["duration"]=dt
        # Keep last quoted price in identical score state solely as an evaluation proxy.
        if k>=0 and qmap:
            for line,(_,un) in qmap.items():last_by_quote[(kind,tuple(map(tuple,scores)),line)]=(t,un)
        if k not in (0,1,2,3):continue
        first_stage.setdefault(k,t)
        if not qmap:continue
        ct=_centre([(ln,(1/u)/(1/u+1/o)) for ln,(o,u) in qmap.items()]) if len(qmap)>=2 else None
        if k<=2 and ct is not None and k not in centers:
            centers[k]=(t,ct)
            if k in (1,2):
                line=min(qmap,key=lambda ln:abs(ln-ct))
                h=dict(gid=gid,league=league,k=k,src=kind,ts=t,day=daystr(t),score=scores,
                       line=line,odds=qmap[line][1],duration=0.,status="censored",first_stage_ms=first_stage[k])
                hazards.append(h);fixed.append(h)
        st5_ok=t>=PRIOR_MS and int(start)*1000>=PRIOR_MS and ev[0][0]>=PRIOR_MS
        def offer(line,leg,selection=False):
            over,odds=qmap[line]
            old=trailing.get((kind,k,line))
            r=dict(gid=gid,ts=t,day=daystr(t),stage=stage,league=league,home=home,away=away,
                   player_home_id=hid,player_away_id=aid,k=k,sets=done,partial=partial,score=scores,
                   line=line,odds=odds,over=over,leg=leg,signal=selection,source=kind,
                   winner_home=wh,winner_away=wa,early_margin=early,early_ts=first_early,
                   prior_price=old[1] if old else None,price_prior_ts=old[0] if old else None,
                   elapsed=(t-first_stage[k])/1000,center=ct,st5_eligible=st5_ok,
                   exec_odds=None,exec_ts=None,exec_status="pending")
            pending.append(r)
            return r
        if k<=2 and (k or len(qmap)>=3):
            for line,(_,under) in qmap.items():
                key=(k,line,cents(under))
                if key not in cells:
                    cells.add(key);offers.append(offer(line,"CONTROL"))
        if k==0 and len(qmap)>=3:
            line=min(qmap);u=qmap[line][1]
            if u>=2.10 and "prehigh" not in used:
                if u>=2.20:used.add("prehigh")
                # Keep first candidate per UTC rating refresh hour and price tier.
                key=("pre",(t//HOUR),u>=2.20)
                if key not in used:
                    used.add(key);pre_candidates.append(offer(line,"ST0",True))
        if k==1 and abs(done[0][0]-done[0][1])>=5 and "ST3" not in used:
            pick=next((ln for ln in sorted(qmap) if qmap[ln][1]>=2.0),None)
            if pick is not None:
                used.add("ST3")
                signals.append(offer(pick,"ST3",qmap[pick][1]>=2.10-1e-9))
        if k in (1,2,3) and "ST5" not in used and st5_ok:
            p=markov.best_under([tuple(s) for s in done],lad,.65,.05)
            if p:
                used.add("ST5")
                if k in (1,2) and .15<p[3]<=.30+1e-9:signals.append(offer(p[0],"ST5",True))
        for line,(_,u) in qmap.items():trailing[(kind,k,line)]=(t,u)
    for o in pending:finalize_probe(o,"end_of_observation")
    for o in offers+signals+pre_candidates:
        t=o["ts"]
        close=last_by_quote.get((o["source"],tuple(map(tuple,o.pop("score"))),o["line"]))
        o["close_price"]=close[1] if close and close[0]>t else None
        o["close_ts"]=close[0] if close and close[0]>t else None
        # Labels can be absent; a future terminal or conflict never creates an opportunity.
        valid=game["terminal_ms"] is not None and t<game["terminal_ms"]
        for key in ("terminal_ms","total","final_sets"):o[key]=game[key] if valid else None
        assert_asof(t,0,{k:v for k,v in {"tick":t,"previous_price":o["price_prior_ts"],"early_score":o["early_ts"]}.items() if v is not None})
    game["centre1"]=centers.get(1)
    return game,offers,signals,pre_candidates,hazards


class PastPlayers:
    def __init__(self):
        self.mom=defaultdict(lambda:[0,0.,0.,0])
        self.global_m=[0,0.,0.,0]
        self.rating=Ratings();self.training=[];self.hour=None;self.latest=None
    def add(self,g):
        scores=g["final_sets"]
        if not scores or not g["terminal_ms"]:return
        self.latest=max(self.latest or 0,g["terminal_ms"])
        clocks=g.get("centre1")
        if clocks and clocks[0]<g["terminal_ms"]:
            self.training.append((g["home"],g["away"],g["total"]-clocks[1],clocks[0]/DAY))
        diffs=[abs(a-b) for a,b in scores]
        stats=(len(scores),sum(diffs),sum(x*x for x in diffs),sum(min(a,b)>=10 for a,b in scores))
        for target in [self.global_m,self.mom[g["home"]],self.mom[g["away"]]]:
            for i in range(4):target[i]+=stats[i]
    def fit_hour(self,t):
        if self.hour!=t//HOUR:
            self.hour=t//HOUR
            self.rating.fit(self.training,t/DAY)
    def features(self,r):
        t=r["ts"]
        if self.latest is not None:assert_asof(t,0,{"last_player_final":self.latest})
        self.fit_hour(t)
        n,l,sq,d=self.global_m
        by=[]
        for p in (r["home"],r["away"]):
            m,a,b,c=self.mom[p]
            defaults=(l/n,sq/n,d/n) if n else (4.,24.,0.10)
            mean=(a+40*defaults[0])/(m+40)
            var=max(0,(b+40*defaults[1])/(m+40)-mean**2)
            by.append(((c+40*defaults[2])/(m+40),math.sqrt(var)))
        return self.rating.z(r["home"]+" vs "+r["away"]),sum(x[0] for x in by)/2,sum(x[1] for x in by)/2


def build(stage,cache,work,jobs):
    bounds=split_for(stage);lo,hi=bounds["start_ms"],bounds["end_ms"]
    folder=work/stage;folder.mkdir(parents=True,exist_ok=True)
    sd=folder/"hours";sd.mkdir(exist_ok=True)
    groups=defaultdict(list)
    for a in assets(stage):
        h=sources.midnight(sources.date.fromisoformat(a["day"]))+int(a["name"][11:13])*HOUR
        groups[h].append(a)
    jobspec=[(h,group,cache,sd) for h,group in sorted(groups.items())]
    clashes=set()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for conflicts in pool.map(slim_job,jobspec):clashes.update(conflicts)
    # Partition by game id on disk to bound memory when attaching decisions to labels.
    spools=[folder/f"bucket_{i}.jsonl" for i in range(32)]
    outs=[p.open("w") for p in spools]
    try:
        for hour in sorted(groups):
            with gzip.open(sd/(str(hour)+".gz"),"rt") as f:
                for line in f:
                    gid=int(line.split(",",2)[1]);outs[gid%len(outs)].write(line)
    finally:
        for f in outs:f.close()
    ordered_stages=("warmup",*STAGES)
    previous=ordered_stages[:ordered_stages.index(stage)]
    seen=set()
    for old in previous:
        seen.update(json.loads((work/old/"seen_ids.json").read_text()))
    current=set(); purged=set(); mechanisms=[]
    g_all=[];s_all=[];pre_all=[];hazards=[];controlfile=folder/"controls.jsonl"
    with controlfile.open("w") as cf:
        for path in spools:
            by=defaultdict(list)
            with path.open() as f:
                for x in f:
                    v=json.loads(x);by[v[1]].append(v)
            for gid,ev in by.items():
                current.add(gid)
                if gid in seen:
                    purged.add(gid)
                    continue
                game,controls,signals,pre,haz=analyze_game(gid,ev,gid in clashes,lo,hi,stage)
                if game["total"] is not None:g_all.append(game)
                canonical={}
                for r in controls:
                    fair=(1/r["odds"])/(1/r["over"]+1/r["odds"])
                    key=(r["ts"],abs(fair-.5),r["line"],r["odds"])
                    if r["k"] not in canonical or key<canonical[r["k"]][0]:
                        canonical[r["k"]]=(key,r)
                mechanisms.extend(v[1] for v in canonical.values())
                for row in controls:cf.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
                s_all.extend(signals);pre_all.extend(pre);hazards.extend(haz)
            path.unlink()
    write_rows(folder/"games.jsonl",sorted(g_all,key=lambda g:(g["terminal_ms"],g["gid"])))
    jsave(folder/"seen_ids.json",sorted(current))
    past=[]
    for old in previous:
        past+=jsonlines(work/old/"games.jsonl")
    finals=iter(sorted(past+g_all,key=lambda g:(g["terminal_ms"],g["gid"])))
    g=next(finals,None);pp=PastPlayers();chosen=set();ss=[];aa=[]
    for r in sorted(s_all+pre_all+mechanisms,key=lambda r:(r["ts"],r["gid"],r["leg"])):
        while g and g["terminal_ms"]<r["ts"]:
            pp.add(g);g=next(finals,None)
        z,deu,vol=pp.features(r)
        r.update(rating_z=z,player_deuce=deu,player_volatility=vol,player_features_known_ms=pp.latest)
        if r["leg"]=="CONTROL":continue
        if r["leg"]=="ST0":
            if r["gid"] in chosen or (r["odds"]<2.20 and z>-.4):continue
            chosen.add(r["gid"])
        if r["leg"]=="ST3":aa.append(r)
        if r["signal"]:ss.append(r)
    write_rows(folder/"mechanism_rows.jsonl",sorted(mechanisms,key=lambda r:(r["ts"],r["gid"],r["k"])))
    write_rows(folder/"signals.jsonl",ss);write_rows(folder/"alternatives.jsonl",aa)
    write_rows(folder/"hazards.jsonl",sorted(hazards,key=lambda h:(h["ts"],h["gid"],h["k"])))
    jsave(folder/"extract.json",dict(stage=stage,files=len(assets(stage)),leg_counts=dict(Counter(r["leg"] for r in ss)),
              labelled_games=len(g_all),seen_ids=len(current),prior_ids_purged=len(purged),ambiguous_tick_ids=len(clashes),st3_paper=len(aa)))
    for h in sd.iterdir():h.unlink()
    sd.rmdir()

if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("action",choices=["fetch","build"])
    cli.add_argument("stage",choices=("warmup",*STAGES))
    cli.add_argument("--cache",type=Path,default=Path("/tmp/v2tape"))
    cli.add_argument("--work",type=Path,default=Path("/tmp/v2"))
    cli.add_argument("--jobs",type=int,default=5)
    a=cli.parse_args()
    if a.stage=="blind":cli.error("Use the single frozen final entry point.")
    if a.action=="fetch":fetch(a.stage,a.cache)
    else:build(a.stage,a.cache,a.work,a.jobs)
