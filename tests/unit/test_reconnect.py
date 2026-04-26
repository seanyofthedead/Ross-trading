"""Atom 8 — ReconnectingProvider behavior under disconnects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.core.errors import FeedDisconnected
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.reconnect import ReconnectingProvider
from ross_trading.data.types import Bar, FeedGap, Quote, Tape

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence

T0 = datetime(2026, 4, 26, 13, 30, tzinfo=UTC)


def _bar(symbol: str, offset: int) -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        timeframe="M1",
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=1,
    )


def _quote(symbol: str, offset: int) -> Quote:
    return Quote(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        bid=Decimal("1"),
        ask=Decimal("1.01"),
        bid_size=1,
        ask_size=1,
    )


class _FlakyMarket:
    """Emits each scripted event exactly once across all subscribes,
    raising :class:`FeedDisconnected` once after ``drop_after`` events."""

    def __init__(
        self,
        live_quotes: Sequence[Quote],
        live_bars: Sequence[Bar],
        backfill_bars: Sequence[Bar],
        drop_after: int,
    ) -> None:
        self._remaining_quotes = list(live_quotes)
        self._remaining_bars = list(live_bars)
        self._backfill_bars = list(backfill_bars)
        self._drop_after = drop_after
        self._dropped = False
        self.connect_calls = 0

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return frozenset({Timeframe.M1})

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        return

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        del symbols
        emitted_this_call = 0
        while self._remaining_quotes:
            if not self._dropped and emitted_this_call == self._drop_after:
                self._dropped = True
                raise FeedDisconnected("websocket-closed")
            yield self._remaining_quotes.pop(0)
            emitted_this_call += 1

    async def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        del symbols, timeframe
        emitted_this_call = 0
        while self._remaining_bars:
            if not self._dropped and emitted_this_call == self._drop_after:
                self._dropped = True
                raise FeedDisconnected("websocket-closed")
            yield self._remaining_bars.pop(0)
            emitted_this_call += 1

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        del symbols
        if False:
            yield  # pragma: no cover

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        del timeframe
        return [b for b in self._backfill_bars if b.symbol == symbol and start <= b.ts < end]


async def test_reconnect_emits_gap_for_quotes() -> None:
    upstream = _FlakyMarket(
        live_quotes=[_quote("AVTX", 0), _quote("AVTX", 1)],
        live_bars=[],
        backfill_bars=[],
        drop_after=1,
    )
    gaps: list[FeedGap] = []
    clock = VirtualClock(T0)
    wrapper = ReconnectingProvider(upstream, on_gap=gaps.append, clock=clock, max_retries=2)
    await wrapper.connect()
    out = [q async for q in wrapper.subscribe_quotes(["AVTX"])]
    # First quote → drop → reconnect → second quote. Consumer sees a
    # continuous 2-event stream and one FeedGap fires for the window.
    assert [q.ts for q in out] == [T0, T0 + timedelta(seconds=1)]
    assert len(gaps) == 1
    assert gaps[0].symbol is None
    assert gaps[0].start == T0  # last seen ts before the disconnect


async def test_reconnect_backfills_bars_after_disconnect() -> None:
    upstream = _FlakyMarket(
        live_quotes=[],
        live_bars=[_bar("AVTX", 0), _bar("AVTX", 60)],
        backfill_bars=[_bar("AVTX", 30), _bar("AVTX", 45)],
        drop_after=1,
    )
    gaps: list[FeedGap] = []
    clock = VirtualClock(T0 + timedelta(minutes=2))
    wrapper = ReconnectingProvider(upstream, on_gap=gaps.append, clock=clock, max_retries=2)
    await wrapper.connect()
    bars = [b async for b in wrapper.subscribe_bars(["AVTX"], Timeframe.M1)]
    # Live first bar (T0), then drop → reconnect → backfill in (T0, gap_end)
    # → resumed live stream yields the remaining bar (T0+60s).
    assert [b.ts for b in bars] == [
        T0,
        T0 + timedelta(seconds=30),
        T0 + timedelta(seconds=45),
        T0 + timedelta(seconds=60),
    ]
    assert len(gaps) == 1
    assert gaps[0].start == T0
    assert upstream.connect_calls == 2  # initial + one reconnect


async def test_max_retries_propagates_disconnect() -> None:
    class _AlwaysFails:
        @property
        def supported_timeframes(self) -> frozenset[Timeframe]:
            return frozenset()

        async def connect(self) -> None:
            return

        async def disconnect(self) -> None:
            return

        async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
            del symbols
            raise FeedDisconnected("dead")
            yield  # pragma: no cover

        async def subscribe_bars(
            self,
            symbols: Iterable[str],
            timeframe: Timeframe,
        ) -> AsyncIterator[Bar]:
            del symbols, timeframe
            if False:
                yield  # pragma: no cover

        async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
            del symbols
            if False:
                yield  # pragma: no cover

        async def historical_bars(
            self,
            symbol: str,
            start: datetime,
            end: datetime,
            timeframe: Timeframe,
        ) -> Sequence[Bar]:
            del symbol, start, end, timeframe
            return []

    clock = VirtualClock(T0)
    wrapper = ReconnectingProvider(_AlwaysFails(), max_retries=0, clock=clock)
    with pytest.raises(FeedDisconnected):
        async for _ in wrapper.subscribe_quotes(["AVTX"]):
            pass
