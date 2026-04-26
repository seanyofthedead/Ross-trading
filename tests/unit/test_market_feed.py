"""Atom 2 — MarketDataProvider protocol contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.data.market_feed import MarketDataProvider, Timeframe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, Quote, Tape


class _Stub:
    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return frozenset({Timeframe.M1})

    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    async def _empty_quotes(self) -> AsyncIterator[Quote]:
        if False:
            yield  # pragma: no cover

    def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        del symbols
        return self._empty_quotes()

    async def _empty_bars(self) -> AsyncIterator[Bar]:
        if False:
            yield  # pragma: no cover

    def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        del symbols, timeframe
        return self._empty_bars()

    async def _empty_tape(self) -> AsyncIterator[Tape]:
        if False:
            yield  # pragma: no cover

    def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        del symbols
        return self._empty_tape()

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        del symbol, start, end, timeframe
        return []


def test_stub_satisfies_protocol() -> None:
    assert isinstance(_Stub(), MarketDataProvider)


def test_timeframe_values_are_stable_strings() -> None:
    assert Timeframe.S1.value == "S1"
    assert Timeframe.S10.value == "S10"
    assert Timeframe.M1.value == "M1"
    assert Timeframe.M5.value == "M5"
    assert Timeframe.D1.value == "D1"
