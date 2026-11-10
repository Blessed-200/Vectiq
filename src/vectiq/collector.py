"""Collection loop: poll -> archive raw -> evaluate -> paper-trade -> notify."""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from vectiq.feed.client import BetwinnerClient, CircuitOpen
from vectiq.feed.scheduler import DetailScheduler
from vectiq.feed.results import day_window, parse_results
from vectiq.feed.schema import MatchState, parse_feed, parse_game, parse_item
from vectiq.notify import telegram
from vectiq.notify.session import PANEL_BUTTONS, PANEL_TEXT, Session
from vectiq.signals import calibra as calibra_mod
from vectiq.signals import candidate, engine, masters, st2, st3, st4, st5
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

log = logging.getLogger("vectiq.collector")


class Collector:
    def __init__(self, client: BetwinnerClient, tape: TapeWriter, ledger: PaperLedger | None = None,
                 *, shadow: PaperLedger | None = None, notify: Callable[[str], bool] | None = None,
                 scheduler: DetailScheduler | None = None, mledger: PaperLedger | None = None,
                 book: masters.MastersBook | None = None, cand: PaperLedger | None = None, st2_ledger: PaperLedger | None = None,
                 st3_ledger: PaperLedger | None = None, alerts: str = "masters,st3", arm: str = "on",
                 notify_alert: Callable[..., int | None] | None = None,
                 poll_updates: Callable[[int | None], list] | None = None,
                 ack_callback: Callable[..., None] | None = None, alert_chat: str | None = None,
                 trade_from_s: float | None = None,
                 trade_until_s: float | None = None, on_trade_start: Callable[["Collector"], None] | None = None,
                 on_trade_stop: Callable[["Collector"], None] | None = None,
                 book_path: Path | None = None, line_interval: float = 45.0,
                 notify_doc: Callable[[str, str], bool] | None = None, dashboard_dir: Path | None = None,
                 dashboard_every_s: float = 6 * 3600.0, notify_photo: Callable[[str, str], bool] | None = None,
                 notify_status: Callable[[str], bool] | None = None, status_every_s: float = 600.0,
                 shadow_state_dir: Path | None = None, masters_live: bool = False, calibra: calibra_mod.Calibra | None = None,
                 st4_ledger: PaperLedger | None = None, st5_ledger: PaperLedger | None = None,
                 session: Session | None = None) -> None:
        self.st5 = st5_ledger                    # ST5 "alta probabilidad": Markov model, Under >= 65 % (supersedes ST4 when it fires)
        self._st5_fired: set[int] = set()
        self.st4 = st4_ledger                    # ST4 "segunda oportunidad": Under after 2-0 blowouts (PRE/ST3 stay as they are)
        self.calibra = calibra                   # CALIBRA: shadow learning layer, never touches stakes or alerts
        self.masters_live = masters_live         # the old Telegram model's after-set-1 entries: off (3-7 % ROI, below ST3)
        self._pre_pause = ""
        self.client, self.tape, self.ledger, self.shadow = client, tape, ledger, shadow
        self.mledger, self.book, self.cand, self.st2 = mledger, book, cand, st2_ledger
        self.arm_mode = arm                      # on | silent | off: heads-up while set 1 looks like a blowout
        self.st3, self.alerts = st3_ledger, {a.strip() for a in alerts.split(",") if a.strip()}   # which engines may message Telegram
        self.session = session if session is not None else Session(None)   # the user's real balance + ▶/⏹ buttons
        self._raw_alert = notify_alert
        self.notify_alert = self._alert_out if notify_alert is not None else None
        self.poll_updates, self.ack_callback, self.alert_chat = poll_updates, ack_callback, alert_chat
        self._vig: dict[str, bool] = {}
        self._upd_offset: int | None = None
        self._upd_t, self._st3_t, self._st3_pause = 0.0, 0.0, ""
        self._armed: set[int] = set()
        self.trade_from_s, self.trade_until_s = trade_from_s, trade_until_s
        self.on_trade_start, self.on_trade_stop = on_trade_start, on_trade_stop
        self.book_path, self._book_saved = book_path, 0.0
        self.line_interval, self._line_t = line_interval, 0.0
        self._sweep_t, self._silent_t, self._alert_t = 0.0, 0.0, 0.0
        self.notify_doc, self.dashboard_dir = notify_doc, dashboard_dir
        self.dashboard_every_s, self._dash_t = dashboard_every_s, 0.0
        self.notify_photo, self.notify_status, self.status_every_s, self._status_t = notify_photo, notify_status, status_every_s, 0.0
        self.shadow_state_dir, self._devin_t, self._devin = shadow_state_dir, 0.0, []
        self._trading_seen = False
        self.absent: dict[int, int] = {}
        self._stopped = False
        self.finals: dict[int, tuple[list[tuple[int, int]], int]] = {}
        self.exposure: dict[int, float] = {}
        self.sprt = "on"
        try:                                       # alerts quote the REAL bank (default $100), never the paper ledger's compounded one
            self.real_bank = float(os.environ.get("VECTIQ_REAL_BANK", "100"))
        except ValueError:
            self.real_bank = 100.0
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
    def _alert_out(self, text: str, buttons=None, silent: bool = False) -> int | None:
        """Every alert goes through here: bet alerts get a one-line nudge while no ▶ session is running."""
        if buttons:
            text += self.session.footer()
        return self._raw_alert(text, buttons, silent) if self._raw_alert is not None else None

    def _bank(self) -> float:
        """The real bank alerts quote dollars on: what the user typed at ▶ INICIAR, else the environment default."""
        return float(self.session.bank) if self.session.bank else self.real_bank

    @staticmethod
    def _norm(why: str) -> str:
        """Pause reason without its numbers, so a drifting percentage does not post the same pause again."""
        return re.sub(r"\d+(\.\d+)? ?%", "", why)

    def _vigilante(self, kind: str, led: PaperLedger, where: str = "") -> float:
        """CUSUM watch on one book: 1.0 normally, 0.5 (and one Telegram warning) while the recent ROI has fallen away
        from the +10 % the stakes assume."""
        try:
            alarm = st3.cusum_stat(led.db, where) >= st3.CUSUM_H
        except Exception:  # noqa: BLE001 - a watchdog must never stop the collector
            return 1.0
        if alarm != self._vig.get(kind, False):
            self._vig[kind] = alarm
            if self.notify_alert is not None and self.trading(time.time()):
                self.notify_alert(f"<b>🚨 VIGILANTE {kind.upper()}</b>\nLa rentabilidad reciente cayó por debajo de lo esperado: stake a la mitad hasta que se recupere."
                                  if alarm else f"<b>✅ VIGILANTE {kind.upper()}</b>\nLa rentabilidad reciente se recuperó: stake normal.", None)
        return 0.5 if alarm else 1.0

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
        if self.ledger is None and self.shadow is None:
            return
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
            self._calibra_final(gid)
            if self.book is not None and masters.is_masters(prev):
                self.book.add_match(gid, done)
            for book in (self.ledger, self.shadow, self.mledger, self.cand, self.st2, self.st3, self.st4, self.st5):
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
        self._calibra_final(gid)
        if self.book is not None and masters.is_masters(st):
            self.book.add_match(gid, done)
        total = sum(a + b for a, b in done)
        for book in (self.ledger, self.shadow, self.mledger, self.cand, self.st2, self.st3, self.st4, self.st5):
            if book:
                book.settle(gid, total, src=src)
        if self.mledger is not None:
            self._refresh_sprt()

    def _settle_unresolved(self, gid: int, prev: MatchState) -> None:
        for book in (self.ledger, self.shadow, self.cand, self.st2, self.st3, self.st4, self.st5):
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
            if "masters" in self.alerts:
                self.notify(f"<b>VECTIQ · MASTERS UNDER</b>\nVentaja {new.upper()} según el test secuencial (SPRT)."
                            + (" Alertas silenciadas hasta que la evidencia se recupere." if new == "off" else ""))

    def _candidate(self, s: MatchState) -> None:
        """Shadow-only frozen rule: one flat $1 paper position per game, never notified."""
        if self.cand is None or self.cand.game_stake(s.game_id) > 0:
            return
        sig = candidate.evaluate_candidate(s)
        if sig is not None:
            self.cand.open_bet(sig, s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts)

    def _st2(self, s: MatchState) -> None:
        """ST2-core shadow bet (k=1, centre line, Under < 1.85) + first-snapshot observation log for k=1 and k=2."""
        if self.st2 is None:
            return
        obs = st2.observe(s)
        if obs is None or not st2.log_obs(self.st2.db, obs):
            return
        sig = st2.signal_from(obs)
        if sig is not None and self.st2.game_stake(s.game_id) == 0 and \
                self.st2.open_bet(sig, s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts):
            st2.mark_bet(self.st2.db, s.game_id, obs["k"])

    # -- ST3: alert + paper book + execution log ------------------------------
    def _st3(self, s: MatchState, now_s: float) -> None:
        """ST3: heads-up while set 1 is going to be a blowout, then the bet alert the moment it is; one paper position
        per match (always recorded: it is the measurement of the rule) and one Telegram alert per match (only while the
        risk guards allow real money)."""
        if self.st3 is None:
            return
        talk = "st3" in self.alerts and self.notify_alert is not None
        arm = st3.arm(s) if talk and self.arm_mode != "off" else None
        if arm is not None and s.game_id not in self._armed:
            self._armed.add(s.game_id)
            if st3.guard(self.st3.db, self.st3.bankroll, self.st3.drawdown(), now_s)[0]:
                self.notify_alert(telegram.format_st3_arm(f"{s.home} vs {s.away}", s.league, arm, masters.match_url(s),
                                                          s.fetched_ms / 1000.0), None, self.arm_mode == "silent")
        al = st3.evaluate(s)
        if al is None or self.st3.game_stake(s.game_id) > 0:
            return
        if not self.st3.open_bet(st3.signal_from(al, st3.STAKE_FRAC), s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts):
            return
        self._armed.discard(s.game_id)
        self._calibra_shadow("st3", self.st3, s, al.price, st3.STAKE_FRAC)
        al.url = masters.match_url(s)
        ok, why = st3.guard(self.st3.db, self.st3.bankroll, self.st3.drawdown(), now_s)
        if ok and self.mledger is not None and self._combined_day_loss(now_s) >= st3.DAY_STOP:
            ok, why = False, "pérdida del día entre PRE y ST3 en el límite"
        ev = st3.evidence_frac(self.st3.db, st3.STAKE_FRAC) * self._vigilante("st3", self.st3)
        if ok and ev <= 0:
            ok, why = False, "evidencia reciente insuficiente: el ROI de las últimas apuestas no supera cero con margen"
        frac = st3.stake_fraction(self.st3.drawdown(), ev)
        note = "Apuesta reducida por la evidencia reciente o por la caída." if 0 < frac < st3.STAKE_FRAC else ""
        if self.mledger is not None and self.mledger.bankroll > 0:      # PRE and ST3 on the same match: 2 % in total
            left = st3.PER_MATCH_CAP - min(self.mledger.game_stake(s.game_id) / self.mledger.bankroll, st3.PRE_CAP)   # PRE alerts never stake more than PRE_CAP
            if left < frac:
                frac = max(0.0, left)
                note = "Ya hay apuesta PRE en este partido: tope 2 % entre las dos."
        if talk and ok and frac >= 0.0025:
            mid = self.notify_alert(telegram.format_st3(al, self._bank(), frac, al.url, note=note),
                                    telegram.exec_buttons(al.game_id))
            st3.record_alert(self.st3.db, al, "bet", mid)
            self._st3_t = now_s
            log.info("st3 alert %s under %s @%s", al.players, al.line, al.price)
        elif talk and not ok:
            if self._norm(why) != self._norm(self._st3_pause):
                self._st3_pause = why
                self.notify_alert(f"<b>ST3 · EN PAUSA</b>\n{why}.\nSigue registrando en papel; no apuestes dinero real hasta revisar.", None)
        if ok and self._st3_pause:
            self._st3_pause = ""
            if talk:
                self.notify_alert("<b>ST3 · REANUDADO</b>\nLos controles de riesgo vuelven a estar en orden.", None)

    def _st5(self, s: MatchState, now_s: float) -> None:
        """ST5: paper position for k=1-3 (the measurement), Telegram alert for k=1-2; same risk stops, stake 2 %."""
        if self.st5 is None:
            return
        al = st5.evaluate(s)
        if al is None or self.st5.game_stake(s.game_id) > 0:
            return
        if not self.st5.open_bet(st5.signal_from(al, st5.STAKE_FRAC), s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts):
            return
        if len(al.sets) not in st5.ALERT_K or not ("st5" in self.alerts and self.notify_alert is not None and self.st3 is not None) or s.game_id in self._st5_fired:
            return           # ST5 (same match, higher-probability rung) already told the user
        self._st5_fired.add(s.game_id)
        al.url = masters.match_url(s)
        ok, why = st3.guard(self.st5.db, self.st5.bankroll, self.st5.drawdown(), now_s)
        if ok and self._combined_day_loss(now_s) >= st3.DAY_STOP:
            ok, why = False, "pérdida del día entre PRE, ST3, ST4 y ST5 en el límite"
        other = 0.0
        for led in (self.mledger, self.st3, self.st4):
            if led is not None and led.bankroll > 0:
                other += min(led.game_stake(s.game_id) / led.bankroll, st3.PRE_CAP if led is self.mledger else 1.0)
        ev = st3.evidence_frac(self.st5.db, st5.STAKE_FRAC) * self._vigilante("st5", self.st5)
        if ok and ev <= 0:
            ok, why = False, "evidencia reciente insuficiente: el ROI de las últimas apuestas no supera cero con margen"
        frac, note = st5.stake_for(self.st5.drawdown(), other, ev)
        if ok and frac >= 0.0025:
            mid = self.notify_alert(telegram.format_st5(al, self._bank(), frac, al.url, note=note),
                                    telegram.exec_buttons(al.game_id))
            st3.record_alert(self.st3.db, st3.Alert(al.game_id, al.players, al.league, tuple(al.sets[0]), al.inplay, al.line,
                                                    al.price, [], al.ts_ms), "st5", mid)
            log.info("st5 alert %s under %s @%s p=%.2f", al.players, al.line, al.price, al.p)
        elif not ok and self._norm(why) != self._norm(getattr(self, "_st5_pause", "")):
            self._st5_pause = why
            self.notify_alert(f"<b>ST5 · EN PAUSA</b>\n{why}.\nSigue registrando en papel; no apuestes dinero real hasta revisar.", None)
        if ok and getattr(self, "_st5_pause", ""):
            self._st5_pause = ""
            self.notify_alert("<b>ST5 · REANUDADO</b>\nLos controles de riesgo vuelven a estar en orden.", None)

    def _st4(self, s: MatchState, now_s: float) -> None:
        """ST4: one paper position per match (the measurement) and one Telegram alert, under the same risk stops."""
        if self.st4 is None:
            return
        al = st4.evaluate(s)
        if al is None or self.st4.game_stake(s.game_id) > 0:
            return
        if not self.st4.open_bet(st4.signal_from(al, st4.STAKE_FRAC), s.fetched_ms, champ_id=s.league_id, start_ts=s.start_ts):
            return
        al.url = masters.match_url(s)
        if not ("st4" in self.alerts and self.notify_alert is not None and self.st3 is not None):
            return
        ok, why = st3.guard(self.st4.db, self.st4.bankroll, self.st4.drawdown(), now_s)
        if ok and self._combined_day_loss(now_s) >= st3.DAY_STOP:
            ok, why = False, "pérdida del día entre PRE, ST3 y ST4 en el límite"
        other = 0.0
        for led in (self.mledger, self.st3):
            if led is not None and led.bankroll > 0:
                other += min(led.game_stake(s.game_id) / led.bankroll, st3.PRE_CAP if led is self.mledger else 1.0)
        ev = st3.evidence_frac(self.st4.db, st4.STAKE_FRAC)
        if ok and ev <= 0:
            ok, why = False, "evidencia reciente insuficiente: el ROI de las últimas apuestas no supera cero con margen"
        frac, note = st4.stake_for(self.st4.drawdown(), other, ev)
        if ok and frac >= 0.0025:
            mid = self.notify_alert(telegram.format_st4(al, self._bank(), frac, al.url, note=note),
                                    telegram.exec_buttons(al.game_id))
            st3.record_alert(self.st3.db, st3.Alert(al.game_id, al.players, al.league, tuple(al.sets[0]), al.inplay, al.line,
                                                    al.price, [], al.ts_ms), "st4", mid)
            log.info("st4 alert %s under %s @%s", al.players, al.line, al.price)
        elif not ok and self._norm(why) != self._norm(getattr(self, "_st4_pause", "")):
            self._st4_pause = why
            self.notify_alert(f"<b>ST4 · EN PAUSA</b>\n{why}.\nSigue registrando en papel; no apuestes dinero real hasta revisar.", None)
        if ok and getattr(self, "_st4_pause", ""):
            self._st4_pause = ""
            self.notify_alert("<b>ST4 · REANUDADO</b>\nLos controles de riesgo vuelven a estar en orden.", None)

    def _poll_exec(self, now_s: float) -> None:
        """Telegram button presses -> execution log (accepted / lower odds / could not), deduplicated by update id; also the
        ▶ INICIAR / ⏹ STOP panel and the balance the user types after pressing it."""
        if self.poll_updates is None or self.st3 is None or not self.trading(now_s) or now_s - self._upd_t < 5:
            return
        self._upd_t = now_s
        if self.notify_alert is not None and self.session.d.get("panel_id") is None:
            self.session.d["panel_id"] = self._raw_alert(PANEL_TEXT, PANEL_BUTTONS, True) or None
            self.session.save()
        labels = {"ok": ("aceptada", "Registrado: aceptada"), "low": ("cuota_menor", "Registrado: cuota menor"),
                  "no": ("no_llego", "Registrado: no llegué")}
        for u in self.poll_updates(self._upd_offset):
            uid = int(u["update_id"])
            self._upd_offset = uid + 1
            cq = u.get("callback_query")
            if not cq:
                post = u.get("message") or u.get("channel_post") or {}
                text = str(post.get("text") or "")
                if not text or self.session.seen(uid):
                    continue
                if text.strip().lower().startswith("/panel") and self._raw_alert is not None:
                    self.session.d["panel_id"] = self._raw_alert(PANEL_TEXT, PANEL_BUTTONS, True) or None
                    self.session.save()
                    continue
                reply = self.session.submit(text, now_s)
                if reply and self._raw_alert is not None:
                    self._raw_alert(reply, None, False)
                continue
            data = str(cq.get("data", ""))
            if data in ("s:start", "s:stop"):
                if self.session.seen(uid):
                    continue
                reply = self.session.press(data[2:], now_s)
                if self.ack_callback is not None:
                    self.ack_callback(cq.get("id", ""), "Escribe tu saldo en el chat", None, None, None)
                if self._raw_alert is not None:
                    self._raw_alert(reply, None, False)
                continue
            parts = data.split(":")
            if len(parts) != 3 or parts[0] != "x" or parts[2] not in labels:
                continue
            status, label = labels[parts[2]]
            st3.record_exec(self.st3.db, uid, int(parts[1]), status, (cq.get("from") or {}).get("first_name", ""))
            if self.ack_callback is not None:
                msg = cq.get("message") or {}
                self.ack_callback(cq.get("id", ""), "Anotado", self.alert_chat, msg.get("message_id"), label)

    def _st3_watchdog(self, now_s: float) -> None:
        if self.st3 is None or "st3" not in self.alerts or self.notify_alert is None or not self.trading(now_s):
            return
        if self._st3_t == 0.0:
            self._st3_t = now_s
            return
        if now_s - self._st3_t > 10800:
            self._st3_t = now_s
            self.notify_alert("<b>ST3 · sin señales hace 3 horas</b>\nPuede ser normal de madrugada; si sigue, revisa el recolector.", None)

    def _masters(self, s: MatchState, now_s: float) -> None:
        if self.trading(now_s):                 # only the run that owns the ledgers records the candidate
            self._candidate(s)
            try:
                self._st2(s)
            except Exception:  # noqa: BLE001 - a shadow book must never stop the collector
                log.warning("st2 shadow failed", exc_info=True)
            try:
                if self.calibra is not None:
                    self.calibra.observe(s)
            except Exception:  # noqa: BLE001 - CALIBRA is shadow: it must never stop the collector
                log.warning("calibra observe failed", exc_info=True)
            try:
                self._st3(s, now_s)
            except Exception:  # noqa: BLE001 - never stop collection because of an alert
                log.warning("st3 failed", exc_info=True)
            try:
                self._st5(s, now_s)             # before ST4: when ST5 fires, ST4 stays on paper only
            except Exception:  # noqa: BLE001 - never stop collection because of an alert
                log.warning("st5 failed", exc_info=True)
            try:
                self._st4(s, now_s)
            except Exception:  # noqa: BLE001 - never stop collection because of an alert
                log.warning("st4 failed", exc_info=True)
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
        if pick.k != 0 and not self.masters_live:       # one tool: pre-match from this model, live from ST3 only
            self.why["live_off"] = self.why.get("live_off", 0) + 1
            return
        if self.mledger.has_stage(s.game_id, pick.k):   # one bet per match and entry stage, never a second line
            return
        if self.sprt == "off":
            log.info("masters muted by SPRT: %s", pick.signal.players)
            return
        if self.mledger.open_bet(pick.signal, s.fetched_ms, pick.minutes_to_start, champ_id=s.league_id, start_ts=s.start_ts):
            self.exposure[s.game_id] = self.exposure.get(s.game_id, 0.0) + pick.signal.stake_frac
            if pick.k == 0:
                self._calibra_shadow("pre", self.mledger, s, pick.signal.price, pick.signal.stake_frac)
            if "pre" in self.alerts and pick.k == 0:
                self._alert_pre(pick, s, now_s)
            elif "masters" in self.alerts:        # legacy format, silent by default
                self._alert_t = now_s
                self.notify(telegram.format_masters(pick, bk, sprt=self.sprt, drawdown=self.mledger.drawdown()))
            log.info("masters %s %s under %s @%s ev=%.3f", pick.stage, pick.signal.players, pick.signal.line,
                     pick.signal.price, pick.signal.ev)

    def _calibra_final(self, gid: int) -> None:
        if self.calibra is not None:
            try:
                self.calibra.set_final(gid, self.finals[gid][1])
            except Exception:  # noqa: BLE001 - shadow layer
                log.warning("calibra final failed", exc_info=True)

    def _calibra_shadow(self, strat: str, ledger, s: MatchState, price: float, ancla_frac: float) -> None:
        if self.calibra is None or ledger is None:
            return
        try:
            self.calibra.shadow_decide(strat, ledger.db, s.game_id, s.league, price, ancla_frac, int(s.fetched_ms))
        except Exception:  # noqa: BLE001 - shadow layer
            log.warning("calibra shadow failed", exc_info=True)

    def _combined_day_loss(self, now_s: float) -> float:
        """Today's (UTC day) loss of the two books together, as a fraction of each bankroll (real money is one pot)."""
        loss = 0.0
        for led, kind in ((self.st3, "st3"), (self.mledger, "pre"), (self.st4, "st4"), (self.st5, "st5")):
            if led is None or led.bankroll <= 0:
                continue
            day0 = st3.day_start_ms(now_s)
            q = "SELECT COALESCE(SUM(pnl),0) FROM bets WHERE result IN ('W','L') AND ts_ms>=?"
            if kind == "pre":
                q += " AND lead_min IS NOT NULL"
            loss += led.db.execute(q, (day0,)).fetchone()[0] / led.bankroll
        return -loss

    def _pre_guard(self, now_s: float) -> tuple[bool, str]:
        m = self.mledger
        if m.drawdown() >= st3.PAUSE_DD:
            return False, f"caída de banca en el límite ({st3.PAUSE_DD * 100:.0f} %)"
        rows = m.db.execute("SELECT pnl,stake FROM bets WHERE result IN ('W','L') AND lead_min IS NOT NULL "
                            "ORDER BY ts_ms DESC, id DESC LIMIT ?", (st3.KILL_WINDOW,)).fetchall()
        if len(rows) >= st3.KILL_WINDOW:
            stake = sum(r[1] for r in rows)
            if stake and sum(r[0] for r in rows) / stake < st3.KILL_ROI:
                return False, f"ROI de las últimas {st3.KILL_WINDOW} apuestas de prepartido por debajo de {st3.KILL_ROI * 100:.0f} %"
        loss = self._combined_day_loss(now_s)
        if loss >= st3.DAY_STOP:
            return False, f"pérdida del día entre PRE y ST3 en el límite ({st3.DAY_STOP * 100:.0f} %)"
        return True, ""

    def _alert_pre(self, pick, s: MatchState, now_s: float) -> None:
        """Pre-match alert from the old Telegram model (the other half of the tool). Same channel, buttons and risk
        stops as ST3; the paper position was already recorded."""
        if self.notify_alert is None or self.st3 is None:
            return
        ok, why = self._pre_guard(now_s)
        ev = st3.evidence_frac(self.mledger.db, st3.PRE_CAP, "lead_min IS NOT NULL")
        if ok and ev <= 0:
            ok, why = False, "evidencia reciente insuficiente: el ROI de las últimas apuestas no supera cero con margen"
        frac = min(pick.signal.stake_frac, st3.stake_fraction(self.st3.drawdown(), ev), st3.PRE_CAP)
        if ok and frac > 0:
            start_s = now_s + (pick.minutes_to_start or 0) * 60
            mid = self.notify_alert(telegram.format_pre(pick, self._bank(), frac, start_s),
                                    telegram.exec_buttons(s.game_id))
            st3.record_alert(self.st3.db, st3.Alert(s.game_id, pick.signal.players, s.league, (0, 0), (0, 0), pick.signal.line,
                                                    pick.signal.price, [], s.fetched_ms), "pre", mid)
            self._alert_t = now_s
            log.info("pre alert %s under %s @%s", pick.signal.players, pick.signal.line, pick.signal.price)
        elif not ok and self._norm(why) != self._norm(self._pre_pause):
            self._pre_pause = why
            self.notify_alert(f"<b>PRE · EN PAUSA</b>\n{why}.\nSigue registrando en papel; no apuestes dinero real hasta revisar.", None)
        if ok and self._pre_pause:
            self._pre_pause = ""
            self.notify_alert("<b>PRE · REANUDADO</b>\nLos controles de riesgo vuelven a estar en orden.", None)

    def _sweep_orphans(self, now_s: float) -> None:
        """No bet may stay open: every ledger's stale bets are settled from the match itself (detail endpoint) or from
        the results service (needs champ_id/start_ts stored when the bet was opened); only if neither knows the final
        after ``grace`` is the bet voided (stake returned, counted apart), never silently dropped or guessed."""
        if now_s - self._sweep_t < 300:
            return
        self._sweep_t = now_s
        for led in (self.mledger, self.cand, self.st2, self.st3, self.st4, self.st5, self.shadow, self.ledger):
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

    def _all_books(self, now_s: float) -> list[dict]:
        """Every paper book (ours + Devin's shadow engine when its state is published) as dashboard records."""
        from vectiq.notify import dashboard
        paths = {"ST3 (alertas)": self.st3.path if self.st3 else None,
                 "Telegram (modelo, silenciado)": self.mledger.path if self.mledger else None,
                 "ST2-core (sombra)": self.st2.path if self.st2 else None,
                 "Manus (regla fija)": self.cand.path if self.cand else None,
                 "Setka / viejo": self.shadow.path if self.shadow else None}
        paths = {k: v for k, v in paths.items() if v}
        for led in (self.st3, self.mledger, self.cand, self.st2, self.shadow):
            if led is not None:
                led.db.commit()
        _, books = dashboard.build(paths, now_s)
        if now_s - self._devin_t > 1800 and self.shadow_state_dir is not None:
            self._devin_t = now_s
            self._devin = self._fetch_devin(now_s)
        return books + self._devin

    def _fetch_devin(self, now_s: float) -> list[dict]:
        import subprocess
        import tarfile
        from vectiq.notify import dashboard
        try:
            d = Path(self.shadow_state_dir)
            d.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["gh", "release", "download", "shadow-ledger", "-p", "state.tar.gz", "-D", str(d), "--clobber"],
                               check=False, capture_output=True, timeout=60)
            tgz = d / "state.tar.gz"
            if r.returncode != 0 or not tgz.exists():
                return []
            with tarfile.open(tgz) as tf:
                tf.extractall(d, filter="data")
            return dashboard.shadow_books(d, now_s)
        except Exception:  # noqa: BLE001
            log.debug("devin state unavailable", exc_info=True)
            return []

    def _status(self, now_s: float) -> None:
        """Pinned live message, edited in place every ~10 minutes (cheap, no spam)."""
        if self.notify_status is None or not self.trading(now_s) or now_s - self._status_t < self.status_every_s:
            return
        self._status_t = now_s
        try:
            from vectiq.notify import dashboard
            books = self._all_books(now_s)
            self.notify_status(dashboard.status_text(books, time.strftime("%H:%M", time.gmtime(now_s))))
        except Exception:  # noqa: BLE001
            log.warning("status update failed", exc_info=True)

    def _dashboard(self, now_s: float) -> None:
        """Every few hours: chart image + HTML board of every paper book, sent to Telegram."""
        if self.notify_doc is None or self.dashboard_dir is None or not self.trading(now_s):
            return
        if now_s - self._dash_t < self.dashboard_every_s:
            return
        first = self._dash_t == 0.0
        self._dash_t = now_s
        if first:                      # do not fire right after a hand-over; wait one full period
            return
        from vectiq.notify import dashboard
        try:
            books = self._all_books(now_s)
            out = Path(self.dashboard_dir)
            png = out / "dashboard.png"
            if self.notify_photo is not None and dashboard.chart_png(books, png):
                self.notify_photo(str(png), dashboard.caption(books))
            page = dashboard.render(books, time.strftime("%Y-%m-%d %H:%M", time.gmtime(now_s)))
            (out / "dashboard.html").write_text(page, encoding="utf-8")
            self.notify_doc(str(out / "dashboard.html"), "Tablero completo (abrir en el navegador)")
        except Exception:  # noqa: BLE001 - a report must never stop collection
            log.warning("dashboard failed", exc_info=True)

    def _watchdog(self, now_s: float) -> None:
        """Silence alarm: tells the user why, instead of leaving them wondering."""
        if self.mledger is None or "masters" not in self.alerts or not self.trading(now_s) \
                or now_s - self._alert_t < 10800 or now_s - self._silent_t < 10800:
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
            prev, self.day = self.day, d
            if self.calibra is not None and self.trading(now_s):
                try:
                    msg = self.calibra.close_day(prev)
                    if msg:
                        self.notify(msg)
                except Exception:  # noqa: BLE001 - shadow layer
                    log.warning("calibra daily failed", exc_info=True)
            if self.mledger is not None and self.trading(now_s) and "masters" in self.alerts:
                self.notify(telegram.format_report("resumen diario · Masters Under", self.mledger.summary(), self.sprt,
                                                   self.mledger.drawdown()))
            if self.st3 is not None and self.trading(now_s):
                sm, ex = self.st3.summary(), st3.exec_summary(self.st3.db)
                self.notify(f"<b>VECTIQ · resumen diario ST3</b>\nLiquidadas {sm['settled']} · ROI {sm['roi'] * 100:+.1f} % · "
                            f"banca de papel ${sm['bankroll']:.2f} (solo medición) · caída {self.st3.drawdown() * 100:.1f} %\n"
                            f"Alertas {ex.get('alerts', 0)} · aceptadas {ex.get('aceptada', 0)} · cuota menor "
                            f"{ex.get('cuota_menor', 0)} · no llegué {ex.get('no_llego', 0)}")

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
        self._status(now_s)
        self._watchdog(now_s)
        self._poll_exec(now_s)
        self._st3_watchdog(now_s)
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
