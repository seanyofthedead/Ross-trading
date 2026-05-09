"""Daily-chart strength filter (architecture §3.3).

Phase 3 — Atom A1 (#72) plus the A2 follow-up (#73), both tracking
under #4. Pure-function consumer of the historical cache populated by
``data.historical.precompute_daily_emas`` (EMA20/50/200) and
``data.historical.populate_daily_bars`` / ``populate_daily_volumes``
(daily highs/lows/volumes for the breakout and turnaround flags).

Scoring (per Cameron's Gap and Go, TA Series p.4):

* ``score = above_ema20 + above_ema50 + above_ema200 + breakout + turnaround``
  counted as ``int(flag is True)`` so a ``None`` flag contributes 0.
* Range 0..3 when only EMAs are observable (no ``daily_bars`` /
  ``daily_volumes`` data); 0..5 once breakout / turnaround inputs are
  populated. Threshold semantics live with the consumer: score ≥ 2 →
  *keep*, score 0-1 → *demote*.

Cache-as-source-of-truth: a missing cache row returns ``None`` for
that flag (matching ``filters.float_le`` "absence of evidence is not
promotion" semantics). When any of the three EMA rows is missing,
``score`` is ``None``; partial EMA evidence does not produce a score.

Strict greater-than: ``close == EMA`` and ``close == prior_max_high``
both return ``False`` for that flag, mirroring the architecture-doc
pseudocode exactly. Today's bar is excluded from breakout / turnaround
windows (the consumer passes ``prior_day = as_of - 1`` to the cache),
so today's price never becomes its own resistance / 52-week-low.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ross_trading.data.cache import HistoricalCache


@dataclass(frozen=True, slots=True)
class DailyStrengthScore:
    """Strength-filter result for a single (symbol, day).

    A field set to ``None`` means "no evidence" (cache miss). Consumers
    must treat ``None`` as a demotion signal, not a permissive default.
    """

    score: int | None
    above_ema20: bool | None
    above_ema50: bool | None
    above_ema200: bool | None
    breakout: bool | None
    turnaround: bool | None


_PERIODS = (20, 50, 200)


def score_daily_strength(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: HistoricalCache,
    *,
    breakout_lookback_days: int = 66,
    turnaround_lookback_days: int = 252,
    near_52w_low_pct: Decimal = Decimal("0.10"),
    reversal_volume_ratio: Decimal = Decimal("2.0"),
    avg_volume_lookback_days: int = 30,
) -> DailyStrengthScore:
    """Score *symbol* against EMA20/50/200 + breakout + turnaround for *as_of*.

    The five flags are independent observations against the cache.
    ``score`` is the sum of explicitly-True flags whenever all three
    EMA rows are present; ``None`` flags contribute 0. Score is ``None``
    when any EMA row is missing.

    Parameters tune the breakout / turnaround thresholds:

    * ``breakout_lookback_days`` — trailing window (calendar days) for
      the rolling resistance high. Default ~3 trading months.
    * ``turnaround_lookback_days`` — trailing window for the 52-week
      low. Default ~one trading year.
    * ``near_52w_low_pct`` — close qualifies as "near" the 52-week low
      when ``close <= min_low * (1 + pct)``. Default 10%.
    * ``reversal_volume_ratio`` — today's volume must be at least this
      multiple of the prior 30-day average for "reversal volume".
      Default 2x.
    * ``avg_volume_lookback_days`` — window for the average-volume
      denominator. Default 30 days.
    """
    above = {
        period: None if (ema := cache.ema(symbol, as_of, period)) is None
        else daily_close > ema
        for period in _PERIODS
    }
    emas_observable = all(flag is not None for flag in above.values())

    breakout = _breakout_flag(
        symbol, as_of, daily_close, cache, lookback_days=breakout_lookback_days
    )
    turnaround = _turnaround_flag(
        symbol,
        as_of,
        daily_close,
        cache,
        lookback_days=turnaround_lookback_days,
        near_low_pct=near_52w_low_pct,
        volume_ratio=reversal_volume_ratio,
        avg_volume_lookback_days=avg_volume_lookback_days,
    )

    if emas_observable:
        flags: tuple[bool | None, ...] = (
            above[20],
            above[50],
            above[200],
            breakout,
            turnaround,
        )
        score: int | None = sum(1 for flag in flags if flag is True)
    else:
        score = None

    return DailyStrengthScore(
        score=score,
        above_ema20=above[20],
        above_ema50=above[50],
        above_ema200=above[200],
        breakout=breakout,
        turnaround=turnaround,
    )


def _breakout_flag(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: HistoricalCache,
    *,
    lookback_days: int,
) -> bool | None:
    prior_day = as_of - timedelta(days=1)
    resistance = cache.max_high(symbol, prior_day, lookback_days)
    if resistance is None:
        return None
    return daily_close > resistance


def _turnaround_flag(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: HistoricalCache,
    *,
    lookback_days: int,
    near_low_pct: Decimal,
    volume_ratio: Decimal,
    avg_volume_lookback_days: int,
) -> bool | None:
    prior_day = as_of - timedelta(days=1)
    low_52w = cache.min_low(symbol, prior_day, lookback_days)
    today_volume = cache.daily_volume(symbol, as_of)
    avg_volume = cache.avg_daily_volume(symbol, prior_day, avg_volume_lookback_days)
    if low_52w is None or today_volume is None or avg_volume is None or avg_volume == 0:
        return None
    near_low = daily_close <= low_52w * (Decimal(1) + near_low_pct)
    heavy_volume = Decimal(today_volume) / avg_volume >= volume_ratio
    return near_low and heavy_volume
