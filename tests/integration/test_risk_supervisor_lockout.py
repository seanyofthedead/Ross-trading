"""End-to-end lockout lifecycle for the RiskSupervisor (#90).

Drives a full day through fills, closes, and gate queries against a real
:class:`~ross_trading.core.risk_supervisor.RiskEventSink` (the in-memory
fake), confirming the lockout, once tripped, blocks every subsequent
``can_open_position`` call and that the journal trail is correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.core.risk_supervisor import RiskLimits, RiskSupervisor
from tests.fakes.risk_event_sink import FakeRiskEventSink

pytestmark = pytest.mark.integration

START = datetime(2026, 5, 30, 13, 30, tzinfo=UTC)


def _ts(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def test_consecutive_loser_lockout_blocks_rest_of_day() -> None:
    sink = FakeRiskEventSink()
    sup = RiskSupervisor(sink=sink, limits=RiskLimits())

    # Three round trips, each a loser, each within per-trade + open caps.
    for i in range(3):
        # Not flattening until the third loser trips the streak lockout.
        assert sup.should_force_flatten() is False
        assert sup.can_open_position(Decimal("40"), _ts(i * 10)).allowed is True
        sup.record_fill(f"p{i}", Decimal("40"), _ts(i * 10))
        sup.record_close(Decimal("-20"), _ts(i * 10 + 5), position_id=f"p{i}")

    assert sup.day_locked() is True
    assert sup.lock_reason == "consecutive_losers"
    assert sup.should_force_flatten() is True

    # Every subsequent entry attempt is blocked for the rest of the day.
    for minute in (40, 60, 90, 120):
        decision = sup.can_open_position(Decimal("10"), _ts(minute))
        assert decision.allowed is False
        assert decision.reason == "day_locked_consecutive_losers"

    blocked = [e for e in sink.events if e.kind == "entry_blocked"]
    assert len(blocked) == 4
    assert all(e.reason == "day_locked_consecutive_losers" for e in blocked)
    assert sum(1 for e in sink.events if e.kind == "lockout_tripped") == 1
    assert sum(1 for e in sink.events if e.kind == "force_flatten") == 1


def test_daily_max_loss_lockout_blocks_rest_of_day() -> None:
    sink = FakeRiskEventSink()
    sup = RiskSupervisor(sink=sink, limits=RiskLimits())

    sup.record_fill("p0", Decimal("50"), _ts(0))
    sup.record_close(Decimal("-100"), _ts(5), position_id="p0")

    assert sup.day_locked() is True
    assert sup.lock_reason == "daily_max_loss"
    assert sup.should_force_flatten() is True
    # open risk fully released on the close
    assert sup.open_risk == Decimal("0")

    decision = sup.can_open_position(Decimal("10"), _ts(30))
    assert decision.allowed is False
    assert decision.reason == "day_locked_daily_max_loss"

    trip = next(e for e in sink.events if e.kind == "lockout_tripped")
    assert trip.reason == "daily_max_loss"
    assert trip.day_pnl == Decimal("-100")
