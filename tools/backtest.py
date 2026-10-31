"""Backtesting ciego (walk-forward, causal) sobre el raw tape de Betwinner.

Mercados evaluados (scope "match"):
  * G1  ganador       (T1 local / T3 visitante)
  * G2  hándicap pts  (T7 gana si margen_local > -P; T8 si margen_local < P)
  * G17 total puntos  (T9 over si total > P; T10 under si total < P)

Modelos (todo causal: nada mira al futuro):
  * Libros empíricos por formato (bo5/bo7): distribución de puntos restantes y
    de margen restante dados k sets completados — se alimentan solo de partidos
    ya finalizados (con imputación de censurados para evitar sesgo de supervivencia).
  * Elo causal por jugador (K=32) para el moneyline prepartido y como prior en vivo.
  * Entradas: stage 0 (prepartido, 60 s–4 h antes) y descansos entre sets (k>=1).
  * Reglas del engine: EV en [5 %, 15 %], p en (0.05, 0.95), Kelly 0.5 con
    shrink 0.5, cap 1 % por apuesta y 2 % por partido, freno de drawdown.

Métricas: ROI por mercado, win rate, n apuestas, max racha de pérdidas, MDD,
CLV vs precio de cierre (última cuota vista de la misma selección+línea),
Brier del modelo, retorno diario compuesto vs objetivo 16.5 %.

Uso:  PYTHONPATH=src python tools/backtest.py data/tape [--test-from 2026-10-27]
El periodo anterior a --test-from se usa SOLO para calentar libros/Elo (train);
todas las apuestas reportadas son del periodo test (fuera de muestra).
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import zstandard as zstd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vectiq.feed.schema import parse_item, _quotes, set_finished  # noqa: E402

MIN_BOOK = 80        # muestras mínimas en un bucket empírico para poder cotizar
MIN_FREQ = 40        # muestras mínimas para el bucket de moneyline en vivo
ELO_MIN = 3          # partidos mínimos por jugador para usar su Elo
ELO_M = 30           # peso del prior Elo en el blend en vivo
MIN_EV, MAX_EV = 0.05, 0.15
KELLY_FRAC, SHRINK, BET_CAP, GAME_CAP = 0.5, 0.5, 0.01, 0.02


# ---------------------------------------------------------------------- tape io
def read_shard(path: Path):
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh, dctx.stream_reader(fh, read_across_frames=True) as rd:
        data = rd.read()
    for line in data.splitlines():
        if line:
            yield json.loads(line)


def iter_tape(root: Path):
    files = sorted(p for p in root.rglob("*.jsonl.zst"))
    for p in files:
        yield from read_shard(p)


# ---------------------------------------------------------------------- books
class EmpBook:
    """Distribuciones empíricas condicionadas a k sets completados.

    rem_total[k]   = puntos que faltan por jugarse tras k sets
    rem_margin[k]  = margen local restante tras k sets
    win_freq[(k,w1,w2)] = [victorias_local, n]
    """

    def __init__(self, need: int) -> None:
        self.need = need
        self.order_t: dict[int, deque] = defaultdict(deque)
        self.rem_t: dict[int, list] = defaultdict(list)
        self.order_m: dict[int, deque] = defaultdict(deque)
        self.rem_m: dict[int, list] = defaultdict(list)
        self.win: dict = defaultdict(lambda: [0, 0])
        self.gids: set = set()
        self.pool: list[int] = []

    def _push(self, order, rem, k, v):
        order[k].append(v)
        bisect.insort(rem[k], v)

    def add(self, gid: int, sets: list, total: int, margin: int, home_won: bool) -> None:
        if gid in self.gids:
            return
        self.gids.add(gid)
        for k in range(min(len(sets), self.need - 1) + 1):
            done = sets[:k]
            self._push(self.order_t, self.rem_t, k, int(total - sum(a + b for a, b in done)))
            self._push(self.order_m, self.rem_m, k, int(margin - sum(a - b for a, b in done)))
        w = [0, 0]
        for i, (a, b) in enumerate(sets[: self.need - 1]):
            w[0 if a > b else 1] += 1
            key = (i + 1, w[0], w[1])
            self.win[key][0 if home_won else 1] += 1
        self.pool.extend(a + b for a, b in sets)
        del self.pool[:-6000]

    def n(self, k: int) -> int:
        return len(self.rem_t.get(k, ()))

    def p_under(self, k: int, played: int, line: float):
        xs = self.rem_t.get(k) or []
        if len(xs) < MIN_BOOK:
            return None
        c = bisect.bisect_left(xs, line - played)
        return (c + 0.5) / (len(xs) + 1.0)

    def p_margin_gt(self, k: int, margin_so_far: int, need_gt: float):
        """P(margen final > need_gt)."""
        xs = self.rem_m.get(k) or []
        if len(xs) < MIN_BOOK:
            return None
        thr = need_gt - margin_so_far
        c = len(xs) - bisect.bisect_right(xs, thr)
        return (c + 0.5) / (len(xs) + 1.0)

    def p_home_win(self, k: int, w1: int, w2: int):
        v = self.win.get((k, w1, w2))
        if not v or (v[0] + v[1]) < MIN_FREQ:
            return None
        return (v[0] + 0.5) / (v[0] + v[1] + 1.0)


# ---------------------------------------------------------------------- elo
class Elo:
    K = 32.0

    def __init__(self) -> None:
        self.r: dict[str, float] = {}
        self.n: dict[str, int] = {}

    def rated(self, a: str, b: str) -> bool:
        return self.n.get(a, 0) >= ELO_MIN and self.n.get(b, 0) >= ELO_MIN

    def p(self, a: str, b: str) -> float:
        return 1.0 / (1.0 + 10 ** (-(self.r.get(a, 1500.0) - self.r.get(b, 1500.0)) / 400.0))

    def update(self, a: str, b: str, a_won: bool) -> None:
        pa = self.p(a, b)
        ra, rb = self.r.get(a, 1500.0), self.r.get(b, 1500.0)
        self.r[a] = ra + self.K * ((1.0 if a_won else 0.0) - pa)
        self.r[b] = rb + self.K * ((0.0 if a_won else 1.0) - (1.0 - pa))
        self.n[a] = self.n.get(a, 0) + 1
        self.n[b] = self.n.get(b, 0) + 1


# ---------------------------------------------------------------------- betting
@dataclass
class Bet:
    gid: int
    t: int
    day: str
    market: str
    stage: int
    sel: str
    line: float | None
    odds: float
    p: float
    ev: float
    frac: float
    league: str
    players: str
    close_odds: float | None = None
    won: bool | None = None
    pnl: float = 0.0
    stake: float = 0.0


@dataclass
class GameCtx:
    bo: int | None = None
    need: int | None = None
    stages_seen: set = field(default_factory=set)
    last_price: dict = field(default_factory=dict)
    last_state: object = None
    finished: bool = False
    result: dict | None = None
    exposure: float = 0.0


def ladder(state, market, t_sel):
    out = []
    for q in state.quotes:
        if q.scope == "match" and q.market == market and q.selection in t_sel and not q.blocked:
            out.append(q)
    return out


def devig(o1, o2):
    return (1 / o1) / (1 / o1 + 1 / o2)


def dd_mult(drawdown: float) -> float:
    if drawdown >= 0.25:
        return 0.0
    if drawdown >= 0.15:
        return 0.5
    return 1.0


def stake_frac(p, odds, exposure_left, mult):
    p_shrunk = 1.0 / odds + (p - 1.0 / odds) * SHRINK
    k = max(0.0, (p_shrunk * odds - 1.0) / (odds - 1.0))
    return min(BET_CAP, exposure_left, KELLY_FRAC * k) * mult


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--test-from", default="2026-10-27")
    ap.add_argument("--no-brake", action="store_true", help="desactiva el freno de drawdown")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    test_from_ms = int(time.mktime(time.strptime(args.test_from, "%Y-%m-%d")) * 1000 - time.timezone * 1000)

    books = {5: EmpBook(3), 7: EmpBook(4)}
    elo = Elo()
    games: dict[int, GameCtx] = {}
    bets: list[Bet] = []
    bankroll, peak = 1.0, 1.0
    daily_marks: dict[str, float] = {}
    n_rec = n_parse_err = 0
    t0 = time.time()

    def settle(gid: int, g: GameCtx, res: dict):
        nonlocal bankroll, peak
        for b in bets:
            if b.gid == gid and b.won is None:
                won = res["won_map"](b)
                b.won = won
                b.pnl = b.stake * (b.odds - 1.0) if won else -b.stake
                b.close_odds = g.last_price.get((b.market, b.sel, b.line))
                bankroll += b.pnl
                peak = max(peak, bankroll)
                daily_marks[b.day] = bankroll

    for rec in iter_tape(args.root):
        n_rec += 1
        try:
            d = rec["d"]
            state = parse_item(d, rec["t"])
            if rec["k"] == "game":   # d ya es GetGameZip.Value: añadir escaleras completas
                for ge in d.get("GE") or []:
                    for row in ge.get("E") or []:
                        state.quotes.extend(_quotes(state.game_id, "match", row))
        except Exception:
            n_parse_err += 1
            continue
        gid = state.game_id
        g = games.setdefault(gid, GameCtx())
        if g.finished:
            continue

        # actualizar últimas cuotas visibles (para CLV)
        for q in state.quotes:
            if q.scope == "match" and q.market in ("winner", "handicap", "total") and not q.blocked:
                g.last_price[(q.market, q.selection, q.line)] = q.price

        g.bo = state.best_of or g.bo
        need = (g.bo // 2 + 1) if g.bo in (5, 7) else None
        book = books.get(g.bo) if g.bo in (5, 7) else None
        g.last_state = state
        in_test = rec["t"] >= test_from_ms

        # -------------------------------------------------- final de partido
        done = state.completed_sets
        if need and max(state.sets_won) >= need and state.in_play_set is None and len(done) == len(state.sets):
            w1, w2 = state.sets_won
            total = sum(a + b for a, b in done)
            margin = sum(a - b for a, b in done)
            home_won = w1 > w2

            def won_map(b, total=total, margin=margin, home_won=home_won):
                if b.market == "winner":
                    return (b.sel == "home") == home_won
                if b.market == "total":
                    return total > b.line if b.sel == "over" else total < b.line
                if b.market == "handicap":
                    return margin > -b.line if b.sel == "home" else margin < b.line
                return False

            g.finished = True
            g.result = {"total": total, "margin": margin, "home_won": home_won}
            settle(gid, g, {"won_map": won_map})
            if book:
                book.add(gid, done, total, margin, home_won)
            if state.home and state.away:
                elo.update(state.home, state.away, home_won)
            continue

        # -------------------------------------------------- ventanas de entrada
        if not in_test or not need or not book:
            continue
        now_s = rec["t"] / 1000.0
        stage = None
        if not state.live and not state.sets and state.start_ts and 60 <= state.start_ts - now_s <= 4 * 3600:
            stage = 0
        elif state.live and done and state.in_play_set is None and len(done) == len(state.sets) and max(state.sets_won) < need:
            stage = len(done)
        if stage is None:
            continue
        drawdown = 1.0 - bankroll / peak if peak else 0.0
        mult = 1.0 if args.no_brake else dd_mult(drawdown)
        if mult <= 0:
            continue
        points_so_far = state.points_played
        margin_so_far = sum(a - b for a, b in done)
        league = state.league_en or state.league
        players = f"{state.home} vs {state.away}"
        day = time.strftime("%Y-%m-%d", time.gmtime(rec["t"] / 1000))

        def try_bet(market, stage_, sel, line, odds, p, ev):
            if (stage_, market) in g.stages_seen:
                return
            if p is None or odds is None or odds <= 1.0:
                return
            if not (MIN_EV <= ev <= MAX_EV and 0.05 < p < 0.95):
                return
            left = GAME_CAP - g.exposure
            f = stake_frac(p, odds, left, mult)
            if f <= 0:
                return
            g.exposure += f
            g.stages_seen.add((stage_, market))
            b = Bet(gid, rec["t"], day, market, stage_, sel, line, odds, p, ev, f, league, players)
            b.stake = f * bankroll
            bets.append(b)

        # ----- G17 total: mejor under y mejor over por EV
        tot = ladder(state, "total", ("over", "under"))
        by_line: dict[float, dict] = {}
        for q in tot:
            by_line.setdefault(q.line, {})[q.selection] = q
        best_under = best_over = None
        for ln, sides in by_line.items():
            if "under" not in sides or "over" not in sides or ln is None:
                continue
            pu = book.p_under(stage, points_so_far, ln)
            if pu is None:
                continue
            evu = pu * sides["under"].price - 1.0
            evo = (1 - pu) * sides["over"].price - 1.0
            if evu > (best_under[0] if best_under else -9):
                best_under = (evu, ln, sides["under"].price, pu)
            if evo > (best_over[0] if best_over else -9):
                best_over = (evo, ln, sides["over"].price, 1 - pu)
        cand = [x for x in (best_under, best_over) if x and x[0] >= MIN_EV]
        if cand:
            cand.sort(reverse=True)
            ev, ln, odds, p = cand[0]
            sel = "under" if cand[0] is best_under else "over"
            try_bet("total", stage, sel, ln, odds, p, ev)

        # ----- G2 hándicap: mejor lado por EV
        hc = ladder(state, "handicap", ("home", "away"))
        best_h = None
        for q in hc:
            if q.line is None:
                continue
            if q.selection == "home":   # gana si margen > -P
                p = book.p_margin_gt(stage, margin_so_far, -q.line)
            else:                     # gana si margen < P
                xs = book.rem_m.get(stage) or []
                if len(xs) < MIN_BOOK:
                    continue
                c = bisect.bisect_left(xs, q.line - margin_so_far)
                p = (c + 0.5) / (len(xs) + 1.0)
            if p is None:
                continue
            ev = p * q.price - 1.0
            if ev > (best_h[0] if best_h else -9):
                best_h = (ev, q.selection, q.line, q.price, p)
        if best_h and best_h[0] >= MIN_EV:
            ev, sel, ln, odds, p = best_h
            try_bet("handicap", stage, sel, ln, odds, p, ev)

        # ----- G1 ganador
        g1 = {q.selection: q.price for q in ladder(state, "winner", ("home", "away"))}
        if len(g1) == 2:
            p_home = None
            if stage == 0:
                if elo.rated(state.home, state.away):
                    p_home = elo.p(state.home, state.away)
            else:
                w1, w2 = state.sets_won
                p_emp = book.p_home_win(stage, w1, w2)
                p_elo = elo.p(state.home, state.away) if elo.rated(state.home, state.away) else None
                if p_emp is not None and p_elo is not None:
                    p_home = (MIN_FREQ * p_emp + ELO_M * p_elo) / (MIN_FREQ + ELO_M)
                else:
                    p_home = p_emp or p_elo
            if p_home is not None:
                cands = []
                for sel, price in g1.items():
                    p = p_home if sel == "home" else 1 - p_home
                    cands.append((p * price - 1.0, sel, price, p))
                cands.sort(reverse=True)
                ev, sel, odds, p = cands[0]
                try_bet("winner", stage, sel, None, odds, p, ev)

    # -------------------------------------------------- censurados -> void
    n_void = 0
    for b in bets:
        if b.won is None:
            b.won = None
            b.stake = b.frac * bankroll
            b.pnl = 0.0
            n_void += 1

    # -------------------------------------------------- métricas
    def metrics(bs):
        n = len(bs)
        if not n:
            return {}
        wins = sum(1 for b in bs if b.won)
        turn = sum(b.stake for b in bs)
        pnl = sum(b.pnl for b in bs)
        streak = mx = 0
        for b in bs:
            streak = streak + 1 if not b.won else 0
            mx = max(mx, streak)
        clv = [b for b in bs if b.close_odds]
        clv_edge = [b.odds / b.close_odds - 1.0 for b in clv]  # >0 = cuota mejor que el cierre
        brier = sum((b.p - (1.0 if b.won else 0.0)) ** 2 for b in bs) / n
        return {
            "n": n, "wins": wins, "win_rate": round(wins / n, 4),
            "turnover": round(turn, 4), "pnl": round(pnl, 4),
            "roi": round(pnl / turn, 4) if turn else 0,
            "avg_odds": round(sum(b.odds for b in bs) / n, 3),
            "avg_ev": round(sum(b.ev for b in bs) / n, 4),
            "brier": round(brier, 4),
            "max_loss_streak": mx,
            "clv_n": len(clv),
            "beat_closing_pct": round(sum(1 for e in clv_edge if e > 0) / len(clv), 3) if clv else None,
            "avg_clv_pct": round(sum(clv_edge) / len(clv) * 100, 2) if clv else None,
        }

    settled = [b for b in bets if b.won is not None]
    out = {
        "config": {"test_from": args.test_from, "records": n_rec, "parse_err": n_parse_err,
                   "games_seen": len(games), "elapsed_s": round(time.time() - t0, 1),
                   "rules": {"min_ev": MIN_EV, "max_ev": MAX_EV, "kelly": KELLY_FRAC,
                             "shrink": SHRINK, "bet_cap": BET_CAP, "game_cap": GAME_CAP,
                             "dd_brake": [0.15, 0.25]}},
        "void_censored": n_void,
        "bankroll_final": round(bankroll, 4),
        "overall": metrics(sorted(settled, key=lambda b: b.t)),
        "by_market": {m: metrics(sorted([b for b in settled if b.market == m], key=lambda b: b.t))
                      for m in ("winner", "handicap", "total")},
        "by_league_family": {},
        "daily": {},
        "bets": [vars(b) for b in sorted(bets, key=lambda b: b.t)],
    }
    fam = lambda lg: "masters" if "masters" in lg.lower() else ("setka" if "setka" in lg.lower() else (
        "pro/tt" if ("pro" in lg.lower() or "tt-cup" in lg.lower() or "tt star" in lg.lower()) else "other"))
    for f in ("masters", "setka", "pro/tt", "other"):
        bs = sorted([b for b in settled if fam(b.league) == f], key=lambda b: b.t)
        if bs:
            out["by_league_family"][f] = metrics(bs)
    # retorno diario compuesto sobre la serie de apuestas liquidadas
    by_day: dict[str, list] = defaultdict(list)
    for b in settled:
        by_day[b.day].append(b)
    run = 1.0
    for day in sorted(by_day):
        pnl = sum(b.pnl for b in by_day[day])
        turn = sum(b.stake for b in by_day[day])
        run += pnl
        out["daily"][day] = {"bets": len(by_day[day]), "turnover": round(turn, 4),
                             "pnl": round(pnl, 4), "bankroll": round(run, 4)}
    days_n = len(out["daily"]) or 1
    out["daily_return_compounded"] = round(run ** (1 / days_n) - 1, 4)
    out["target_daily"] = 0.165
    # MDD sobre curva de banca
    pk, mdd = 1.0, 0.0
    run2 = 1.0
    for b in sorted(settled, key=lambda b: b.t):
        run2 += b.pnl
        pk = max(pk, run2)
        mdd = max(mdd, 1 - run2 / pk if pk else 0)
    out["mdd"] = round(mdd, 4)
    txt = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(txt)
    summary = {k: v for k, v in out.items() if k != "bets"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
