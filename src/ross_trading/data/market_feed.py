"""Market data provider interface.

Concrete vendor implementations live under ``data/providers/``.
Anything that streams quotes, bars, or tape prints — including
recordings replayed from disk — implements :class:`MarketDataProvider`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, Quote, Tape


class Timeframe(StrEnum):
    """Bar aggregation periods.

    Not every provider supports every timeframe (10-second bars are
    a known gap — see decision issue #14). Providers should expose
    ``supported_timeframes`` so callers can fail fast.
    """

    S1 = "S1"
    S10 = "S10"
    M1 = "M1"
    M5 = "M5"
    D1 = "D1"


@runtime_checkable
class MarketDataProvider(Protocol):
    """Streaming + historical access to NBBO quotes, bars, and tape prints."""

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]: ...

    def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]: ...

    def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]: ...

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]: ...
