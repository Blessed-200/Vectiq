"""Almacenamiento append-only del Shadow Engine.

Todo registro es una línea JSON en un archivo .jsonl por tipo. Nunca se
sobrescribe una línea: una corrección es una línea nueva con `supersedes`.
El estado vive en `shadow/state/` y se sincroniza con el release
`shadow-ledger` (sube/baja entero cada ejecución del workflow).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SCHEMA_VERSION = "shadow-1"

FILES = {
    "matches": "matches.jsonl",          # un registro por partido conocido
    "market": "market_ticks.jsonl",      # escaleras completas por tick st0
    "decisions": "decisions.jsonl",      # una por (partido, estrategia)
    "players": "player_results.jsonl",   # totales finales por jugador
    "settlements": "settlements.jsonl",  # liquidaciones y conciliación
    "capacity": "capacity.jsonl",        # probe UpdateCoupon por decisión BET
    "errors": "errors.jsonl",
    "runs": "runs.jsonl",                # cabecera de cada ejecución
}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_cache: dict[str, dict] = {}

    def path(self, kind: str) -> Path:
        return self.root / FILES[kind]

    def append(self, kind: str, rec: dict) -> None:
        rec = dict(rec)
        rec.setdefault("schema", SCHEMA_VERSION)
        rec.setdefault("ingested_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        line = json.dumps(rec, ensure_ascii=False)
        rec["sha256"] = hashlib.sha256(line.encode()).hexdigest()
        with open(self.path(kind), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._index_cache.pop(kind, None)

    def load(self, kind: str) -> list[dict]:
        p = self.path(kind)
        if not p.exists():
            return []
        out = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return out

    def index(self, kind: str, key: str) -> dict:
        if kind not in self._index_cache:
            self._index_cache[kind] = {str(r.get(key)): r for r in self.load(kind)}
        return self._index_cache[kind]

    def manifest(self) -> dict:
        man = {"schema": SCHEMA_VERSION,
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "files": {}}
        for kind, name in FILES.items():
            p = self.path(kind)
            if p.exists():
                data = p.read_bytes()
                man["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(),
                                      "bytes": len(data),
                                      "lines": data.count(b"\n")}
        return man


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
