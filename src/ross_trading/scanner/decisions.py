"""Decision rows emitted by the scanner loop.

Phase 2 -- Atom A3 (#42), extended in A8 (#51) with the fourth
``rejected`` kind and the :meth:`DecisionSink.record_scan` batch API.
``ScannerDecision`` is the unit the loop writes to its sink per
emit-style decision (stale_feed, feed_gap); ``record_scan`` carries
the per-tick batch of picks + rejections atomically.

Per #51 plan D-A8-1: the loop calls :meth:`record_scan` for the
scan branch (one call per tick, atomic) and :meth:`emit` for
stale_feed and feed_gap (which fire alone -- no atomicity at risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ross_trading.journal.models import RejectionReason
    from ross_trading.scanner.types import RejectionReasonLit, ScannerPick


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    """One row emitted to the journal per non-batched tick outcome.

    Four kinds:
    - ``picked``: ticker passed all hard filters; ``pick`` carries
      the ranked ScannerPick; ``ticker`` mirrors ``pick.ticker``.
      (Carried via :meth:`DecisionSink.record_scan` post-#51, not emit.)
    - ``stale_feed``: emitted in real time, once per suppressed tick;
      ``ticker`` is None (loop-wide); ``reason`` is human-readable.
    - ``feed_gap``: emitted retrospectively when the reconnect provider
      fires its on_gap callback; ``gap_start`` / ``gap_end`` are
      quote-time, not wall-time.
    - ``rejected`` (#51): a universe member that failed the scanner's
      hard filters; ``rejection_reason`` carries the first-failing-
      filter literal. Carried via :meth:`record_scan`, not emit.
    """

    kind: Literal["picked", "stale_feed", "feed_gap", "rejected"]
    decision_ts: datetime
    ticker: str | None
    pick: ScannerPick | None
    reason: str | None
    gap_start: datetime | None
    gap_end: datetime | None
    rejection_reason: RejectionReasonLit | None = None

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
    """Where ScannerLoop writes decisions. A5 (#44) implements this.

    Two surfaces (per #51 D-A8-1):
    - :meth:`emit`: one-row writes for ``stale_feed`` and ``feed_gap``,
      which fire alone and have no atomicity requirement.
    - :meth:`record_scan`: per-tick batch of picks + rejections, written
      atomically. Used by the loop's scan branch every non-stale tick.
    """

    def emit(self, decision: ScannerDecision) -> None: ...

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None: ...
