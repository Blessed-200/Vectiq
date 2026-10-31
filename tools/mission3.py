"""Misión: reconstrucción forense + capacidad económica + hipótesis A-F.
Todo desde datos originales (/tmp/prematch.json, /tmp/mech.json, /tmp/bt3.json,
/tmp/finals.json, /tmp/finals_oct30.json). Un solo pase por sección.
"""
import math
import json
import sys
import statistics as st
from collections import defaultdict

import numpy as np

rng = np.random.default_rng(11)
M = json.load(open("/tmp/prematch.json"))
W = json.load(open("/tmp/mech.json"))

def devig(o, u):
    return (1 / u) / ((1 / u) + (1 / o))

def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    d = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - d) / den, (c + d) / den)

# ================== RECONSTRUCCIÓN FORENSE ==================
print("========== 1. RECONSTRUCCIÓN FORENSE ==========")
n_series = len(M)
n_fin = sum(1 for m in M.values() if m["final_total"] is not None)
print(f"partidos con serie prepartido: {n_series} | con final: {n_fin} | sin final: {n_series - n_fin}")

mt = {}
for w in W:
    if w["stage"] == 0 and w.get("final_total") is not None:
        mt[w["gid"]] = w
print(f"ventanas st0 con final (mech): {len(mt)}")

n3 = 0
shifts = []
for w in mt.values():
    pts = sorted((L["line"], devig(L["over"], L["under"])) for L in w["ladder"])
    if len(pts) < 3:
        continue
    n3 += 1
    cen = None
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 <= 0.5 <= p2 or p2 <= 0.5 <= p1:
            cen = l1 + (0.5 - p1) * (l2 - l1) / (p2 - p1)
            break
    if cen is not None:
        shifts.append((w["gid"], cen - w["final_total"]))
sh = np.array([s[1] for s in shifts])
print(f"partidos >=3 líneas: {n3} | con cruce p=0.5 interpolable: {len(sh)}")
print(f"delta = centro-final: media={sh.mean():+.2f} IC95%=[{sh.mean()-1.96*sh.std()/len(sh)**.5:+.2f},{sh.mean()+1.96*sh.std()/len(sh)**.5:+.2f}] med={np.median(sh):+.1f}")

# GAP reconstrucción (historial causal >=8 por jugador)
ms = sorted(M.values(), key=lambda m: m["series"][0][0])
hist = defaultdict(list)
res = []
for m in ms:
    if m["final_total"] is None:
        continue
    last = m["series"][-1][1]
    if not last:
        continue
    c = min(last.items(), key=lambda kv: abs(devig(kv[1][0], kv[1][1]) - 0.5))
    ln = float(c[0]); o, u = c[1]
    p1, p2 = m["players"].split(" vs ")
    means = [np.mean(hist[p]) for p in (p1, p2) if len(hist[p]) >= 8]
    if len(means) == 2:
        res.append({"gid": None, "pmu": float(np.mean(means)), "ln": ln,
                    "won": m["final_total"] < ln, "roi": (u - 1) if m["final_total"] < ln else -1.0,
                    "u": u, "day": m["day"], "league": m["league"], "players": m["players"]})
    for p_ in (p1, p2):
        hist[p_].append(m["final_total"])
print(f"partidos con historial >=8 ambos jugadores: {len(res)}")
for lo, hi in [(-99, 0), (0, 5), (5, 10), (10, 99)]:
    sel = [r for r in res if lo <= r["ln"] - r["pmu"] < hi]
    k = sum(r["won"] for r in sel)
    ci = wilson(k, len(sel))
    print(f"  gap[{lo},{hi}): n={len(sel)} WR={k/max(1,len(sel)):.3f} ICwr=[{ci[0]:.3f},{ci[1]:.3f}] ROI={np.mean([r['roi'] for r in sel]) if sel else 0:+.1%}")
allr = [r["roi"] for r in res]
print(f"  st0-misma-muestra: n={len(allr)} ROI={np.mean(allr):+.1%}")

# exclusión: cuántos se perdieron por falta de historial
n_with_hist0 = sum(1 for m in ms if m["final_total"] is not None)
print(f"denominadores: finales={n_with_hist0} -> historial-suficiente={len(res)} ({len(res)/n_with_hist0:.0%}); excluidos por historial<8")

