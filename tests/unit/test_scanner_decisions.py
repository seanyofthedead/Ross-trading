"""Atom A3 -- ScannerDecision + DecisionSink (issue #42)."""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.scanner.decisions import DecisionSink, ScannerDecision
from ross_trading.scanner.types import ScannerPick
from tests.fakes.decision_sink import FakeDecisionSink

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick() -> ScannerPick:
    return ScannerPick(
        ticker="AVTX",
        ts=T0,
        rel_volume=Decimal("8.5"),
        pct_change=Decimal("18.4"),
        price=Decimal("5.50"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=2,
        rank=1,
    )


def _picked() -> ScannerDecision:
    p = _pick()
    return ScannerDecision(
        kind="picked",
        decision_ts=T0,
        ticker=p.ticker,
        pick=p,
        reason=None,
        gap_start=None,
        gap_end=None,
    )


def _stale() -> ScannerDecision:
    return ScannerDecision(
        kind="stale_feed",
        decision_ts=T0,
        ticker=None,
        pick=None,
        reason="feed stale by 12.3s",
        gap_start=None,
        gap_end=None,
    )


def _gap() -> ScannerDecision:
    return ScannerDecision(
        kind="feed_gap",
        decision_ts=T0,
        ticker=None,
        pick=None,
        reason="upstream socket reset",
        gap_start=T0 - timedelta(seconds=30),
        gap_end=T0,
    )


# --------------------------------------------------------------- ScannerDecision


def test_decision_is_frozen() -> None:
    d = _picked()
    with pytest.raises(FrozenInstanceError):
        d.kind = "stale_feed"  # type: ignore[misc]


def test_decision_has_slots() -> None:
    assert "__slots__" in ScannerDecision.__dict__


def test_decision_picklable_roundtrip() -> None:
    for d in (_picked(), _stale(), _gap()):
        revived = pickle.loads(pickle.dumps(d))  # noqa: S301
        assert revived == d


def test_picked_carries_pick_and_mirrors_ticker() -> None:
    d = _picked()
    assert d.pick is not None
    assert d.ticker == d.pick.ticker


def test_stale_feed_has_no_ticker_no_pick_and_a_reason() -> None:
    d = _stale()
    assert d.ticker is None
    assert d.pick is None
    assert d.reason is not None
    assert d.gap_start is None
    assert d.gap_end is None


def test_feed_gap_carries_quote_time_window() -> None:
    d = _gap()
    assert d.kind == "feed_gap"
    assert d.gap_start is not None
    assert d.gap_end is not None
    assert d.gap_end > d.gap_start


# ------------------------------------------------------------ DecisionSink Protocol


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeDecisionSink(), DecisionSink)


def test_fake_records_emit_calls_in_order() -> None:
    sink = FakeDecisionSink()
    a, b = _picked(), _stale()
    sink.emit(a)
    sink.emit(b)
    assert sink.decisions == [a, b]
