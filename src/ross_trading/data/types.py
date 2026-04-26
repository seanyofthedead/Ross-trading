"""Frozen value types emitted by data providers.

Prices are ``Decimal`` so equality and arithmetic are exact (bid/ask
spreads of $0.01 must not drift through float rounding). Timestamps
are tz-aware UTC; provider implementations are responsible for
converting vendor-native zones (often ET) before emitting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal


class Side(StrEnum):
    """Trade side as recorded on the tape."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class Timeframe(StrEnum):
    """Bar aggregation periods.

    Not every provider supports every timeframe (10-second bars are a
    known gap — see decision issue #14). Providers should expose
    ``supported_timeframes`` so callers can fail fast.
    """

    S1 = "S1"
    S10 = "S10"
    M1 = "M1"
    M5 = "M5"
    D1 = "D1"


_VALID_TIMEFRAMES: frozenset[str] = frozenset(t.value for t in Timeframe)


@dataclass(frozen=True, slots=True)
class Quote:
    """Top-of-book quote (NBBO)."""

    symbol: str
    ts: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int

    def __post_init__(self) -> None:
        _require_utc(self.ts, "Quote.ts")


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar at a fixed timeframe.

    ``ts`` is the bar's *open* time (left edge of the interval).
    """

    symbol: str
    ts: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        _require_utc(self.ts, "Bar.ts")
        if self.timeframe not in _VALID_TIMEFRAMES:
            msg = (
                f"Bar.timeframe must be a Timeframe value "
                f"(one of {sorted(_VALID_TIMEFRAMES)}), got {self.timeframe!r}"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Tape:
    """Single trade execution as printed on the consolidated tape."""

    symbol: str
    ts: datetime
    price: Decimal
    size: int
    side: Side = Side.UNKNOWN

    def __post_init__(self) -> None:
        _require_utc(self.ts, "Tape.ts")


@dataclass(frozen=True, slots=True)
class Headline:
    """News headline event.

    ``dedup_key`` is the canonical identity used by ``HeadlineDeduper`` —
    same key within the dedup window means "same story", even if two
    sources report it.
    """

    ticker: str
    ts: datetime
    source: str
    title: str
    url: str | None = None
    body: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.ts, "Headline.ts")

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        return (self.source, _normalize_title(self.title), self.ticker.upper())


@dataclass(frozen=True, slots=True)
class FloatRecord:
    """Daily float-reference snapshot for a ticker."""

    ticker: str
    as_of: date
    float_shares: int
    shares_outstanding: int
    source: str


@dataclass(frozen=True, slots=True)
class FeedGap:
    """Emitted by the reconnect wrapper to mark a window of missed events."""

    symbol: str | None
    start: datetime
    end: datetime
    reason: str

    def __post_init__(self) -> None:
        _require_utc(self.start, "FeedGap.start")
        _require_utc(self.end, "FeedGap.end")


def _require_utc(ts: datetime, field: str) -> None:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) != UTC.utcoffset(ts):
        msg = f"{field} must be tz-aware UTC, got {ts.tzinfo!r}"
        raise ValueError(msg)


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())
