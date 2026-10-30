"""Telemetría de capacidad real del mercado — endpoint UpdateCoupon.

`POST /service-api/LiveBet-update/Open/UpdateCoupon` es el dry-run de cupón del
frontend: sin autenticación ni apuesta real devuelve por selección
  - maxBet / minBet   -> límite que acepta la casa EN ESE INSTANTE
  - Block             -> mercado abierto/suspendido
  - changeCf          -> la cuota se movió desde la referencia enviada
  - Coef devuelta     -> cuota vigente confirmada

Verificado en vivo (2026-10-30): Masters España Under 110.5 @1.848 ->
maxBet=62500, minBet=10.0, Block=false, changeCf=false.

Solo lectura: el endpoint no coloca apuestas; es la llamada que el frontend
hace al añadir una selección al cupón.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

COUPON_PATH = "/service-api/LiveBet-update/Open/UpdateCoupon"
DEFAULT_BASE = "https://betwinner2.com"

_HEADERS = {
    "Content-Type": "application/json",
    "x-requested-with": "XMLHttpRequest",
    "x-svc-source": "__BETTING_APP__",
    "x-app-n": "__BETTING_APP__",
    "is-srv": "false",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://betwinner2.com",
    "Referer": "https://betwinner2.com/es/line/table-tennis",
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 "
                   "Mobile/15E148 Safari/604.1"),
}

# Type 10 = total UNDER (mismo código que el feed VZip/T10 y las capturas)
_TYPE_UNDER = 10
_KIND_LINE = 1   # Kind observado en capturas prematch (Kind 3 = otra familia)


def _session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def probe_capacity(gid: int, line: float, coef: float,
                   session: requests.Session | None = None,
                   base: str = DEFAULT_BASE,
                   timeout: float = 8.0) -> dict[str, Any]:
    """Dry-run de cupón para UNDER `line` @ `coef` en `gid`.

    Devuelve dict con la telemetría; nunca lanza excepción (ok=False + error).
    """
    body = {
        "Events": [{
            "GameId": gid, "Type": _TYPE_UNDER, "Coef": coef, "Param": line,
            "PV": None, "PlayerId": 0, "Kind": _KIND_LINE, "InstrumentId": 0,
            "Seconds": 0, "Price": 0, "Expired": 0, "PlayersDuel": [],
        }],
        "Vid": 0, "partner": 152, "Lng": "es", "CfView": 0,
        "CalcSystemsMin": False, "Group": 534, "Country": 97,
        "Currency": 278, "SaleBetId": 0, "IsPowerBet": False,
        "WithLobby": False,
    }
    t0 = time.monotonic()
    try:
        s = session or _session(os.environ.get("BW_PROXY_URL") or None)
        r = s.post(base + COUPON_PATH, json=body, timeout=timeout)
        lat = (time.monotonic() - t0) * 1000
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "latency_ms": lat}
        v = r.json().get("Value") or {}
        ev = (v.get("Events") or [{}])[0]
        return {
            "ok": True, "status": 200, "latency_ms": lat,
            "max_bet": v.get("maxBet"), "min_bet": v.get("minBet"),
            "block": ev.get("Block"), "change_cf": v.get("changeCf"),
            "coef_returned": ev.get("Coef"), "finish": ev.get("Finish"),
            "need_update_line": v.get("NeedUpdateLine"),
            "currency": 278,
        }
    except Exception as e:  # nunca interrumpir el loop por un probe
        return {"ok": False, "status": 0, "latency_ms": (time.monotonic() - t0) * 1000,
                "error": repr(e)[:300]}
