/* VECTIQ panel — vanilla JS, SVG propio. Todo número sale de data/*.json. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("aria")) e.setAttribute(k, v);
    else e[k] = v;
  }
  for (const k of kids) e.append(k);
  return e;
};
const pct = (x, d = 1) => x == null ? "—" : (x * 100).toFixed(d) + "%";
const signed = x => x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
const num = (x, d = 2) => x == null ? "—" : (+x).toFixed(d);
const int = x => x == null ? "—" : String(Math.round(x));
const ciTxt = ci => ci ? `[${(ci[0] * 100).toFixed(1)}, ${(ci[1] * 100).toFixed(1)}]` : "—";
const ciOk = ci => ci && ci[0] > 0;
const dayOf = ts => new Date(ts).toISOString().slice(0, 10);
const ageTxt = min => {
  if (min == null) return "—";
  if (min < 60) return `${Math.round(min)} min`;
  if (min < 1440) return `${(min / 60).toFixed(1)} h`;
  return `${(min / 1440).toFixed(1)} d`;
};
const CHART_COLORS = ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5"];
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const state = { summary: null, details: {}, tab: "resumen", strat: null,
  filters: { league: "", stage: "", from: "", to: "" } };

/* ---------- SVG charts ---------- */
function svgEl(name, attrs = {}) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function scales(xs, ys, w, h, pad) {
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  if (y1 - y0 < 1e-9) { y1 += 1; y0 -= 1; }
  const sx = x => pad.l + (x - x0) / Math.max(x1 - x0, 1e-9) * (w - pad.l - pad.r);
  const sy = y => h - pad.b - (y - y0) / (y1 - y0) * (h - pad.t - pad.b);
  return { sx, sy, x0, x1, y0, y1 };
}
function axis(svg, s, w, h, pad, xfmt, yfmt, nx = 5, ny = 4) {
  const ink = css("--faint"), line = css("--line-soft");
  for (let i = 0; i <= ny; i++) {
    const y = s.y0 + (s.y1 - s.y0) * i / ny, py = s.sy(y);
    svg.append(svgEl("line", { x1: pad.l, x2: w - pad.r, y1: py, y2: py, stroke: line, "stroke-width": 1 }));
    const t = svgEl("text", { x: pad.l - 6, y: py + 3, "text-anchor": "end", "font-size": 10, fill: ink });
    t.textContent = yfmt(y); svg.append(t);
  }
  for (let i = 0; i <= nx; i++) {
    const x = s.x0 + (s.x1 - s.x0) * i / nx, px = s.sx(x);
    const t = svgEl("text", { x: px, y: h - pad.b + 14, "text-anchor": "middle", "font-size": 10, fill: ink });
    t.textContent = xfmt(x); svg.append(t);
  }
  const zero = s.sy(0);
  svg.append(svgEl("line", { x1: pad.l, x2: w - pad.r, y1: zero, y2: zero, stroke: css("--line"), "stroke-width": 1.2 }));
}
function lineChart(series, { w = 920, h = 300, xfmt = x => dayOf(x), yfmt = y => num(y, 0) } = {}) {
  // series: [{name, pts:[[x,y],...]}]
  const pad = { l: 46, r: 14, t: 10, b: 30 };
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  const xs = series.flatMap(s => s.pts.map(p => p[0]));
  const ys = series.flatMap(s => s.pts.map(p => p[1]));
  if (!xs.length) return el("div", { class: "empty-note" }, "Sin datos todavía");
  const s = scales(xs, ys, w, h, pad);
  axis(svg, s, w, h, pad, xfmt, yfmt);
  series.forEach((ser, i) => {
    const d = ser.pts.map((p, j) => `${j ? "L" : "M"}${s.sx(p[0]).toFixed(1)},${s.sy(p[1]).toFixed(1)}`).join("");
    svg.append(svgEl("path", { d, fill: "none", stroke: css(CHART_COLORS[i % 5]), "stroke-width": 1.6 }));
  });
  const wrap = el("div", {});
  wrap.append(svg);
  const leg = el("div", { class: "legend" });
  series.forEach((ser, i) => {
    const l = el("span", {});
    const sw = el("i", {}); sw.style.background = css(CHART_COLORS[i % 5]);
    l.append(sw, document.createTextNode(`${ser.name} (${ser.pts.length})`));
    leg.append(l);
  });
  wrap.append(leg);
  return wrap;
}
function barChart(rows, { w = 920, h = 240 } = {}) {
  // rows: [{k, n, roi}] — ROI en % con etiqueta n
  const pad = { l: 46, r: 14, t: 10, b: 44 };
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  if (!rows.length) return el("div", { class: "empty-note" }, "Sin datos todavía");
  const ys = rows.map(r => r.roi);
  const s = scales(rows.map((_, i) => i), ys, w, h, pad);
  axis(svg, s, w, h, pad, () => "", y => (y * 100).toFixed(0) + "%", 0, 4);
  const bw = Math.min(60, (w - pad.l - pad.r) / rows.length * 0.6);
  rows.forEach((r, i) => {
    const px = s.sx(i) - bw / 2, py = s.sy(Math.max(0, r.roi)), ph = Math.abs(s.sy(r.roi) - s.sy(0));
    svg.append(svgEl("rect", { x: px, y: py, width: bw, height: ph, fill: r.roi >= 0 ? css("--ok") : css("--bad"), "fill-opacity": .8 }));
    const t = svgEl("text", { x: s.sx(i), y: h - pad.b + 14, "text-anchor": "middle", "font-size": 10, fill: css("--muted"), transform: `rotate(-30 ${s.sx(i)} ${h - pad.b + 14})` });
    t.textContent = `${r.k} (n=${r.n})`; svg.append(t);
    const v = svgEl("text", { x: s.sx(i), y: py - 4 + (r.roi < 0 ? ph + 12 : 0), "text-anchor": "middle", "font-size": 10, fill: css("--ink") });
    v.textContent = (r.roi >= 0 ? "+" : "") + (r.roi * 100).toFixed(1); svg.append(v);
  });
  return el("div", {}, svg);
}
function calibrationChart(rows, { w = 560, h = 300 } = {}) {
  const pad = { l: 46, r: 14, t: 10, b: 34 };
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  if (!rows.length) return el("div", { class: "empty-note" }, "Sin probabilidades del modelo");
  const xs = rows.map(r => r.pred), ys = rows.map(r => r.actual);
  const lo = Math.min(...xs, ...ys) - 0.02, hi = Math.max(...xs, ...ys) + 0.02;
  const sx = x => pad.l + (x - lo) / (hi - lo) * (w - pad.l - pad.r);
  const sy = y => h - pad.b - (y - lo) / (hi - lo) * (h - pad.t - pad.b);
  svg.append(svgEl("line", { x1: sx(lo), y1: sy(lo), x2: sx(hi), y2: sy(hi), stroke: css("--line"), "stroke-dasharray": "4 3" }));
  const ink = css("--faint");
  for (const [v] of [[0.45], [0.5], [0.55], [0.6], [0.65]]) {
    const t1 = svgEl("text", { x: sx(v), y: h - pad.b + 14, "text-anchor": "middle", "font-size": 10, fill: ink });
    t1.textContent = v.toFixed(2); svg.append(t1);
    const t2 = svgEl("text", { x: pad.l - 6, y: sy(v) + 3, "text-anchor": "end", "font-size": 10, fill: ink });
    t2.textContent = v.toFixed(2); svg.append(t2);
  }
  rows.forEach(r => {
    const px = sx(r.pred);
    if (r.ci) svg.append(svgEl("line", { x1: px, x2: px, y1: sy(r.ci[0]), y2: sy(r.ci[1]), stroke: css("--muted"), "stroke-width": 1.4 }));
    svg.append(svgEl("circle", { cx: px, cy: sy(r.actual), r: Math.max(3, Math.sqrt(r.n) / 3), fill: css("--chart-1"), "fill-opacity": .85 }));
  });
  const cap = el("p", { class: "foot-note" }, "Puntos sobre la diagonal = modelo bien calibrado. Barras = IC95 % (Wilson). Tamaño = n.");
  return el("div", {}, svg, cap);
}
function histChart(book, { w = 920, h = 220 } = {}) {
  const pad = { l: 46, r: 14, t: 10, b: 30 };
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  const c = book.hist.counts, n = c.length;
  const mx = Math.max(...c);
  const bw = (w - pad.l - pad.r) / n;
  const sy = y => h - pad.b - y / mx * (h - pad.t - pad.b);
  c.forEach((v, i) => {
    svg.append(svgEl("rect", { x: pad.l + i * bw + 1, y: sy(v), width: bw - 2, height: h - pad.b - sy(v), fill: css("--chart-2"), "fill-opacity": .75 }));
  });
  const ink = css("--faint");
  for (const f of [0, 0.5, 1]) {
    const x = pad.l + f * (w - pad.l - pad.r), v = book.hist.lo + f * book.hist.step * n;
    const t = svgEl("text", { x, y: h - 8, "text-anchor": "middle", "font-size": 10, fill: ink });
    t.textContent = Math.round(v); svg.append(t);
  }
  return el("div", {}, svg);
}

