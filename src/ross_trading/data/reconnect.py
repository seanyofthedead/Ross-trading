"""Reconnect / backfill wrapper for :class:`MarketDataProvider`.

If an upstream subscription raises :class:`FeedDisconnected`, this
wrapper:

1. sleeps with exponential backoff (capped at ``max_backoff``);
2. calls ``upstream.connect()`` and ``subscribe_*`` again;
3. for **bar** subscriptions, calls ``historical_bars`` to backfill
   the gap window before resuming the live stream;
4. fires the ``on_gap`` callback with a :class:`FeedGap` event so
   the journal (or a regression test) can record what was missed.

Quote and tape subscriptions don't backfill — historical retrieval
of NBBO and individual prints is rarely available and rarely useful;
the gap event is sufficient.

Related: risk issue #21.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from typing import TYPE_CHECKING

from ross_trading.core.clock import Clock, RealClock
from ross_trading.core.errors import FeedDisconnected
from ross_trading.data.types import Bar, FeedGap, Quote, Tape

if TYPE_CHECKING:
    from datetime import datetime

    from ross_trading.data.market_feed import Timeframe

GapCallback = Callable[[FeedGap], None]


def _noop_on_gap(_: FeedGap) -> None:
    return


INITIAL_BACKOFF = 1.0
DEFAULT_MAX_BACKOFF = 30.0


class ReconnectingProvider:
    """Wrap an upstream :class:`MarketDataProvider` with retry + backfill."""

    def __init__(
        self,
        upstream: object,  # duck-typed MarketDataProvider
        *,
        on_gap: GapCallback | None = None,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        max_retries: int | None = None,
        clock: Clock | None = None,
    ) -> None:
        if max_backoff <= 0:
            msg = "max_backoff must be positive"
            raise ValueError(msg)
        self._upstream = upstream
        self._on_gap: GapCallback = on_gap if on_gap is not None else _noop_on_gap
        self._max_backoff = max_backoff
        self._max_retries = max_retries
        self._clock: Clock = clock if clock is not None else RealClock()

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return self._upstream.supported_timeframes  # type: ignore[no-any-return,attr-defined]

    async def connect(self) -> None:
        await self._upstream.connect()  # type: ignore[attr-defined]

    async def disconnect(self) -> None:
        await self._upstream.disconnect()  # type: ignore[attr-defined]

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        symbols_list = list(symbols)
        last_ts: datetime | None = None
        backoff = INITIAL_BACKOFF
        retries = 0
        while True:
            try:
                async for quote in self._upstream.subscribe_quotes(symbols_list):  # type: ignore[attr-defined]
                    last_ts = quote.ts
                    backoff = INITIAL_BACKOFF
                    yield quote
                return
            except FeedDisconnected as exc:
                if self._max_retries is not None and retries >= self._max_retries:
                    raise
                retries += 1
                gap_start = last_ts or self._clock.now()
                await self._reconnect(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                self._on_gap(
                    FeedGap(
                        symbol=None,
                        start=gap_start,
                        end=self._clock.now(),
                        reason=str(exc) or type(exc).__name__,
                    )
                )

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        symbols_list = list(symbols)
        last_ts: datetime | None = None
        backoff = INITIAL_BACKOFF
        retries = 0
        while True:
            try:
                async for tape in self._upstream.subscribe_tape(symbols_list):  # type: ignore[attr-defined]
                    last_ts = tape.ts
                    backoff = INITIAL_BACKOFF
                    yield tape
                return
            except FeedDisconnected as exc:
                if self._max_retries is not None and retries >= self._max_retries:
                    raise
                retries += 1
                gap_start = last_ts or self._clock.now()
                await self._reconnect(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                self._on_gap(
                    FeedGap(
                        symbol=None,
                        start=gap_start,
                        end=self._clock.now(),
                        reason=str(exc) or type(exc).__name__,
                    )
                )

    async def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        symbols_list = list(symbols)
        last_ts: dict[str, datetime] = {}
        backoff = INITIAL_BACKOFF
        retries = 0
        while True:
            try:
                async for bar in self._upstream.subscribe_bars(symbols_list, timeframe):  # type: ignore[attr-defined]
                    last_ts[bar.symbol] = bar.ts
                    backoff = INITIAL_BACKOFF
                    yield bar
                return
            except FeedDisconnected as exc:
                if self._max_retries is not None and retries >= self._max_retries:
                    raise
                retries += 1
                await self._reconnect(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                gap_end = self._clock.now()
                gap_start = min(last_ts.values()) if last_ts else gap_end
                async for backfilled in self._backfill(symbols_list, last_ts, timeframe, gap_end):
                    yield backfilled
                self._on_gap(
                    FeedGap(
                        symbol=None,
                        start=gap_start,
                        end=gap_end,
                        reason=str(exc) or type(exc).__name__,
                    )
                )

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        return await self._upstream.historical_bars(symbol, start, end, timeframe)  # type: ignore[attr-defined,no-any-return]

    async def _reconnect(self, backoff: float) -> None:
        await self._clock.sleep(backoff)
        try:
            await self._upstream.connect()  # type: ignore[attr-defined]
        except FeedDisconnected:
            # Caller's outer loop will retry — propagate to next iteration
            # by re-raising so the surrounding try/except picks it up.
            raise

    async def _backfill(
        self,
        symbols: Iterable[str],
        last_ts: dict[str, datetime],
        timeframe: Timeframe,
        gap_end: datetime,
    ) -> AsyncIterator[Bar]:
        for symbol in symbols:
            anchor = last_ts.get(symbol)
            if anchor is None:
                continue
            bars = await self._upstream.historical_bars(  # type: ignore[attr-defined]
                symbol, anchor, gap_end, timeframe
            )
            for bar in bars:
                if bar.ts > anchor:
                    last_ts[symbol] = bar.ts
                    yield bar
