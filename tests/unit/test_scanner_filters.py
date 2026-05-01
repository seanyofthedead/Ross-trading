"""Atom A1 — scanner filter primitives (issue #40)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar
from ross_trading.scanner.filters import pct_change_ge, price_in_band, rel_volume_ge

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


# ----------------------------------------------------------------- pct_change_ge


@pytest.mark.parametrize(
    ("current", "reference", "threshold_pct", "expected"),
    [
        ("5.50", "5.00", "10",  True),    # exact +10%
        ("5.501", "5.00", "10", True),    # just above
        ("5.499", "5.00", "10", False),   # just below
        ("10.00", "5.00", "10", True),    # well above
        ("4.50", "5.00", "10",  False),   # negative move
        ("5.50", "5.00", "5",   True),    # lower threshold passes
        ("5.50", "5.00", "20",  False),   # higher threshold fails
    ],
)
def test_pct_change_ge_boundaries(
    current: str, reference: str, threshold_pct: str, expected: bool,
) -> None:
    assert pct_change_ge(
        Decimal(current), Decimal(reference), Decimal(threshold_pct)
    ) is expected


def test_pct_change_ge_zero_reference_is_false() -> None:
    """Avoid divide-by-zero — return False rather than raising."""
    assert pct_change_ge(Decimal("1.00"), Decimal("0"), Decimal("10")) is False


# ----------------------------------------------------------------- price_in_band


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        ("1.00", True),    # exact low
        ("0.99", False),   # just below low
        ("1.01", True),    # just above low
        ("19.99", True),   # just below high
        ("20.00", True),   # exact high
        ("20.01", False),  # just above high
        ("5.50", True),    # mid-band
    ],
)
def test_price_in_band_default_bounds(close: str, expected: bool) -> None:
    snapshot = _bar(close=close)
    assert price_in_band("AVTX", snapshot) is expected


def test_price_in_band_custom_bounds() -> None:
    snapshot = _bar(close="50.00")
    assert price_in_band("AVTX", snapshot, low=Decimal("10"), high=Decimal("100")) is True
    assert price_in_band("AVTX", snapshot, low=Decimal("60"), high=Decimal("100")) is False
