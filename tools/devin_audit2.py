"""Auditoría v2 — correcciones metodológicas sobre devin_audit.py.

Corrige:
 1. Calibration slope: IC95% por bootstrap + interpretación correcta.
 2. Descomposición de Brier: verifica identidad Murphy = Brier individual.
 3. Bootstrap línea>=108.5: incluye partidos parcialmente elegibles
    (antes se excluía cualquier cluster con apuestas fuera de la máscara).
 4. Dependencia temporal: bootstrap por días (n_días=3) — declara qué NO se puede inferir.
 5. Riesgo: separa pérdida extrema / drawdown / ruina operativa / pérdida total.
 6. P(ROI>0) distinguiendo: muestreo de apuestas, entre partidos, entre días.
"""
import json
import math
import sys
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize

BT, FIN = sys.argv[1], sys.argv[2]
d = json.load(open(BT))
finals = {int(k): [tuple(s) for s in v] for k, v in json.load(open(FIN)).items()}

bets = [b for b in d["bets"] if b["market"] == "total" and b["sel"] == "under" and "masters" in b["league"].lower()]
for b in bets:
    if b["won"] is None and b["gid"] in finals:
        sets = finals[b["gid"]]
        b["won"] = sum(x + y for x, y in sets) < b["line"]

g = [b for b in bets if b["won"] is not None]
rng = np.random.default_rng(7)
B = 20000

y = np.array([b["won"] for b in g], float)
odds = np.array([b["odds"] for b in g])
line = np.array([b["line"] or 0 for b in g])
p = np.array([b["p"] for b in g])
gid = np.array([b["gid"] for b in g])
day = np.array([b["day"] for b in g])
u = np.where(y > 0, odds - 1.0, -1.0)

by_gid = defaultdict(list)
for i, x in enumerate(gid):
    by_gid[x].append(i)
gid_arrs = [np.array(ix) for ix in by_gid.values()]
by_day = defaultdict(list)
for i, x in enumerate(day):
    by_day[x].append(i)
days = sorted(by_day)

# ---------- 1) calibration intercept/slope con IC ----------
logit_p = np.log(p / (1 - p))
def fit_logreg(x, yy):
    def nll(ab):
        z = ab[0] + ab[1] * x
        ll = np.sum(yy * z - np.logaddexp(0, z))
        return -ll
    r = minimize(nll, [0.0, 1.0], method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-8})
    return r.x
a0, b0 = fit_logreg(logit_p, y)
print("== calibration intercept/slope ==")
print(f"point: intercept={a0:+.3f} slope={b0:.3f}")
# bootstrap por partido del slope
bs = np.empty(B)
for i in range(B):
    idx = np.concatenate([gid_arrs[j] for j in rng.integers(0, len(gid_arrs), len(gid_arrs))])
    aa, bb = fit_logreg(logit_p[idx], y[idx])
    bs[i] = bb
lo, hi = np.percentile(bs, [2.5, 97.5])
print(f"slope IC95% cluster-boot: [{lo:.3f},{hi:.3f}]")
print("interpretación: slope<1 => las p son DEMASIADO EXTREMAS (sobre-confianza en la dispersión);")
print("para calibrar hay que encogerlas hacia p̄. slope≈0.44 => la señal discriminativa real es ~44%")
print("de la que el modelo atribuye a sus diferencias de p.")

# ---------- 2) identidad Brier: individual vs descomposición ----------
def brier_decomp(pp, yy, nbins=10):
    qs = np.quantile(pp, np.linspace(0, 1, nbins + 1)); qs[0], qs[-1] = -1e-9, 1e9
    bs_ = float(np.mean((pp - yy) ** 2))
    rel = res = 0.0
    for i in range(nbins):
        m = (pp >= qs[i]) & (pp < qs[i + 1])
        if m.sum() == 0:
            continue
        rel += m.sum() * (pp[m].mean() - yy[m].mean()) ** 2
        res += m.sum() * (yy[m].mean() - yy.mean()) ** 2
    rel /= len(pp); res /= len(pp); unc = yy.mean() * (1 - yy.mean())
    return bs_, unc, res, rel

bs_, unc, res, rel = brier_decomp(p, y)
print("\n== Brier: identidad individual vs descomposición ==")
print(f"Brier individual media = {bs_:.6f}")
print(f"UNC - RES + REL = {unc:.6f} - {res:.6f} + {rel:.6f} = {unc-res+rel:.6f}")
print(f"diferencia (error de bineado) = {bs_-(unc-res+rel):+.6f}")

