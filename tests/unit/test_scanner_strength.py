"""Phase 3 — A1 daily-chart strength filter (issue #72) + A2 follow-up (issue #73)."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from ross_trading.data.cache import HistoricalCache
from ross_trading.scanner.strength import DailyStrengthScore, score_daily_strength

AS_OF = date(2026, 5, 8)


def _populated_cache(
    *,
    ema20: str | None = "5.00",
    ema50: str | None = "5.00",
    ema200: str | None = "5.00",
    symbol: str = "AVTX",
) -> HistoricalCache:
    cache = HistoricalCache(":memory:")
    if ema20 is not None:
        cache.record_ema(symbol, AS_OF, 20, Decimal(ema20))
    if ema50 is not None:
        cache.record_ema(symbol, AS_OF, 50, Decimal(ema50))
    if ema200 is not None:
        cache.record_ema(symbol, AS_OF, 200, Decimal(ema200))
    return cache


def _seed_daily_bars(
    cache: HistoricalCache,
    symbol: str,
    *,
    end_inclusive: date,
    days: int,
    high: str,
    low: str,
) -> None:
    """Seed N consecutive prior trading days (excluding ``end_inclusive``).

    Each prior day gets the same ``(high, low)`` so callers can target
    a specific resistance / 52-week-low value without per-day tuning.
    """
    rows = [
        (symbol, end_inclusive - timedelta(days=offset + 1), Decimal(high), Decimal(low))
        for offset in range(days)
    ]
    cache.record_daily_bars(rows)


def _seed_daily_volumes(
    cache: HistoricalCache,
    symbol: str,
    *,
    today: date,
    today_volume: int,
    avg_prior_volume: int,
    days: int = 30,
) -> None:
    cache.record_daily_volume(symbol, today, today_volume)
    cache.record_daily_volumes(
        (symbol, today - timedelta(days=offset + 1), avg_prior_volume)
        for offset in range(days)
    )


# ---------------------------------------------------------------- DailyStrengthScore


def test_score_dataclass_is_frozen_with_slots() -> None:
    assert is_dataclass(DailyStrengthScore)
    field_names = {f.name for f in fields(DailyStrengthScore)}
    assert field_names == {
        "score",
        "above_ema20",
        "above_ema50",
        "above_ema200",
        "breakout",
        "turnaround",
    }
    instance = DailyStrengthScore(
        score=3,
        above_ema20=True,
        above_ema50=True,
        above_ema200=True,
        breakout=None,
        turnaround=None,
    )
    # `frozen=True` blocks mutation of declared fields.
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        instance.score = 0  # type: ignore[misc]
    # `slots=True` removes ``__dict__``.
    assert not hasattr(instance, "__dict__")


# ---------------------------------------------------------------- score_daily_strength


@pytest.mark.parametrize(
    ("close", "ema20", "ema50", "ema200", "expected_score", "expected_flags"),
    [
        # All three above → score 3.
        ("5.50", "5.00", "5.00", "5.00", 3, (True, True, True)),
        # All three below → score 0.
        ("4.50", "5.00", "5.00", "5.00", 0, (False, False, False)),
        # Mixed: above 20 + 200, below 50 → score 2.
        ("5.50", "5.00", "6.00", "5.00", 2, (True, False, True)),
        # Mixed: above 20 only → score 1.
        ("5.50", "5.00", "6.00", "6.00", 1, (True, False, False)),
    ],
)
def test_score_combines_three_emas(
    close: str,
    ema20: str,
    ema50: str,
    ema200: str,
    expected_score: int,
    expected_flags: tuple[bool, bool, bool],
) -> None:
    cache = _populated_cache(ema20=ema20, ema50=ema50, ema200=ema200)

    result = score_daily_strength("AVTX", AS_OF, Decimal(close), cache)

    assert result.score == expected_score
    assert (result.above_ema20, result.above_ema50, result.above_ema200) == expected_flags
    # Breakout and turnaround stay None until follow-up atom #73 lands.
    assert result.breakout is None
    assert result.turnaround is None


@pytest.mark.parametrize("ema_value", ["5.00", "5.0000000000", "5"])
def test_close_equal_to_ema_is_strict_greater_than(ema_value: str) -> None:
    """`close == EMA` → False (architecture §3.3 uses `>`, not `>=`)."""
    cache = _populated_cache(ema20=ema_value, ema50=ema_value, ema200=ema_value)

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.00"), cache)

    assert result.above_ema20 is False
    assert result.above_ema50 is False
    assert result.above_ema200 is False
    assert result.score == 0


def test_close_one_cent_above_ema_passes() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.01"), cache)

    assert result.score == 3
    assert result.above_ema20 is True
    assert result.above_ema50 is True
    assert result.above_ema200 is True


def test_close_one_cent_below_ema_fails() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("4.99"), cache)

    assert result.score == 0
    assert result.above_ema20 is False
    assert result.above_ema50 is False
    assert result.above_ema200 is False


def test_missing_single_ema_yields_score_none_with_partial_flags() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200=None)

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.50"), cache)

    assert result.score is None
    assert result.above_ema20 is True
    assert result.above_ema50 is True
    assert result.above_ema200 is None


def test_all_emas_missing_yields_all_none() -> None:
    cache = _populated_cache(ema20=None, ema50=None, ema200=None)

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.50"), cache)

    assert result.score is None
    assert result.above_ema20 is None
    assert result.above_ema50 is None
    assert result.above_ema200 is None
    assert result.breakout is None
    assert result.turnaround is None


def test_lookup_is_case_insensitive_via_cache_normalization() -> None:
    """`HistoricalCache.ema` upper-cases the symbol; scorer must round-trip cleanly."""
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00", symbol="avtx")

    result = score_daily_strength("avtx", AS_OF, Decimal("5.50"), cache)

    assert result.score == 3


def test_other_symbol_is_isolated() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00", symbol="AVTX")

    result = score_daily_strength("ZZZZ", AS_OF, Decimal("5.50"), cache)

    assert result.score is None
    assert result.above_ema20 is None
    assert result.above_ema50 is None
    assert result.above_ema200 is None


# ---------------------------------------------------------------- breakout (issue #73)


def test_breakout_at_resistance_is_false_strict_greater_than() -> None:
    """`close == prior_max_high` → not a breakout (strict `>`)."""
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=66, high="6.00", low="4.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("6.00"), cache)

    assert result.breakout is False


def test_breakout_one_cent_over_resistance_is_true() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=66, high="6.00", low="4.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("6.01"), cache)

    assert result.breakout is True


def test_breakout_close_below_resistance_is_false() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=66, high="6.00", low="4.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.50"), cache)

    assert result.breakout is False


def test_breakout_no_daily_bars_yields_none_but_ema_score_still_reports() -> None:
    """No `daily_bars` rows → `breakout` None; EMA-only score (0..3) still reports."""
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.50"), cache)

    assert result.breakout is None
    assert result.score == 3
    assert (result.above_ema20, result.above_ema50, result.above_ema200) == (True, True, True)


def test_breakout_lookback_excludes_today() -> None:
    """Today's bar is not its own resistance — only prior days count."""
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    cache.record_daily_bar("AVTX", AS_OF, Decimal("9.00"), Decimal("8.00"))
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=66, high="6.00", low="4.00")

    result = score_daily_strength("AVTX", AS_OF, Decimal("6.50"), cache)

    assert result.breakout is True


