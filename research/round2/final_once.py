"""Single native tape opening after a public Git freeze. Outputs may be read independently."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import platform
import numpy as np
import scipy

from data import STAGES, assets, build, fetch, jsave
import benchmarks
import h6_followup
import micro
import monitor
import policies
import portfolio
import structural

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SPLIT=HERE.parent/"split.json"
CANDIDATES=("data.py","mechanisms.py","policies.py","policy_stats.py","structural.py","micro.py",
            "benchmarks.py","portfolio.py","monitor.py","final_once.py","acceptance.md","policies_protocol.md",
            "structural_protocol.md","monitor_protocol.md","h6_followup.py","h6_followup.md","warmup.json","micro_frozen.json","policy_frozen.json",
            "portfolio_frozen.json","monitor_reference.json","monitor_simulation.json")
STATE=("structural_state.json","monitor.json","seen_ids.json","games.jsonl")
def digest(p):
    with Path(p).open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def git(*args):
    return subprocess.check_output(["git","-C",str(REPO),*args],text=True).strip()
def now():
    return datetime.now(timezone.utc).isoformat()
def jload(p):
    return json.loads(Path(p).read_bytes())
def dependencies():
    base=[SPLIT,HERE.parent/"sources.json",*(HERE/k for k in CANDIDATES)]
    return sorted(set(base+list((REPO/"src").rglob("*.py"))+list(HERE.parent.glob("*.py"))))
def pinned_inputs(work):
    files={str(p.relative_to(REPO)):digest(p) for p in dependencies()}
    checkpoints={}
    for prior in ("warmup","discovery","confirmation"):
        for f in (STATE if prior=="confirmation" else ("seen_ids.json","games.jsonl")):
            checkpoints[f"{prior}/{f}"]=digest(work/prior/f)
    return {"code_and_inputs":files,"training_checkpoints":checkpoints,"runtime":{"python":platform.python_version(),"numpy":np.__version__,"scipy":scipy.__version__}}
def ensure_tracked(ref):
    for p in dependencies():
        rel=p.relative_to(REPO).as_posix()
        if git("hash-object",str(p))!=git("rev-parse",f"{ref}:{rel}"):
            raise ValueError("Candidate differs from frozen Git object: "+rel)
def score(stage,work):
    path=work/stage
    before=work/("discovery" if stage=="confirmation" else "confirmation")
    sig,alt,gs=(structural.read_jsonl(path/f) for f in ("signals.jsonl","alternatives.jsonl","games.jsonl"))
    s,a,state,_=structural.run_stage(stage,sig,alt,gs,jload(before/"structural_state.json"))
    structural.write_jsonl(path/"predictions.jsonl",s);structural.write_jsonl(path/"alternative_predictions.jsonl",a)
    # Keep a byte-stable, portable calibration checkpoint.
    (path/"structural_state.json").write_text(json.dumps(state,ensure_ascii=False,allow_nan=False,sort_keys=True))
    split=jload(SPLIT);days=portfolio.days_for(stage)
    rows=lambda name:policies.read_rows(path/name,split[stage],stage)
    # Validation of forecasts before aggregation also exercises stage and clock guards.
    validated=list(rows("predictions.jsonl"));alt_valid=list(rows("alternative_predictions.jsonl"))
    v=policies.evaluate_policies(validated,alt_valid,rows("controls.jsonl"),jload(HERE/"policy_frozen.json"),
          days,split["seed"],split["bootstrap_draws"],stage,rows("mechanism_rows.jsonl"))
    jsave(path/"policy.json",v)
    jsave(path/"h6_followup.json",h6_followup.evaluate(stage,path,split=split,seed=split["seed"],draws=split["bootstrap_draws"]))
    jsave(path/"benchmarks.json",benchmarks.evaluate(stage,path,split))
    jsave(path/"micro.json",micro.evaluate(stage,path,jload(HERE/"micro_frozen.json")))
    jsave(path/"portfolio.json",portfolio.evaluate(sig,days,jload(HERE/"portfolio_frozen.json")))
    previous=before/"monitor.json" if stage=="blind" else None
    m=monitor.evaluate_registered_stage(stage,path,HERE/"monitor_reference.json",HERE/"monitor_simulation.json",
                                        previous,final_authorized=stage=="blind")
    monitor.write_json(path/"monitor.json",m)
def verify_or_make_seal(work):
    ensure_tracked("HEAD")
    seal_path=HERE/"final_seal.json"
    if seal_path.exists():raise FileExistsError("Seal already exists")
    jsave(seal_path,{"algorithm_commit":git("rev-parse","HEAD"),"created_utc":now(),**pinned_inputs(work),
                     "split":jload(SPLIT)["blind"],"sha256_manifest_count":len(assets("blind"))})
def run_final(a):
    seal_path=HERE/"final_seal.json";seal=jload(seal_path);ref=git("log","-1","--format=%H","--",str(seal_path))
    if not ref or git("hash-object",str(seal_path))!=git("rev-parse",ref+":research/round2/final_seal.json"):
        raise ValueError("Seal must have been committed before the first access")
    ensure_tracked(seal["algorithm_commit"])
    if any(seal[k]!=v for k,v in pinned_inputs(a.work).items()):raise ValueError("Input or trained state no longer matches its seal")
    rdir=HERE/"results";rdir.mkdir(exist_ok=True)
    receipt=rdir/"final_access.json";end=rdir/"final_complete.json"
    if end.exists() or (a.work/"blind").exists():raise FileExistsError("Prior access or native output exists")
    # Exclusive creation comes before even downloading a raw blind shard.
    now_open=now()
    with receipt.open("x") as f:
        json.dump(dict(utc=now_open,action="single_final_open",study_prereg="7fb51cb07799f0e8ec1b704072d0e002f7e918cc",
            algorithm_commit=seal["algorithm_commit"],seal_commit=ref,seal_sha256=digest(seal_path)),f,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
    fetch("blind",a.cache)
    build("blind",a.cache,a.work,a.jobs)
    score("blind",a.work)
    jsave(end,dict(opened_utc=now_open,completed_utc=now(),seal_commit=ref,
           result_sha256={f:digest(a.work/"blind"/f) for f in ("extract.json","policy.json","h6_followup.json","benchmarks.json","micro.json","portfolio.json","monitor.json")}))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("action",choices=("confirmation","seal","blind"))
    p.add_argument("--work",type=Path,required=True);p.add_argument("--cache",type=Path,required=True);p.add_argument("--jobs",type=int,default=6)
    a=p.parse_args()
    if a.action=="seal":verify_or_make_seal(a.work)
    elif a.action=="blind":run_final(a)
    else:
        ensure_tracked("HEAD")
        fetch("confirmation",a.cache);build("confirmation",a.cache,a.work,a.jobs);score("confirmation",a.work)
if __name__=="__main__":main()
