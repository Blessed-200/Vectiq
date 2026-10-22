"""Typed view over the Betwinner LiveFeed payloads.

The raw payload is always archived losslessly (see ``store.tape``); this module
only derives a normalised view from it. Unknown fields are never dropped from
the archive, so a future hypothesis can always be re-derived from raw data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Market group ids observed in captures (G) -> (name, {type id: selection}).
GROUPS: dict[int, tuple[str, dict[int, str]]] = {
    1: ("winner", {1: "home", 3: "away", 2: "draw"}),
    2: ("handicap", {7: "home", 8: "away"}),
    4: ("set_total", {9: "over", 10: "under"}),
    15: ("team_total_home", {11: "over", 12: "under"}),
    17: ("total", {9: "over", 10: "under"}),
    62: ("team_total_away", {13: "over", 14: "under"}),
}

_BEST_OF = re.compile(r"(\d+)\s*(?:sets|juegos)", re.I)


@dataclass(frozen=True, slots=True)
class Quote:
    game_id: int
    scope: str  # "match" or "set<N>"
    group: int
    market: str
    selection: str
    line: float | None
    price: float
    main: bool  # CE flag: the line the bookmaker currently centres on
    blocked: bool


@dataclass(slots=True)
class MatchState:
    game_id: int
    number: int | None
    start_ts: int | None
    update_ts: int | None  # "U": bookmaker-side last update (unix s)
    fetched_ms: int
    league: str
    league_id: int | None
    home: str
    away: str
    home_id: int | None
    away_id: int | None
    best_of: int | None
    status: str  # raw SLS text
    live: bool
    sets_won: tuple[int, int]
    current_set: int
    sets: list[tuple[int, int]]  # every set incl. the one in progress
    server: int | None  # 1 / 2 when the feed says so
    quotes: list[Quote] = field(default_factory=list)

    @property
    def completed_sets(self) -> list[tuple[int, int]]:
        return [s for s in self.sets if set_finished(*s)]

    @property
    def in_play_set(self) -> tuple[int, int] | None:
        sets = self.sets
        if sets and not set_finished(*sets[-1]):
            return sets[-1]
        return None

    @property
    def points_played(self) -> int:
        return sum(a + b for a, b in self.sets)


def set_finished(a: int, b: int) -> bool:
    return max(a, b) >= 11 and abs(a - b) >= 2


def _best_of(item: dict[str, Any]) -> int | None:
    txt = (item.get("MIO") or {}).get("MaF") or ""
    m = _BEST_OF.search(txt)
    if not m:
        return None
    n = int(m.group(1))
    return n


def _sets(sc: dict[str, Any]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in sorted(sc.get("PS") or [], key=lambda x: x.get("Key", 0)):
        v = p.get("Value") or {}
        if "S1" not in v and "S2" not in v:
            continue  # period announced but not started
        out.append((int(v.get("S1", 0)), int(v.get("S2", 0))))
    return out


def _quotes(game_id: int, scope: str, entries: Iterable[dict[str, Any]], extra_main: bool = False) -> list[Quote]:
    res = []
    for e in entries:
        g = e.get("G")
        t = e.get("T")
        name, sel = GROUPS.get(g, (f"g{g}", {}))
        res.append(
            Quote(
                game_id=game_id,
                scope=scope,
                group=g,
                market=name,
                selection=sel.get(t, f"t{t}"),
                line=e.get("P"),
                price=float(e["C"]),
                main=bool(e.get("CE")) or extra_main,
                blocked=bool(e.get("B")),
            )
        )
    return res


def parse_item(item: dict[str, Any], fetched_ms: int) -> MatchState:
    """Parse one element of ``Get1x2_VZip.Value`` (or ``GetGameZip.Value``)."""
    sc = item.get("SC") or {}
    fs = sc.get("FS") or {}
    gid = int(item["I"])
    quotes = _quotes(gid, "match", item.get("E") or [], extra_main=True)
    seen = {(q.group, q.selection, q.line) for q in quotes}
    for blk in item.get("AE") or []:
        for q in _quotes(gid, "match", blk.get("ME") or []):
            if (q.group, q.selection, q.line) not in seen:
                quotes.append(q)
    status = str(sc.get("SLS") or "")
    live = "curso" in status.lower() or "progress" in status.lower() or (sc.get("TD") is None and bool(sc.get("PS")) and bool(_sets(sc)))
    return MatchState(
        game_id=gid,
        number=item.get("N"),
        start_ts=item.get("S"),
        update_ts=item.get("U"),
        fetched_ms=fetched_ms,
        league=str(item.get("L") or ""),
        league_id=item.get("LI"),
        home=str(item.get("O1E") or item.get("O1") or ""),
        away=str(item.get("O2E") or item.get("O2") or ""),
        home_id=item.get("O1I"),
        away_id=item.get("O2I"),
        best_of=_best_of(item),
        status=status,
        live=live,
        sets_won=(int(fs.get("S1", 0)), int(fs.get("S2", 0))),
        current_set=int(sc.get("CP") or 0),
        sets=_sets(sc),
        server=sc.get("P"),
        quotes=quotes,
    )


def parse_feed(payload: dict[str, Any], fetched_ms: int) -> list[MatchState]:
    if not payload.get("Success", True):
        raise ValueError(f"feed error: {payload.get('Error')!r}")
    return [parse_item(it, fetched_ms) for it in payload.get("Value") or []]


@dataclass(slots=True)
class GameDetail:
    state: MatchState
    stats: dict[str, tuple[str, str]]  # label -> (home, away)
    set_quotes: list[Quote]


def parse_game(payload: dict[str, Any], fetched_ms: int) -> GameDetail:
    """Parse ``GetGameZip``: full ladders, per-set sub-games and live stats."""
    if not payload.get("Success", True):
        raise ValueError(f"game error: {payload.get('Error')!r}")
    v = payload["Value"]
    state = parse_item(v, fetched_ms)
    stats: dict[str, tuple[str, str]] = {}
    for blk in (v.get("SC") or {}).get("ST") or []:
        for s in blk.get("Value") or []:
            stats[str(s.get("N"))] = (str(s.get("S1")), str(s.get("S2")))
    # Full match ladders live in GE -> G -> E (list of lists)
    for ge in v.get("GE") or []:
        for row in ge.get("E") or []:
            state.quotes.extend(_quotes(state.game_id, "match", row))
    set_quotes: list[Quote] = []
    for sg in v.get("SG") or []:
        scope = f"set{sg.get('PN') or sg.get('N') or sg.get('I')}"
        for ge in sg.get("GE") or []:
            for row in ge.get("E") or []:
                set_quotes.extend(_quotes(int(sg["I"]), scope, row))
    return GameDetail(state=state, stats=stats, set_quotes=set_quotes)
