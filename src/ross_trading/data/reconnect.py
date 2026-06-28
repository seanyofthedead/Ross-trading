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

If reconnection itself raises :class:`FeedDisconnected` (e.g. the
vendor is still down after the backoff wait), it propagates out and
terminates the subscription — ``max_retries`` only governs the live
subscription's retry budget, not the connect-on-recovery step.

Related: risk issue #21.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from typing import TYPE_CHECKING, TypeVar

from ross_trading.core.clock import Clock, RealClock
from ross_trading.core.errors import FeedDisconnected
from ross_trading.data.types import Bar, FeedGap, Halt, Quote, Tape

if TYPE_CHECKING:
    from datetime import datetime

    from ross_trading.data.market_feed import MarketDataProvider, Timeframe

GapCallback = Callable[[FeedGap], None]
_T = TypeVar("_T", Quote, Tape)


def _noop_on_gap(_: FeedGap) -> None:
    return


INITIAL_BACKOFF = 1.0
DEFAULT_MAX_BACKOFF = 30.0


class _SeqTracker:
    """Per-symbol sequence watermark for one channel.

    Surfaces a :class:`FeedGap` when a forward jump in ``seq`` proves the
    vendor silently dropped one or more messages -- the hole a
    socket-lifecycle gap detector can't see. ``seq`` is only monotonic
    per ``(symbol, channel)``, so the tracker is scoped to a single
    channel and keyed by symbol.

    Streams that don't carry sequence numbers leave ``seq`` at its
    default ``0``; the tracker then never advances past ``0`` and never
    fires, so unsequenced feeds behave exactly as before.
    """

    __slots__ = ("_channel", "_last_seq", "_last_ts")

    def __init__(self, channel: str) -> None:
        self._channel = channel
        self._last_seq: dict[str, int] = {}
        self._last_ts: dict[str, datetime] = {}

    def observe(self, symbol: str, seq: int, exchange_ts: datetime) -> FeedGap | None:
        prev_seq = self._last_seq.get(symbol)
        gap: FeedGap | None = None
        if prev_seq is not None and seq > prev_seq + 1:
            prev_ts = self._last_ts.get(symbol, exchange_ts)
            missed = seq - prev_seq - 1
            gap = FeedGap(
                symbol=symbol,
                start=prev_ts,
                end=exchange_ts,
                reason=(
                    f"seq discontinuity on {self._channel}: "
                    f"expected {prev_seq + 1}, got {seq} ({missed} missed)"
                ),
            )
        # Advance only on forward progress so a late/duplicate lower-seq
        # arrival can't rewind the watermark and re-flag the same hole.
        if prev_seq is None or seq > prev_seq:
            self._last_seq[symbol] = seq
            self._last_ts[symbol] = exchange_ts
        return gap


class ReconnectingProvider:
    """Wrap an upstream :class:`MarketDataProvider` with retry + backfill."""

    def __init__(
        self,
        upstream: MarketDataProvider,
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
        return self._upstream.supported_timeframes

    async def connect(self) -> None:
        await self._upstream.connect()

    async def disconnect(self) -> None:
        await self._upstream.disconnect()

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        symbols_list = list(symbols)

        def factory() -> AsyncIterator[Quote]:
            return self._upstream.subscribe_quotes(symbols_list)

        async for quote in self._stream_with_retry(
            factory, ts_of=lambda q: q.ts, channel="quote",
        ):
            yield quote

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        symbols_list = list(symbols)

        def factory() -> AsyncIterator[Tape]:
            return self._upstream.subscribe_tape(symbols_list)

        async for tape in self._stream_with_retry(
            factory, ts_of=lambda t: t.ts, channel="tape",
        ):
            yield tape

    async def subscribe_halts(self, symbols: Iterable[str]) -> AsyncIterator[Halt]:
        """Pass typed halt/resume events through from the upstream feed.

        Halts are propagated, not gap-detected: a venue halt is a typed
        market event, distinct from a dropped message, and the downstream
        consumer must treat it as such (do not bridge a resume off a
        pre-halt ``last``).
        """
        symbols_list = list(symbols)
        async for halt in self._upstream.subscribe_halts(symbols_list):
            yield halt

    async def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        symbols_list = list(symbols)
        tracker = _SeqTracker(f"bar:{timeframe.value}")
        last_ts: dict[str, datetime] = {}
        backoff = INITIAL_BACKOFF
        retries = 0
        while True:
            try:
                async for bar in self._upstream.subscribe_bars(symbols_list, timeframe):
                    seq_gap = tracker.observe(bar.symbol, bar.seq, bar.exchange_ts)
                    if seq_gap is not None:
                        self._on_gap(seq_gap)
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
                async for backfilled in self._backfill(
                    symbols_list, last_ts, timeframe, gap_end
                ):
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
        return await self._upstream.historical_bars(symbol, start, end, timeframe)

    async def _stream_with_retry(
        self,
        factory: Callable[[], AsyncIterator[_T]],
        ts_of: Callable[[_T], datetime],
        channel: str,
    ) -> AsyncIterator[_T]:
        """Shared retry/gap-callback loop for non-backfilling streams.

        Fires ``on_gap`` twice over for two different kinds of loss: a
        per-``(symbol, channel)`` sequence discontinuity (a silent vendor
        drop, detected while the socket is still up) and a
        :class:`FeedDisconnected` (the socket lifecycle gap).
        """
        tracker = _SeqTracker(channel)
        last_ts: datetime | None = None
        backoff = INITIAL_BACKOFF
        retries = 0
        while True:
            try:
                async for event in factory():
                    seq_gap = tracker.observe(event.symbol, event.seq, event.exchange_ts)
                    if seq_gap is not None:
                        self._on_gap(seq_gap)
                    last_ts = ts_of(event)
                    backoff = INITIAL_BACKOFF
                    yield event
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

    async def _reconnect(self, backoff: float) -> None:
        await self._clock.sleep(backoff)
        # If connect() itself raises FeedDisconnected, the exception
        # propagates out of the active `except FeedDisconnected` block
        # in the caller, ending the retry loop. Vendor implementations
        # that want extended retry of connect() should layer their own
        # backoff inside connect(), not rely on this wrapper.
        await self._upstream.connect()

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
            bars = await self._upstream.historical_bars(symbol, anchor, gap_end, timeframe)
            for bar in bars:
                if bar.ts > anchor:
                    last_ts[symbol] = bar.ts
                    yield bar

