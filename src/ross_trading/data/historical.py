"""Historical computations: 30-day relative volume, daily-EMA precompute.

These functions read daily bars from any :class:`MarketDataProvider`
(including the replay provider) and persist derived values into a
shared :class:`HistoricalCache`. Architecture §3.1 (rel-vol filter)
and §3.3 (EMA20/50/200 daily-strength filter) consume what this
module produces.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from ross_trading.data.market_feed import MarketDataProvider, Timeframe
from ross_trading.indicators.ema import ema_series

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from decimal import Decimal

    from ross_trading.data.cache import HistoricalCache
    from ross_trading.data.types import Bar

DEFAULT_VOLUME_LOOKBACK = 30
DEFAULT_EMA_PERIODS: tuple[int, ...] = (20, 50, 200)


async def populate_daily_volumes(
    provider: MarketDataProvider,
    symbol: str,
    end_inclusive: date,
    cache: HistoricalCache,
    lookback_days: int = DEFAULT_VOLUME_LOOKBACK,
) -> int:
    """Fetch daily bars for the trailing window and write volumes to cache.

    Returns the number of rows written.
    """
    bars = await _fetch_daily_bars(provider, symbol, end_inclusive, lookback_days + 5)
    cache.record_daily_volumes((b.symbol, b.ts.date(), b.volume) for b in bars)
    return len(bars)


async def precompute_daily_emas(
    provider: MarketDataProvider,
    symbol: str,
    end_inclusive: date,
    cache: HistoricalCache,
    periods: Iterable[int] = DEFAULT_EMA_PERIODS,
    history_days: int = 250,
) -> int:
    """Compute and persist EMA(period) for each day in the supplied window.

    ``history_days`` should be at least the longest period plus a small
    margin so the EMA has stabilised by the dates the scanner queries.
    Returns the number of (symbol, day, period) rows written.
    """
    periods_list = list(periods)
    bars = await _fetch_daily_bars(provider, symbol, end_inclusive, history_days)
    if not bars:
        return 0
    closes = [b.close for b in bars]
    rows: list[tuple[str, date, int, Decimal]] = []
    for period in periods_list:
        emas = ema_series(closes, period)
        for bar, value in zip(bars, emas, strict=True):
            rows.append((symbol.upper(), bar.ts.date(), period, value))
    cache.record_emas(rows)
    return len(rows)


async def _fetch_daily_bars(
    provider: MarketDataProvider,
    symbol: str,
    end_inclusive: date,
    days: int,
) -> Sequence[Bar]:
    end_dt = datetime.combine(end_inclusive + timedelta(days=1), time.min, tzinfo=UTC)
    start_dt = datetime.combine(
        end_inclusive - timedelta(days=days),
        time.min,
        tzinfo=UTC,
    )
    return await provider.historical_bars(symbol, start_dt, end_dt, Timeframe.D1)
