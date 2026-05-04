"""Atom A3 + A8 -- ScannerDecision + DecisionSink (#42), ScannerRejection +
ScanResult + scan_with_decisions (#51).

Per #51 plan D-A8-3: this file extends rather than forks because the new
types live in the same semantic domain (scanner-decision shapes) as the
existing ones.
"""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord
from ross_trading.scanner.decisions import DecisionSink, ScannerDecision
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import (
    ScannerPick,
    ScannerRejection,
    ScannerSnapshot,
    ScanResult,
)
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


# --------------------------------------------------- tz-aware datetime validation


def test_decision_rejects_naive_decision_ts() -> None:
    """Tz-naive timestamps are a programming error; refuse rather than guess."""
    with pytest.raises(ValueError, match="decision_ts must be tz-aware"):
        ScannerDecision(
            kind="stale_feed",
            decision_ts=datetime(2026, 4, 26, 14, 30),  # naive
            ticker=None,
            pick=None,
            reason="x",
            gap_start=None,
            gap_end=None,
        )


def test_decision_rejects_naive_gap_start() -> None:
    with pytest.raises(ValueError, match="gap_start must be tz-aware"):
        ScannerDecision(
            kind="feed_gap",
            decision_ts=T0,
            ticker=None,
            pick=None,
            reason="x",
            gap_start=datetime(2026, 4, 26, 14, 0),  # naive
            gap_end=T0,
        )


def test_decision_rejects_naive_gap_end() -> None:
    with pytest.raises(ValueError, match="gap_end must be tz-aware"):
        ScannerDecision(
            kind="feed_gap",
            decision_ts=T0,
            ticker=None,
            pick=None,
            reason="x",
            gap_start=T0 - timedelta(seconds=30),
            gap_end=datetime(2026, 4, 26, 14, 30),  # naive
        )


# =============================================================================
# Issue #51 -- ScannerRejection, ScanResult value types
# =============================================================================


def _rejection(reason: str = "rel_volume", ticker: str = "AVTX") -> ScannerRejection:
    return ScannerRejection(ticker=ticker, ts=T0, reason=reason)  # type: ignore[arg-type]


def test_rejection_is_frozen() -> None:
    r = _rejection()
    with pytest.raises(FrozenInstanceError):
        r.reason = "pct_change"  # type: ignore[misc]


def test_rejection_has_slots() -> None:
    assert "__slots__" in ScannerRejection.__dict__


def test_rejection_picklable_roundtrip() -> None:
    r = _rejection()
    revived = pickle.loads(pickle.dumps(r))  # noqa: S301
    assert revived == r


def test_rejection_equality_value_based() -> None:
    assert _rejection() == _rejection()
    assert _rejection(reason="rel_volume") != _rejection(reason="pct_change")


def test_scan_result_is_frozen() -> None:
    sr = ScanResult(picks=[_pick()], rejections=[_rejection()])
    with pytest.raises(FrozenInstanceError):
        sr.picks = []  # type: ignore[misc]


def test_scan_result_has_slots() -> None:
    assert "__slots__" in ScanResult.__dict__


def test_scan_result_picklable_roundtrip() -> None:
    sr = ScanResult(picks=[_pick()], rejections=[_rejection()])
    revived = pickle.loads(pickle.dumps(sr))  # noqa: S301
    assert revived == sr


def test_scan_result_empty_both_lists_ok() -> None:
    sr = ScanResult(picks=[], rejections=[])
    assert sr.picks == []
    assert sr.rejections == []


# =============================================================================
# Issue #51 -- Scanner.scan_with_decisions
# =============================================================================

S_T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _passing_snap(
    *,
    symbol: str = "AVTX",
    close: str = "5.50",
    volume: int = 5_000_000,
    last: str = "5.50",
    prev_close: str = "5.00",
    baseline_30d: Decimal | None = Decimal("1000000"),
    float_shares: int | None = 8_500_000,
) -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol, ts=S_T0, timeframe="M1",
        open=Decimal("5.00"), high=Decimal(close), low=Decimal("4.95"),
        close=Decimal(close), volume=volume,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=baseline_30d,
        float_record=FloatRecord(
            ticker=symbol, as_of=date(2026, 4, 26),
            float_shares=float_shares, shares_outstanding=12_000_000,
            source="test",
        ) if float_shares is not None else None,
        headlines=(),
    )


