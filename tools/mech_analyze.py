"""Análisis del mecanismo de la ventaja Masters-Under a partir de mech.json.

Para cada ventana (partido × stage) con final conocido, cada línea de la
escalera tiene: cuota over, cuota under, y resultado real (final_total < line).
Agrupando por línea y stage obtenemos P(under|line,stage) verdadera vs la
probabilidad implícita del mercado devigueada — donde está el error del book.
"""
import json
import math
import statistics as st
import sys
from collections import defaultdict

W = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mech.json"))
W = [w for w in W if w.get("final_total") is not None]
print(f"# {len(W)} ventanas con final")

def devig(o_over, o_under):
    return (1.0 / o_under) / (1.0 / o_over + 1.0 / o_under)

# ---------------- 1) línea vs total final (st0) ----------------
print("\n== st0: (línea - final_total) por cubo de línea ==")
buckets = defaultdict(list)
for w in W:
    if w["stage"] != 0:
        continue
    for L in w["ladder"]:
        buckets[round(L["line"])].append((L["line"] - w["final_total"], L["under"], L["line"]))
rows = []
for b in sorted(buckets):
    xs = buckets[b]
    gaps = [x[0] for x in xs]
    rows.append((b, len(xs), st.mean(gaps), st.median(gaps)))
for b, n, m, md in rows:
    print(f"  línea {b}: n={n}  media(line-final)={m:+.2f}  mediana={md:+.1f}")

# ---------------- 2) P(under) real vs implícita por línea (st0) ----------------
print("\n== st0: P(under) REAL vs IMPLÍCITA (devig) por línea ==")
agg = defaultdict(lambda: [0, 0, [], []])  # wins, n, implied, odds
for w in W:
    if w["stage"] != 0:
        continue
    for L in w["ladder"]:
        k = round(L["line"])
        u = w["final_total"] < L["line"]
        a = agg[k]
        a[0] += u; a[1] += 1
        a[2].append(devig(L["over"], L["under"]))
        a[3].append(L["under"])
print("  línea | n | P_real | P_impl | edge(pp) | cuota_under media")
for k in sorted(agg):
    a = agg[k]
    if a[1] < 30:
        continue
    pr = a[0] / a[1]
    pi = st.mean(a[2])
    od = st.mean(a[3])
    flag = " <==+" if pr - pi > 0.08 else ""
    print(f"  {k:5.1f} | {a[1]:5d} | {pr:.3f} | {pi:.3f} | {(pr-pi)*100:+5.1f} | {od:.2f}{flag}")

# ---------------- 3) mismo por stage (descansos) ----------------
for stg in (1, 2, 3):
    print(f"\n== st{stg}: P(under) real vs implícita (jugado={stg} sets) ==")
    agg = defaultdict(lambda: [0, 0, [], []])
    for w in W:
        if w["stage"] != stg:
            continue
        for L in w["ladder"]:
            k = round(L["line"])
            u = w["final_total"] < L["line"]
            a = agg[k]; a[0] += u; a[1] += 1
            a[2].append(devig(L["over"], L["under"])); a[3].append(L["under"])
    for k in sorted(agg):
        a = agg[k]
        if a[1] < 30:
            continue
        pr = a[0] / a[1]; pi = st.mean(a[2]); od = st.mean(a[3])
        print(f"  {k:5.1f} | {a[1]:5d} | {pr:.3f} | {pi:.3f} | {(pr-pi)*100:+5.1f} | {od:.2f}")

# ---------------- 4) cuota under vs línea (¿el book ajusta?) ----------------
print("\n== st0: cuota_under media por línea ==")
agg = defaultdict(list)
for w in W:
    if w["stage"] != 0:
        continue
    for L in w["ladder"]:
        agg[round(L["line"])].append(L["under"])
for k in sorted(agg):
    if len(agg[k]) < 30:
        continue
    print(f"  {k:5.1f}: cuota_under={st.mean(agg[k]):.3f}  (rango {min(agg[k]):.2f}-{max(agg[k]):.2f})")

# ---------------- 5) margen de la casa ----------------
print("\n== overround medio por stage ==")
for stg in (0, 1, 2, 3):
    ovs = []
    for w in W:
        if w["stage"] != stg:
            continue
        for L in w["ladder"]:
            ovs.append(1 / L["over"] + 1 / L["under"])
    if ovs:
        print(f"  st{stg}: overround={st.mean(ovs):.3f} (vig {(st.mean(ovs)-1)*100:.1f}%)")

# ---------------- 6) estabilidad por día (st0, línea central) ----------------
print("\n== edge medio por día (st0, todas las líneas) ==")
agg = defaultdict(lambda: [0, 0, []])
for w in W:
    if w["stage"] != 0:
        continue
    for L in w["ladder"]:
        a = agg[w["day"]]
        a[0] += (w["final_total"] < L["line"]); a[1] += 1
        a[2].append(devig(L["over"], L["under"]))
for day in sorted(agg):
    a = agg[day]
    print(f"  {day}: P_real={a[0]/a[1]:.3f} P_impl={st.mean(a[2]):.3f} edge={(a[0]/a[1]-st.mean(a[2]))*100:+.1f}pp n={a[1]}")
