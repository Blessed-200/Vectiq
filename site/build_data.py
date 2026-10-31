#!/usr/bin/env python3
"""build_data.py — ledgers reales → site/data/*.json para el panel GitHub Pages.

Solo stdlib. Lee los assets descargados de los releases (`ledger`,
`shadow-ledger`) y emite JSON versionados. Ningún número es inventado:
si un asset falta, la estrategia correspondiente sale con estado "sin datos".

Uso:
    python3 site/build_data.py --assets DIR --out site/data [--repo-root .]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import tarfile
from collections import defaultdict

SCHEMA_VERSION = 1
BOOT_N = 2000
BOOT_SEED = 20261031

# Fuentes de liquidación que cuentan como verificadas (PROMOTION.md addendum)
SRC_STRONG = {"observed", "results_api"}
SRC_WEAK = {"imputed", "partial"}

# Cortes prospectivos (PROMOTION.md + brief)
CUT_MAIN = 1793142468000          # corte PROMOTION.md (H_A, H_B)
CUT_CAND = 1793154783487          # inicio prueba limpia de Manus

STRATEGIES = {
    "masters": {
        "name": "Telegram (modelo)",
        "db": "masters.sqlite",
        "desc": "Masters Under con modelo. Etapas: prepartido, tras set 1, tras set 2.",
        "hypothesis": "H_A (solo etapa prepartido)",
    },
    "cand": {
        "name": "Manus (regla fija)",
        "db": "cand.sqlite",
        "desc": "Regla fija MS_G17_UNDER_K1_PB12_BUD6, stake plano ~$1.",
        "hypothesis": None,
        "cut": CUT_CAND,
    },
    "setka": {
        "name": "Setka / viejo",
        "db": "shadow.sqlite",
        "desc": "Under tras set 1 en ligas de 5 sets. Silenciado.",
        "hypothesis": "H_B (solo tras set 1)",
    },
}
PRICE_BANDS = [(0, 1.7, "<1.70"), (1.7, 1.9, "1.70–1.89"),
               (1.9, 2.1, "1.90–2.09"), (2.1, 2.3, "2.10–2.29"),
               (2.3, 99, "≥2.30")]


# ---------- utilidades estadísticas ----------

def stage_of(sets: str | None) -> int:
    if not sets:
        return 0
    return len([s for s in sets.split(";") if s.strip()])


def pnl_1u(result: str, price: float) -> float:
    if result == "W":
        return price - 1.0
    if result == "L":
        return -1.0
    return 0.0


def wilson(w: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = w / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]


def cluster_bootstrap(pnl_by_cluster: list[list[float]], n_boot: int = BOOT_N,
                      seed: int = BOOT_SEED) -> dict:
    """Bootstrap agrupado por partido: re-muestrea partidos enteros.

    Devuelve IC95 del ROI medio por apuesta, P(ROI>0) y P(ROI>3%)."""
    rng = random.Random(seed)
    n_cl = len(pnl_by_cluster)
    if n_cl == 0:
        return {"ci95": None, "p_pos": None, "p_roi3": None}
    means = []
    for _ in range(n_boot):
        tp = tn = 0
        for _ in range(n_cl):
            cl = pnl_by_cluster[rng.randrange(n_cl)]
            tp += sum(cl)
            tn += len(cl)
        means.append(tp / tn if tn else 0.0)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[min(int(0.975 * n_boot), n_boot - 1)]
    p_pos = sum(1 for m in means if m > 0) / n_boot
    p_roi3 = sum(1 for m in means if m > 0.03) / n_boot
    return {"ci95": [lo, hi], "p_pos": p_pos, "p_roi3": p_roi3}


def equity_and_dd(pnls: list[float]) -> tuple[list[float], float, int]:
    """Devuelve (curva, max drawdown en unidades, racha de pérdidas máxima)."""
    curve, peak, mdd, run, max_run = [0.0], 0.0, 0.0, 0, 0
    for p in pnls:
        curve.append(curve[-1] + p)
        peak = max(peak, curve[-1])
        mdd = max(mdd, peak - curve[-1])
        run = run + 1 if p < 0 else 0
        max_run = max(max_run, run)
    return curve, mdd, max_run


def dd_stake_real(pnls: list[float], bank0: float = 100.0,
                  frac: float = 0.01) -> float:
    """Drawdown % con stake proporcional (1% del bankroll corriente)."""
    bank, peak, mdd = bank0, bank0, 0.0
    for p in pnls:
        stake = bank * frac
        bank += stake * p
        peak = max(peak, bank)
        if peak > 0:
            mdd = max(mdd, (peak - bank) / peak)
    return mdd


def roi_block(bets: list[dict]) -> dict:
    """Métricas núcleo sobre una lista de apuestas liquidadas fuertes."""
    n = len(bets)
    if n == 0:
        return {"n": 0}
    w = sum(1 for b in bets if b["result"] == "W")
    l = sum(1 for b in bets if b["result"] == "L")
    p = n - w - l
    pnls = [b["pnl_1u"] for b in bets]
    roi = sum(pnls) / n
    by_gid: dict[int, list[float]] = defaultdict(list)
    for b in bets:
        by_gid[b["game_id"]].append(b["pnl_1u"])
    boot = cluster_bootstrap(list(by_gid.values()))
    curve, mdd, streak = equity_and_dd(pnls)
    clv_bets = [b for b in bets
                if b.get("closing_price") and b["closing_price"] > 0]
    clv = None
    if clv_bets:
        clv_vals = [b["price"] / b["closing_price"] - 1 for b in clv_bets]
        clv = {"mean": sum(clv_vals) / len(clv_vals),
               "pct_beat": sum(1 for v in clv_vals if v > 0) / len(clv_vals),
               "n": len(clv_vals)}
    days = sorted({dt.datetime.fromtimestamp(b["ts_ms"] / 1000, dt.timezone.utc)
                   .date().isoformat() for b in bets})
    return {
        "n": n, "w": w, "l": l, "p": p, "wr": w / (w + l) if w + l else None,
        "wr_ci95": wilson(w, w + l) if w + l else None,
        "roi": roi, "net_u": sum(pnls),
        "ci95": boot["ci95"], "p_pos": boot["p_pos"], "p_roi3": boot["p_roi3"],
        "mdd_u": mdd, "max_loss_streak": streak,
        "mdd_pct_1pct": dd_stake_real(pnls),
        "clv": clv,
        "n_matches": len(by_gid), "n_days": len(days),
        "first_day": days[0], "last_day": days[-1],
    }


def group_stats(bets: list[dict], keyfn) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for b in bets:
        groups[keyfn(b)].append(b)
    out = []
    for k in sorted(groups):
        g = groups[k]
        n = len(g)
        w = sum(1 for b in g if b["result"] == "W")
        l = sum(1 for b in g if b["result"] == "L")
        out.append({"k": k, "n": n, "roi": sum(b["pnl_1u"] for b in g) / n,
                    "wr": w / (w + l) if w + l else None})
    return out


def price_band(price: float) -> str:
    for lo, hi, label in PRICE_BANDS:
        if lo <= price < hi:
            return label
    return PRICE_BANDS[-1][2]


def calibration(bets: list[dict], bins: int = 8) -> list[dict]:
    pts = [(b["p_model"], 1.0 if b["result"] == "W" else 0.0)
           for b in bets
           if b.get("p_model") is not None and b["result"] in ("W", "L")]
    pts.sort()
    out = []
    if not pts:
        return out
    step = max(1, len(pts) // bins)
    for i in range(0, len(pts), step):
        chunk = pts[i:i + step]
        n = len(chunk)
        w = sum(1 for _, y in chunk if y == 1.0)
        out.append({"pred": sum(x for x, _ in chunk) / n,
                    "actual": w / n, "n": n, "ci": wilson(w, n)})
    return out


# ---------- lectura de ledgers sqlite ----------

def load_sqlite_bets(path: str) -> list[dict]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    bets = []
    for r in con.execute("SELECT * FROM bets ORDER BY ts_ms"):
        b = dict(r)
        b["stage"] = stage_of(b.get("sets"))
        b["src_norm"] = ("legado" if b.get("src") is None else b["src"])
        if b.get("result") in ("W", "L", "P"):
            b["pnl_1u"] = pnl_1u(b["result"], b["price"])
        bets.append(b)
    return bets


def classify(bets: list[dict]) -> dict[str, list[dict]]:
    out = {"strong": [], "weak": [], "void": [], "open": []}
    for b in bets:
        res, src = b.get("result"), b.get("src")
        if res is None:
            out["open"].append(b)
        elif res == "V" or src == "void":
            out["void"].append(b)
        elif res in ("W", "L", "P"):
            if src in SRC_STRONG or src is None:
                out["strong"].append(b)
            else:
                out["weak"].append(b)
        else:
            out["void"].append(b)
    return out


def analyze_sqlite(path: str, cut: int | None = None,
                   stage_filter: int | None = None) -> dict:
    bets = load_sqlite_bets(path)
    cls = classify(bets)
    strong = cls["strong"]
    forward = [b for b in strong
               if (cut is None or b["ts_ms"] >= cut)
               and (stage_filter is None or b["stage"] == stage_filter)]
    last_ts = max((b["ts_ms"] for b in bets), default=None)
    res = {
        "total": len(bets),
        "open": len(cls["open"]), "void": len(cls["void"]),
        "weak": len(cls["weak"]),
        "legacy": sum(1 for b in bets if b.get("src") is None),
        "src_mix": _src_mix(bets),
        "last_ts": last_ts,
        "all": roi_block(strong),
        "forward": roi_block(forward),
    }
    # desgloses sobre liquidadas fuertes (todo el histórico)
    res["by_league"] = group_stats(strong, lambda b: b["league"] or "?")
    res["by_stage"] = group_stats(strong,
                                  lambda b: ["Prepartido", "Tras set 1",
                                             "Tras set 2", "Tras set 3+"]
                                  [min(b["stage"], 3)])
    res["by_day"] = group_stats(
        strong, lambda b: dt.datetime.fromtimestamp(
            b["ts_ms"] / 1000, dt.timezone.utc).date().isoformat())
    res["by_price"] = group_stats(strong, lambda b: price_band(b["price"]))
    res["calibration"] = calibration(strong)
    res["equity"] = _equity_series(strong)
    res["recent"] = [{
        "ts": b["ts_ms"], "players": b["players"], "league": b["league"],
        "stage": b["stage"], "side": b["side"], "line": b["line"],
        "price": b["price"], "p": b.get("p_model"), "ev": b.get("ev"),
        "result": b.get("result"), "src": b["src_norm"],
        "pnl": b.get("pnl_1u")} for b in bets[-40:][::-1]]
    # todas las apuestas liquidables para el filtro cliente
    res["bets"] = [{
        "ts": b["ts_ms"], "gid": b["game_id"], "players": b["players"],
        "league": b["league"], "stage": b["stage"], "line": b["line"],
        "price": b["price"], "p": b.get("p_model"), "result": b.get("result"),
        "src": b["src_norm"], "pnl": b.get("pnl_1u"),
        "closing": b.get("closing_price")} for b in bets]
    return res


def _src_mix(bets: list[dict]) -> dict:
    mix = defaultdict(int)
    for b in bets:
        src = b.get("src") or "legado"
        mix[src] += 1
    return dict(mix)


def _equity_series(bets: list[dict]) -> list[list]:
    curve, c = [], 0.0
    for b in sorted(bets, key=lambda x: x["ts_ms"]):
        c += b["pnl_1u"]
        curve.append([b["ts_ms"], round(c, 4)])
    return curve


# ---------- lectura del shadow-ledger (ST0/ST1) ----------

def load_shadow_state(assets_dir: str) -> dict | None:
    """Extrae state.tar.gz del release shadow-ledger si existe."""
    tgz = os.path.join(assets_dir, "state.tar.gz")
    if not os.path.exists(tgz):
        return None
    out: dict[str, list[dict]] = defaultdict(list)
    with tarfile.open(tgz) as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".jsonl"):
                continue
            kind = os.path.basename(member.name).replace(".jsonl", "")
            f = tf.extractfile(member)
            if f is None:
                continue
            for line in f.read().decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out[kind].append(json.loads(line))
                except json.JSONDecodeError:
                    out["errors"].append({"parse_error": kind, "line": line[:120]})
    return dict(out)


def analyze_shadow(state: dict | None) -> dict:
    """ST0 y ST1 desde decisions.jsonl + settlements.jsonl + capacity.jsonl."""
    if not state:
        return {"empty": True}
    decisions = state.get("decisions", [])
    settles = {int(s["gid"]): s for s in state.get("settlements", [])
               if s.get("final_total") is not None}
    matches = {int(m["gid"]): m for m in state.get("matches", [])}
    ticks = state.get("market_ticks", state.get("market", []))
    # precio de cierre por (gid, line): última escalera registrada
    closing: dict[tuple, float] = {}
    for t in ticks:
        gid = int(t["gid"])
        for L in t.get("ladder", []):
            closing[(gid, L["line"])] = L["under"]
    capacity = state.get("capacity", [])

    res: dict = {"empty": False, "strategies": {}}
    for strat in ("ST0", "ST1"):
        bets, opps = [], 0
        for d in decisions:
            if d.get("strategy") != strat:
                continue
            opps += 1
            if d.get("decision") != "BET":
                continue
            gid = int(d["gid"])
            s = settles.get(gid)
            m = matches.get(gid, {})
            b = {
                "ts": d["decision_at_ms"], "ts_ms": d["decision_at_ms"],
                "game_id": gid, "gid": gid,
                "players": m.get("players") or d.get("players"),
                "league": m.get("league") or d.get("league"),
                "stage": 0, "line": d["line"], "price": d["odds"],
                "p": d.get("p"), "ev": d.get("ev"),
                "closing": closing.get((gid, d["line"])),
            }
            if s and s.get("final_total") is not None:
                ft = s["final_total"]
                b["result"] = "W" if ft < d["line"] else "L"
                b["pnl_1u"] = pnl_1u(b["result"], b["price"])
                b["final"] = ft
                b["src"] = s.get("src", "tape")
                b["game_id"] = gid
                bets.append(b)
            else:
                b["result"] = None
        blk = roi_block(bets)
        blk["opportunities"] = opps
        blk["bets"] = bets
        res["strategies"][strat] = blk

    # capacidad agregada
    cap = {"probes": len(capacity)}
    if capacity:
        ok = [c for c in capacity if c.get("ok")]
        mb = sorted(c["max_bet"] for c in ok if c.get("max_bet") is not None)
        cap.update({
            "ok": len(ok),
            "blocked": sum(1 for c in ok if c.get("block")),
            "coef_moved": sum(1 for c in ok if c.get("change_cf")),
            "max_bet_median": mb[len(mb) // 2] if mb else None,
            "max_bet_min": mb[0] if mb else None,
            "max_bet_max": mb[-1] if mb else None,
        })
    res["capacity"] = cap
    return res


# ---------- distribución aprendida (masters_book.json) ----------

def load_book(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    book = json.load(open(path))
    rem = book.get("remaining", {}).get("0", [])
    if not rem:
        return None
    rem = sorted(rem)
    n = len(rem)
    # histograma de 24 bins
    lo, hi = rem[0], rem[-1]
    span = max(hi - lo, 1)
    nb = 24
    hist = [0] * nb
    for v in rem:
        hist[min(int((v - lo) / span * nb), nb - 1)] += 1
    return {
        "n": n,
        "mean": sum(rem) / n,
        "stdev": (sum((v - sum(rem) / n) ** 2 for v in rem) / n) ** 0.5,
        "p5": rem[int(0.05 * n)], "p95": rem[int(0.95 * n)],
        "hist": {"lo": lo, "step": span / nb, "counts": hist},
    }


# ---------- commits recientes ----------

def recent_commits(repo_root: str, n: int = 12) -> list[dict]:
    try:
        out = subprocess.check_output(
            ["git", "log", f"-{n}", "--format=%H|%cs|%s", "main"],
            cwd=repo_root, stderr=subprocess.DEVNULL, text=True)
        return [{"sha": l.split("|")[0][:8], "date": l.split("|")[1],
                 "msg": l.split("|", 2)[2].strip()}
                for l in out.splitlines() if "|" in l]
    except Exception:
        return []


# ---------- puertas PROMOTION ----------

def gates(strategies: dict, shadow: dict) -> dict:
    def gate(name, blk, need_n, need_days):
        n = blk.get("n", 0)
        ci = blk.get("ci95")
        return {
            "name": name, "n": n, "need_n": need_n,
            "n_days": blk.get("n_days", 0), "need_days": need_days,
            "ci95": ci, "ci_ok": bool(ci and ci[0] > 0),
            "mdd_pct": blk.get("mdd_pct_1pct"),
            "mdd_ok": bool(blk.get("mdd_pct_1pct") is not None
                           and blk["mdd_pct_1pct"] < 0.15),
            "roi": blk.get("roi"), "p_pos": blk.get("p_pos"),
            "p_roi3": blk.get("p_roi3"),
            "progress": min(n / need_n, 1.0) if need_n else None,
        }
    g = {
        "rules": {
            "cut_main": CUT_MAIN, "cut_cand": CUT_CAND,
            "h_a_need": 400, "h_b_need": 200, "target": 1000,
            "need_days": 5, "dd_max_pct": 0.15,
            "src_strong": sorted(SRC_STRONG),
        },
        "h_a": gate("H_A — Masters Under prepartido",
                    strategies["masters"]["forward"], 400, 5)
                    if "masters" in strategies else None,
        "h_b": gate("H_B — Setka Under tras set 1",
                    strategies["setka"]["forward"], 200, 5)
                    if "setka" in strategies else None,
        "cand": gate("Manus — prueba limpia",
                     strategies["cand"]["forward"], 1000, 5)
                     if "cand" in strategies else None,
    }
    for k in ("ST0", "ST1"):
        if not shadow.get("empty") and k in shadow.get("strategies", {}):
            g[k.lower()] = gate(f"{k} — motor Devin",
                                shadow["strategies"][k], 1000, 5)
    return g


# ---------- ensamblado ----------

def build(assets: str, out_dir: str, repo_root: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    sources = {}

    strategies = {}
    for key, meta in STRATEGIES.items():
        path = os.path.join(assets, meta["db"])
        if not os.path.exists(path):
            strategies[key] = {"empty": True, "meta": meta}
            continue
        sources[meta["db"]] = _src_info(path)
        res = analyze_sqlite(path, cut=meta.get("cut", CUT_MAIN),
                             stage_filter=0 if key == "masters" else
                             (1 if key == "setka" else None))
        res["meta"] = meta
        res["empty"] = False
        strategies[key] = res

    shadow = load_shadow_state(assets)
    shadow_res = analyze_shadow(shadow)
    if os.path.exists(os.path.join(assets, "state.tar.gz")):
        sources["state.tar.gz"] = _src_info(
            os.path.join(assets, "state.tar.gz"))

    book = load_book(os.path.join(assets, "masters_book.json"))
    if book:
        sources["masters_book.json"] = _src_info(
            os.path.join(assets, "masters_book.json"))

    commits = recent_commits(repo_root)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": sources,
        "strategies": {},
        "gates": gates(strategies, shadow_res),
        "capacity": shadow_res.get("capacity", {}),
        "book": book,
        "commits": commits,
        "quality": _quality(strategies, shadow_res),
    }
    for key, res in strategies.items():
        if res.get("empty"):
            summary["strategies"][key] = {"empty": True, "meta": res["meta"]}
            continue
        summary["strategies"][key] = {
            "meta": res["meta"], "total": res["total"], "open": res["open"],
            "void": res["void"], "weak": res["weak"], "legacy": res["legacy"],
            "last_ts": res["last_ts"], "all": res["all"],
            "forward": res["forward"],
        }
        detail = {k: res[k] for k in
                  ("meta", "all", "forward", "by_league", "by_stage",
                   "by_day", "by_price", "calibration", "equity",
                   "recent", "bets", "src_mix", "open", "void", "weak",
                   "legacy", "last_ts", "total")}
        _write(os.path.join(out_dir, f"strategy_{key}.json"),
               {"schema_version": SCHEMA_VERSION, "strategy": key,
                **detail})

    for k in ("ST0", "ST1"):
        if not shadow_res.get("empty"):
            summary["strategies"][k.lower()] = {
                "meta": {"name": f"Devin {k}", "desc":
                         "Motor shadow ST0/ST1 (este repo, rama shadow)."},
                **{kk: shadow_res["strategies"][k].get(kk)
                   for kk in ("n", "w", "l", "p", "wr", "wr_ci95", "roi",
                              "net_u", "ci95", "p_pos", "p_roi3", "mdd_u",
                              "max_loss_streak", "clv", "n_matches",
                              "n_days", "first_day", "last_day",
                              "opportunities")},
            }
            detail = shadow_res["strategies"][k]
            detail["meta"] = {"name": f"Devin {k}"}
            _write(os.path.join(out_dir, f"strategy_{k.lower()}.json"),
                   {"schema_version": SCHEMA_VERSION, "strategy": k.lower(),
                    **_shadow_detail(detail)})

    _write(os.path.join(out_dir, "summary.json"), summary)
    return summary


def _shadow_detail(detail: dict) -> dict:
    bets = detail.get("bets", [])
    strong = [b for b in bets if b.get("result") in ("W", "L")]
    out = {"bets": bets,
           "equity": _equity_series([{**b, "ts_ms": b["ts"]}
                                     for b in strong]),
           "by_league": group_stats(
               [{**b, "ts_ms": b["ts"], "game_id": b["gid"],
                 "pnl_1u": b["pnl_1u"], "result": b["result"]}
                for b in strong], lambda b: b["league"] or "?"),
           "by_day": group_stats(
               [{**b, "ts_ms": b["ts"], "game_id": b["gid"],
                 "pnl_1u": b["pnl_1u"], "result": b["result"]}
                for b in strong],
               lambda b: dt.datetime.fromtimestamp(
                   b["ts_ms"] / 1000, dt.timezone.utc).date().isoformat()),
           "by_price": group_stats(
               [{**b, "ts_ms": b["ts"], "game_id": b["gid"],
                 "pnl_1u": b["pnl_1u"], "result": b["result"]}
                for b in strong], lambda b: price_band(b["price"])),
           "calibration": calibration(
               [{**b, "p_model": b["p"], "result": b["result"]}
                for b in strong]),
           "recent": [{**b, "src": b.get("src", "tape")} for b in bets[-40:][::-1]],
           "opportunities": detail.get("opportunities", 0)}
    out.update({k: detail.get(k) for k in
                ("n", "w", "l", "p", "wr", "wr_ci95", "roi", "net_u",
                 "ci95", "p_pos", "p_roi3", "mdd_u", "max_loss_streak",
                 "clv", "n_matches", "n_days", "first_day", "last_day")})
    return out


def _src_info(path: str) -> dict:
    st = os.stat(path)
    return {"bytes": st.st_size,
            "mtime": dt.datetime.fromtimestamp(
                st.st_mtime, dt.timezone.utc).isoformat()}


def _quality(strategies: dict, shadow: dict) -> dict:
    q = {"per_strategy": {}}
    now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    for key, res in strategies.items():
        if res.get("empty"):
            continue
        mix = res["src_mix"]
        verified = mix.get("observed", 0) + mix.get("results_api", 0)
        q["per_strategy"][key] = {
            "src_mix": mix,
            "pct_verified": verified / res["total"] if res["total"] else None,
            "open": res["open"], "void": res["void"], "weak": res["weak"],
            "last_ts": res["last_ts"],
            "age_min": (now - res["last_ts"]) / 60000
            if res["last_ts"] else None,
        }
    if not shadow.get("empty"):
        caps = shadow.get("capacity", {})
        q["per_strategy"]["st0_st1"] = {
            "capacity_probes": caps.get("probes", 0),
            "capacity_blocked": caps.get("blocked", 0),
            "capacity_coef_moved": caps.get("coef_moved", 0),
        }
    return q


def _write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True,
                    help="Directorio con los assets de los releases")
    ap.add_argument("--out", required=True, help="Directorio site/data")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--require", nargs="*", default=[],
                    help="Assets que deben existir o falla")
    a = ap.parse_args()

    missing = [r for r in a.require
               if not os.path.exists(os.path.join(a.assets, r))]
    if missing:
        print(f"FALLO: faltan assets requeridos: {missing}", file=sys.stderr)
        return 2
    summary = build(a.assets, a.out, a.repo_root)
    n_strat = sum(1 for v in summary["strategies"].values()
                  if not v.get("empty"))
    print(f"OK: {n_strat} estrategias, "
          f"{len(summary['sources'])} assets, "
          f"summary.json -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
