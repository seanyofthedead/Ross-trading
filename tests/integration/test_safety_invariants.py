"""CI safety invariant harness for entry decision streams.

Run directly with:

    pytest tests/integration/test_safety_invariants.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.safety.invariants import (
    CatalystStatus,
    SafetyDecision,
    SafetyInvariantViolation,
    assert_safety_invariants,
    decision_stream_hash,
)

pytestmark = pytest.mark.integration

T0 = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)


def _entry(
    *,
    offset_s: int = 0,
    ticker: str = "AVTX",
    entry_price: Decimal = Decimal("5.50"),
    stop_price: Decimal | None = Decimal("5.20"),
    catalyst_status: CatalystStatus = "approved",
) -> SafetyDecision:
    return SafetyDecision(
        kind="entry",
        decision_ts=T0 + timedelta(seconds=offset_s),
        ticker=ticker,
        entry_price=entry_price,
        stop_price=stop_price,
        catalyst_status=catalyst_status,
    )


def test_no_entry_without_stop_invariant() -> None:
    with pytest.raises(SafetyInvariantViolation, match="entry without stop"):
        assert_safety_invariants((_entry(stop_price=None),))


def test_no_entry_during_lockout_invariant() -> None:
    decisions = (
        SafetyDecision(
            kind="lockout_started",
            decision_ts=T0,
            reason="daily max loss",
        ),
        _entry(offset_s=2),
    )

    with pytest.raises(SafetyInvariantViolation, match="entry during lockout"):
        assert_safety_invariants(decisions)


def test_lockout_end_allows_later_entry() -> None:
    decisions = (
        SafetyDecision(kind="lockout_started", decision_ts=T0, reason="three losers"),
        SafetyDecision(kind="lockout_ended", decision_ts=T0 + timedelta(seconds=1)),
        _entry(offset_s=2),
    )

    assert_safety_invariants(decisions)


def test_catalyst_hard_reject_blocks_entry() -> None:
    with pytest.raises(SafetyInvariantViolation, match="catalyst hard reject"):
        assert_safety_invariants((_entry(catalyst_status="hard_reject"),))


def test_replay_decision_stream_hash_is_stable() -> None:
    first = (
        _entry(offset_s=0, ticker="AVTX"),
        SafetyDecision(
            kind="reject",
            decision_ts=T0 + timedelta(seconds=2),
            ticker="BBAI",
            catalyst_status="hard_reject",
            reason="registered direct offering",
        ),
    )
    second = tuple(
        SafetyDecision(
            kind=d.kind,
            decision_ts=d.decision_ts,
            ticker=d.ticker,
            entry_price=d.entry_price,
            stop_price=d.stop_price,
            catalyst_status=d.catalyst_status,
            reason=d.reason,
        )
        for d in first
    )

    assert_safety_invariants(first)
    assert decision_stream_hash(first) == decision_stream_hash(second)
    assert decision_stream_hash(first) == (
        "254b3f2c1a5300ffe2b7dddeabefdc08d1c8d3ff9c9e758b8a29da679b77102e"
    )
