"""Unit tests for the RiskSupervisor hard-rule state machine (#90).

Table-driven boundary tests: daily-loss exactly at threshold vs a cent
under/over; loser-streak reset by a winner; the third consecutive loser
locking the day; the max-open-risk and per-trade-risk boundaries;
force-flatten transitions; and that blocked actions journal a RiskEvent
carrying the correct reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ross_trading.core.risk_supervisor import (
    RiskDecision,
    RiskEvent,
    RiskEventSink,
    RiskLimits,
    RiskSupervisor,
)
from tests.fakes.risk_event_sink import FakeRiskEventSink

NOW = datetime(2026, 5, 30, 14, 0, tzinfo=UTC)


def make_supervisor(
    limits: RiskLimits | None = None,
) -> tuple[RiskSupervisor, FakeRiskEventSink]:
    sink = FakeRiskEventSink()
    sup = RiskSupervisor(sink=sink, limits=limits or RiskLimits())
    return sup, sink


# --- protocol / construction ------------------------------------------------


def test_fake_sink_satisfies_protocol() -> None:
    assert isinstance(FakeRiskEventSink(), RiskEventSink)


def test_default_limits_match_section_3_8() -> None:
    limits = RiskLimits()
    assert limits.max_risk_per_trade == Decimal("50")
    assert limits.daily_max_loss == Decimal("-100")
    assert limits.max_consecutive_losers == 3
    assert limits.max_open_risk == Decimal("50")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_risk_per_trade": Decimal("0")},
        {"daily_max_loss": Decimal("0")},
        {"daily_max_loss": Decimal("100")},
        {"max_consecutive_losers": 0},
        {"max_open_risk": Decimal("0")},
    ],
)
def test_invalid_limits_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        RiskLimits(**kwargs)  # type: ignore[arg-type]


def test_risk_decision_invariants() -> None:
    with pytest.raises(ValueError, match="must carry a deny reason"):
        RiskDecision(allowed=False)
    with pytest.raises(ValueError, match="must not carry a deny reason"):
        RiskDecision(allowed=True, reason="per_trade_risk_exceeded")


def test_risk_event_requires_tz_aware() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        RiskEvent(
            event_ts=datetime(2026, 5, 30, 14, 0),  # naive on purpose
            kind="entry_blocked",
            reason="per_trade_risk_exceeded",
            detail="x",
            day_pnl=Decimal("0"),
            consecutive_losers=0,
        )


# --- per-trade risk cap -----------------------------------------------------


@pytest.mark.parametrize(
    ("risk", "allowed"),
    [
        (Decimal("49.99"), True),
        (Decimal("50"), True),  # exactly at cap is allowed
        (Decimal("50.01"), False),  # one cent over denies
    ],
)
def test_per_trade_risk_boundary(risk: Decimal, *, allowed: bool) -> None:
    # raise open cap so only the per-trade rule is in play
    sup, sink = make_supervisor(RiskLimits(max_open_risk=Decimal("1000")))
    decision = sup.can_open_position(risk, NOW)
    assert decision.allowed is allowed
    if not allowed:
        assert decision.reason == "per_trade_risk_exceeded"
        assert sink.events[-1].kind == "entry_blocked"
        assert sink.events[-1].reason == "per_trade_risk_exceeded"


# --- max open risk ----------------------------------------------------------


def test_max_open_risk_boundary() -> None:
    sup, sink = make_supervisor(
        RiskLimits(max_risk_per_trade=Decimal("100"), max_open_risk=Decimal("50"))
    )
    sup.record_fill("p1", Decimal("30"), NOW)
    # 30 open + 20 new == 50 cap -> allowed
    assert sup.can_open_position(Decimal("20"), NOW).allowed is True
    # 30 open + 20.01 new -> over cap
    denied = sup.can_open_position(Decimal("20.01"), NOW)
    assert denied.allowed is False
    assert denied.reason == "max_open_risk_exceeded"
    assert sink.events[-1].reason == "max_open_risk_exceeded"


def test_open_risk_released_on_close() -> None:
    sup, _ = make_supervisor(RiskLimits(max_open_risk=Decimal("50")))
    sup.record_fill("p1", Decimal("50"), NOW)
    assert sup.open_risk == Decimal("50")
    assert sup.can_open_position(Decimal("1"), NOW).allowed is False
    sup.record_close(Decimal("10"), NOW, position_id="p1")
    assert sup.open_risk == Decimal("0")
    assert sup.can_open_position(Decimal("50"), NOW).allowed is True


def test_duplicate_fill_rejected() -> None:
    sup, _ = make_supervisor()
    sup.record_fill("p1", Decimal("10"), NOW)
    with pytest.raises(ValueError, match="already recorded"):
        sup.record_fill("p1", Decimal("10"), NOW)


# --- daily max loss boundary ------------------------------------------------


@pytest.mark.parametrize(
    ("pnl", "locked"),
    [
        (Decimal("-99.99"), False),  # one cent under the floor
        (Decimal("-100"), True),  # exactly at floor locks
        (Decimal("-100.01"), True),  # over the floor locks
    ],
)
def test_daily_max_loss_boundary(pnl: Decimal, *, locked: bool) -> None:
    sup, sink = make_supervisor()
    sup.record_close(pnl, NOW)
    assert sup.day_locked() is locked
    if locked:
        assert sup.lock_reason == "daily_max_loss"
        assert sup.should_force_flatten() is True
        kinds = [e.kind for e in sink.events]
        assert "lockout_tripped" in kinds
        assert "force_flatten" in kinds
        assert all(
            e.reason == "daily_max_loss" for e in sink.events if e.kind != "entry_blocked"
        )
    else:
        assert sup.should_force_flatten() is False
        assert sink.events == []


def test_daily_loss_accumulates_across_trades() -> None:
    sup, _ = make_supervisor()
    sup.record_close(Decimal("-60"), NOW)
    assert sup.day_locked() is False
    sup.record_close(Decimal("-40"), NOW)  # -100 total
    assert sup.day_locked() is True
    assert sup.lock_reason == "daily_max_loss"


# --- consecutive losers -----------------------------------------------------


def test_two_losers_then_winner_resets_streak() -> None:
    sup, _ = make_supervisor()
    sup.record_close(Decimal("-10"), NOW)
    sup.record_close(Decimal("-10"), NOW)
    assert sup.consecutive_losers == 2
    sup.record_close(Decimal("20"), NOW)  # winner resets
    assert sup.consecutive_losers == 0
    assert sup.day_locked() is False
    # two more losers now only brings streak to 2, still unlocked
    sup.record_close(Decimal("-10"), NOW)
    sup.record_close(Decimal("-10"), NOW)
    assert sup.consecutive_losers == 2
    assert sup.day_locked() is False


def test_third_consecutive_loser_locks_day() -> None:
    sup, sink = make_supervisor()
    sup.record_close(Decimal("-10"), NOW)
    sup.record_close(Decimal("-10"), NOW)
    assert sup.day_locked() is False
    sup.record_close(Decimal("-10"), NOW)
    assert sup.consecutive_losers == 3
    assert sup.day_locked() is True
    assert sup.lock_reason == "consecutive_losers"
    assert sup.should_force_flatten() is True
    assert any(
        e.kind == "lockout_tripped" and e.reason == "consecutive_losers" for e in sink.events
    )


def test_scratch_does_not_change_streak() -> None:
    sup, _ = make_supervisor()
    sup.record_close(Decimal("-10"), NOW)
    sup.record_close(Decimal("0"), NOW)  # scratch
    assert sup.consecutive_losers == 1
    assert sup.day_locked() is False


# --- lockout gates entries --------------------------------------------------


def test_locked_day_blocks_entries_with_streak_reason() -> None:
    sup, sink = make_supervisor()
    for _ in range(3):
        sup.record_close(Decimal("-10"), NOW)
    assert sup.day_locked() is True
    sink.events.clear()
    decision = sup.can_open_position(Decimal("1"), NOW)  # tiny, well within caps
    assert decision.allowed is False
    assert decision.reason == "day_locked_consecutive_losers"
    assert sink.events[-1].kind == "entry_blocked"
    assert sink.events[-1].reason == "day_locked_consecutive_losers"


def test_locked_day_daily_loss_blocks_entries() -> None:
    sup, _ = make_supervisor()
    sup.record_close(Decimal("-100"), NOW)
    decision = sup.can_open_position(Decimal("1"), NOW)
    assert decision.allowed is False
    assert decision.reason == "day_locked_daily_max_loss"


def test_lockout_precedence_over_sizing() -> None:
    # an oversized request on a locked day reports the lock, not the size.
    sup, _ = make_supervisor()
    sup.record_close(Decimal("-100"), NOW)
    decision = sup.can_open_position(Decimal("9999"), NOW)
    assert decision.reason == "day_locked_daily_max_loss"


def test_lockout_not_re_tripped_on_further_closes() -> None:
    sup, sink = make_supervisor()
    sup.record_close(Decimal("-100"), NOW)  # trips once
    trip_events = [e for e in sink.events if e.kind == "lockout_tripped"]
    assert len(trip_events) == 1
    sup.record_close(Decimal("-50"), NOW)  # already locked, no new trip
    trip_events = [e for e in sink.events if e.kind == "lockout_tripped"]
    assert len(trip_events) == 1


# --- happy path -------------------------------------------------------------


def test_clean_entry_allowed_no_events() -> None:
    sup, sink = make_supervisor()
    decision = sup.can_open_position(Decimal("50"), NOW)
    assert decision.allowed is True
    assert decision.reason is None
    assert sink.events == []


def test_negative_inputs_rejected() -> None:
    sup, _ = make_supervisor()
    with pytest.raises(ValueError, match="non-negative"):
        sup.can_open_position(Decimal("-1"), NOW)
    with pytest.raises(ValueError, match="non-negative"):
        sup.record_fill("p1", Decimal("-1"), NOW)
