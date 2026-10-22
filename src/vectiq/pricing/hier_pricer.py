"""Hierarchical latent-strength Markov pricer for a best-of-5 table-tennis match (VECTIQ).

Model
  * A point is won by player A with probability  BASE + s  (s = match-specific strength gap, unknown to us).
  * Prior:  s ~ N(KAPPA * rating_diff/100, TAU^2).   KAPPA=0.028 (measured on 4.1M points), TAU=0.03 (maximum likelihood on Jan-Jun 2026).
  * Every completed set (exact score) updates the posterior over s (Bayes, 61-point grid).  => sets are NOT independent,
    which an i.i.d.-sets pricer gets wrong (it over-predicts total points by ~1.4-1.8 pts).
  * Serve rule exact (2 serves each, alternating every point from 10-10); starter alternates set by set.
Usage:  HierMatchPricer().price(sets=[(11,7),(9,11)], rating_a=900, rating_b=870, a_starts_set1=True)
Scope: prices from a SET BREAK (completed sets only). For mid-set states combine with engines/markov_pricer.py.
"""
import math
import numpy as np

G = 46; TM = 2 * G; BASE = 0.5286
KAPPA = 0.028; TAU = 0.03
SGRID = np.round(np.arange(-0.15, 0.15001, 0.005), 4); K = len(SGRID)
_TABLES = None

def _a_serves(k, a_starts):
    ss = ((k // 2) % 2 == 0) if k < 20 else ((k - 20) % 2 == 0)
    return ss if a_starts else (not ss)

def _terminal(s, a_starts):
    psa, psb = BASE + s, BASE - s
    F = np.zeros((G, G)); F[0, 0] = 1.0; T = np.zeros((G, G))
    for k in range(0, 2 * G - 2):
        for a in range(max(0, k - G + 1), min(k, G - 1) + 1):
            b = k - a; p = F[a, b]
            if p == 0.0: continue
            if max(a, b) >= 11 and abs(a - b) >= 2: T[a, b] = p; continue
            if a + 1 >= G or b + 1 >= G: continue
            pa = psa if _a_serves(k, a_starts) else 1.0 - psb
            F[a + 1, b] += p * pa; F[a, b + 1] += p * (1 - pa)
    return T

def _tables():
    global _TABLES
    if _TABLES is None:
        TERM = np.zeros((K, 2, G, G)); PW = np.zeros((K, 2)); DIST = np.zeros((K, 2, TM))
        agt = np.arange(G)[:, None] > np.arange(G)[None, :]
        for i, s in enumerate(SGRID):
            for j, st in enumerate((True, False)):
                T = _terminal(float(s), st); TERM[i, j] = T
                PW[i, j] = (T * agt).sum() / T.sum()
                for a in range(G):
                    for b in range(G):
                        if T[a, b] > 0 and a + b < TM: DIST[i, j, a + b] += T[a, b]
                DIST[i, j] /= DIST[i, j].sum()
        _TABLES = (TERM, PW, DIST)
    return _TABLES

class HierMatchPricer:
    def __init__(self, kappa=KAPPA, tau=TAU):
        self.kappa, self.tau = kappa, tau; _tables()

    def posterior(self, sets, rating_a, rating_b, a_starts_set1):
        TERM, _, _ = _tables()
        mu = self.kappa * (rating_a - rating_b) / 100.0
        w = np.exp(-0.5 * ((SGRID - mu) / self.tau) ** 2); w /= w.sum()
        for j, (x, y) in enumerate(sets):
            a_st = a_starts_set1 if j % 2 == 0 else (not a_starts_set1)
            w = w * np.maximum(TERM[:, 0 if a_st else 1, int(x), int(y)], 1e-300)
        return w / w.sum()

    def price(self, sets, rating_a=0.0, rating_b=0.0, a_starts_set1=True):
        _, PW, DIST = _tables()
        n = len(sets); aw = sum(1 for x, y in sets if x > y); bw = n - aw
        if aw >= 3 or bw >= 3: raise ValueError("match already finished")
        sofar = int(sum(x + y for x, y in sets))
        post = self.posterior(sets, rating_a, rating_b, a_starts_set1)
        pdf = np.zeros(sofar + 5 * TM + 5); pA = 0.0
        for i in np.nonzero(post > 1e-9)[0]:
            stack = [(aw, bw, n, 1.0, np.array([1.0]))]
            while stack:
                sa, sb, nd, pr, cv = stack.pop()
                if sa == 3 or sb == 3:
                    if sa == 3: pA += post[i] * pr
                    pdf[sofar:sofar + len(cv)] += post[i] * pr * cv; continue
                j = 0 if (a_starts_set1 if nd % 2 == 0 else (not a_starts_set1)) else 1
                cv2 = np.convolve(cv, DIST[i, j]); pw = PW[i, j]
                stack.append((sa + 1, sb, nd + 1, pr * pw, cv2)); stack.append((sa, sb + 1, nd + 1, pr * (1 - pw), cv2))
        pdf /= pdf.sum()
        return PriceResult(float(pA), pdf)

class PriceResult:
    def __init__(self, p_a_match, pdf): self.p_a_match, self.pdf = p_a_match, pdf
    @property
    def expected_total(self): return float((np.arange(len(self.pdf)) * self.pdf).sum())
    def p_over(self, line):  i = int(math.floor(line)) + 1; return float(self.pdf[i:].sum()) if i < len(self.pdf) else 0.0
    def p_under(self, line): return float(self.pdf[:int(math.ceil(line))].sum())
    def fair_odds_over(self, line):  return 1.0 / max(self.p_over(line), 1e-9)
    def fair_odds_under(self, line): return 1.0 / max(self.p_under(line), 1e-9)
