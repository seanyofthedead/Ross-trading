"""Canonical ET-window registry for the trading day.

Single source of truth for the named wall-clock America/New_York windows
referenced across ``docs/architecture.md``. Resolves spec contradiction
#26: scanner refresh (Section 3.1, 7:00-11:00 ET), Gap-and-Go entry
trigger sub-window (Section 3.4.1, 09:30-10:00 ET), and the pre-market
routine trigger time (Section 3.9, 07:00 ET) are defined here so future
modules read constants from one place instead of re-deriving them.

Wall-clock semantics. The values are wall-clock America/New_York times,
not UTC offsets -- DST is the consumer's problem (see
``core/clock.is_market_hours`` for the canonical translation).

Half-open membership. ``TradingWindow.contains`` matches the existing
``is_market_hours`` contract: ``open`` is inclusive, ``close`` is
exclusive. Equivalent to the math interval ``[open, close)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True, slots=True)
class TradingWindow:
    """A wall-clock ET window with half-open ``[open, close)`` semantics."""

    open: time
    close: time

    def __post_init__(self) -> None:
        if self.open >= self.close:
            msg = (
                f"open must be before close (got open={self.open}, "
                f"close={self.close})"
            )
            raise ValueError(msg)

    def contains(self, t: time) -> bool:
        return self.open <= t < self.close


# Section 3.1 line 117: scanner refresh window.
SCANNER_WINDOW = TradingWindow(open=time(7, 0), close=time(11, 0))

# Section 3.4.1 line 188: Gap-and-Go entry trigger sub-window. Sits
# inside SCANNER_WINDOW; consumed by the pattern detector (Phase 4).
GAP_AND_GO_ENTRY_WINDOW = TradingWindow(open=time(9, 30), close=time(10, 0))

# Section 3.9 line 317: pre-market routine fires once at this trigger.
# Single instant rather than a window because the routine produces the
# day's plan and exits; consumed by the pre-market scheduler (Phase 6).
PREMARKET_ROUTINE_TIME = time(7, 0)