# ============== 2. CAPACIDAD / LIQUIDEZ ==============
print("\n========== 2. AUDITORÍA DE LIQUIDEZ ==========")
# el tape contiene: flag B (bloqueada), desaparición de mercados (E->null), cambios de cuota
# NO contiene: importes, aceptaciones/rechazos, parciales, latencia fill, límites máximos
n_blocked = n_null = n_q = 0
for w_ in W:
    for L in w_["ladder"]:
        n_q += 1
# los flags B ya se filtraron en extracción; chequear frecuencia de 'blocked' en bets
d3 = json.load(open("/tmp/bt3.json"))
fin_old = {int(k): [tuple(s) for s in v] for k, v in json.load(open("/tmp/finals.json")).items()}
bets = [b for b in d3["bets"] if b["market"] == "total" and b["sel"] == "under" and "masters" in b["league"].lower()]
for b in bets:
    if b["won"] is None and b["gid"] in fin_old:
        b["won"] = sum(x + y for x, y in fin_old[b["gid"]]) < b["line"]
dB = json.load(open("/tmp/frozenB.json"))
fin30 = {int(k): v for k, v in json.load(open("/tmp/finals_oct30.json")).items()}
for b in dB["bets"]:
    if b["won"] is None and b["gid"] in fin30:
        b["won"] = sum(x + y for x, y in fin30[b["gid"]]) < b["line"]
settled = [b for b in bets if b["won"] is not None]
print(f"[VERIFICADO] en tape: flag B de mercado bloqueado, suspensiones, desaparición de mercado (E->null), frecuencia de cambio de cuota")
print(f"[DESCONOCIDO] límites máximos por selección, importes aceptados/rechazados, parciales, latencia señal->fill, deslizamiento real")
print(f"[ESTIMADO] capacidad de ejecución: solo modelable vía supuestos, no verificable")

# escenarios de capital — simulación sobre serie empírica de pnl unitario
u_all = np.array([(b["odds"] - 1) if b["won"] else -1.0 for b in settled])
odds_all = np.array([b["odds"] for b in settled])
p_all = np.array([b["p"] for b in settled])
per_day = 345  # ritmo observado estrategia original
print(f"\n== escenarios de capital (serie empírica n={len(u_all)}, simulación) ==")
print(f"{'bankroll':>8} | {'stake_mod':>10} | {'apostado/día':>11} | {'exp máx':>7} | {'pnl30d med':>10} | {'MDD95':>6}")
def run_mc(B0, mode, n_days=30, per=per_day, nsim=800):
    # vectorizado por día: equity como suma de stakes en el día (aprox lineal)
    finals_eq = np.empty(nsim); mdds = np.empty(nsim); expmax = np.empty(nsim)
    for i in range(nsim):
        eq = B0; pk = B0; mdd = 0.0; em = 0.0
        ix_all = rng.integers(0, len(u_all), (n_days, per))
        for ix in ix_all:
            if mode == "fixed1pct":
                s = np.full(per, B0 * 0.01)
            elif mode == "prop2.5":
                s = np.full(per, eq * 0.025)
            else:
                psh = 1 / odds_all[ix] + (p_all[ix] - 1 / odds_all[ix]) * 0.5
                k = np.maximum(0.0, (psh * odds_all[ix] - 1) / (odds_all[ix] - 1))
                s = eq * np.minimum(0.05, 0.5 * k)
            em = max(em, s.max())
            eq += float((s * u_all[ix]).sum())
            pk = max(pk, eq); mdd = max(mdd, 1 - eq / pk)
        finals_eq[i] = eq; mdds[i] = mdd; expmax[i] = em
    return np.median(finals_eq) - B0, np.percentile(mdds, 95), np.percentile(expmax, 50)
for B0 in (100, 500, 1000, 5000):
    for mode in ("fixed1pct", "prop2.5", "kelly"):
        pnl, mdd95, em = run_mc(B0, mode)
        stakenote = {"fixed1pct": B0*0.01, "prop2.5": B0*0.025, "kelly": B0*0.025}[mode]
        print(f"{B0:>8} | {mode:>10} | {stakenote*per_day:>11.0f} | {em:>7.0f} | {pnl:>10.0f} | {mdd95:>5.0%}")

