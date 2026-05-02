"""In-memory DecisionSink for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ross_trading.scanner.decisions import ScannerDecision


class FakeDecisionSink:
    """Records every ``emit`` call in order on ``self.decisions``."""

    def __init__(self) -> None:
        self.decisions: list[ScannerDecision] = []

    def emit(self, decision: ScannerDecision) -> None:
        self.decisions.append(decision)