/* ---------- renderers ---------- */
function stratLabel(key, s) {
  const names = { masters: "Telegram (modelo)", cand: "Manus (regla fija)",
    setka: "Setka / viejo", st0: "Devin ST0", st1: "Devin ST1" };
  return names[key] || s.meta?.name || key;
}
function renderResumen() {
  const s = state.summary, tbl = $("#tbl-resumen");
  tbl.innerHTML = "";
  const head = el("tr", {},
    el("th", {}, "Estrategia"), el("th", { class: "num" }, "n liq."),
    el("th", { class: "num" }, "ROI 1u"), el("th", { class: "num" }, "IC95 %"),
    el("th", { class: "num" }, "P(ROI>0)"), el("th", { class: "num" }, "Acierto"),
    el("th", { class: "num" }, "Ritmo/día"), el("th", { class: "num" }, "Abiertas"),
    el("th", { class: "num" }, "Anuladas"), el("th", { class: "num" }, "CLV"),
    el("th", { class: "num" }, "Días a 1.000"));
  tbl.append(el("thead", {}, head));
  const body = el("tbody", {});
  const pace = (all, fwd) => {
    const days = fwd?.n_days || all?.n_days || 1;
    return (fwd?.n || 0) / Math.max(days, 1);
  };
  for (const [key, s2] of Object.entries(s.strategies)) {
    if (s2.empty) { body.append(el("tr", {}, el("td", {}, stratLabel(key, s2)), el("td", { colSpan: 10 }, "Sin datos todavía"))); continue; }
    const f = s2.forward || {}, all = s2.all || {};
    const p = pace(all, f);
    const eta = p > 0 && f.n ? Math.ceil(Math.max(0, 1000 - f.n) / p) : null;
    const tr = el("tr", { class: f.roi >= 0 ? "r-pos" : "r-neg" },
      el("td", {}, stratLabel(key, s2), el("div", { class: "dim" }, s2.meta?.hypothesis || "")),
      el("td", { class: "num" }, `${int(f.n)} `, el("span", { class: "dim" }, `/ ${int(all.n)}`)),
      el("td", { class: "num roi" }, signed(f.roi), el("div", { class: "dim" }, "hist " + signed(all.roi))),
      el("td", { class: "num" }, ciTxt(f.ci95)),
      el("td", { class: "num" }, pct(f.p_pos, 0)),
      el("td", { class: "num" }, pct(f.wr)),
      el("td", { class: "num" }, p ? num(p, 0) : "—"),
      el("td", { class: "num" }, int(s2.open)),
      el("td", { class: "num" }, int(s2.void + (s2.weak || 0))),
      el("td", { class: "num" }, f.clv ? signed(f.clv.mean) : "—"),
      el("td", { class: "num" }, eta == null ? "—" : `~${eta}`));
    body.append(tr);
  }
  tbl.append(body);
  $("#generated").textContent = "Datos al " + new Date(s.generated_at).toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
  $("#resumen-sources").textContent = "Fuentes: " + Object.entries(s.sources)
    .map(([k, v]) => `${k} (${(v.bytes / 1024).toFixed(0)} KB)`).join(" · ");
  // curva comparativa: carga detalles
  const keys = Object.keys(s.strategies).filter(k => !s.strategies[k].empty);
  Promise.all(keys.map(loadDetail)).then(() => {
    const series = keys.map((k, i) => ({ name: stratLabel(k, s.strategies[k]), pts: (state.details[k]?.equity || []) }));
    const c = $("#chart-equity-all"); c.innerHTML = "";
    c.append(lineChart(series));
  });
}
function loadDetail(key) {
  if (state.details[key]) return Promise.resolve(state.details[key]);
  return fetch(`data/strategy_${key}.json`).then(r => {
    if (!r.ok) { state.details[key] = { empty: true }; return state.details[key]; }
    return r.json().then(d => (state.details[key] = d));
  });
}