# ============== 3. HIPÓTESIS ==============
print("\n========== 3. HIPÓTESIS ==========")
# A: ST1 gap>=5 vs ST0 en los MISMOS partidos
g5 = [r for r in res if r["ln"] - r["pmu"] >= 5]
g0 = [r for r in res if r["ln"] - r["pmu"] < 5]
print(f"A gap>=5: n={len(g5)} ROI={np.mean([r['roi'] for r in g5]):+.1%} | gap<5 (mismo universo): n={len(g0)} ROI={np.mean([r['roi'] for r in g0]):+.1%}")
# dependencia: % beneficio de top-3 jugadores
pl_pnl = defaultdict(float)
for r in g5:
    for p_ in r["players"].split(" vs "):
        pl_pnl[p_] += r["roi"]
tot = sum(pl_pnl.values())
top3 = sum(sorted(pl_pnl.values(), reverse=True)[:3])
print(f"  concentración: top3 jugadores aportan {top3/tot:.0%} del pnl total ({len(pl_pnl)} jugadores)")
agg = defaultdict(list)
for r in g5:
    agg[r["league"]].append(r["roi"])
print("  por liga gap>=5:", {k: f"{np.mean(v):+.0%}(n={len(v)})" for k, v in sorted(agg.items()) if len(v) >= 3})
# cuotas: ¿el ROI viene de cuotas más altas?
print(f"  cuota media gap>=5: {np.mean([r['u'] for r in g5]):.3f} vs gap<5: {np.mean([r['u'] for r in g0]):.3f}")
# prospectivo: gap>=5 en 30 oct
g30 = [r for r in res if r["day"] == "2026-10-30"]
g30_5 = [r for r in g30 if r["ln"] - r["pmu"] >= 5]
if g30:
    print(f"  30oct: n={len(g30)} ROI={np.mean([r['roi'] for r in g30]):+.1%} | gap>=5: n={len(g30_5)} ROI={np.mean([r['roi'] for r in g30_5]) if g30_5 else 0:+.1%}")

# B: shrinkage — estimar kappa cronológicamente
# kappa = var_liga / var_players_within... estimación por momentos: EB
print("\nB shrinkage:")
kappa_est = []
pl_tot = defaultdict(list)
for m in ms:
    if m["final_total"] is None:
        continue
    for p_ in m["players"].split(" vs "):
        pl_tot[p_].append(m["final_total"])
# momentos: var total = var_entre_jugadores + var_dentro
mu_pl = np.array([np.mean(v) for v in pl_tot.values() if len(v) >= 8])
var_between = mu_pl.var()
var_within = np.mean([np.var(v) for v in pl_tot.values() if len(v) >= 8])
kappa = var_within / max(var_between, 1e-9)
print(f"  EB momentos: var_entre={var_between:.1f} var_dentro={var_within:.1f} -> kappa≈{kappa:.1f} partidos previos equivalentes")
# comparar predicción: media cruda vs shrink (kappa) vs liga en error cuadrático cronológico
hist2 = defaultdict(list)
err = defaultdict(list)
LG_MU = defaultdict(lambda: 103.0)
for m in ms:
    if m["final_total"] is None:
        continue
    p1, p2 = m["players"].split(" vs ")
    for p_ in (p1, p2):
        h = hist2[p_]
        if len(h) >= 3:
            raw = np.mean(h)
            shr = (len(h) * raw + kappa * 103.0) / (len(h) + kappa)
            err["raw"].append((raw - m["final_total"]) ** 2)
            err["shrunk"].append((shr - m["final_total"]) ** 2)
            err["liga"].append((103.0 - m["final_total"]) ** 2)
    for p_ in (p1, p2):
        hist2[p_].append(m["final_total"])
for k_, v in err.items():
    print(f"  {k_}: RMSE={np.sqrt(np.mean(v)):.2f} n={len(v)}")

