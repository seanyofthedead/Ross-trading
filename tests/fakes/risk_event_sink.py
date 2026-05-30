"""In-memory RiskEventSink for tests.

Records every ``record_risk_event`` call in order so RiskSupervisor tests
(#90) can assert that blocked actions and lockout trips journal the right
:class:`~ross_trading.core.risk_supervisor.RiskEvent`s. Mirrors
``tests/fakes/decision_sink.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ross_trading.core.risk_supervisor import RiskEvent


class FakeRiskEventSink:
    """Records every ``record_risk_event`` call in order."""

    def __init__(self) -> None:
        self.events: list[RiskEvent] = []

    def record_risk_event(self, event: RiskEvent) -> None:
        self.events.append(event)