const STAGE_LABELS = ["Prepartido", "Tras set 1", "Tras set 2", "Tras set 3+"];
function renderEstrategias() {
  const s = state.summary, pills = $("#strat-pills");
  pills.innerHTML = "";
  const keys = Object.keys(s.strategies);
  if (!state.strat || !keys.includes(state.strat)) state.strat = keys[0];
  for (const k of keys) {
    const b = el("button", { "aria-pressed": String(k === state.strat) }, stratLabel(k, s.strategies[k]));
    b.onclick = () => { state.strat = k; syncHash(); renderEstrategias(); };
    pills.append(b);
  }
  loadDetail(state.strat).then(d => renderStratDetail(state.strat, d));
}
function filteredBets(d) {
  const f = state.filters;
  return (d.bets || []).filter(b =>
    (!f.league || b.league === f.league) &&
    (!f.stage || String(b.stage ?? 0) === f.stage) &&
    (!f.from || dayOf(b.ts) >= f.from) &&
    (!f.to || dayOf(b.ts) <= f.to));
}
function renderStratDetail(key, d) {
  const body = $("#strat-body"); body.innerHTML = "";
  if (d.empty || !d.bets?.length) { body.append(el("div", { class: "empty-note" }, "Sin datos todavía para esta estrategia")); return; }
  // rellenar filtros una vez
  const leagues = [...new Set(d.bets.map(b => b.league).filter(Boolean))].sort();
  const selL = $("#f-league");
  if (selL.options.length <= 1) leagues.forEach(l => selL.append(el("option", { value: l }, l)));
  const selS = $("#f-stage");
  if (selS.options.length <= 1) STAGE_LABELS.forEach((l, i) => selS.append(el("option", { value: String(i) }, l)));
  selL.value = state.filters.league; selS.value = state.filters.stage;
  $("#f-from").value = state.filters.from; $("#f-to").value = state.filters.to;

  const bets = filteredBets(d);
  const settled = bets.filter(b => ["W", "L", "P"].includes(b.result));
  const w = settled.filter(b => b.result === "W").length;
  const l = settled.filter(b => b.result === "L").length;
  const pnl = settled.reduce((a, b) => a + (b.pnl ?? 0), 0);
  const roi = settled.length ? pnl / settled.length : null;

  const all = d.all || {}, fwd = d.forward || d;
  const stats = el("div", { class: "stat-row" },
    statCell("n (filtrado)", String(settled.length)),
    statCell("ROI 1u", signed(roi), roi),
    statCell("Acierto", w + l ? pct(w / (w + l)) : "—"),
    statCell("Neto", num(pnl, 1) + " u"),
    statCell("ROI hacia adelante", signed(fwd.roi), fwd.roi),
    statCell("IC95 % fwd", ciTxt(fwd.ci95)),
    statCell("MDD", num(all.mdd_u, 1) + " u"),
    statCell("Racha L", int(all.max_loss_streak)));
  body.append(stats);

  body.append(el("h3", { class: "chart-title" }, "Curva acumulada y drawdown"));
  body.append(lineChart([{ name: "unidades", pts: d.equity || [] }], { yfmt: y => num(y, 0) }));

  for (const [title, rows] of [["Por liga", d.by_league], ["Por etapa", d.by_stage],
                               ["Por banda de cuota", d.by_price]]) {
    if (rows?.length) {
      body.append(el("h3", { class: "chart-title" }, `ROI ${title.toLowerCase()}`));
      body.append(barChart(rows));
    }
  }
  if (d.by_day?.length > 1) {
    body.append(el("h3", { class: "chart-title" }, "ROI por día"));
    body.append(barChart(d.by_day));
  }
  if (d.calibration?.length) {
    body.append(el("h3", { class: "chart-title" }, "Calibración del modelo"));
    body.append(calibrationChart(d.calibration));
  }

  body.append(el("h3", { class: "chart-title" }, `Últimas apuestas (${settled.length || bets.length} en el filtro)`));
  const tbl = el("table", { class: "dense" });
  tbl.append(el("thead", {}, el("tr", {},
    ...["Fecha", "Partido", "Liga", "Etapa", "Línea", "Cuota", "Resultado", "PnL", "Fuente"]
      .map((h, i) => el("th", { class: i >= 4 && i <= 7 ? "num" : "" }, h)))));
  const tb = el("tbody", {});
  bets.slice(-60).reverse().forEach(b => tb.append(el("tr", {},
    el("td", { class: "mono" }, dayOf(b.ts)),
    el("td", {}, b.players || `#${b.gid}`),
    el("td", {}, b.league || "—"),
    el("td", {}, STAGE_LABELS[Math.min(b.stage || 0, 3)]),
    el("td", { class: "num" }, num(b.line, 1)),
    el("td", { class: "num" }, num(b.price, 3)),
    el("td", {}, b.result === "W" ? "✓" : b.result === "L" ? "✗" : b.result === "P" ? "=" : b.result === "V" ? "V" : "…"),
    el("td", { class: "num" }, b.pnl == null ? "—" : (b.pnl >= 0 ? "+" : "") + num(b.pnl, 2)),
    el("td", { class: "dim" }, b.src || "—"))));
  tbl.append(tb);
  body.append(el("div", { class: "table-scroll" }, tbl));
}
function statCell(label, value, raw) {
  const cls = raw == null ? "" : raw > 0 ? "pos" : raw < 0 ? "neg" : "";
  const c = el("div", { class: "stat" });
  c.append(el("div", { class: "v " + cls }, value), el("div", { class: "l" }, label));
  return c;
}
function renderPuertas() {
  const g = state.summary.gates, wrap = $("#gates"); wrap.innerHTML = "";
  const mk = (gate) => {
    if (!gate) return el("div", { class: "gate" }, el("p", { class: "note" }, "Sin datos"));
    const items = [
      [`n ≥ ${gate.need_n}`, `${gate.n}/${gate.need_n}`, gate.n >= gate.need_n],
      [`días ≥ ${gate.need_days}`, `${gate.n_days}/${gate.need_days}`, gate.n_days >= gate.need_days],
      ["IC95 % límite inferior > 0", ciTxt(gate.ci95), gate.ci_ok],
      ["Drawdown (stake 1 %) < 15 %", pct(gate.mdd_pct), gate.mdd_ok],
    ];
    const card = el("div", { class: "gate" }, el("h4", {}, gate.name));
    const bar = el("div", { class: "bar" }); const fill = el("i", {}); fill.style.width = `${(gate.progress || 0) * 100}%`; bar.append(fill);
    card.append(bar, el("div", { class: "dim" }, `${pct(gate.progress || 0, 0)} del volumen requerido`));
    const ul = el("ul", { class: "checks" });
    for (const [label, val, ok] of items)
      ul.append(el("li", { class: ok ? "ok" : "no" },
        el("span", { class: "mark" }, ok ? "✓" : "·"),
        el("span", {}, label + " "), el("b", {}, val)));
    if (gate.p_pos != null)
      card.append(el("p", { class: "foot-note" }, `Posterior: P(ROI>0)=${pct(gate.p_pos, 0)} · P(ROI>3 %)=${pct(gate.p_roi3, 0)}`));
    card.append(ul);
    return card;
  };
  ["h_a", "h_b", "cand", "st0", "st1"].forEach(k => g[k] && wrap.append(mk(g[k])));
}
function renderEjecucion() {
  const c = state.summary.capacity || {}, wrap = $("#capacity"); wrap.innerHTML = "";
  if (!c.probes) { wrap.append(el("div", { class: "empty-note" }, "Sin telemetría de capacidad todavía — el shadow empieza a registrar tras su primer run.")); return; }
  wrap.append(el("div", { class: "stat-row" },
    statCell("Probes", int(c.probes)),
    statCell("Bloqueados", int(c.blocked)),
    statCell("Cuota movida", int(c.coef_moved)),
    statCell("maxBet mediana", c.max_bet_median ?? "—"),
    statCell("maxBet min", c.max_bet_min ?? "—"),
    statCell("maxBet max", c.max_bet_max ?? "—")));
  wrap.append(el("div", { class: "callout" },
    "Lectura anónima del endpoint UpdateCoupon: no incluye límites por cuenta ni historial. Sirve para detectar mercados bloqueados y cuotas movidas en el instante de la decisión."));
}
function renderCalidad() {
  const q = state.summary.quality?.per_strategy || {}, tbl = $("#tbl-calidad");
  tbl.innerHTML = "";
  tbl.append(el("thead", {}, el("tr", {},
    ...["Estrategia", "src observado", "results_api", "legado", "imputed/partial", "void", "abiertas", "% verificado", "Último dato", "Edad"].map(h => el("th", { class: "num" }, h)))));
  const tb = el("tbody", {});
  for (const [k, v] of Object.entries(q)) {
    if (k === "st0_st1") continue;
    const m = v.src_mix || {};
    tb.append(el("tr", {},
      el("td", {}, stratLabel(k, {})),
      el("td", { class: "num" }, int(m.observed)),
      el("td", { class: "num" }, int(m.results_api)),
      el("td", { class: "num" }, int(m.legado)),
      el("td", { class: "num" }, int((m.imputed || 0) + (m.partial || 0))),
      el("td", { class: "num" }, int(m.void)),
      el("td", { class: "num" }, int(v.open)),
      el("td", { class: "num" }, pct(v.pct_verified, 0)),
      el("td", { class: "num mono" }, v.last_ts ? dayOf(v.last_ts) : "—"),
      el("td", { class: "num" }, ageTxt(v.age_min))));
  }
  if (q.st0_st1) tb.append(el("tr", {},
    el("td", {}, "Devin ST0/ST1"),
    el("td", { colSpan: 6 }, `probes de capacidad: ${int(q.st0_st1.capacity_probes)} · bloqueados: ${int(q.st0_st1.capacity_blocked)} · cuota movida: ${int(q.st0_st1.capacity_coef_moved)}`),
    el("td", { class: "num" }, "tape"),
    el("td", { class: "num" }, "—"), el("td", { class: "num" }, "—")));
  tbl.append(tb);
}
function renderMetodo() {
  const s = state.summary;
  $("#src-list").textContent = Object.entries(s.sources)
    .map(([k, v]) => `${k} — ${(v.bytes / 1024).toFixed(0)} KB, mtime ${v.mtime}`).join("  ·  ");
  const b = s.book;
  if (b) {
    const g = $("#glossary");
    g.append(el("h3", {}, "Distribución aprendida de puntos restantes (masters_book)"));
    g.append(el("p", { class: "note" }, `n=${b.n} · media ${num(b.mean, 1)} · σ ${num(b.stdev, 1)} · p5–p95 ${b.p5}–${b.p95}`));
    g.append(histChart(b));
  }
  if (s.commits?.length) {
    const g = $("#glossary");
    g.append(el("h3", {}, "Registro de cambios reciente"));
    const ul = el("ul", { class: "checks" });
    s.commits.forEach(c => ul.append(el("li", {},
      el("b", {}, c.sha), el("span", { class: "dim" }, c.date), el("span", {}, " " + c.msg))));
    g.append(ul);
  }
}

