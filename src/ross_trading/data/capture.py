"""Live capture composition (#87).

Vendor-agnostic glue that runs a live :class:`MarketDataProvider`
behind :class:`ReconnectingProvider` and pipes every event into a
:class:`FeedRecorder`. The composition closes the production-side half
of the FEED_GAP loop that PR #86 opened on the replay side: the
recorder's ``record_feed_gap`` is wired as the reconnect ``on_gap``
callback, so reconnect-induced gap windows persist to disk alongside
the rest of the streams.

Vendor-neutral: the composition only depends on the existing
``MarketDataProvider`` / ``NewsProvider`` / ``FloatReferenceProvider``
Protocols, so it can land and be tested with fakes today without
waiting on the #11 vendor decision.

Out of scope: live trading composition (recorder + scanner + journal in
one process), CLI daemon wrapper, recording rotation/archival policy.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ross_trading.core.clock import Clock, RealClock
from ross_trading.core.errors import FeedError
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.reconnect import DEFAULT_MAX_BACKOFF, ReconnectingProvider
from ross_trading.data.recorder import FeedRecorder

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date
    from pathlib import Path

    from ross_trading.data.float_reference import FloatReferenceProvider
    from ross_trading.data.market_feed import MarketDataProvider
    from ross_trading.data.news_feed import NewsProvider


_DEFAULT_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.M1, Timeframe.D1)


async def capture_session(
    *,
    upstream_market_data: MarketDataProvider,
    upstream_news: NewsProvider | None,
    upstream_float: FloatReferenceProvider | None,
    universe: Iterable[str],
    output_dir: Path,
    timeframes: Iterable[Timeframe] = _DEFAULT_TIMEFRAMES,
    as_of: date | None = None,
    clock: Clock | None = None,
    max_backoff: float = DEFAULT_MAX_BACKOFF,
) -> None:
    """Run a recording session for ``universe`` until every upstream stream ends.

    The market provider is wrapped in :class:`ReconnectingProvider` with
    ``on_gap=recorder.record_feed_gap``, so disconnect/reconnect cycles
    persist a ``FeedGap`` row to disk alongside the live event streams.
    Backfilled bars produced by the reconnect wrapper after recovery are
    written through the same ``record_bar`` path, leaving the recording
    internally consistent: the gap window contains both the marker and
    the recovered bars.

    Termination: the composition runs until every ``subscribe_*`` stream
    is exhausted. Tests therefore drive it with finite-fake providers;
    production deployments cancel the surrounding task to stop.

    Errors: any exception other than the reconnect-handled
    :class:`FeedDisconnected` propagates out so the caller can fail loudly
    instead of silently losing the recording session. The recorder is
    flushed and closed in all paths via the async-context exit.
    """
    symbols = tuple(s.upper() for s in universe)
    timeframe_list = tuple(timeframes)
    _validate_timeframes(upstream_market_data, timeframe_list)

    real_clock: Clock = clock if clock is not None else RealClock()

    async with FeedRecorder(output_dir, clock=real_clock) as recorder:
        market = ReconnectingProvider(
            upstream_market_data,
            on_gap=recorder.record_feed_gap,
            max_backoff=max_backoff,
            clock=real_clock,
        )
        await market.connect()
        # ``news_connected`` guards the symmetric cleanup case: if
        # ``upstream_news.connect()`` raises, we must still disconnect
        # ``market`` (so the live socket/session doesn't leak on a failed
        # startup path) but must NOT call ``disconnect`` on the
        # never-connected news provider. Disconnect order is intentional:
        # news first (reverse of connect order), then market.
        news_connected = False
        try:
            if upstream_news is not None:
                await upstream_news.connect()
                news_connected = True
            await _capture_floats(upstream_float, recorder, symbols, as_of, real_clock)
            # Trade corrections/busts are intentionally NOT captured here.
            # `MarketDataProvider` has no correction subscription: real feeds
            # deliver corrections flagged on the trade stream (vendor-specific),
            # not a separate channel, so the port shape isn't knowable until a
            # vendor is chosen. Live correction-sourcing lands with vendor
            # integration (Wave 5/6); replay already consumes recorded
            # corrections, and `FeedRecorder.record_correction` is ready for
            # that wiring. See plans/waves/HANDOFF.md ("explicitly deferred").
            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(_capture_quotes(market, recorder, symbols)),
                asyncio.create_task(_capture_tape(market, recorder, symbols)),
                asyncio.create_task(_capture_halts(market, recorder, symbols)),
            ]
            for tf in timeframe_list:
                tasks.append(
                    asyncio.create_task(_capture_bars(market, recorder, symbols, tf))
                )
            if upstream_news is not None:
                tasks.append(
                    asyncio.create_task(
                        _capture_headlines(upstream_news, recorder, symbols)
                    )
                )
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                # gather() doesn't auto-cancel siblings when one task fails.
                # We cancel the rest and drain them so the original error
                # surfaces unwrapped (TaskGroup would wrap it in
                # ExceptionGroup) and no task is left orphaned.
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        finally:
            if news_connected:
                assert upstream_news is not None  # noqa: S101  # guarded by flag
                await upstream_news.disconnect()
            await market.disconnect()


def _validate_timeframes(
    provider: MarketDataProvider,
    timeframes: tuple[Timeframe, ...],
) -> None:
    supported = provider.supported_timeframes
    missing = [tf for tf in timeframes if tf not in supported]
    if missing:
        msg = (
            f"requested timeframes not supported by upstream: {missing!r} "
            f"(provider supports {sorted(s.value for s in supported)!r})"
        )
        raise ValueError(msg)


async def _capture_quotes(
    market: ReconnectingProvider,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
) -> None:
    async for quote in market.subscribe_quotes(symbols):
        recorder.record_quote(quote)


async def _capture_tape(
    market: ReconnectingProvider,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
) -> None:
    async for trade in market.subscribe_tape(symbols):
        recorder.record_tape(trade)


async def _capture_halts(
    market: ReconnectingProvider,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
) -> None:
    """Persist typed halt/resume events so replay can suppress halted symbols.

    Without this, a live recording would drop venue halts entirely and
    replay could fire entries off a stale pre-halt quote on resume.
    """
    async for halt in market.subscribe_halts(symbols):
        recorder.record_halt(halt)


async def _capture_bars(
    market: ReconnectingProvider,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
    timeframe: Timeframe,
) -> None:
    async for bar in market.subscribe_bars(symbols, timeframe):
        recorder.record_bar(bar)


async def _capture_headlines(
    news: NewsProvider,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
) -> None:
    async for headline in news.subscribe_headlines(symbols):
        recorder.record_headline(headline)


async def _capture_floats(
    float_provider: FloatReferenceProvider | None,
    recorder: FeedRecorder,
    symbols: tuple[str, ...],
    as_of: date | None,
    clock: Clock,
) -> None:
    """Snapshot the daily float reference for each ticker once at session start.

    Float is a daily, slow-moving reference -- one fetch per ticker per
    session is sufficient and matches the cadence the cached provider
    already enforces. Per-ticker :class:`FeedError` (missing record, vendor
    rate-limit on that ticker) is swallowed so a single bad lookup doesn't
    kill the rest of the recording; non-FeedError exceptions (e.g.
    programming errors or transport failures the provider doesn't wrap)
    still propagate.
    """
    if float_provider is None:
        return
    snapshot_day = as_of if as_of is not None else clock.now().date()
    for ticker in symbols:
        try:
            record = await float_provider.get_float(ticker, snapshot_day)
        except FeedError:
            # Single-ticker vendor gap; recording the rest of the session
            # is more valuable than failing the whole capture for one
            # missing record.
            continue
        recorder.record_float(record)
