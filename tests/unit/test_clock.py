"""VirtualClock + RealClock contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ross_trading.core.clock import Clock, RealClock, VirtualClock


def test_real_clock_satisfies_protocol() -> None:
    assert isinstance(RealClock(), Clock)


def test_virtual_clock_satisfies_protocol() -> None:
    assert isinstance(VirtualClock(datetime(2026, 4, 26, tzinfo=UTC)), Clock)


def test_virtual_clock_rejects_naive_start() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        VirtualClock(datetime(2026, 4, 26))


def test_virtual_clock_advance_moves_now_and_monotonic() -> None:
    start = datetime(2026, 4, 26, 13, 30, tzinfo=UTC)
    clock = VirtualClock(start)
    clock.advance(2.5)
    assert clock.now() == start + timedelta(seconds=2.5)
    assert clock.monotonic() == pytest.approx(2.5)


def test_virtual_clock_set_time_forward_only() -> None:
    start = datetime(2026, 4, 26, 13, 30, tzinfo=UTC)
    clock = VirtualClock(start)
    clock.set_time(start + timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=5)
    with pytest.raises(ValueError, match="cannot move backwards"):
        clock.set_time(start)


async def test_virtual_clock_sleep_does_not_block_wall_time() -> None:
    real = RealClock()
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    t0 = real.monotonic()
    await clock.sleep(60.0)
    assert real.monotonic() - t0 < 0.5
    assert clock.monotonic() == pytest.approx(60.0)


# ----------------------------------------------------------- is_market_hours

# 2025-01-02 (Thu) is winter (EST, UTC-5): 07:00 ET = 12:00 UTC.
# 2025-07-02 (Wed) is summer (EDT, UTC-4): 07:00 ET = 11:00 UTC.
# 2025-01-04 (Sat) is a non-trading weekend day.

WINTER_OPEN_UTC = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)   # 07:00 EST
WINTER_CLOSE_UTC = datetime(2025, 1, 2, 16, 0, tzinfo=UTC)  # 11:00 EST
SUMMER_OPEN_UTC = datetime(2025, 7, 2, 11, 0, tzinfo=UTC)   # 07:00 EDT
SUMMER_CLOSE_UTC = datetime(2025, 7, 2, 15, 0, tzinfo=UTC)  # 11:00 EDT
SATURDAY_NOON_UTC = datetime(2025, 1, 4, 14, 30, tzinfo=UTC)


def test_market_hours_winter_inside_window() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC + timedelta(hours=2)) is True


def test_market_hours_winter_open_inclusive() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC) is True


def test_market_hours_winter_close_exclusive() -> None:
    """The window is [07:00, 11:00) ET -- 11:00:00 itself is outside."""
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_CLOSE_UTC) is False


def test_market_hours_winter_just_before_open() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC - timedelta(seconds=1)) is False


def test_market_hours_winter_just_after_close() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_CLOSE_UTC + timedelta(seconds=1)) is False


def test_market_hours_summer_inside_window() -> None:
    """DST: window is wall-clock ET, so the corresponding UTC range shifts."""
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(SUMMER_OPEN_UTC + timedelta(hours=2)) is True


def test_market_hours_summer_pre_window_utc_matches_winter_window() -> None:
    """11:30 UTC is 06:30 EDT in summer (outside) and 06:30 EST in winter (outside).

    Sanity check that both DST regimes correctly reject 06:30 ET.
    """
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(datetime(2025, 7, 2, 10, 30, tzinfo=UTC)) is False  # 06:30 EDT
    assert is_market_hours(datetime(2025, 1, 2, 11, 30, tzinfo=UTC)) is False  # 06:30 EST


def test_market_hours_weekend_always_false() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(SATURDAY_NOON_UTC) is False


def test_market_hours_naive_datetime_raises() -> None:
    """Tz-naive input is a programming error; refuse rather than guess."""
    from ross_trading.core.clock import is_market_hours
    with pytest.raises(ValueError, match="tz-aware"):
        is_market_hours(datetime(2025, 1, 2, 14, 0))