def test_scan_with_decisions_passing_ticker_yields_one_pick_no_rejections() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap()},
    )
    assert len(result.picks) == 1
    assert result.picks[0].ticker == "AVTX"
    assert result.rejections == []


def test_scan_with_decisions_universe_not_in_snapshot_is_silently_skipped() -> None:
    """Per D-A8-5: not-in-snapshot is silent skip, NOT a NO_SNAPSHOT rejection."""
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX", "BBAI"]), {"AVTX": _passing_snap()},  # BBAI missing
    )
    assert [p.ticker for p in result.picks] == ["AVTX"]
    assert result.rejections == []  # BBAI is NOT a rejection


def test_scan_with_decisions_missing_baseline_rejects() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(baseline_30d=None)},
    )
    assert result.picks == []
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "missing_baseline"
    assert result.rejections[0].ticker == "AVTX"


def test_scan_with_decisions_missing_float_rejects() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(float_shares=None)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["missing_float"]


def test_scan_with_decisions_rel_volume_rejects() -> None:
    scanner = Scanner()  # default 5x
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(volume=4_000_000)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["rel_volume"]


def test_scan_with_decisions_pct_change_rejects() -> None:
    scanner = Scanner()  # default 10%
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(last="5.40", prev_close="5.00")},  # +8%
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["pct_change"]


def test_scan_with_decisions_price_band_rejects_high() -> None:
    scanner = Scanner()  # default [1, 20]
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(close="25.00", last="25.50", prev_close="22.00")},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["price_band"]


def test_scan_with_decisions_float_size_rejects() -> None:
    scanner = Scanner()  # default 20M
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(float_shares=25_000_000)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["float_size"]


def test_scan_with_decisions_first_failure_wins_when_multiple_filters_fail() -> None:
    """Snapshot fails BOTH rel_volume AND pct_change -- reason should be the
    earlier one (rel_volume), preserving the AND-chain order."""
    scanner = Scanner()
    snap = _passing_snap(volume=4_000_000, last="5.40", prev_close="5.00")
    result = scanner.scan_with_decisions(frozenset(["AVTX"]), {"AVTX": snap})
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["rel_volume"]  # not pct_change


def test_scan_with_decisions_mixed_partition() -> None:
    scanner = Scanner()
    universe = frozenset(["GOOD", "REJ_VOL", "REJ_PCT"])
    snapshot = {
        "GOOD": _passing_snap(symbol="GOOD"),
        "REJ_VOL": _passing_snap(symbol="REJ_VOL", volume=4_000_000),
        "REJ_PCT": _passing_snap(symbol="REJ_PCT", last="5.40", prev_close="5.00"),
    }
    result = scanner.scan_with_decisions(universe, snapshot)
    assert [p.ticker for p in result.picks] == ["GOOD"]
    assert sorted((r.ticker, r.reason) for r in result.rejections) == [
        ("REJ_PCT", "pct_change"), ("REJ_VOL", "rel_volume"),
    ]


def test_scan_with_decisions_all_rejected() -> None:
    scanner = Scanner()
    universe = frozenset(["A", "B", "C"])
    snapshot = {
        "A": _passing_snap(symbol="A", baseline_30d=None),     # missing_baseline
        "B": _passing_snap(symbol="B", float_shares=None),     # missing_float
        "C": _passing_snap(symbol="C", volume=4_000_000),      # rel_volume
    }
    result = scanner.scan_with_decisions(universe, snapshot)
    assert result.picks == []
    assert sorted((r.ticker, r.reason) for r in result.rejections) == [
        ("A", "missing_baseline"), ("B", "missing_float"), ("C", "rel_volume"),
    ]


def test_scan_is_thin_wrapper_returning_only_picks() -> None:
    """Issue #51: scan(...) must produce identical picks to scan_with_decisions(...).picks."""
    scanner = Scanner()
    universe = frozenset(["GOOD", "REJ_VOL"])
    snapshot = {
        "GOOD": _passing_snap(symbol="GOOD"),
        "REJ_VOL": _passing_snap(symbol="REJ_VOL", volume=4_000_000),
    }
    via_scan = scanner.scan(universe, snapshot)
    via_decisions = scanner.scan_with_decisions(universe, snapshot)
    assert via_scan == via_decisions.picks
