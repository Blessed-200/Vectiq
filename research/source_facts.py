"""Reproduce the source history, default switches, age of deploy artifacts and catalog."""
import argparse
import ast
from pathlib import Path
import subprocess
from sources import BASE, ROOT, catalog, get_json, API, save


def git(*args):
    return subprocess.check_output(("git", *args), cwd=ROOT.parent, text=True).strip()


def alerts_default(sha):
    tree = ast.parse(git("show",f"{sha}:src/vectiq/cli.py"))
    for obj in ast.walk(tree):
        if (isinstance(obj,ast.Call) and len(obj.args)>=2 and isinstance(obj.args[0],ast.Constant)
                and obj.args[0].value=="VECTIQ_ALERTS" and isinstance(obj.args[1],ast.Constant)):
            return obj.args[1].value
    return None


def local_facts():
    timeline = []
    # Traverse the first-parent history of production. Topic commits dated earlier than
    # their merge may contain the same switches; this reports when main acquired them.
    ids = git("rev-list","--first-parent","--reverse",BASE,"--","src/vectiq/cli.py").splitlines()
    for sha in ids:
        setting = alerts_default(sha)
        if setting and (not timeline or timeline[-1]["default"]!=setting):
            timeline.append(dict(commit=sha,date=git("show","-s","--format=%cI",sha),default=setting))
    data = {"source_commit":BASE, "seed":20261122, "default_alerts_first_parent_history":timeline,
            "release_metadata":catalog(), "source_files":{}, "market_parser":{},
            "setting_date_commits":{}}
    for name in ("st0","st3","st4","st5","players","markov_prior"):
        p = "src/vectiq/signals/"+name+(".json" if name=="markov_prior" else ".py")
        first = git("log","--diff-filter=A","--format=%H",BASE,"--",p).splitlines()[-1]
        data["source_files"][p] = dict(introduced=first,commit_time=git("show","-s","--format=%cI",first))
    for change in ("04df6e2","207035d"):
        data["setting_date_commits"][change] = dict(sha=git("rev-parse",change),time=git("show","-s","--format=%cI",change))
    schema = ast.parse(git("show",BASE+":src/vectiq/feed/schema.py"))
    for node in schema.body:
        if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=="GROUPS":
            data["market_parser"] = {str(k):v[0] for k,v in ast.literal_eval(node.value).items()}
    return data


def historical_pages_probe():
    url = API+"/actions/workflows/pages.yml/runs?created=2026-11-10&status=success&per_page=100"
    runs = get_json(url)
    if len(runs["workflow_runs"]) != runs["total_count"]:
        raise ValueError("Unexpected pagination")
    found = []
    for row in runs["workflow_runs"]:
        ars = get_json(row["artifacts_url"])
        found.append({"id":row["id"],"updated_at":row["updated_at"],"artifacts_returned":ars["total_count"]})
    return {"query":url,"checked_runs":found}


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out",type=Path,required=True)
    cli.add_argument("--historical-pages",action="store_true")
    args=cli.parse_args()
    save(args.out,historical_pages_probe() if args.historical_pages else local_facts())
