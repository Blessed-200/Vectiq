"""Collection loop: poll -> archive raw -> evaluate -> paper-trade -> notify."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from vectiq.feed.client import BetwinnerClient, CircuitOpen
from vectiq.feed.scheduler import DetailScheduler
from vectiq.feed.schema import MatchState, parse_feed, parse_game
from vectiq.notify import telegram
from vectiq.signals import engine
from vectiq.signals.paper import PaperLedger
from vectiq.store.tape import TapeWriter

log = logging.getLogger("vectiq.collector")


class Collector:
    def __init__(self, client: BetwinnerClient, tape: TapeWriter, ledger: PaperLedger | None = None,
                 *, notify: Callable[[str], bool] | None = None, scheduler: DetailScheduler | None = None) -> None:
        self.client, self.tape, self.ledger = client, tape, ledger
        self.notify = notify or telegram.send
        self.sched = scheduler or DetailScheduler()
        self.anchor: dict[int, float | None] = {}
        self.last: dict[int, MatchState] = {}
        self.cycle = 0

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
        for sig in sigs:
            if self.ledger and self.ledger.open_bet(sig, s.fetched_ms):
                self.notify(telegram.format_signal(sig))
                log.info("signal %s %s %s @%s ev=%.3f", sig.players, sig.side, sig.line, sig.price, sig.ev)

    def _finished(self, gid: int) -> None:
        prev = self.last.pop(gid, None)
        self.anchor.pop(gid, None)
        self.tape.forget("item", gid)
        self.tape.forget("game", gid)
        if prev and self.ledger and max(prev.sets_won) >= 3:
            self.ledger.settle(gid, prev.points_played)

    # -- one cycle ---------------------------------------------------------
    def step(self, now_s: float | None = None) -> int:
        now_s = now_s or time.time()
        resp = self.client.live_list()
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
        present = {s.game_id for s in states}
        for gid in [g for g in self.last if g not in present]:
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
