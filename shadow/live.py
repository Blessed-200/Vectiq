"""Shadow Engine en vivo — polling de LineFeed/Get1x2_VZip (prematch).

Misma lógica ST0/ST1 que engine.py pero con decision_at real (tiempo real,
antes del inicio). Persiste el estado cada ciclo para tolerar reinicios del
runner; el workflow lo relanza encadenado (~48 min + sucesor).

Uso: python shadow/live.py --state shadow/state --minutes 48
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shadow"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from vectiq.feed.client import BetwinnerClient  # noqa: E402
from vectiq.feed.schema import parse_item  # noqa: E402
from engine import ShadowEngine  # noqa: E402
from results_api import fetch_result  # noqa: E402
from store import now_iso  # noqa: E402

POLL_S = 20          # intervalo de polling del board prematch
SETTLE_EVERY_S = 300


def run(state_dir: Path, minutes: int) -> dict:
    eng = ShadowEngine(state_dir)
    eng.restore()
    bases = [b.strip() for b in os.environ.get("BW_BASE_URLS", "").split(",") if b.strip()]
    client = BetwinnerClient(bases or ["https://betwinner.com"], max_rps=2.0,
                             proxy=os.environ.get("BW_PROXY_URL") or None)
    t_end = time.time() + minutes * 60
    last_settle = 0.0
    stats = {"started": now_iso(), "polls": 0, "errors": 0, "decisions": 0, "settled": 0}
    while time.time() < t_end:
        try:
            r = client.line_list()
            stats["polls"] += 1
            if r.ok and r.payload:
                live_gids = set()
                for it in (r.payload.get("Value") or []):
                    try:
                        s = parse_item(it, r.fetched_ms)
                    except Exception:
                        continue
                    live_gids.add(s.game_id)
                    eng._on_state(s, r.fetched_ms)
                eng.mark_vanished(live_gids, r.fetched_ms)
            if time.time() - last_settle > SETTLE_EVERY_S:
                stats["settled"] += eng.settle_pending(results_api=fetch_result)
                last_settle = time.time()
        except Exception as e:  # registrar y seguir: no simular cobertura
            stats["errors"] += 1
            eng.st.append("errors", {"kind": "poll_error", "detail": str(e)[:500]})
        time.sleep(POLL_S)
    stats["decisions"] = eng.n_new
    stats["finished"] = now_iso()
    eng.st.append("runs", stats)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--minutes", type=int, default=48)
    args = ap.parse_args()
    print(json.dumps(run(args.state, args.minutes), ensure_ascii=False))


if __name__ == "__main__":
    main()
