"""Atom 15 — Phase 1 exit criterion.

> "Can replay a historical trading day from disk through the data
>  layer with realistic timing." — issue #2

Records a synthetic trading day (quotes, bars, headlines, float)
through :class:`FeedRecorder`, then plays it back through
:class:`ReplayProvider` and verifies every stream survives the
roundtrip and that scanner-shaped queries (relative volume + EMA)
work against the replayed data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.clock import RealClock, VirtualClock
from ross_trading.data.cache import HistoricalCache
from ross_trading.data.float_reference import CachedFloatReference
from ross_trading.data.historical import (
    populate_daily_volumes,
    precompute_daily_emas,
)
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.providers.replay import ReplayMode, ReplayProvider
from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import Bar, FloatRecord, Headline, Quote

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


# Pre-market start of a trading day in Cameron's Trading Plan window.
DAY = date(2026, 4, 26)
SESSION_START = datetime(2026, 4, 26, 11, 0, tzinfo=UTC)  # 7:00 AM ET


def _quote(offset_s: int, bid: str, ask: str) -> Quote:
    return Quote(
        symbol="AVTX",
        ts=SESSION_START + timedelta(seconds=offset_s),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=500,
        ask_size=500,
    )


def _bar(offset_s: int, close: str, volume: int = 50_000) -> Bar:
    return Bar(
        symbol="AVTX",
        ts=SESSION_START + timedelta(seconds=offset_s),
        timeframe="M1",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=volume,
    )


def _headline(offset_s: int, title: str) -> Headline:
    return Headline(
        ticker="AVTX",
        ts=SESSION_START + timedelta(seconds=offset_s),
        source="Benzinga",
        title=title,
        url=f"https://example.com/{offset_s}",
    )


SCRIPTED_QUOTES = [
    _quote(0, "4.21", "4.22"),
    _quote(15, "4.30", "4.31"),
    _quote(30, "4.45", "4.46"),
]

SCRIPTED_BARS = [
    _bar(0, "4.22"),
    _bar(60, "4.40", volume=120_000),
    _bar(120, "4.55", volume=200_000),
]

SCRIPTED_HEADLINES = [
    _headline(5, "AVTX Phase 3 Trial Hits Primary Endpoint"),
    _headline(70, "AVTX Confirms No Dilutive Offering Pending"),
]

SCRIPTED_FLOAT = FloatRecord(
    ticker="AVTX",
    as_of=DAY,
    float_shares=8_500_000,
    shares_outstanding=12_000_000,
    source="benzinga",
)


async def _record_session(out_dir: Path) -> None:
    async with FeedRecorder(out_dir) as rec:
        for q in SCRIPTED_QUOTES:
            rec.record_quote(q)
        for b in SCRIPTED_BARS:
            rec.record_bar(b)
        for h in SCRIPTED_HEADLINES:
            rec.record_headline(h)
        rec.record_float(SCRIPTED_FLOAT)


async def test_replay_full_session_roundtrip(tmp_path: Path) -> None:
    await _record_session(tmp_path)

    replay = ReplayProvider(tmp_path, mode=ReplayMode.AS_FAST_AS_POSSIBLE)
    await replay.connect()

    quotes = [q async for q in replay.subscribe_quotes(["AVTX"])]
    bars = [b async for b in replay.subscribe_bars(["AVTX"], Timeframe.M1)]
    heads = [h async for h in replay.subscribe_headlines(["AVTX"])]
    float_rec = await replay.get_float("AVTX", DAY)

    assert [q.bid for q in quotes] == [Decimal("4.21"), Decimal("4.30"), Decimal("4.45")]
    assert [b.close for b in bars] == [Decimal("4.22"), Decimal("4.40"), Decimal("4.55")]
    assert [h.title for h in heads] == [h.title for h in SCRIPTED_HEADLINES]
    assert float_rec.float_shares == 8_500_000


async def test_replay_realtime_mode_paces_intervals(tmp_path: Path) -> None:
    await _record_session(tmp_path)

    clock = VirtualClock(SESSION_START)
    replay = ReplayProvider(tmp_path, mode=ReplayMode.REALTIME, clock=clock)
    await replay.connect()

    real = RealClock()
    real_t0 = real.monotonic()
    bars = [b async for b in replay.subscribe_bars(["AVTX"], Timeframe.M1)]
    real_elapsed = real.monotonic() - real_t0

    # Three bars at 0s, 60s, 120s. VirtualClock advances 120s; wall
    # clock stays small because VirtualClock.sleep doesn't block.
    assert clock.monotonic() == pytest.approx(120.0, abs=0.5)
    assert real_elapsed < 1.0
    assert len(bars) == 3


async def test_replay_realtime_with_real_clock_actually_paces(tmp_path: Path) -> None:
    """Spot-check that under :class:`RealClock`, pacing introduces real delay."""
    quick_dir = tmp_path / "quick"
    async with FeedRecorder(quick_dir) as rec:
        for offset_ms in (0, 50, 100, 150):
            rec.record_quote(
                Quote(
                    symbol="AVTX",
                    ts=SESSION_START + timedelta(milliseconds=offset_ms),
                    bid=Decimal("1"),
                    ask=Decimal("1.01"),
                    bid_size=1,
                    ask_size=1,
                )
            )

    replay = ReplayProvider(quick_dir, mode=ReplayMode.REALTIME, clock=RealClock())
    await replay.connect()

    real = RealClock()
    t0 = real.monotonic()
    out = [q async for q in replay.subscribe_quotes(["AVTX"])]
    elapsed = real.monotonic() - t0

    assert len(out) == 4
    # Original spans 0.15s; pacing should make this take ≥0.10s
    # (some leeway for asyncio scheduling on Windows).
    assert elapsed >= 0.10


async def test_replay_supports_relative_volume_and_ema_lookups(tmp_path: Path) -> None:
    """Atom 15 — replayed historical data feeds the scanner filters."""
    # Synthesize 250 days of daily bars via a separate recording directory.
    daily_dir = tmp_path / "daily"
    async with FeedRecorder(daily_dir) as rec:
        for i in range(250):
            day = DAY - timedelta(days=i)
            ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            rec.record_bar(
                Bar(
                    symbol="AVTX",
                    ts=ts,
                    timeframe="D1",
                    open=Decimal("10"),
                    high=Decimal("10"),
                    low=Decimal("10"),
                    close=Decimal("10") + Decimal(i % 5),
                    volume=1_000_000,
                )
            )

    replay = ReplayProvider(daily_dir)
    await replay.connect()

    cache = HistoricalCache(tmp_path / "h.sqlite")
    await populate_daily_volumes(replay, "AVTX", end_inclusive=DAY, cache=cache)
    await precompute_daily_emas(
        replay, "AVTX", end_inclusive=DAY, cache=cache, history_days=250
    )

    rel_vol = cache.relative_volume("AVTX", DAY, today_volume=5_000_000)
    assert rel_vol is not None
    assert rel_vol == Decimal("5")
    ema_20 = cache.ema("AVTX", DAY, 20)
    assert ema_20 is not None
    cache.close()


async def test_cached_float_reference_in_front_of_replay(tmp_path: Path) -> None:
    await _record_session(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    cached = CachedFloatReference(replay)

    # Two lookups → only one upstream replay scan (the second hits cache).
    a = await cached.get_float("AVTX", DAY)
    b = await cached.get_float("AVTX", DAY)
    assert a == b
