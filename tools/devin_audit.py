"""Auditoría forense de la estrategia «Devin» (Masters bo7, total UNDER, st0+1).

Reproduce todas las cifras del informe de elevación a partir de:
  - bt3.json: salida de tools/backtest.py (apuestas con p, odds, ev, resultado, CLV)
  - finals.json: finales reales de partidos censurados, API oficial de resultados
Uso: python3 tools/devin_audit.py bt3.json finals.json
"""
import json, sys, collections
import numpy as np
from scipy.stats import beta as beta_dist
from scipy.special import logit, expit
from scipy.optimize import minimize

BT, FN = sys.argv[1], sys.argv[2]
finals = {int(k): v for k, v in json.load(open(FN)).items()}
bets = json.load(open(BT))["bets"]

def settle(b):
    """Resultado honesto: observado en tape o final real de la API; void si no hay."""
    if b["won"] is not None:
        return b["won"]
    fs = finals.get(b["gid"])
    if fs is None:
        return None
    tot = sum(a + c for a, c in fs)
    return "P" if tot == b["line"] else (tot < b["line"])

g = [b for b in bets
     if b["market"] == "total" and b.get("sel") == "under"
     and "masters" in (b.get("league") or "").lower() and b["stage"] in (0, 1)]
g = [b for b in g if settle(b) is not None]
for b in g:
    b["o"] = settle(b)
    b["u"] = (b["odds"] - 1.0) if b["o"] is True else -1.0   # beneficio por unidad apostada

p  = np.array([b["p"] for b in g])
y  = np.array([b["o"] is True for b in g], float)
o  = np.array([b["odds"] for b in g])
u  = np.array([b["u"] for b in g])
day = np.array([b["day"] for b in g])
gid = np.array([b["gid"] for b in g])
N = len(g); NG = len(set(gid)); ND = len(set(day))
print(f"# datos: {N} apuestas liquidadas, {NG} partidos, {ND} días, "
      f"{sum(1 for b in g if b['o'] is True)}/{N} ganadas")

rng = np.random.default_rng(2026)
B = 20000

# ---------- 1. Tamaño efectivo de muestra (design effect por cluster de partido)
by_gid = collections.defaultdict(list)
for i, gd in enumerate(gid):
    by_gid[gd].append(i)
sizes = np.array([len(v) for v in by_gid.values()])
deff = 1.0 + (sizes.mean() - 1.0)          # DEFF ≈ 1+(m-1)ρ, ρ≈1 dentro del mismo partido
N_eff = NG * sizes.mean() / deff
print(f"# tamaño efectivo ≈ {N_eff:.0f} (clusters {NG}, m̄={sizes.mean():.2f}, DEFF={deff:.2f})")

# ---------- 2. Reliability diagram con IC de Wilson 95%
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0, 0)
    ph = k / n; d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return ph, c - h, c + h

print("\n== reliability (bin de p | p̄ | WR | IC95% Wilson | n) ==")
qb = np.quantile(p, np.linspace(0, 1, 11))
rel_pts = []
for i in range(10):
    m = (p >= qb[i]) & (p <= qb[i + 1] if i == 9 else p < qb[i + 1])
    if m.sum() < 10: continue
    ph, lo, hi = wilson(int(y[m].sum()), int(m.sum()))
    rel_pts.append((p[m].mean(), ph, lo, hi, int(m.sum())))
    print(f"[{qb[i]:.3f},{qb[i+1]:.3f}) p̄={p[m].mean():.3f} WR={ph:.3f} IC=[{lo:.3f},{hi:.3f}] n={m.sum()}")

# ---------- 3. Brier completo + Log Loss
def brier_decomp(pp, yy, bins=10):
    base = yy.mean(); unc = base * (1 - base)
    bs = np.mean((pp - yy) ** 2)
    qs = np.quantile(pp, np.linspace(0, 1, bins + 1))
    rel = res = 0.0
    for i in range(bins):
        m = (pp >= qs[i]) & (pp <= qs[i + 1] if i == bins - 1 else pp < qs[i + 1])
        if not m.sum(): continue
        rel += m.sum() * (pp[m].mean() - yy[m].mean()) ** 2
        res += m.sum() * (yy[m].mean() - base) ** 2
    return bs, unc, res / len(yy), rel / len(yy)

