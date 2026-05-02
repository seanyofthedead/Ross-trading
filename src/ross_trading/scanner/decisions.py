"""Decision rows emitted by the scanner loop.

Phase 2 -- Atom A3 (#42). ``ScannerDecision`` is the unit the loop
writes to its sink per tick outcome. Three kinds for now -- ``picked``,
``stale_feed``, ``feed_gap`` -- with a fourth (``rejected``) deferred
to #51. ``DecisionSink`` is the Protocol A5 (#44) implements; A3 ships
with a fake sink so it does not block on A5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from ross_trading.scanner.types import ScannerPick


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    """One row emitted to the journal per tick outcome.

    Three kinds:
    - ``picked``: ticker passed all hard filters; ``pick`` carries
      the ranked ScannerPick; ``ticker`` mirrors ``pick.ticker``.
    - ``stale_feed``: emitted in real time, once per suppressed tick;
      ``ticker`` is None (loop-wide); ``reason`` is human-readable.
    - ``feed_gap``: emitted retrospectively when the reconnect provider
      fires its on_gap callback; ``gap_start`` / ``gap_end`` are
      quote-time, not wall-time.
    """

    kind: Literal["picked", "stale_feed", "feed_gap"]
    decision_ts: datetime
    ticker: str | None
    pick: ScannerPick | None
    reason: str | None
    gap_start: datetime | None
    gap_end: datetime | None

    def __post_init__(self) -> None:
        if self.decision_ts.tzinfo is None:
            msg = "decision_ts must be tz-aware"
            raise ValueError(msg)
        if self.gap_start is not None and self.gap_start.tzinfo is None:
            msg = "gap_start must be tz-aware"
            raise ValueError(msg)
        if self.gap_end is not None and self.gap_end.tzinfo is None:
            msg = "gap_end must be tz-aware"
            raise ValueError(msg)


@runtime_checkable
class DecisionSink(Protocol):
    """Where ScannerLoop writes decisions. A5 (#44) implements this."""

    def emit(self, decision: ScannerDecision) -> None: ...
