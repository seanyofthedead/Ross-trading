"""Atom A1 — scanner filter primitives (issue #40)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar
from ross_trading.scanner.filters import rel_volume_ge

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar(
    *,
    symbol: str = "AVTX",
    ts: datetime | None = None,
    open_: str = "5.00",
    high: str = "5.50",
    low: str = "4.95",
    close: str = "5.50",
    volume: int = 1_000_000,
) -> Bar:
    return Bar(
        symbol=symbol,
        ts=ts or T0,
        timeframe="D1",
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


# ---------------------------------------------------------------- rel_volume_ge


@pytest.mark.parametrize(
    ("today_volume", "baseline", "threshold", "expected"),
    [
        (5_000_000, Decimal("1_000_000"), 5.0, True),   # exact 5.0x
        (5_000_001, Decimal("1_000_000"), 5.0, True),   # just above
        (4_999_999, Decimal("1_000_000"), 5.0, False),  # just below
        (10_000_000, Decimal("1_000_000"), 5.0, True),  # well above
    ],
)
def test_rel_volume_ge_boundaries(
    today_volume: int,
    baseline: Decimal,
    threshold: float,
    expected: bool,
) -> None:
    snapshot = _bar(volume=today_volume)
    assert rel_volume_ge("AVTX", snapshot, baseline, threshold) is expected


def test_rel_volume_ge_missing_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), None) is False


def test_rel_volume_ge_zero_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), Decimal("0")) is False
