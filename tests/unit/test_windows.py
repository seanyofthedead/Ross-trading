"""Tests for :mod:`ross_trading.core.windows` -- the canonical ET-window registry.

Resolves spec contradiction #26: scanner refresh (Section 3.1, 7:00-11:00 ET),
Gap-and-Go entries (Section 3.4.1, 09:30-10:00 ET), and the pre-market routine
trigger (Section 3.9, 07:00 ET) live in a single module so future modules
read from one source instead of re-deriving constants from the spec.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from ross_trading.core.windows import (
    GAP_AND_GO_ENTRY_WINDOW,
    PREMARKET_ROUTINE_TIME,
    SCANNER_WINDOW,
    TradingWindow,
)


def test_trading_window_rejects_open_at_or_after_close() -> None:
    with pytest.raises(ValueError, match="open must be before close"):
        TradingWindow(open=time(11, 0), close=time(11, 0))
    with pytest.raises(ValueError, match="open must be before close"):
        TradingWindow(open=time(11, 0), close=time(7, 0))


def test_trading_window_contains_is_inclusive_open_exclusive_close() -> None:
    """Half-open [open, close) matches the existing ``is_market_hours`` contract."""
    window = TradingWindow(open=time(7, 0), close=time(11, 0))
    assert window.contains(time(7, 0)) is True
    assert window.contains(time(10, 59, 59, 999_999)) is True
    assert window.contains(time(11, 0)) is False
    assert window.contains(time(6, 59, 59, 999_999)) is False


def test_scanner_window_matches_architecture_3_1() -> None:
    """Section 3.1 line 117: 7:00-11:00 AM ET refresh window."""
    assert SCANNER_WINDOW.open == time(7, 0)
    assert SCANNER_WINDOW.close == time(11, 0)


def test_gap_and_go_entry_window_matches_architecture_3_4_1() -> None:
    """Section 3.4.1 line 188: Gap & Go entries within [09:30, 10:00] ET."""
    assert GAP_AND_GO_ENTRY_WINDOW.open == time(9, 30)
    assert GAP_AND_GO_ENTRY_WINDOW.close == time(10, 0)


def test_premarket_routine_time_matches_architecture_3_9() -> None:
    """Section 3.9 line 317: pre-market routine runs at ~07:00 ET."""
    assert time(7, 0) == PREMARKET_ROUTINE_TIME


def test_gap_and_go_window_lies_inside_scanner_window() -> None:
    """The entry sub-window must not exit the scanner's active window.

    If a future spec change pushes Gap-and-Go past 11:00 ET, this test
    fires before any consuming module silently goes off-window.
    """
    assert SCANNER_WINDOW.open <= GAP_AND_GO_ENTRY_WINDOW.open
    assert GAP_AND_GO_ENTRY_WINDOW.close <= SCANNER_WINDOW.close


def test_premarket_routine_runs_at_scanner_window_open() -> None:
    """Section 3.9's 07:00 trigger and Section 3.1's 07:00 scanner-open agree.

    Future change-control: if the scanner window slides, the pre-market
    routine likely wants to slide with it. Asserting the relationship here
    catches a one-sided edit.
    """
    assert SCANNER_WINDOW.open == PREMARKET_ROUTINE_TIME


# is_market_hours after the refactor -- the existing window-membership
# contract from tests/unit/test_clock.py must keep holding because the
# scanner window is now sourced from windows.py rather than hardcoded.

WINTER_OPEN_UTC = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)   # 07:00 EST
WINTER_CLOSE_UTC = datetime(2025, 1, 2, 16, 0, tzinfo=UTC)  # 11:00 EST


def test_is_market_hours_reads_scanner_window_open() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC) is True


def test_is_market_hours_reads_scanner_window_close() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_CLOSE_UTC) is False
    assert is_market_hours(WINTER_CLOSE_UTC - timedelta(seconds=1)) is True