# ---------------------------------------------------------------- turnaround (issue #73)


def test_turnaround_near_low_with_heavy_volume_is_true() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="20.00", low="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=3_000_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)

    assert result.turnaround is True


def test_turnaround_near_low_without_heavy_volume_is_false() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="20.00", low="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=1_000_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)

    assert result.turnaround is False


def test_turnaround_far_above_low_is_false_even_with_heavy_volume() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="20.00", low="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=5_000_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("18.00"), cache)

    assert result.turnaround is False


def test_turnaround_missing_today_volume_yields_none() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="20.00", low="5.00")
    cache.record_daily_volumes(
        ("AVTX", AS_OF - timedelta(days=offset + 1), 1_000_000) for offset in range(30)
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)

    assert result.turnaround is None


def test_turnaround_missing_min_low_yields_none() -> None:
    cache = _populated_cache(ema20="5.00", ema50="5.00", ema200="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=3_000_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)

    assert result.turnaround is None


# ---------------------------------------------------------------- score range (0..5)


def test_score_five_with_observable_breakout_and_turnaround() -> None:
    """Construct a scenario where every flag is independently observable and True."""
    cache = _populated_cache(ema20="3.00", ema50="3.00", ema200="3.00")
    # 252-day low is 5.00, current close 5.40 is within the +10% band.
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="5.10", low="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=3_000_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)

    # close 5.40 > EMAs (3.00) → 3 EMA flags True.
    # close 5.40 > prior max high 5.10 → breakout True.
    # close 5.40 ≤ low 5.00 * 1.10 = 5.50 AND today_volume / avg = 3.0 ≥ 2.0 → turnaround True.
    assert result.score == 5
    assert result.breakout is True
    assert result.turnaround is True


def test_score_zero_when_emas_observable_and_all_flags_false() -> None:
    """A1 backward-compat: EMA-only 0..3 score still reports when all flags are observable False."""
    cache = _populated_cache(ema20="10.00", ema50="10.00", ema200="10.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=252, high="20.00", low="1.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=500_000, avg_prior_volume=1_000_000
    )

    result = score_daily_strength("AVTX", AS_OF, Decimal("5.00"), cache)

    assert result.score == 0
    assert result.above_ema20 is False
    assert result.above_ema50 is False
    assert result.above_ema200 is False
    assert result.breakout is False
    assert result.turnaround is False


def test_threshold_overrides_apply() -> None:
    """Caller can tighten / loosen breakout lookback and turnaround thresholds."""
    cache = _populated_cache(ema20="3.00", ema50="3.00", ema200="3.00")
    _seed_daily_bars(cache, "AVTX", end_inclusive=AS_OF, days=66, high="6.00", low="5.00")
    _seed_daily_volumes(
        cache, "AVTX", today=AS_OF, today_volume=2_000_000, avg_prior_volume=1_000_000
    )

    # Default thresholds: 5.40 ≤ 5.00 * 1.10 = 5.50 → near low; vol ratio 2.0 ≥ 2.0 → True.
    default_result = score_daily_strength("AVTX", AS_OF, Decimal("5.40"), cache)
    assert default_result.turnaround is True

    # Tightened band: require close within 1% of low → 5.40 > 5.00 * 1.01 → False.
    tight_result = score_daily_strength(
        "AVTX",
        AS_OF,
        Decimal("5.40"),
        cache,
        near_52w_low_pct=Decimal("0.01"),
    )
    assert tight_result.turnaround is False

    # Tightened volume ratio: require 5x avg → 2.0 < 5.0 → False.
    high_vol_result = score_daily_strength(
        "AVTX",
        AS_OF,
        Decimal("5.40"),
        cache,
        reversal_volume_ratio=Decimal("5.0"),
    )
    assert high_vol_result.turnaround is False