# C: distribución empírica de residuos vs normal
print("\nC residual empírico vs normal:")
histc = defaultdict(list)
resid_past = []
comp = []
for m in ms:
    if m["final_total"] is None:
        continue
    last = m["series"][-1][1]
    if not last:
        continue
    pts = sorted((float(k2), devig(v[0], v[1])) for k2, v in last.items())
    cen = None
    for (l1, pp1), (l2, pp2) in zip(pts, pts[1:]):
        if pp1 <= 0.5 <= pp2 or pp2 <= 0.5 <= pp1:
            cen = l1 + (0.5 - pp1) * (l2 - l1) / (pp2 - pp1)
            break
    if cen is None:
        continue
    resid = m["final_total"] - cen
    if len(resid_past) >= 60:
        xs = sorted(resid_past)
        ln_c = ln  # línea centrada
        target = ln_c - cen  # residuo necesario para under
        c_emp = sum(x < target for x in xs) / len(xs)
        mu_, sd_ = np.mean(xs), np.std(xs)
        c_norm = 0.5 * (1 + math.erf((target - mu_) / (sd_ * np.sqrt(2)))) if sd_ > 0 else 0.5
        won = resid < target
        comp.append((c_emp, c_norm, won))
    resid_past.append(resid)
if comp:
    b_e = np.mean([(c - w) ** 2 for c, _, w in comp])
    b_n = np.mean([(c - w) ** 2 for _, c, w in comp])
    ll_e = -np.mean([w * np.log(max(c, 1e-9)) + (1 - w) * np.log(max(1 - c, 1e-9)) for c, _, w in comp])
    ll_n = -np.mean([w * np.log(max(c, 1e-9)) + (1 - w) * np.log(max(1 - c, 1e-9)) for _, c, w in comp])
    print(f"  n={len(comp)} Brier: empírico={b_e:.4f} normal={b_n:.4f} | LogLoss: emp={ll_e:.4f} norm={ll_n:.4f}")

# D: línea -4..+2 del centro vs completa
print("\nD selección de línea por offset:")
off = defaultdict(list)
for w in mt.values():
    pts = sorted((L["line"], devig(L["over"], L["under"])) for L in w["ladder"])
    cen = None
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 <= 0.5 <= p2 or p2 <= 0.5 <= p1:
            cen = l1 + (0.5 - p1) * (l2 - l1) / (p2 - p1)
            break
    if cen is None:
        continue
    for L in w["ladder"]:
        won = w["final_total"] < L["line"]
        u_roi = (L["under"] - 1) if won else -1
        o = round(L["line"] - cen)
        off[o].append(u_roi)
inb = [x for o_, v in off.items() if -4 <= o_ <= 2 for x in v]
out_ = [x for o_, v in off.items() if not (-4 <= o_ <= 2) for x in v]
print(f"  dentro[-4,+2]: n={len(inb)} ROI={np.mean(inb):+.1%} | fuera: n={len(out_)} ROI={np.mean(out_):+.1%}")

# E: ligas — persistencia 27-29 vs 30
print("\nE ligas δ por periodo:")
lag = defaultdict(lambda: defaultdict(list))
for w in mt.values():
    pts = sorted((L["line"], devig(L["over"], L["under"])) for L in w["ladder"])
    cen = None
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 <= 0.5 <= p2 or p2 <= 0.5 <= p1:
            cen = l1 + (0.5 - p1) * (l2 - l1) / (p2 - p1)
            break
    if cen is None:
        continue
    period = "30oct" if w["day"] == "2026-10-30" else "27-29"
    lag[w["league"]][period].append(cen - w["final_total"])
for lg, per in sorted(lag.items()):
    a = np.mean(per["27-29"]) if per["27-29"] else None
    b = np.mean(per["30oct"]) if per["30oct"] else None
    print(f"  {lg}: δ27-29={a:+.1f}(n={len(per['27-29'])}) δ30={b if b is None else f'{b:+.1f}'}(n={len(per['30oct'])})")

# F: patrón en U por día
print("\nF patrón U por día:")
Mv = json.load(open("/tmp/prematch.json"))
per_day_u = defaultdict(lambda: defaultdict(list))
for g, m in Mv.items():
    if m["final_total"] is None or len(m["series"]) < 2:
        continue
    s = m["series"]
    for ln, (o0, u0) in s[0][1].items():
        if ln in s[-1][1]:
            du = s[-1][1][ln][1] - u0
            won = m["final_total"] < float(ln)
            grp = "ext" if abs(du) >= 0.05 else ("quiet" if abs(du) <= 0.01 else "mid")
            per_day_u[m["day"]][grp].append((s[-1][1][ln][1] - 1) if won else -1)
for day, gr in sorted(per_day_u.items()):
    print(f"  {day}: " + " ".join(f"{k}={np.mean(v):+.0%}(n={len(v)})" for k, v in sorted(gr.items())))
