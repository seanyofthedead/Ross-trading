"""SQLite-backed historical-data cache.

Three tables:

* ``daily_volumes(symbol, as_of, volume)`` — one row per (symbol, day);
  feeds 30-day relative-volume calculations (architecture §3.1).
* ``daily_emas(symbol, as_of, period, value)`` — one row per
  (symbol, day, period); feeds the daily-strength filter (§3.3).
* ``daily_bars(symbol, as_of, high, low)`` — one row per (symbol, day);
  feeds rolling multi-month resistance and 52-week-low aggregates for
  the breakout / turnaround flags (§3.3, issue #73).

The store is opened in WAL mode so concurrent readers don't block
writers. Decimal values are persisted as strings to preserve precision.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_volumes (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, as_of)
);
CREATE TABLE IF NOT EXISTS daily_emas (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    period INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of, period)
);
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
);
"""


class HistoricalCache:
    """Thin SQLite wrapper. Synchronous — calls are sub-millisecond."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> HistoricalCache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_daily_volume(self, symbol: str, as_of: date, volume: int) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO daily_volumes(symbol, as_of, volume) VALUES (?, ?, ?)",
                (symbol.upper(), as_of.isoformat(), int(volume)),
            )
        self._conn.commit()

    def record_daily_volumes(
        self,
        rows: Iterable[tuple[str, date, int]],
    ) -> None:
        materialized = [(s.upper(), d.isoformat(), int(v)) for s, d, v in rows]
        if not materialized:
            return
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO daily_volumes(symbol, as_of, volume) VALUES (?, ?, ?)",
                materialized,
            )
        self._conn.commit()

    def daily_volume(self, symbol: str, as_of: date) -> int | None:
        with closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT volume FROM daily_volumes WHERE symbol = ? AND as_of = ?",
                (symbol.upper(), as_of.isoformat()),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def avg_daily_volume(
        self,
        symbol: str,
        end_inclusive: date,
        lookback_days: int = 30,
    ) -> Decimal | None:
        if lookback_days <= 0:
            msg = "lookback_days must be positive"
            raise ValueError(msg)
        with closing(self._conn.cursor()) as cur:
            row = cur.execute(
                """
                SELECT AVG(volume), COUNT(*)
                FROM (
                    SELECT volume FROM daily_volumes
                    WHERE symbol = ? AND as_of <= ?
                    ORDER BY as_of DESC
                    LIMIT ?
                )
                """,
                (symbol.upper(), end_inclusive.isoformat(), lookback_days),
            ).fetchone()
        if row is None or row[1] == 0:
            return None
        return Decimal(str(row[0]))

    def relative_volume(
        self,
        symbol: str,
        as_of: date,
        today_volume: int,
        lookback_days: int = 30,
    ) -> Decimal | None:
        # Average over the trailing window *before* today.
        prior_day = date.fromordinal(as_of.toordinal() - 1)
        avg = self.avg_daily_volume(symbol, prior_day, lookback_days)
        if avg is None or avg == 0:
            return None
        return Decimal(today_volume) / avg

    def record_ema(self, symbol: str, as_of: date, period: int, value: Decimal) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO daily_emas(symbol, as_of, period, value)
                VALUES (?, ?, ?, ?)
                """,
                (symbol.upper(), as_of.isoformat(), int(period), str(value)),
            )
        self._conn.commit()

    def record_emas(
        self,
        rows: Iterable[tuple[str, date, int, Decimal]],
    ) -> None:
        materialized = [
            (s.upper(), d.isoformat(), int(p), str(v)) for s, d, p, v in rows
        ]
        if not materialized:
            return
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                """
                INSERT OR REPLACE INTO daily_emas(symbol, as_of, period, value)
                VALUES (?, ?, ?, ?)
                """,
                materialized,
            )
        self._conn.commit()

    def ema(self, symbol: str, as_of: date, period: int) -> Decimal | None:
        with closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT value FROM daily_emas WHERE symbol = ? AND as_of = ? AND period = ?",
                (symbol.upper(), as_of.isoformat(), int(period)),
            ).fetchone()
        return Decimal(row[0]) if row is not None else None

    def record_daily_bar(
        self,
        symbol: str,
        as_of: date,
        high: Decimal,
        low: Decimal,
    ) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO daily_bars(symbol, as_of, high, low)
                VALUES (?, ?, ?, ?)
                """,
                (symbol.upper(), as_of.isoformat(), str(high), str(low)),
            )
        self._conn.commit()

    def record_daily_bars(
        self,
        rows: Iterable[tuple[str, date, Decimal, Decimal]],
    ) -> None:
        materialized = [
            (s.upper(), d.isoformat(), str(h), str(low)) for s, d, h, low in rows
        ]
        if not materialized:
            return
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                """
                INSERT OR REPLACE INTO daily_bars(symbol, as_of, high, low)
                VALUES (?, ?, ?, ?)
                """,
                materialized,
            )
        self._conn.commit()

    def max_high(
        self,
        symbol: str,
        end_inclusive: date,
        lookback_days: int,
    ) -> Decimal | None:
        """Highest ``high`` over the last ``lookback_days`` rows ending at ``end_inclusive``.

        Returns ``None`` when no rows fall in the window. Trailing-window
        semantics match :meth:`avg_daily_volume` — callers exclude today
        themselves by passing ``prior_day = end - 1`` if needed.
        """
        return self._extreme_high_low(symbol, end_inclusive, lookback_days, kind="max")

    def min_low(
        self,
        symbol: str,
        end_inclusive: date,
        lookback_days: int,
    ) -> Decimal | None:
        """Lowest ``low`` over the last ``lookback_days`` rows ending at ``end_inclusive``.

        Same windowing semantics as :meth:`max_high`.
        """
        return self._extreme_high_low(symbol, end_inclusive, lookback_days, kind="min")

    def _extreme_high_low(
        self,
        symbol: str,
        end_inclusive: date,
        lookback_days: int,
        *,
        kind: str,
    ) -> Decimal | None:
        if lookback_days <= 0:
            msg = "lookback_days must be positive"
            raise ValueError(msg)
        column = "high" if kind == "max" else "low"
        order = "DESC" if kind == "max" else "ASC"
        with closing(self._conn.cursor()) as cur:
            row = cur.execute(
                f"""
                SELECT {column}
                FROM (
                    SELECT {column} FROM daily_bars
                    WHERE symbol = ? AND as_of <= ?
                    ORDER BY as_of DESC
                    LIMIT ?
                )
                ORDER BY CAST({column} AS REAL) {order}
                LIMIT 1
                """,  # noqa: S608 — column/order are class-internal literals, not user input.
                (symbol.upper(), end_inclusive.isoformat(), lookback_days),
            ).fetchone()
        return Decimal(row[0]) if row is not None else None
