"""Phase 3 — A1 daily-chart strength filter (issue #72)."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
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
