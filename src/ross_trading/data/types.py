"""Frozen value types emitted by data providers.

Prices are ``Decimal`` so equality and arithmetic are exact (bid/ask
spreads of $0.01 must not drift through float rounding). Timestamps
are tz-aware UTC; provider implementations are responsible for
converting vendor-native zones (often ET) before emitting.

Sequencing & timestamps (Wave 0 ingestion contract). Market-data
events (:class:`Quote`, :class:`Bar`, :class:`Tape`, :class:`Halt`,
:class:`Correction`) carry a per-``(symbol, channel)`` monotonic vendor
``seq`` plus a three-way timestamp split:

- ``exchange_ts`` -- when the participant/exchange stamped the event.
  **All as-of selection keys off this** (ordered by ``(exchange_ts,
  seq)``). The legacy ``ts`` property aliases it.
- ``vendor_ts`` -- when the vendor sent the message to us.
- ``ingest_ts`` -- when we received it locally. Staleness/watermark
  logic keys off this.

``seq`` is only monotonic *within* a ``(symbol, channel)`` stream
("channel" being quotes / tape / bars-at-a-timeframe), so dedup and
ordering must scope on the channel they were read from -- de-duping on
bare ``seq`` would discard valid events when two streams reuse a
number. ``vendor_ts`` and ``ingest_ts`` default to ``exchange_ts`` when
not supplied, so callers that only have one clock stay terse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal
    from typing import Literal


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

# Sentinel meaning "derive this timestamp from exchange_ts". A unique
# object so the identity check in ``_fill_derived_ts`` can never collide
# with a real, caller-supplied datetime that merely compares equal.
_DERIVE: datetime = datetime(1, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Quote:
    """Top-of-book quote (NBBO)."""

    symbol: str
    exchange_ts: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    seq: int = 0
    vendor_ts: datetime = _DERIVE
    ingest_ts: datetime = _DERIVE

    def __post_init__(self) -> None:
        _require_utc(self.exchange_ts, "Quote.exchange_ts")
        _fill_derived_ts(self, "vendor_ts", self.exchange_ts, "Quote.vendor_ts")
        _fill_derived_ts(self, "ingest_ts", self.exchange_ts, "Quote.ingest_ts")

    @property
    def ts(self) -> datetime:
        """As-of timestamp (alias of :attr:`exchange_ts`)."""
        return self.exchange_ts


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar at a fixed timeframe.

    ``exchange_ts`` is the bar's *open* time (left edge of the interval).
    """

    symbol: str
    exchange_ts: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    seq: int = 0
    vendor_ts: datetime = _DERIVE
    ingest_ts: datetime = _DERIVE

    def __post_init__(self) -> None:
        _require_utc(self.exchange_ts, "Bar.exchange_ts")
        _fill_derived_ts(self, "vendor_ts", self.exchange_ts, "Bar.vendor_ts")
        _fill_derived_ts(self, "ingest_ts", self.exchange_ts, "Bar.ingest_ts")
        if self.timeframe not in _VALID_TIMEFRAMES:
            msg = (
                f"Bar.timeframe must be a Timeframe value "
                f"(one of {sorted(_VALID_TIMEFRAMES)}), got {self.timeframe!r}"
            )
            raise ValueError(msg)

    @property
    def ts(self) -> datetime:
        """Bar open time (alias of :attr:`exchange_ts`)."""
        return self.exchange_ts


@dataclass(frozen=True, slots=True)
class Tape:
    """Single trade execution as printed on the consolidated tape."""

    symbol: str
    exchange_ts: datetime
    price: Decimal
    size: int
    side: Side = Side.UNKNOWN
    seq: int = 0
    vendor_ts: datetime = _DERIVE
    ingest_ts: datetime = _DERIVE

    def __post_init__(self) -> None:
        _require_utc(self.exchange_ts, "Tape.exchange_ts")
        _fill_derived_ts(self, "vendor_ts", self.exchange_ts, "Tape.vendor_ts")
        _fill_derived_ts(self, "ingest_ts", self.exchange_ts, "Tape.ingest_ts")

    @property
    def ts(self) -> datetime:
        """As-of timestamp (alias of :attr:`exchange_ts`)."""
        return self.exchange_ts


@dataclass(frozen=True, slots=True)
class Halt:
    """Trading-halt / resume event for a symbol.

    A typed event distinct from :class:`FeedGap`: a halt is the venue
    deliberately suspending trading, not the feed dropping data. Firing
    an entry on the resume off a stale pre-halt ``last`` is a real-money
    error, so the assembler/loop treat ``halted`` as "do not act on the
    pre-halt quote" rather than as missing data.
    """

    symbol: str
    state: Literal["halted", "resumed"]
    seq: int
    exchange_ts: datetime
    reason_code: str | None = None
    ingest_ts: datetime = _DERIVE

    def __post_init__(self) -> None:
        _require_utc(self.exchange_ts, "Halt.exchange_ts")
        _fill_derived_ts(self, "ingest_ts", self.exchange_ts, "Halt.ingest_ts")

    @property
    def ts(self) -> datetime:
        """As-of timestamp (alias of :attr:`exchange_ts`)."""
        return self.exchange_ts


@dataclass(frozen=True, slots=True)
class Correction:
    """Append-only amendment to a previously printed trade.

    Also covers busts: a busted print is a correction whose ``new_size``
    is ``0`` (the trade never counted). Corrections never overwrite the
    original print in the recording -- they are stored as a separate,
    append-only event referencing ``corrects_seq`` so the audit trail
    (original + amendment) survives, and rel-volume can be recomputed
    deterministically in replay.
    """

    symbol: str
    corrects_seq: int
    new_size: int | None
    new_price: Decimal | None
    seq: int
    exchange_ts: datetime
    ingest_ts: datetime = _DERIVE

    def __post_init__(self) -> None:
        _require_utc(self.exchange_ts, "Correction.exchange_ts")
        _fill_derived_ts(self, "ingest_ts", self.exchange_ts, "Correction.ingest_ts")

    @property
    def ts(self) -> datetime:
        """As-of timestamp (alias of :attr:`exchange_ts`)."""
        return self.exchange_ts


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


def _fill_derived_ts(obj: object, attr: str, source_ts: datetime, field: str) -> None:
    """Resolve a derived timestamp slot on a frozen value object.

    If the slot still holds the :data:`_DERIVE` sentinel, copy
    ``source_ts`` into it; otherwise validate the caller-supplied value
    is tz-aware UTC. Uses ``object.__setattr__`` because the dataclass is
    frozen.
    """
    current = getattr(obj, attr)
    if current is _DERIVE:
        object.__setattr__(obj, attr, source_ts)
    else:
        _require_utc(current, field)


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())
