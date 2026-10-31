"""Collection loop: poll -> archive raw -> evaluate -> paper-trade -> notify."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from vectiq.feed.client import BetwinnerClient, CircuitOpen
from vectiq.feed.scheduler import DetailScheduler
from vectiq.feed.results import day_window, parse_results
from vectiq.feed.schema import MatchState, parse_feed, parse_game, parse_item
from vectiq.notify import telegram
from vectiq.signals import candidate, engine, masters
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

log = logging.getLogger("vectiq.collector")


class Collector:
    def __init__(self, client: BetwinnerClient, tape: TapeWriter, ledger: PaperLedger | None = None,
                 *, shadow: PaperLedger | None = None, notify: Callable[[str], bool] | None = None,
                 scheduler: DetailScheduler | None = None, mledger: PaperLedger | None = None,
                 book: masters.MastersBook | None = None, cand: PaperLedger | None = None, trade_from_s: float | None = None,
                 trade_until_s: float | None = None, on_trade_start: Callable[["Collector"], None] | None = None,
                 on_trade_stop: Callable[["Collector"], None] | None = None,
                 book_path: Path | None = None, line_interval: float = 45.0,
                 notify_doc: Callable[[str, str], bool] | None = None, dashboard_dir: Path | None = None,
                 dashboard_every_s: float = 6 * 3600.0) -> None:
        self.client, self.tape, self.ledger, self.shadow = client, tape, ledger, shadow
        self.mledger, self.book, self.cand = mledger, book, cand
        self.trade_from_s, self.trade_until_s = trade_from_s, trade_until_s
        self.on_trade_start, self.on_trade_stop = on_trade_start, on_trade_stop
        self.book_path, self._book_saved = book_path, 0.0
        self.line_interval, self._line_t = line_interval, 0.0
        self._sweep_t, self._silent_t, self._alert_t = 0.0, 0.0, 0.0
        self.notify_doc, self.dashboard_dir = notify_doc, dashboard_dir
        self.dashboard_every_s, self._dash_t = dashboard_every_s, 0.0
        self._trading_seen = False
        self.absent: dict[int, int] = {}
        self._stopped = False
        self.finals: dict[int, tuple[list[tuple[int, int]], int]] = {}
        self.exposure: dict[int, float] = {}
        self.sprt = "on"
        self.day = time.strftime("%Y-%m-%d", time.gmtime())
        self.notify = notify or telegram.send
        self.sched = scheduler or DetailScheduler()
        self.anchor: dict[int, float | None] = {}
        self.last: dict[int, MatchState] = {}
        self.cycle = 0
        self.why: dict[str, int] = {}              # why evaluations produced no bet (since the last silence report)
        self.pending: dict[int, dict] = {}       # vanished without a final: waiting for the results service
        self._res_cache: dict[tuple[int, int], tuple[float, dict]] = {}
        self.pending_deadline_s = 5400.0

    # -- helpers -----------------------------------------------------------
    def _anchor(self, s: MatchState) -> None:
        if s.game_id in self.anchor:
            return
        if s.sets:  # first seen mid-match: no clean pre-match price to anchor on
            self.anchor[s.game_id] = None
            return
        w = {q.selection: q.price for q in s.quotes if q.market == "winner" and q.scope == "match"}
        if "home" in w and "away" in w:
            self.anchor[s.game_id] = engine.implied_rating_diff(w["home"], w["away"])

    def _signals(self, s: MatchState) -> None:
        sigs, why = engine.evaluate(s, self.anchor.get(s.game_id))
        calibrated = engine.is_calibrated(s.league)
        book = self.ledger if calibrated else self.shadow  # uncalibrated leagues: silent evidence only
        for sig in sigs:
            if book and book.open_bet(sig, s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts):
                if calibrated:
                    self.notify(telegram.format_signal(sig))
                log.info("%s %s %s %s @%s ev=%.3f", "signal" if calibrated else "shadow", sig.players, sig.side,
                         sig.line, sig.price, sig.ev)

    def _rescue(self, gid: int):
        """Ask the detail endpoint once for a final score the list feed never showed."""
        fn = getattr(self.client, "game", None)
        if fn is None:
            return None
        try:
            r = fn(gid)
            if r.ok and r.payload and r.payload.get("Value"):
                st = parse_game(r.payload, r.fetched_ms).state
                need = (st.best_of // 2 + 1) if st.best_of else 3
                if max(st.sets_won) >= need and len(st.completed_sets) == sum(st.sets_won):
                    return st
        except Exception:  # noqa: BLE001 - never let a rescue attempt break the loop
            log.debug("rescue failed for %s", gid, exc_info=True)
        return None

    def _finished(self, gid: int) -> None:
        prev = self.last.pop(gid, None)
        self.anchor.pop(gid, None)
        self.tape.forget("item", gid)
        self.tape.forget("game", gid)
        need = (prev.best_of // 2 + 1) if prev and prev.best_of else 3
        if prev and not (max(prev.sets_won) >= need and len(prev.completed_sets) == sum(prev.sets_won)):
            prev = self._rescue(gid) or prev      # one last look at the detail endpoint
        if prev and max(prev.sets_won) >= need and len(prev.completed_sets) == sum(prev.sets_won):
            done = prev.completed_sets
            self.finals[gid] = (done, sum(a + b for a, b in done))
            if self.book is not None and masters.is_masters(prev):
                self.book.add_match(gid, done)
            for book in (self.ledger, self.shadow, self.mledger, self.cand):
                if book:
                    book.settle(gid, prev.points_played)
            if self.mledger is not None:
                self._refresh_sprt()
        elif prev:
            done = self._results_final(prev.league_id, prev.start_ts, gid)
            if done:
                self._settle_final(gid, prev, done, "results_api")
            elif getattr(self.client, "results", None) is None:
                self._settle_unresolved(gid, prev)
            else:                                  # not published yet: keep the bets open and retry, then fall back
                self.pending[gid] = {"prev": prev, "next": time.time() + 120, "deadline": time.time() + self.pending_deadline_s}

    def _settle_final(self, gid: int, st: MatchState, done: list[tuple[int, int]], src: str) -> None:
        self.finals[gid] = (done, sum(a + b for a, b in done))
        if self.book is not None and masters.is_masters(st):
            self.book.add_match(gid, done)
        total = sum(a + b for a, b in done)
        for book in (self.ledger, self.shadow, self.mledger, self.cand):
            if book:
                book.settle(gid, total, src=src)
        if self.mledger is not None:
            self._refresh_sprt()

    def _settle_unresolved(self, gid: int, prev: MatchState) -> None:
        for book in (self.ledger, self.shadow, self.cand):
            if book:
                book.settle_vanished(gid, prev.points_played)
        if self.mledger is not None:
            if self.book is not None and masters.is_masters(prev):
                total = self.book.add_censored(gid, prev)      # imputed: never drop long matches silently
                self.finals[gid] = (prev.completed_sets, total)
                self.mledger.settle(gid, total, src="imputed")
            else:
                self.mledger.settle_vanished(gid, prev.points_played)
            self._refresh_sprt()

    def _results_final(self, league_id, start_ts, gid: int) -> list[tuple[int, int]] | None:
        """Final sets from the results service (None if unavailable/not yet published). One request per
        (championship, day) per 2 minutes, shared by every pending match of that block."""
        fn = getattr(self.client, "results", None)
        if fn is None or league_id is None or not start_ts:
            return None
        d0, d1 = day_window(int(start_ts))
        key = (int(league_id), d0)
        hit = self._res_cache.get(key)
        if hit is None or time.time() - hit[0] > 120:
            try:
                r = fn(int(league_id), d0, d1)
                data = parse_results(r.payload) if r.ok and r.payload else {}
            except Exception:  # noqa: BLE001 - results are best-effort; the fallback path still settles
                log.debug("results lookup failed for %s", gid, exc_info=True)
                data = {}
            hit = self._res_cache[key] = (time.time(), data)
            if len(self._res_cache) > 64:
                self._res_cache.pop(next(iter(self._res_cache)))
        return hit[1].get(gid)

    def _poll_pending(self, now_s: float) -> None:
        for gid, p in list(self.pending.items()):
            if time.time() < p["next"]:
                continue
            prev = p["prev"]
            done = self._results_final(prev.league_id, prev.start_ts, gid)
            if done:
                self.pending.pop(gid)
                self._settle_final(gid, prev, done, "results_api")
            elif time.time() >= p["deadline"]:
                self.pending.pop(gid)
                self._settle_unresolved(gid, prev)
            else:
                p["next"] = time.time() + 300

    # -- Masters UNDER engine -------------------------------------------------
    def trading(self, now_s: float) -> bool:
        if self.mledger is None or self.book is None:
            return False
        if self.trade_from_s is not None and now_s < self.trade_from_s:
            return False
        if self.trade_until_s is not None and now_s >= self.trade_until_s:
            return False
        return True

    def _trade_edges(self, now_s: float) -> None:
        on = self.trading(now_s)
        if on and not self._trading_seen:
            self._trading_seen = True
            if self.on_trade_start:
                self.on_trade_start(self)       # take over ledger + book from the previous run
            self._settle_known()
        if self.trade_until_s is not None and now_s >= self.trade_until_s and self._trading_seen and not self._stopped:
            self._stopped = True
            if self.on_trade_stop:
                self.on_trade_stop(self)        # final upload of the state the next run will take over

    def _settle_known(self) -> None:
        if self.mledger is None:
            return
        for gid in self.mledger.open_games():
            if gid in self.finals:
                self.mledger.settle(gid, self.finals[gid][1])
        self._refresh_sprt()

    def _refresh_sprt(self) -> None:
        if self.mledger is None:
            return
        new, _ = masters.sprt_state((p, o, w) for p, o, w, _ in self.mledger.settled_series())
        if new != self.sprt:
            self.sprt = new
            self.notify(f"<b>VECTIQ · MASTERS UNDER</b>\nVentaja {new.upper()} según el test secuencial (SPRT)."
                        + (" Alertas silenciadas hasta que la evidencia se recupere." if new == "off" else ""))

    def _candidate(self, s: MatchState) -> None:
        """Shadow-only frozen rule: one flat $1 paper position per game, never notified."""
        if self.cand is None or self.cand.game_stake(s.game_id) > 0:
            return
        sig = candidate.evaluate_candidate(s)
        if sig is not None:
            self.cand.open_bet(sig, s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts)

    def _masters(self, s: MatchState, now_s: float) -> None:
        if self.trading(now_s):                 # only the run that owns the ledgers records the candidate
            self._candidate(s)
        if not masters.is_masters(s) or self.mledger is None or self.book is None:
            return
        if not s.live:  # closing-line value: the last pre-start Under price on every line we hold
            for ln, q in engine.total_ladder(s).items():
                self.mledger.note_close(s.game_id, ln, "under", q["under"].price)
        if not self.trading(now_s):
            return
        bk = self.mledger.bankroll
        # bets on the same match are ~80 % correlated (audit): one position per match, 1 % of equity in total;
        # and never more than 35 % of equity waiting to settle at once (all of them share the same hypothesis)
        if self.mledger.open_stake >= 0.35 * bk:
            return
        pick, why = masters.evaluate_masters(
            s, self.book, now_s=now_s, size_mult=masters.drawdown_multiplier(self.mledger.drawdown()),
            exposure_left=0.01 - self.mledger.game_stake(s.game_id) / bk)
        self.why[why if pick is None else "signal"] = self.why.get(why if pick is None else "signal", 0) + 1
        if pick is None:
            return
        if self.mledger.has_stage(s.game_id, pick.k):   # one bet per match and entry stage, never a second line
            return
        if self.sprt == "off":
            log.info("masters muted by SPRT: %s", pick.signal.players)
            return
        if self.mledger.open_bet(pick.signal, s.fetched_ms, pick.minutes_to_start, champ_id=s.league_id, start_ts=s.start_ts):
            self.exposure[s.game_id] = self.exposure.get(s.game_id, 0.0) + pick.signal.stake_frac
            self._alert_t = now_s
            self.notify(telegram.format_masters(pick, bk, sprt=self.sprt, drawdown=self.mledger.drawdown()))
            log.info("masters %s %s under %s @%s ev=%.3f", pick.stage, pick.signal.players, pick.signal.line,
                     pick.signal.price, pick.signal.ev)

    def _sweep_orphans(self, now_s: float) -> None:
        """No bet may stay open: every ledger's stale bets are settled from the match itself (detail endpoint) or from
        the results service (needs champ_id/start_ts stored when the bet was opened); only if neither knows the final
        after ``grace`` is the bet voided (stake returned, counted apart), never silently dropped or guessed."""
        if now_s - self._sweep_t < 300:
            return
        self._sweep_t = now_s
        for led in (self.mledger, self.cand, self.shadow, self.ledger):
            if led is None:
                continue
            for gid, champ, start in led.stale_open_meta(now_s):
                if gid in self.last or gid in self.pending:
                    continue
                done = self._results_final(champ, start, gid)
                if done:
                    led.settle(gid, sum(a + b for a, b in done), src="results_api")
                    continue
                st = self._rescue(gid)
                if st is not None:
                    led.settle(gid, st.points_played, src="observed")
                elif champ is None or now_s - (start or 0) > 6 * 3600:   # legacy row, or service silent for 6 h
                    led.settle_vanished(gid, 0)
                    log.info("orphan bet voided for game %s", gid)

    def _dashboard(self, now_s: float) -> None:
        """Every few hours: one HTML board of every paper book (curves, ROI with uncertainty, ETA), sent to Telegram."""
        if self.notify_doc is None or self.dashboard_dir is None or not self.trading(now_s):
            return
        if now_s - self._dash_t < self.dashboard_every_s:
            return
        first = self._dash_t == 0.0
        self._dash_t = now_s
        if first:                      # do not fire right after a hand-over; wait one full period
            return
        from vectiq.notify import dashboard
        paths = {"Telegram (modelo)": self.mledger.path if self.mledger else None,
                 "Manus (regla fija)": self.cand.path if self.cand else None,
                 "Setka / viejo": self.shadow.path if self.shadow else None}
        try:
            for led in (self.mledger, self.cand, self.shadow):
                if led is not None:
                    led.db.commit()
            page, books = dashboard.build(paths, now_s)
            out = Path(self.dashboard_dir) / "dashboard.html"
            out.write_text(page, encoding="utf-8")
            self.notify_doc(str(out), dashboard.caption(books))
        except Exception:  # noqa: BLE001 - a report must never stop collection
            log.warning("dashboard failed", exc_info=True)

    def _watchdog(self, now_s: float) -> None:
        """Silence alarm: tells the user why, instead of leaving them wondering."""
        if self.mledger is None or not self.trading(now_s) or now_s - self._alert_t < 10800 or now_s - self._silent_t < 10800:
            return
        if self._alert_t == 0.0:
            self._alert_t = now_s
            return
        self._silent_t = now_s
        bk = self.mledger.bankroll
        top = ", ".join(f"{k} {v}" for k, v in sorted(self.why.items(), key=lambda kv: -kv[1])[:4]) or "sin evaluaciones"
        self.why.clear()
        self.notify(f"<b>VECTIQ · sin señales hace 3 h</b>\nEn juego ${self.mledger.open_stake:.2f} de ${bk:.2f} "
                    f"(tope {0.35 * bk:.2f}) · caída {100 * self.mledger.drawdown():.1f} % · SPRT {self.sprt}\n"
                    f"Motivos (partidos evaluados): {top}")

    def _poll_line(self, now_s: float) -> None:
        """Pre-match board: archives it and lets the Masters engine see lines hours before kick-off."""
        fn = getattr(self.client, "line_list", None)
        if fn is None or self.line_interval <= 0 or now_s - self._line_t < self.line_interval:
            return
        self._line_t = now_s
        try:
            r = fn()
        except CircuitOpen:
            return
        if not r.ok or not r.payload:
            log.warning("line board failed: %s", r.error)
            return
        for it in r.payload.get("Value") or []:
            try:
                s = parse_item(it, r.fetched_ms)
            except (KeyError, ValueError, TypeError):
                continue
            self.tape.append("line", s.game_id, it, r.fetched_ms)
            if masters.is_masters(s):          # not stored in self.last: only the live list may end a match
                self._masters(s, r.fetched_ms / 1000.0)

    def _save_book(self, now_s: float) -> None:
        if self.book is not None and self.book_path is not None and now_s - self._book_saved >= 600:
            self._book_saved = now_s
            self.book.save(self.book_path)

    def _daily_report(self, now_s: float) -> None:
        d = time.strftime("%Y-%m-%d", time.gmtime(now_s))
        if d != self.day:
            self.day = d
            if self.mledger is not None and self.trading(now_s):
                self.notify(telegram.format_report("resumen diario · Masters Under", self.mledger.summary(), self.sprt,
                                                   self.mledger.drawdown()))

    # -- one cycle ---------------------------------------------------------
    def step(self, now_s: float | None = None) -> int:
        now_s = now_s or time.time()
        resp = self.client.live_list()
        self._trade_edges(now_s)
        self._daily_report(now_s)
        self._save_book(now_s)
        self._poll_line(now_s)
        self._poll_pending(now_s)
        self._sweep_orphans(now_s)
        self._dashboard(now_s)
        self._watchdog(now_s)
        if not resp.ok or resp.payload is None:
            log.warning("list failed: %s", resp.error)
            return 0
        raw_by_id = {int(it["I"]): it for it in resp.payload.get("Value") or []}
        states = parse_feed(resp.payload, resp.fetched_ms)
        for s in states:
            self.tape.append("item", s.game_id, raw_by_id[s.game_id], resp.fetched_ms)
            self._anchor(s)
            self.last[s.game_id] = s
            if s.live:
                self._signals(s)
            self._masters(s, resp.fetched_ms / 1000.0)
        present = {s.game_id for s in states}
        for gid in list(self.last):
            if gid in present:
                self.absent.pop(gid, None)
            else:                                   # tolerate a one-poll hiccup before declaring a match over
                self.absent[gid] = self.absent.get(gid, 0) + 1
                if self.absent[gid] >= 3:
                    self.absent.pop(gid, None)
                    self._finished(gid)
        self.sched.drop_missing(present)
        for gid in self.sched.pick(states, now_s):
            try:
                r = self.client.game(gid)
            except CircuitOpen:
                break
            if r.ok and r.payload and r.payload.get("Value"):
                self.tape.append("game", gid, r.payload["Value"], r.fetched_ms)
                det = parse_game(r.payload, r.fetched_ms)
                self.last[gid] = det.state
        self.cycle += 1
        return len(states)

    def run(self, duration_s: float, interval_s: float = 3.0, stop: Callable[[], bool] = lambda: False) -> None:
        end = time.time() + duration_s
        while time.time() < end and not stop():
            t0 = time.time()
            try:
                n = self.step()
                if self.cycle % 100 == 0:
                    log.info("cycle=%d matches=%d written=%d skipped=%d http=%s", self.cycle, n,
                             self.tape.written, self.tape.skipped, self.client.stats)
            except CircuitOpen as exc:
                log.warning("%s", exc)
                time.sleep(10)
            except Exception:  # never let one bad payload stop 24/7 collection
                log.exception("cycle failed")
            time.sleep(max(0.0, interval_s - (time.time() - t0)))
        self.tape.close()
