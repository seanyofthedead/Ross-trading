"""Atom 3 — NewsProvider protocol + HeadlineDeduper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.news_feed import HeadlineDeduper
from ross_trading.data.types import Headline

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _h(
    title: str = "AVTX up on FDA approval",
    source: str = "Benzinga",
    ticker: str = "AVTX",
    ts: datetime | None = None,
) -> Headline:
    return Headline(ticker=ticker, ts=ts or T0, source=source, title=title)


def test_deduper_flags_exact_duplicate() -> None:
    clock = VirtualClock(T0)
    deduper = HeadlineDeduper(clock=clock)
    assert deduper.is_duplicate(_h()) is False
    assert deduper.is_duplicate(_h()) is True


def test_deduper_normalizes_whitespace_and_case() -> None:
    clock = VirtualClock(T0)
    deduper = HeadlineDeduper(clock=clock)
    deduper.is_duplicate(_h(title="AVTX up on FDA approval"))
    assert deduper.is_duplicate(_h(title="  avtx UP  ON  fda APPROVAL ")) is True


def test_deduper_distinguishes_sources() -> None:
    clock = VirtualClock(T0)
    deduper = HeadlineDeduper(clock=clock)
    deduper.is_duplicate(_h(source="Benzinga"))
    assert deduper.is_duplicate(_h(source="Polygon")) is False


def test_deduper_evicts_after_window() -> None:
    clock = VirtualClock(T0)
    deduper = HeadlineDeduper(window=timedelta(hours=1), clock=clock)
    deduper.is_duplicate(_h())
    clock.advance(3601)
    assert deduper.is_duplicate(_h()) is False


def test_deduper_respects_max_entries() -> None:
    clock = VirtualClock(T0)
    deduper = HeadlineDeduper(clock=clock, max_entries=3)
    for i in range(5):
        deduper.is_duplicate(_h(title=f"story {i}"))
    # First two should have been evicted by capacity bound.
    assert deduper.is_duplicate(_h(title="story 0")) is False
    assert deduper.is_duplicate(_h(title="story 4")) is True


def test_deduper_rejects_zero_window() -> None:
    with pytest.raises(ValueError, match="window must be positive"):
        HeadlineDeduper(window=timedelta(0))
