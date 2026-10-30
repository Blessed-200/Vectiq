"""API de resultados de respaldo para el Shadow Engine.

Primaria: final observado en el tape (ya cubierto por engine.process_shard).
Respaldo: vectiq.feed.results (Betwinner results service) por champId+día;
verificado offline con 0 discrepancias en 2,332 finales.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vectiq.feed.client import BetwinnerClient  # noqa: E402
from vectiq.feed.results import day_window, parse_results  # noqa: E402

_client = None


def get_client() -> BetwinnerClient:
    global _client
    if _client is None:
        bases = [b.strip() for b in os.environ.get("BW_BASE_URLS", "").split(",") if b.strip()]
        _client = BetwinnerClient(bases or ["https://betwinner.com"], max_rps=2.0,
                                proxy=os.environ.get("BW_PROXY_URL") or None)
    return _client


def fetch_result(match: dict, decision: dict) -> dict | None:
    """Devuelve {'final_total','sets'} si la API tiene el final; None si no."""
    champ = match.get("champ_id")
    gid = int(decision.get("gid", 0))
    ts = (match.get("start_ts") or decision.get("event_time_ms") or 0) / 1000.0
    if not champ or not gid or not ts:
        return None
    try:
        d0, d1 = day_window(int(ts))
        r = get_client().results(int(champ), d0, d1)
        if not r.ok or not r.payload:
            return None
        sets = parse_results(r.payload).get(gid)
        if not sets:
            return None
        return {"final_total": sum(a + b for a, b in sets), "sets": [list(s) for s in sets]}
    except Exception:
        return None
