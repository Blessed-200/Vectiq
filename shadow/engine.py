"""Shadow Engine ST0/ST1 — procesador incremental del tape.

ST0: regla idéntica a tools/frozen_eval.py (argmax_EV>=5% sobre escalera under,
     libro EmpBook(4) causal, 1 apuesta/partido, primera ventana st0 elegible).
     Implementada aquí como adaptador (frozen_eval no es importable); la suite
     de tests verifica decisión-a-decisión que reproduce frozen_eval.
ST1: misma selección ST0 cuando gap = centro_implícito - media_hist_jugadores >= 5.

Liquidación: primaria = final observado en tape; respaldo = API de resultados
(vectiq.feed.results) tras `settle_grace_h` horas sin final en tape.

Uso: python -m shadow.engine --tape data/tape --state shadow/state \
     --shards-manifest shadow/state/shards.json [--results-api]
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
from backtest import read_shard, EmpBook, MIN_EV, MIN_BOOK  # noqa: E402
from vectiq.feed.schema import parse_item, _quotes  # noqa: E402
from store import Store, now_iso  # noqa: E402

GAP_MIN = 5.0              # ST1 congelado: gap >= 5
HIST_MIN = 8               # mínimo de partidos previos por jugador para gap
SETTLE_GRACE_S = 4 * 3600  # sin final en tape -> API de respaldo

STRATEGIES = {"ST0": "st0-argmaxEV-min5pct-book4", "ST1": "st0+gap>=5-hist8"}


def devig(over, under):
    return (1 / under) / ((1 / under) + (1 / over))


def implied_center(ladder):
    """Centro implícito = línea donde devig(under) cruza 0.5 (interpolada)."""
    pts = sorted((L["line"], devig(L["over"], L["under"])) for L in ladder
                 if L.get("over") and L.get("under"))
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 <= 0.5 <= p2 or p2 <= 0.5 <= p1:
            if p2 == p1:
                return (l1 + l2) / 2, True
            return l1 + (0.5 - p1) * (l2 - l1) / (p2 - p1), True
    return None, False


class ShadowEngine:
    def __init__(self, state_dir: Path):
        self.st = Store(state_dir)
        self.book = EmpBook(4)
        self.player_hist: dict[str, list] = defaultdict(list)
        self.games: dict[int, dict] = {}
        self.n_new = 0

    # ---------- carga de estado previo ----------
    def restore(self):
        for m in self.st.load("matches"):
            m.setdefault("decisions", {})
            m.setdefault("last_price", {})
            m["last_price"] = {tuple(k) if isinstance(k, list) else k: v
                               for k, v in m.get("last_price", {}).items()}
            self.games[int(m["gid"])] = m
        for d in self.st.load("decisions"):
            if d.get("decision") in ("BET", "NO_BET", "GAP_FILTERED"):
                g = self.games.get(int(d["gid"]))
                if g is not None:
                    g["decisions"][d["strategy"]] = d
        for pr in self.st.load("players"):
            self.player_hist[pr["player"]].append(
                (pr["event_time_ms"], pr["final_total"], pr["gid"]))
        # reconstruir libro + estado de partidos finalizados
        for s in self.st.load("settlements"):
            if s.get("final_total") is not None and s.get("sets"):
                sets = [tuple(x) for x in s["sets"]]
                self.book.add(int(s["gid"]), sets, s["final_total"], 0, True)
            g = self.games.get(int(s.get("gid", -1)))
            if g is not None and s.get("final_total") is not None:
                g["finished"] = True
                g["final_total"] = s["final_total"]
                g["sets"] = s.get("sets")
        # Semilla del libro: sin ella el arranque en frío rechaza todo
        # ("book_insuficiente n=0") durante semanas hasta juntar MIN_BOOK
        # liquidaciones propias. Solo se aplica si el libro aún está vacío.
        if self.book.n(0) < MIN_BOOK:
            seed_path = self.st.root / "masters_book.json"
            if seed_path.exists():
                seed = json.loads(seed_path.read_text())
                for k, xs in (seed.get("remaining") or {}).items():
                    self.book.rem_t[int(k)] = sorted(int(x) for x in xs)

    def _players_hist_stats(self, players: str, before_ms: int):
        """(media causal, n min de los dos) o None si algún jugador < HIST_MIN."""
        means = []
        for p in players.split(" vs "):
            h = [t for t0, t, g in self.player_hist.get(p, []) if t0 < before_ms]
            if len(h) < HIST_MIN:
                return None, min(len(h), HIST_MIN)
            means.append(sum(h) / len(h))
        return sum(means) / len(means), HIST_MIN

    # ---------- procesamiento ----------
    def process_shard(self, shard: Path):
        for rec in read_shard(shard):
            try:
                d = rec["d"]
                state = parse_item(d, rec["t"])
                if rec["k"] == "game":
                    for ge in d.get("GE") or []:
                        for row in ge.get("E") or []:
                            state.quotes.extend(_quotes(state.game_id, "match", row))
            except Exception:
                continue
            self._on_state(state, rec["t"])

    def _on_state(self, state, t_ms: int):
            gid = state.game_id
            g = self.games.get(gid)
            if g is None:
                g = {"gid": gid, "bo": None, "league": None, "players": None,
                     "start_ts": None, "first_seen": t_ms, "finished": False,
                     "final_total": None, "sets": None, "decisions": {},
                     "last_price": {}, "st0_logged": False}
                self.games[gid] = g
                self.st.append("matches", {k: v for k, v in g.items()
                                           if k not in ("last_price", "decisions")})
            g["last_seen"] = t_ms
            if g["finished"]:
                return
            if state.best_of:
                g["bo"] = state.best_of
            if getattr(state, "league_id", None):
                g["champ_id"] = state.league_id
            if state.league_en or state.league:
                g["league"] = state.league_en or state.league
            if state.home and state.away:
                g["players"] = f"{state.home} vs {state.away}"
            if state.start_ts:
                g["start_ts"] = state.start_ts
            for q in state.quotes:
                if q.scope == "match" and q.market == "total" and not q.blocked:
                    g["last_price"][(q.selection, q.line)] = q.price

            need = (g["bo"] // 2 + 1) if g["bo"] in (5, 7) else None
            done = state.completed_sets

            # ---- final en tape: liquidación primaria + libro + jugadores ----
            if need and max(state.sets_won) >= need and state.in_play_set is None \
                    and len(done) == len(state.sets):
                total = sum(a + b for a, b in done)
                g["finished"] = True
                g["final_total"] = total
                g["sets"] = [list(s) for s in done]
                self.book.add(gid, done, total, 0, True)
                self.st.append("settlements", {
                    "gid": gid, "final_total": total, "sets": g["sets"],
                    "source": "tape", "conciliation": "PRIMARY_CONFIRMED",
                    "event_time_ms": t_ms})
                if g["players"]:
                    for p in g["players"].split(" vs "):
                        self.st.append("players", {
                            "gid": gid, "player": p, "final_total": total,
                            "event_time_ms": g["start_ts"] or t_ms,
                            "league": g["league"]})
                        self.player_hist[p].append(
                            (g["start_ts"] or t_ms, total, gid))
                return

            # ---- ventana st0: registrar mercado + decisiones ----
            if need != 4 or "masters" not in (g["league"] or "").lower():
                return
            now_s = t_ms / 1000.0
            if not (not state.live and not state.sets and state.start_ts
                    and 60 <= state.start_ts - now_s <= 4 * 3600):
                return
            if "ST0" in g["decisions"]:
                return  # una decisión por partido (primera ventana elegible)

            by_line: dict[float, dict] = {}
            for q in state.quotes:
                if q.scope == "match" and q.market == "total" and q.line is not None \
                        and not q.blocked:
                    by_line.setdefault(q.line, {})[q.selection] = q.price
            ladder = [{"line": ln, "under": s["under"], "over": s["over"]}
                      for ln, s in sorted(by_line.items()) if s.get("under") and s.get("over")]
            cen, cen_ok = implied_center(ladder)
            self.st.append("market", {
                "gid": gid, "observed_at_ms": t_ms, "event_time_ms": g["start_ts"],
                "ladder": ladder, "implied_center": cen, "center_valid": cen_ok,
                "seconds_to_start": (g["start_ts"] or 0) - now_s})

            pmu, _ = self._players_hist_stats(g["players"] or "", t_ms) \
                if g["players"] else (None, 0)
            gap = (cen - pmu) if (cen is not None and pmu is not None) else None

            base = {"gid": gid, "decision_at_ms": t_ms, "observed_at_ms": t_ms,
                    "event_time_ms": g["start_ts"], "ingested_at": now_iso(),
                    "league": g["league"], "players": g["players"],
                    "implied_center": cen, "player_mean": pmu, "gap": gap,
                    "ladder_snapshot": ladder}
            # ---- ST0 (regla idéntica a frozen_eval) ----
            best = None
            for L in ladder:
                pu = self.book.p_under(0, 0, L["line"])
                ev = pu * L["under"] - 1.0 if pu is not None else None
                L["p_model"] = pu
                L["ev"] = ev
                if ev is not None and ev >= MIN_EV and (best is None or ev > best["ev"]):
                    best = {"line": L["line"], "odds": L["under"], "p": pu, "ev": ev}
            d0 = dict(base, strategy="ST0", version=STRATEGIES["ST0"])
            if self.book.n(0) < MIN_BOOK:
                d0.update(decision="NO_BET", reason=f"book_insuficiente n={self.book.n(0)}")
            elif not ladder:
                d0.update(decision="NO_BET", reason="sin_escalera_over_under")
            elif best is None:
                d0.update(decision="NO_BET", reason="ninguna_linea_EV>=5%")
            else:
                d0.update(decision="BET", reason=None, **best,
                          stake_u=1.0, won=None, pnl_u=None, close_odds=None)
            self.st.append("decisions", d0)
            g["decisions"]["ST0"] = d0

            # ---- ST1 = ST0 cuando gap>=5 ----
            d1 = dict(base, strategy="ST1", version=STRATEGIES["ST1"])
            if gap is None:
                d1.update(decision="GAP_FILTERED",
                          reason="sin_historial_suficiente_o_centro_invalido")
            elif gap < GAP_MIN:
                d1.update(decision="GAP_FILTERED", reason=f"gap={gap:.2f}<5")
            elif d0["decision"] != "BET":
                d1.update(decision="NO_BET", reason="st0_sin_seleccion")
            else:
                d1.update(decision="BET", reason=None, **best,
                          stake_u=1.0, won=None, pnl_u=None, close_odds=None)
            self.st.append("decisions", d1)
            g["decisions"]["ST1"] = d1
            self.n_new += 1

    # ---------- detección de finales desaparecidos (modo live) ----------
    def mark_vanished(self, live_gids: set[int], t_ms: int):
        """Partido con decisión que ya no aparece en el feed -> posible final.
        La liquidación real la confirma settle_pending (tape/API); aquí solo
        dejamos de esperar nuevos ticks."""
        for gid, g in self.games.items():
            if g.get("finished") or not g.get("decisions"):
                continue
            if gid in live_gids or (t_ms - (g.get("last_seen") or 0)) < 3600_000:
                continue
            g["vanished"] = True

    # ---------- liquidación pendiente ----------
    def settle_pending(self, results_api=None):
        """Completa `won`/`pnl_u`/`close_odds` en decisions BET sin liquidar."""
        all_dec = self.st.load("decisions")
        already = {(str(d.get("gid")), d.get("strategy"))
                   for d in all_dec if d.get("decision") == "SETTLEMENT"}
        pend = [d for d in all_dec
                if d.get("decision") == "BET" and d.get("won") is None
                and (str(d.get("gid")), d.get("strategy")) not in already]
        if not pend:
            return 0
        seen_gid = set()  # liquidar una vez por (gid): ST0 y ST1 comparten partido
        # reindexar partidos (los finales de tape ya están en games/settlements)
        finals = {int(s["gid"]): s for s in self.st.load("settlements")
                  if s.get("final_total") is not None}
        n = 0
        for d in pend:
            gid = int(d["gid"])
            g = self.games.get(gid, {})
            s = finals.get(gid)
            source = "tape"
            if s is None and results_api is not None:
                if time.time() * 1000 - (d.get("event_time_ms") or 0) > SETTLE_GRACE_S * 1000 // 3600 * 1000 / 1000:
                    r = results_api(g, d)
                    if r is not None:
                        s, source = r, "results_api"
            if s is None:
                continue
            seen_gid.add(gid)
            total = s["final_total"]
            won = total < d["line"]
            close = g.get("last_price", {}).get(("under", d["line"])) \
                if isinstance(g.get("last_price"), dict) else None
            self.st.append("decisions", {
                "gid": gid, "strategy": d["strategy"], "decision": "SETTLEMENT",
                "supersedes": "BET", "won": won,
                "pnl_u": (d["odds"] - 1) if won else -1.0,
                "final_total": total, "close_odds": close,
                "settle_source": source,
                "event_time_ms": d.get("event_time_ms"),
                "ingested_at": now_iso()})
            self.st.append("settlements", {
                "gid": gid, "final_total": total, "sets": s.get("sets"),
                "source": source, "conciliation": "BACKUP_CONFIRMED" if source != "tape" else "PRIMARY_CONFIRMED",
                "event_time_ms": d.get("event_time_ms")})
            # actualizar índice local para siguientes bets
            finals[gid] = s
            n += 1
        return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--shards-manifest", type=Path, required=True)
    ap.add_argument("--results-api", action="store_true")
    args = ap.parse_args()

    eng = ShadowEngine(args.state)
    eng.restore()
    man = json.loads(args.shards_manifest.read_text()) if args.shards_manifest.exists() else {}
    run = {"run_at": now_iso(), "shards_processed": [], "git_commit": None}
    for shard in sorted(args.tape.rglob("*.jsonl.zst")):
        data = shard.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        key = shard.name
        # shard puede crecer: reprocesar si cambió el sha; dedup por decisions[g]
        eng.process_shard(shard)
        man[key] = sha
        run["shards_processed"].append({"name": key, "sha256": sha})
    n_settled = eng.settle_pending()
    run.update(n_new_decisions=eng.n_new, n_settled=n_settled,
               manifest=eng.st.manifest())
    eng.st.append("runs", run)
    args.shards_manifest.write_text(json.dumps(man, indent=1))
    print(json.dumps({k: v for k, v in run.items() if k != "manifest"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
