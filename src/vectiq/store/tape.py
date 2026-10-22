"""Lossless raw tape: the single source of truth for every derived dataset.

Format: ``tape/YYYY/MM/DD/HH.jsonl.zst`` — concatenated zstd frames, each frame a
batch of JSON lines ``{"t": fetched_ms, "k": kind, "id": game_id, "d": raw}``.
A record is written only when its content changed (delta encoding), which keeps
volume at a fraction of the naive snapshot-per-poll size without losing any
state transition. Heartbeats are written so gaps are distinguishable from calm.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

import zstandard as zstd

_VOLATILE = ("U",)  # bookmaker-side keepalive timestamp; changes without information


def _digest(obj: Any) -> bytes:
    if isinstance(obj, dict):
        obj = {k: v for k, v in obj.items() if k not in _VOLATILE}
    return hashlib.blake2b(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode(), digest_size=12).digest()


class TapeWriter:
    def __init__(self, root: Path, flush_every: int = 200, level: int = 9) -> None:
        self.root = Path(root)
        self.flush_every = flush_every
        self._cctx = zstd.ZstdCompressor(level=level)
        self._buf: list[bytes] = []
        self._last: dict[tuple[str, int], bytes] = {}
        self._hour: str | None = None
        self.written = self.skipped = 0

    def _path(self, ms: int) -> Path:
        t = time.gmtime(ms / 1000)
        return self.root / f"{t.tm_year:04d}/{t.tm_mon:02d}/{t.tm_mday:02d}/{t.tm_hour:02d}.jsonl.zst"

    def append(self, kind: str, game_id: int, raw: dict[str, Any], fetched_ms: int) -> bool:
        key = (kind, game_id)
        dg = _digest(raw)
        if self._last.get(key) == dg:
            self.skipped += 1
            return False
        self._last[key] = dg
        path = self._path(fetched_ms)
        if self._hour is not None and str(path) != self._hour:
            self.flush()
        self._hour = str(path)
        self._buf.append(json.dumps({"t": fetched_ms, "k": kind, "id": game_id, "d": raw},
                                    ensure_ascii=False, separators=(",", ":")).encode())
        self.written += 1
        if len(self._buf) >= self.flush_every:
            self.flush()
        return True

    def forget(self, kind: str, game_id: int) -> None:
        self._last.pop((kind, game_id), None)

    def flush(self) -> None:
        if not self._buf or self._hour is None:
            return
        path = Path(self._hour)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(self._cctx.compress(b"\n".join(self._buf) + b"\n"))
        self._buf.clear()

    def close(self) -> None:
        self.flush()


def read_tape(path: Path) -> Iterator[dict[str, Any]]:
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh, dctx.stream_reader(fh, read_across_frames=True) as rd:
        data = rd.read()
    for line in data.splitlines():
        if line:
            yield json.loads(line)


def iter_tape(root: Path) -> Iterator[dict[str, Any]]:
    for p in sorted(Path(root).rglob("*.jsonl.zst")):
        yield from read_tape(p)
