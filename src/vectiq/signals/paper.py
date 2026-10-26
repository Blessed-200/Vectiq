"""Paper-trading ledger (SQLite). Kept apart from anything production-facing.

Every simulated bet stores the price available *at signal time*; settlement uses
the bookmaker's final score; closing-line value is measured against the last
price seen for the same line before the match ended.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from vectiq.signals.engine import Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets(
  id INTEGER PRIMARY KEY, ts_ms INTEGER, game_id INTEGER, players TEXT, league TEXT, sets TEXT,
  side TEXT, line REAL, price REAL, p_model REAL, ev REAL, stake REAL, bankroll_before REAL,
  closing_price REAL, final_total INTEGER, result TEXT, pnl REAL,
  UNIQUE(game_id, side, line));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


class PaperLedger:
    def __init__(self, path: Path | str, start_bankroll: float = 100.0) -> None:
        self.path = str(path)
        self.start_bankroll = start_bankroll
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('start',?)", (str(start_bankroll),))
        self.db.commit()

    def reopen(self) -> None:
        """Reconnect after the file was replaced on disk (state handed over by the previous run)."""
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('start',?)", (str(self.start_bankroll),))
        self.db.commit()

    def settled_series(self) -> list[tuple[float, float, bool, float]]:
        """Settled bets in chronological order: (p_model, price, won, pnl)."""
        rows = self.db.execute("SELECT p_model,price,result,pnl FROM bets WHERE result IN ('W','L') ORDER BY ts_ms,id").fetchall()
        return [(p, o, r == "W", pnl) for p, o, r, pnl in rows]

    def open_games(self) -> set[int]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT game_id FROM bets WHERE result IS NULL")}

    def drawdown(self) -> float:
        """Current fall from the peak of the settled equity curve (0.15 = -15 %)."""
        eq = peak = float(self.db.execute("SELECT v FROM meta WHERE k='start'").fetchone()[0])
        for *_, pnl in self.settled_series():
            eq += pnl
            peak = max(peak, eq)
        return 0.0 if peak <= 0 else max(0.0, 1.0 - eq / peak)

    @property
    def bankroll(self) -> float:
        start = float(self.db.execute("SELECT v FROM meta WHERE k='start'").fetchone()[0])
        pnl = self.db.execute("SELECT COALESCE(SUM(pnl),0) FROM bets WHERE result IS NOT NULL").fetchone()[0]
        open_stake = self.db.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE result IS NULL").fetchone()[0]
        return start + pnl - open_stake

    def open_bet(self, s: Signal, now_ms: int | None = None) -> bool:
        bk = self.bankroll
        stake = round(bk * s.stake_frac, 2)
        if stake < 0.5:
            return False
        try:
            self.db.execute(
                "INSERT INTO bets(ts_ms,game_id,players,league,sets,side,line,price,p_model,ev,stake,bankroll_before)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_ms or int(time.time() * 1000), s.game_id, s.players, s.league,
                 ";".join(f"{a}-{b}" for a, b in s.sets), s.side, s.line, s.price, s.p_model, s.ev, stake, bk))
        except sqlite3.IntegrityError:
            return False
        self.db.commit()
        return True

    def note_close(self, game_id: int, line: float, side: str, price: float) -> None:
        self.db.execute("UPDATE bets SET closing_price=? WHERE game_id=? AND line=? AND side=? AND result IS NULL",
                        (price, game_id, line, side))
        self.db.commit()

    def settle(self, game_id: int, final_total: int) -> int:
        rows = self.db.execute("SELECT id,side,line,price,stake FROM bets WHERE game_id=? AND result IS NULL",
                               (game_id,)).fetchall()
        for bid, side, line, price, stake in rows:
            won = (final_total > line) if side == "over" else (final_total < line)
            self.db.execute("UPDATE bets SET final_total=?, result=?, pnl=? WHERE id=?",
                            (final_total, "W" if won else "L", stake * (price - 1) if won else -stake, bid))
        self.db.commit()
        return len(rows)

    def settle_vanished(self, game_id: int, min_total: int) -> int:
        """The match left the feed without a final score (about 16 % of matches). An Under whose line is
        already <= the points seen is certainly lost; everything else is voided (stake returned, pnl 0) so
        it neither inflates nor hides results. Voids are counted in ``summary()['void']``."""
        rows = self.db.execute("SELECT id,side,line,stake FROM bets WHERE game_id=? AND result IS NULL",
                               (game_id,)).fetchall()
        for bid, side, line, stake in rows:
            if side == "under" and min_total >= line:
                self.db.execute("UPDATE bets SET final_total=?, result='L', pnl=? WHERE id=?", (min_total, -stake, bid))
            else:
                self.db.execute("UPDATE bets SET result='V', pnl=0 WHERE id=?", (bid,))
        self.db.commit()
        return len(rows)

    def summary(self) -> dict[str, float]:
        n, pnl, stake = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(pnl),0),COALESCE(SUM(stake),0) FROM bets WHERE result IN ('W','L')").fetchone()
        void = self.db.execute("SELECT COUNT(*) FROM bets WHERE result='V'").fetchone()[0]
        clv = self.db.execute(
            "SELECT AVG(price/closing_price-1) FROM bets WHERE closing_price IS NOT NULL").fetchone()[0]
        return {"settled": n, "void": void, "pnl": pnl, "roi": (pnl / stake) if stake else 0.0, "clv": clv or 0.0,
                "bankroll": self.bankroll}
