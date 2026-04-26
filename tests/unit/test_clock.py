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
