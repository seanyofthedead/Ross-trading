"""Scripted MarketDataProvider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.data.market_feed import Timeframe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, Quote, Tape


class FakeMarketDataProvider:
    """Replays a fixed list of events for a configurable symbol set.

    Events are emitted in declaration order. ``connect`` and
    ``disconnect`` are idempotent; the provider tracks call counts so
    tests can assert lifecycle. ``historical_bars`` returns the subset
    of pre-recorded bars whose timestamp falls in [start, end).
    """

    def __init__(
        self,
        *,
        quotes: Sequence[Quote] = (),
        bars: Sequence[Bar] = (),
        tape: Sequence[Tape] = (),
        timeframes: Iterable[Timeframe] = (Timeframe.M1, Timeframe.D1),
    ) -> None:
        self._quotes = list(quotes)
        self._bars = list(bars)
        self._tape = list(tape)
        self._timeframes = frozenset(timeframes)
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return self._timeframes

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        wanted = {s.upper() for s in symbols}
        for quote in self._quotes:
            if quote.symbol.upper() in wanted:
                yield quote

    async def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        wanted = {s.upper() for s in symbols}
        for bar in self._bars:
            if bar.symbol.upper() in wanted and bar.timeframe == timeframe.value:
                yield bar

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        wanted = {s.upper() for s in symbols}
        for trade in self._tape:
            if trade.symbol.upper() in wanted:
                yield trade

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        upper = symbol.upper()
        return [
            b
            for b in self._bars
            if b.symbol.upper() == upper
            and b.timeframe == timeframe.value
            and start <= b.ts < end
        ]