def logloss(pp, yy):
    e = np.clip(pp, 1e-9, 1 - 1e-9)
    return -np.mean(yy * np.log(e) + (1 - yy) * np.log(1 - e))

bs, unc, res, rel = brier_decomp(p, y)
p_mkt = 1.0 / o                                     # cuota implícita con vig (limitación documentada)
bsm, _, resm, relm = brier_decomp(p_mkt, y)
print(f"\nBrier modelo={bs:.4f} (Unc {unc:.4f} − Res {res:.4f} + Rel {rel:.4f})")
print(f"Brier mercado(1/odds)={bsm:.4f} (Res {resm:.4f} + Rel {relm:.4f})")
print(f"LogLoss modelo={logloss(p, y):.4f} | mercado={logloss(p_mkt, y):.4f} | base-rate={logloss(np.full(N, y.mean()), y):.4f}")

# ---------- 4. Calibration intercept & slope (y ~ logit regresión sobre logit(p))
def fit_cal(pp, yy):
    x = logit(np.clip(pp, 1e-4, 1 - 1e-4))
    X = np.c_[np.ones(len(x)), x]
    def nll(ab):
        z = X @ ab; pr = expit(z)
        return -np.sum(yy * np.log(np.clip(pr, 1e-9, 1)) + (1 - yy) * np.log(np.clip(1 - pr, 1e-9, 1)))
    r = minimize(nll, [0.0, 1.0], method="Nelder-Mead")
    return r.x
a, sl = fit_cal(p, y)
print(f"calibration intercept={a:+.3f} slope={sl:.3f}   (0/1 = perfecto)")

# ---------- 5. Calibración por día y por stage
for lab, m in [("st0", np.array([b["stage"] == 0 for b in g])),
               ("st1", np.array([b["stage"] == 1 for b in g]))] + \
              [(dd, day == dd) for dd in sorted(set(day))]:
    bsl, _, rl, rll = brier_decomp(p[m], y[m])
    print(f"{lab}: n={m.sum()} WR={y[m].mean():.3f} Brier={bsl:.4f} Res={rl:.4f} Rel={rll:.4f} "
          f"LogLoss={logloss(p[m], y[m]):.4f}")

# ---------- 6. Distribución de probabilidades (sharpness)
print(f"dist p modelo: min={p.min():.3f} q25={np.quantile(p,.25):.3f} med={np.median(p):.3f} "
      f"q75={np.quantile(p,.75):.3f} max={p.max():.3f}  rango={p.max()-p.min():.3f}")
print(f"dist p mercado: med={np.median(p_mkt):.3f} rango={p_mkt.max()-p_mkt.min():.3f}")

# ---------- 7. Auditoría del umbral (todas las reglas probadas)
print("\n== barrido completo de umbrales de línea (sin cap) ==")
for thr in np.arange(103.5, 111.5, 1.0):
    m = np.array([(b["line"] or 0) >= thr for b in g])
    if m.sum() < 30: continue
    per = {}
    for dd in sorted(set(day)):
        md = m & (day == dd)
        per[dd] = u[md].mean() if md.sum() else float("nan")
    print(f"línea>={thr:.1f}: n={m.sum()} ({m.sum()/ND:.0f}/día) WR={y[m].mean():.3f} "
          f"unitROI={u[m].mean():+.1%} | " + " ".join(f"{k}:{v:+.1%}" for k, v in per.items()))

# bootstrap por cluster del candidato elegido
def boot_roi(mask):
    """Bootstrap por cluster (partido): solo clusters totalmente dentro de mask."""
    cl = [u[np.array(ix)] for ix in by_gid.values() if np.all(mask[np.array(ix)])]
    out = np.empty(B)
    for i in range(B):
        pick = rng.integers(0, len(cl), len(cl))
        out[i] = np.concatenate([cl[j] for j in pick]).mean()
    return out

mask108 = np.array([(b["line"] or 0) >= 108.5 for b in g])
bb = boot_roi(mask108)
print(f"\nlínea≥108.5: n={mask108.sum()} ROI={u[mask108].mean():+.1%} "
      f"IC95%=[{np.percentile(bb,2.5):+.1%},{np.percentile(bb,97.5):+.1%}] P(>0)={(bb>0).mean():.1%}")