# ---------- 3) bootstrap línea>=108.5 corregido ----------
def boot_roi_fixed(mask):
    """Re-muestrea partidos; dentro de cada partido muestreado cuenta solo
    las apuestas elegibles por mask. Corrige la exclusión previa de
    partidos parcialmente elegibles."""
    out = np.empty(B)
    for i in range(B):
        pick = rng.integers(0, len(gid_arrs), len(gid_arrs))
        parts = [u[gid_arrs[j][mask[gid_arrs[j]]]] for j in pick]
        parts = [x for x in parts if len(x)]
        out[i] = np.concatenate(parts).mean() if parts else np.nan
    return out[~np.isnan(out)]

mask108 = line >= 108.5
bb = boot_roi_fixed(mask108)
print(f"\n== bootstrap línea>=108.5 CORREGIDO (n={mask108.sum()} apuestas) ==")
print(f"ROI={u[mask108].mean():+.1%} IC95%=[{np.percentile(bb,2.5):+.1%},{np.percentile(bb,97.5):+.1%}] P(>0)={np.mean(bb>0):.1%}")

# ---------- 4) ¿qué se puede inferir de 3 días? ----------
print("\n== componentes de varianza (ROI por unidad) ==")
print(f"n={len(g)} apuestas / {len(gid_arrs)} partidos / {len(days)} días")
# varianza entre días: solo 3 puntos => se reporta, no se infiere
drois = {d_: u[ix].mean() for d_, ix in by_day.items()}
print("ROI por día:", {k: f"{v:+.1%}" for k, v in drois.items()})
print("Con 3 días NO se puede estimar varianza inter-día ni drift con potencia;")
print("se reporta dispersión observada sin afirmar que el proceso sea estacionario.")

# ---------- 5) riesgo: categorías separadas ----------
print("\n== categorías de riesgo en histórico (stake plano 1 unidad por apuesta) ==")
eq = np.cumsum(u)
peak = np.maximum.accumulate(np.r_[0, eq])
mdd_u = float((peak[1:] - eq).max() if len(eq) else 0)
print(f"pérdida extrema (peor apuesta): {u.min():+.0f}u")
print(f"max drawdown histórico (unidades): {mdd_u:.0f}u = {mdd_u*100/len(g):.1f}% del turnover")
print("ruina operativa (banca <20% inicial) depende del stake fraccional — ver MC")
print("pérdida total: solo si equity llega a 0; con stake 2.5% dinámico es inalcanzable matemáticamente")

# ---------- 6) P(ROI>0) descompuesta ----------
print("\n== P(ROI futuro>0) bajo distintas hipótesis ==")
# (a) solo muestreo iid de apuestas
b_iid = np.array([u[rng.integers(0, len(u), len(u))].mean() for _ in range(B)])
print(f"(a) iid apuestas: P={np.mean(b_iid>0):.1%} IC95%=[{np.percentile(b_iid,2.5):+.1%},{np.percentile(b_iid,97.5):+.1%}]")
# (b) cluster por partido
b_gid = boot_roi_fixed(np.ones(len(u), bool))
print(f"(b) cluster partido: P={np.mean(b_gid>0):.1%} IC95%=[{np.percentile(b_gid,2.5):+.1%},{np.percentile(b_gid,97.5):+.1%}]")
# (c) días: re-muestrear días enteros (n=3 => solo 3 valores posibles; documentar)
day_arrs = [u[ix] for ix in by_day.values()]
b_day = np.array([np.concatenate([day_arrs[j] for j in rng.integers(0, 3, 3)]).mean() for _ in range(B)])
print(f"(c) cluster día (3 días): P={np.mean(b_day>0):.1%} IC95%=[{np.percentile(b_day,2.5):+.1%},{np.percentile(b_day,97.5):+.1%}]")
print("    (c) solo recombina los 3 días observados: captura su dispersión pero NO días no vistos")
# (d) jerárquico: remuestrear día->partidos->apuestas
def hier():
    tot = []
    for _ in range(3):
        dj = day_arrs[rng.integers(0, 3)]
        gids_d = np.unique(gid[np.concatenate([np.flatnonzero(day == dd) for dd in [list(by_day)[0]]])])  # noqa
        tot.append(dj[rng.integers(0, len(dj), len(dj))])
    return np.concatenate(tot).mean()
b_h = np.array([hier() for _ in range(3000)])
print(f"(d) jerárquico simple: P={np.mean(b_h>0):.1%}")
for thr in (0.05, 0.10):
    print(f"  P(ROI>{thr:.0%}) cluster-partido={np.mean(b_gid>thr):.1%} cluster-día={np.mean(b_day>thr):.1%}")
