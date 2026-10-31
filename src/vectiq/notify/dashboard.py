"""Self-contained HTML dashboard of every paper book (equity curves, ROI with uncertainty, sample size, ETA).

Every bet is counted as one flat unit so the books are comparable no matter how each sizes its stake. Only bets with a
real outcome (W/L/P) enter the ROI; voids and still-open bets are shown separately so nothing is hidden."""
from __future__ import annotations

import html
import math
import sqlite3
import time
from pathlib import Path
from typing import Iterable

TARGET_N = 1000          # forward sample at which the promotion verdict is taken (see docs/PROMOTION.md)


def _rows(db: sqlite3.Connection) -> list[dict]:
    cols = [r[1] for r in db.execute("PRAGMA table_info(bets)")]
    return [dict(zip(cols, r)) for r in db.execute("SELECT * FROM bets ORDER BY ts_ms, id")]


def book_stats(name: str, db: sqlite3.Connection, now_s: float) -> dict:
    rows = _rows(db)
    real = [r for r in rows if r["result"] in ("W", "L", "P")]
    units = [(0.0 if r["result"] == "P" else (r["price"] - 1 if r["result"] == "W" else -1.0), r["ts_ms"]) for r in real]
    n = len(units)
    mean = sum(u for u, _ in units) / n if n else 0.0
    sd = math.sqrt(sum((u - mean) ** 2 for u, _ in units) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    p_pos = 0.5 * (1 + math.erf(mean / se / math.sqrt(2))) if n > 1 and se > 0 else float("nan")
    first = rows[0]["ts_ms"] / 1000 if rows else now_s
    days = max((now_s - first) / 86400, 1e-9)
    rate = n / days if n else 0.0
    eta = (TARGET_N - n) / rate if rate > 0 and n < TARGET_N else 0.0
    curve, acc = [], 0.0
    for u, _ in units:
        acc += u
        curve.append(acc)
    src = {}
    for r in real:
        src[r.get("src") or "legacy"] = src.get(r.get("src") or "legacy", 0) + 1
    return {"name": name, "n": n, "open": sum(r["result"] is None for r in rows), "void": sum(r["result"] == "V" for r in rows),
            "hit": (sum(r["result"] == "W" for r in real) / max(1, sum(r["result"] in ("W", "L") for r in real))),
            "roi": mean, "lo": mean - 1.96 * se if n > 1 else float("nan"), "hi": mean + 1.96 * se if n > 1 else float("nan"),
            "p_pos": p_pos, "rate": rate, "eta_days": eta, "curve": curve, "src": src,
            "since": time.strftime("%Y-%m-%d", time.gmtime(first)) if rows else "-"}


def _svg(curve: list[float], color: str, w: int = 300, h: int = 70) -> str:
    if len(curve) < 2:
        return f'<svg viewBox="0 0 {w} {h}" class="spark"></svg>'
    lo, hi = min(0.0, min(curve)), max(0.0, max(curve))
    span = (hi - lo) or 1.0
    step = max(1, len(curve) // 300)
    pts = [(i, v) for i, v in enumerate(curve) if i % step == 0 or i == len(curve) - 1]
    path = " ".join(f"{(i / (len(curve) - 1)) * (w - 4) + 2:.1f},{h - 4 - (v - lo) / span * (h - 8):.1f}" for i, v in pts)
    zero = h - 4 - (0 - lo) / span * (h - 8)
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="curva acumulada en unidades">'
            f'<line x1="0" x2="{w}" y1="{zero:.1f}" y2="{zero:.1f}" class="zero"/>'
            f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/></svg>')


def _pct(x: float, plus: bool = True) -> str:
    return "–" if x != x else (f"{x * 100:+.1f} %" if plus else f"{x * 100:.1f} %")


def render(books: Iterable[dict], generated: str, notes: str = "") -> str:
    colors = ["#2a7de1", "#e1822a", "#2aa876", "#a24fd1", "#d1424f"]
    cards = []
    for i, b in enumerate(books):
        c = colors[i % len(colors)]
        verdict = ("sin datos" if b["n"] < 30 else f"P(ROI&gt;0) {b['p_pos'] * 100:.0f} %" if b["p_pos"] == b["p_pos"] else "–")
        eta = "meta alcanzada" if b["n"] >= TARGET_N else (f"~{b['eta_days']:.1f} días" if b["rate"] > 0 else "–")
        srcs = ", ".join(f"{html.escape(k)} {v}" for k, v in sorted(b["src"].items())) or "–"
        cards.append(
            f'<section class="card"><h2 style="color:{c}">{html.escape(b["name"])}</h2>{_svg(b["curve"], c)}'
            f'<dl><dt>Apuestas con resultado</dt><dd>{b["n"]}</dd><dt>ROI (1 u por apuesta)</dt><dd><b>{_pct(b["roi"])}</b></dd>'
            f'<dt>Intervalo 95 %</dt><dd>{_pct(b["lo"])} a {_pct(b["hi"])}</dd><dt>Acierto</dt><dd>{_pct(b["hit"], False)}</dd>'
            f'<dt>Lectura</dt><dd>{verdict}</dd><dt>Ritmo</dt><dd>{b["rate"]:.0f}/día desde {b["since"]}</dd>'
            f'<dt>Hasta {TARGET_N} apuestas</dt><dd>{eta}</dd><dt>Abiertas / anuladas</dt><dd>{b["open"]} / {b["void"]}</dd>'
            f'<dt>Origen del final</dt><dd class="small">{srcs}</dd></dl></section>')
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VECTIQ · tablero</title><style>
:root{{--bg:#f6f7f9;--fg:#15181d;--card:#fff;--mut:#5b6573;--line:#d8dde4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1318;--fg:#e8ebef;--card:#181e26;--mut:#98a2b0;--line:#2a323d}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--mut);margin-bottom:14px}}
.grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}} h2{{font-size:16px;margin:0 0 6px}}
.spark{{width:100%;height:70px}} .zero{{stroke:var(--line);stroke-dasharray:3 3}}
dl{{display:grid;grid-template-columns:1fr auto;gap:3px 10px;margin:8px 0 0}} dt{{color:var(--mut)}} dd{{margin:0;text-align:right}} .small{{font-size:12px}}
.note{{margin-top:14px;color:var(--mut);font-size:13px}}</style></head><body>
<h1>VECTIQ · todas las estrategias en papel</h1><div class="sub">Generado {html.escape(generated)} UTC · cada apuesta cuenta 1 unidad · solo resultados reales (anuladas y abiertas aparte)</div>
<div class="grid">{''.join(cards)}</div><div class="note">{notes}</div></body></html>"""


def build(ledgers: dict[str, Path | str | None], now_s: float | None = None, notes: str = "") -> tuple[str, list[dict]]:
    now_s = now_s or time.time()
    books = []
    for name, path in ledgers.items():
        if not path or not Path(path).exists():
            continue
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            books.append(book_stats(name, db, now_s))
        finally:
            db.close()
    return render(books, time.strftime("%Y-%m-%d %H:%M", time.gmtime(now_s)), notes), books


def caption(books: list[dict]) -> str:
    lines = ["<b>VECTIQ · tablero</b>"]
    for b in books:
        lines.append(f"{html.escape(b['name'])}: n {b['n']} · ROI {_pct(b['roi'])} [{_pct(b['lo'])}, {_pct(b['hi'])}] · "
                     f"{b['rate']:.0f}/día")
    return "\n".join(lines)[:1000]
