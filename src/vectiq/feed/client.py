"""HTTP client for the Betwinner LiveFeed service.

Design goals: polite (bounded request rate, jitter, honours Retry-After),
observable (every call returns latency/size/status), and fail-safe (circuit
breaker instead of hammering the host after repeated refusals).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

LIST_PATH = "/service-api/LiveFeed/Get1x2_VZip"
GAME_PATH = "/service-api/LiveFeed/GetGameZip"
from vectiq.feed.results import RESULT_PATH  # noqa: E402
LINE_PATH = "/service-api/LineFeed/Get1x2_VZip"   # pre-match board (unverified path: see `probe --line`)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-419,es;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass(slots=True)
class Response:
    ok: bool
    status: int
    payload: dict[str, Any] | None
    latency_ms: float
    size: int
    fetched_ms: int
    error: str | None = None


class CircuitOpen(RuntimeError):
    pass


class BetwinnerClient:
    def __init__(
        self,
        base_urls: list[str],
        *,
        max_rps: float = 4.0,
        timeout: float = 8.0,
        proxy: str | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        breaker_failures: int = 6,
        breaker_cooldown: float = 90.0,
    ) -> None:
        if not base_urls:
            raise ValueError("at least one base url required")
        self.bases = [b.rstrip("/") for b in base_urls]
        self._i = 0
        self.min_gap = 1.0 / max_rps
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers.update(DEFAULT_HEADERS)
        if proxy:
            self.s.proxies.update({"http": proxy, "https": proxy})
        self._clock, self._sleep = clock, sleep
        self._last = 0.0
        self._fail = 0
        self._open_until = 0.0
        self._breaker_failures, self._cooldown = breaker_failures, breaker_cooldown
        self.stats = {"calls": 0, "ok": 0, "fail": 0, "bytes": 0}

    # -- internals ---------------------------------------------------------
    def _pace(self) -> None:
        gap = self._clock() - self._last
        wait = self.min_gap * random.uniform(1.0, 1.25) - gap
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def _get(self, path: str, params: dict[str, Any]) -> Response:
        if self._clock() < self._open_until:
            raise CircuitOpen(f"circuit open for {self._open_until - self._clock():.0f}s")
        self._pace()
        base = self.bases[self._i % len(self.bases)]
        t0 = self._clock()
        self.stats["calls"] += 1
        try:
            r = self.s.get(base + path, params=params, timeout=self.timeout,
                           headers={"Referer": f"{base}/es/live/table-tennis"})
            lat = (self._clock() - t0) * 1000
            size = len(r.content)
            self.stats["bytes"] += size
            if r.status_code == 200:
                payload = r.json()
                self._fail = 0
                self.stats["ok"] += 1
                return Response(True, 200, payload, lat, size, int(time.time() * 1000))
            self._register_failure(r.status_code, r.headers.get("Retry-After"))
            return Response(False, r.status_code, None, lat, size, int(time.time() * 1000), f"http {r.status_code}")
        except (requests.RequestException, ValueError) as exc:
            self._register_failure(0, None)
            return Response(False, 0, None, (self._clock() - t0) * 1000, 0, int(time.time() * 1000), repr(exc))

    def _register_failure(self, status: int, retry_after: str | None) -> None:
        self.stats["fail"] += 1
        self._fail += 1
        if retry_after and retry_after.isdigit():
            self._open_until = max(self._open_until, self._clock() + float(retry_after))
        if self._fail >= self._breaker_failures or status in (403, 429):
            self._open_until = max(self._open_until, self._clock() + self._cooldown * min(self._fail, 4))
            self._i += 1  # try the next mirror after the cooldown

    # -- public ------------------------------------------------------------
    def live_list(self, sport: int = 10, count: int = 100, lng: str = "es") -> Response:
        return self._get(LIST_PATH, {"sports": sport, "count": count, "lng": lng, "mode": 4})

    def line_list(self, sport: int = 10, count: int = 400, lng: str = "es") -> Response:
        """Pre-match board: matches (and their ladders) hours before kick-off, unlike the live list."""
        return self._get(LINE_PATH, {"sports": sport, "count": count, "lng": lng, "mode": 4})

    def game(self, game_id: int, lng: str = "es") -> Response:
        return self._get(GAME_PATH, {
            "id": game_id, "lng": lng, "isSubGames": "true", "GroupEvents": "true",
            "countevents": 250, "grMode": 2,
        })

    def results(self, champ_id: int, date_from: int, date_to: int, lng: str = "es") -> Response:
        """Finished matches of one championship in [date_from, date_to) (unix s): final set scores."""
        return self._get(RESULT_PATH, {"champId": champ_id, "dateFrom": date_from, "dateTo": date_to,
                                       "lng": lng, "ref": 152})