ball = boot_roi(np.ones(N, bool))
print(f"todas:      n={N} ROI={u.mean():+.1%} "
      f"IC95%=[{np.percentile(ball,2.5):+.1%},{np.percentile(ball,97.5):+.1%}] P(>0)={(ball>0).mean():.1%} "
      f"P(>5%)={(ball>0.05).mean():.1%} P(>10%)={(ball>0.10).mean():.1%}")

# ---------- 8. Auditoría probabilidades futuras
W = int(y.sum())
post = beta_dist(1 + W, 1 + N - W)
be = 1.0 / o.mean()
print(f"\nPosterior WR~Beta(1+{W},1+{N-W}): P(p>{be:.4f} breakeven@cuota media {o.mean():.3f})"
      f"={1 - post.cdf(be):.1%} IC95% WR [{post.ppf(.025):.3f},{post.ppf(.975):.3f}]")

# P(día>0) y P(mes>0): un día = ~345 apuestas iid de la empírica; mes = 30 días
nd = int(np.median([np.sum(day == d_) for d_ in set(day)]))
pd_day = np.array([u[rng.integers(0, N, nd)].mean() for _ in range(B)])
pd_month = np.array([u[rng.integers(0, N, nd * 30)].mean() for _ in range(5000)])
print(f"P(día>0)={np.mean(pd_day>0):.1%} | P(mes>0)={np.mean(pd_month>0):.1%} (iid por apuesta)")

# ---------- 9. Riesgo: iid vs preservando dependencia por partido
def mc(seqs, stake=0.025, n_days=30, per_day=None, nsim=20000, floor=0.2):
    """seqs: lista de arrays de resultados unitarios (iid) o listas por cluster (bloque)."""
    hits = np.zeros(nsim); eq = np.ones(nsim); peak = np.ones(nsim)
    mdd = np.zeros(nsim); ruined = np.zeros(nsim, bool); loss = np.zeros(nsim)
    tot = n_days * per_day
    for i in range(nsim):
        # construir la secuencia: iid o por bloques de partido
        if isinstance(seqs[0], np.ndarray) and seqs[0].size > 1:
            drawn = np.concatenate([seqs[j] for j in rng.integers(0, len(seqs), tot)])[:tot]
        else:
            drawn = rng.choice(np.concatenate(seqs), tot)
        e = np.cumprod(1 + stake * drawn)      # drawn = pnl unitario (odds-1 ó -1)
        hits[i] = (e >= 100).any()
        pk = np.maximum.accumulate(np.r_[1.0, e])
        mdd[i] = (1 - e / pk[1:]).max()
        ruined[i] = (e < floor).any()
        eq[i] = e[-1]; loss[i] = eq[i] < 1
    return eq, hits.mean(), loss.mean(), ruined.mean(), np.median(mdd), np.percentile(mdd, 95)

flat = [np.array([x]) for x in u]                      # iid apuesta a apuesta
blocks = [u[np.array(ix)] for ix in by_gid.values()]   # bloque = partido
for nm, seqs in (("iid", flat), ("bloque-partido", blocks)):
    eq, ph, pl, pr, md, m95 = mc(seqs, per_day=55)
    print(f"MC {nm}: P(100x)={ph:.2%} P(pérdida)={pl:.1%} P(ruina<20%)={pr:.1%} "
          f"MDDmed={md:.0%} MDD95={m95:.0%} med30d={np.median(eq):.1f}x")
# P(DD>20/30/40/50) con bloque
eq, *_ = mc(blocks, per_day=55)
mdds = []
for i in range(20000):
    drawn = np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), 30 * 55)])[:30 * 55]
    e = np.cumprod(1 + 0.025 * drawn)
    pk = np.maximum.accumulate(np.r_[1.0, e])
    mdds.append((1 - e / pk[1:]).max())
mdds = np.array(mdds)
for x in (0.2, 0.3, 0.4, 0.5):
    print(f"P(MDD30d>{x:.0%})={(mdds > x).mean():.1%}")
EOF = None