/* ---------- tabs + estado URL ---------- */
function syncHash() {
  const f = state.filters;
  const p = new URLSearchParams();
  if (state.strat) p.set("s", state.strat);
  for (const k of ["league", "stage", "from", "to"]) if (f[k]) p.set(k, f[k]);
  location.hash = state.tab + (p.toString() ? "?" + p : "");
}
function readHash() {
  const [tab, qs] = (location.hash || "#resumen").slice(1).split("?");
  state.tab = ["resumen", "estrategias", "puertas", "ejecucion", "calidad", "metodo"].includes(tab) ? tab : "resumen";
  const p = new URLSearchParams(qs || "");
  if (p.get("s")) state.strat = p.get("s");
  for (const k of ["league", "stage", "from", "to"]) state.filters[k] = p.get(k) || "";
}
function showTab() {
  document.querySelectorAll(".view").forEach(v => v.hidden = true);
  document.querySelectorAll("#tabs button").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab)));
  const v = $("#view-" + state.tab); if (v) v.hidden = false;
  ({ resumen: renderResumen, estrategias: renderEstrategias, puertas: renderPuertas,
     ejecucion: renderEjecucion, calidad: renderCalidad, metodo: renderMetodo })[state.tab]?.();
}
document.querySelectorAll("#tabs button").forEach(b =>
  b.onclick = () => { state.tab = b.dataset.tab; syncHash(); showTab(); });
for (const id of ["f-league", "f-stage", "f-from", "f-to"])
  $("#" + id).onchange = e => {
    state.filters[id.slice(2).replace("league", "league").replace("stage", "stage")] = e.target.value;
    syncHash(); renderStratDetail(state.strat, state.details[state.strat] || {});
  };
$("#f-clear").onclick = () => {
  state.filters = { league: "", stage: "", from: "", to: "" };
  syncHash(); renderEstrategias();
};

/* ---------- boot ---------- */
window.addEventListener("hashchange", () => { readHash(); if (state.summary) showTab(); });
readHash();
fetch("data/summary.json")
  .then(r => { if (!r.ok) throw new Error(`summary.json ${r.status}`); return r.json(); })
  .then(s => { state.summary = s; $("#loading").hidden = true; showTab(); })
  .catch(e => {
    $("#loading").hidden = true;
    const er = $("#error"); er.hidden = false;
    er.textContent = "No se pudieron cargar los datos (data/summary.json). El panel se regenera cada 30 min; si el error persiste, revisa el workflow pages. " + e.message;
  });
