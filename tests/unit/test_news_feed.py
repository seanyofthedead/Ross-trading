"""Atom 3 — NewsProvider protocol + HeadlineDeduper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    deduper = HeadlineDeduper()
    assert deduper.is_duplicate(_h()) is False
    assert deduper.is_duplicate(_h()) is True


def test_deduper_normalizes_whitespace_and_case() -> None:
    deduper = HeadlineDeduper()
    deduper.is_duplicate(_h(title="AVTX up on FDA approval"))
    assert deduper.is_duplicate(_h(title="  avtx UP  ON  fda APPROVAL ")) is True


def test_deduper_distinguishes_sources() -> None:
    deduper = HeadlineDeduper()
    deduper.is_duplicate(_h(source="Benzinga"))
    assert deduper.is_duplicate(_h(source="Polygon")) is False


def test_deduper_evicts_after_window_using_event_time() -> None:
    """Eviction must work without a clock — drives off headline.ts."""
    deduper = HeadlineDeduper(window=timedelta(hours=1))
    deduper.is_duplicate(_h(ts=T0))
    later = _h(ts=T0 + timedelta(hours=1, seconds=1))
    assert deduper.is_duplicate(later) is False


def test_deduper_works_under_fast_replay() -> None:
    """Regression: under AS_FAST_AS_POSSIBLE replay, no clock advance
    happens. Eviction must still fire because it keys off headline.ts."""
    deduper = HeadlineDeduper(window=timedelta(hours=1))
    for i in range(10):
        ts = T0 + timedelta(hours=i)
        # Each headline is unique by title, so they all insert.
        # The expiry pass on each insert evicts older ones.
        h = _h(title=f"story {i}", ts=ts)
        deduper.is_duplicate(h)
    # By story 9 (T0 + 9h), only stories within the 1h window survive.
    earliest_still_valid = _h(title="story 9", ts=T0 + timedelta(hours=9))
    assert deduper.is_duplicate(earliest_still_valid) is True
    # story 0 is long gone — re-inserting is a fresh entry, not a dup.
    fresh = _h(title="story 0", ts=T0 + timedelta(hours=9))
    assert deduper.is_duplicate(fresh) is False


def test_deduper_respects_max_entries() -> None:
    deduper = HeadlineDeduper(max_entries=3)
    for i in range(5):
        deduper.is_duplicate(_h(title=f"story {i}"))
    # First two should have been evicted by capacity bound.
    assert deduper.is_duplicate(_h(title="story 0")) is False
    assert deduper.is_duplicate(_h(title="story 4")) is True


def test_deduper_rejects_zero_window() -> None:
    with pytest.raises(ValueError, match="window must be positive"):
        HeadlineDeduper(window=timedelta(0))
