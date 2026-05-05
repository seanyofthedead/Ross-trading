"""In-memory DecisionSink for tests.

Records both ``emit`` calls and ``record_scan`` batches, so loop tests
can assert per-tick batched outputs (#51) alongside one-off emits
(stale_feed, feed_gap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ross_trading.journal.models import RejectionReason
    from ross_trading.scanner.decisions import ScannerDecision
    from ross_trading.scanner.types import ScannerPick


class FakeDecisionSink:
    """Records every ``emit`` and ``record_scan`` call in order."""

    def __init__(self) -> None:
        self.decisions: list[ScannerDecision] = []
        self.scans: list[
            tuple[datetime, list[ScannerPick], dict[str, RejectionReason]]
        ] = []

    def emit(self, decision: ScannerDecision) -> None:
        self.decisions.append(decision)

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None:
        self.scans.append((decision_ts, list(picks), dict(rejected)))
