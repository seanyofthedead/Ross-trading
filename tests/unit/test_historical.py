"""Atoms 9 & 10 — HistoricalCache + populate_daily_volumes + precompute_daily_emas."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.data.cache import HistoricalCache
from ross_trading.data.historical import (
    populate_daily_volumes,
    precompute_daily_emas,
)
from ross_trading.data.types import Bar
from ross_trading.indicators.ema import ema_alpha, ema_series
from tests.fakes import FakeMarketDataProvider

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 4, 26, tzinfo=UTC)


def _bar(symbol: str, day_offset: int, close: str, volume: int) -> Bar:
    ts = T0 - timedelta(days=day_offset)
    return Bar(
        symbol=symbol,
        ts=ts,
        timeframe="D1",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=volume,
    )


def test_avg_daily_volume_uses_last_n() -> None:
    cache = HistoricalCache()
    rows = [("AVTX", date(2026, 4, 26) - timedelta(days=i), 100 * (i + 1)) for i in range(40)]
    cache.record_daily_volumes(rows)
    avg = cache.avg_daily_volume("AVTX", date(2026, 4, 26), lookback_days=30)
    assert avg is not None
    expected = sum(100 * (i + 1) for i in range(30)) / 30
    assert avg == Decimal(str(expected))
    cache.close()


def test_relative_volume_excludes_today() -> None:
    cache = HistoricalCache()
    today = date(2026, 4, 26)
    # 30 days of 1000 volume, prior to today.
    rows = [("AVTX", today - timedelta(days=i + 1), 1000) for i in range(30)]
    cache.record_daily_volumes(rows)
    rel = cache.relative_volume("AVTX", today, today_volume=5_000)
    assert rel == Decimal("5")
    cache.close()


def test_relative_volume_returns_none_with_no_history() -> None:
    cache = HistoricalCache()
    rel = cache.relative_volume("AVTX", date(2026, 4, 26), today_volume=1000)
    assert rel is None
    cache.close()


def test_ema_alpha_period_3() -> None:
    assert ema_alpha(3) == Decimal("0.5")


def test_ema_series_matches_known_values() -> None:
    values = [Decimal(v) for v in [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]]
    out = ema_series(values, period=3)
    # SMA of first 3 = 11; alpha = 0.5
    assert out[2] == Decimal("11")
    assert out[3] == Decimal("12")  # 0.5*13 + 0.5*11 = 12
    assert out[4] == Decimal("13")  # 0.5*14 + 0.5*12 = 13


def test_ema_alpha_rejects_zero_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        ema_alpha(0)


async def test_populate_daily_volumes_writes_to_cache(tmp_path: Path) -> None:
    bars = [_bar("AVTX", i, "10", 1_000_000 + i) for i in range(30)]
    provider = FakeMarketDataProvider(bars=bars)
    cache = HistoricalCache(tmp_path / "h.sqlite")
    written = await populate_daily_volumes(
        provider, "AVTX", end_inclusive=T0.date(), cache=cache
    )
    assert written == 30
    avg = cache.avg_daily_volume("AVTX", T0.date(), lookback_days=30)
    assert avg is not None
    assert avg > Decimal("999_000")
    cache.close()


async def test_precompute_daily_emas_persists_all_periods(tmp_path: Path) -> None:
    bars = [_bar("AVTX", 249 - i, str(10 + (i % 5)), 1_000_000) for i in range(250)]
    provider = FakeMarketDataProvider(bars=bars)
    cache = HistoricalCache(tmp_path / "h.sqlite")
    written = await precompute_daily_emas(
        provider, "AVTX", end_inclusive=T0.date(), cache=cache, history_days=250
    )
    assert written == 250 * 3
    val_20 = cache.ema("AVTX", T0.date(), 20)
    val_50 = cache.ema("AVTX", T0.date(), 50)
    val_200 = cache.ema("AVTX", T0.date(), 200)
    assert val_20 is not None
    assert val_50 is not None
    assert val_200 is not None
    cache.close()
