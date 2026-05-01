"""Atom A2 — ScannerPick + ScannerSnapshot value types (issue #41)."""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.types import ScannerPick, ScannerSnapshot

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick(**overrides: object) -> ScannerPick:
    base: dict[str, object] = {
        "ticker": "AVTX",
        "ts": T0,
        "rel_volume": Decimal("8.5"),
        "pct_change": Decimal("18.4"),
        "price": Decimal("5.50"),
        "float_shares": 8_500_000,
        "news_present": True,
        "headline_count": 2,
        "rank": 1,
    }
    base.update(overrides)
    return ScannerPick(**base)  # type: ignore[arg-type]


def _snap(**overrides: object) -> ScannerSnapshot:
    bar = Bar(
        symbol="AVTX",
        ts=T0,
        timeframe="M1",
        open=Decimal("5.30"),
        high=Decimal("5.55"),
        low=Decimal("5.25"),
        close=Decimal("5.50"),
        volume=900_000,
    )
    base: dict[str, object] = {
        "bar": bar,
        "last": Decimal("5.52"),
        "prev_close": Decimal("4.80"),
        "baseline_30d": Decimal("100_000"),
        "float_record": FloatRecord(
            ticker="AVTX",
            as_of=date(2026, 4, 26),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        "headlines": (
            Headline(ticker="AVTX", ts=T0, source="Benzinga", title="story"),
        ),
    }
    base.update(overrides)
    return ScannerSnapshot(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------- ScannerPick


def test_pick_is_frozen() -> None:
    p = _pick()
    with pytest.raises(FrozenInstanceError):
        p.rank = 99  # type: ignore[misc]


def test_pick_has_slots() -> None:
    assert "__slots__" in ScannerPick.__dict__


def test_pick_picklable_roundtrip() -> None:
    p = _pick()
    revived = pickle.loads(pickle.dumps(p))  # noqa: S301
    assert revived == p
    assert revived is not p


def test_pick_default_rank_is_zero() -> None:
    p = ScannerPick(
        ticker="AVTX",
        ts=T0,
        rel_volume=Decimal("8.5"),
        pct_change=Decimal("18.4"),
        price=Decimal("5.50"),
        float_shares=8_500_000,
        news_present=False,
        headline_count=0,
    )
    assert p.rank == 0


def test_pick_equality_value_based() -> None:
    assert _pick() == _pick()
    assert _pick(rank=1) != _pick(rank=2)


# ------------------------------------------------------------- ScannerSnapshot


def test_snapshot_is_frozen() -> None:
    s = _snap()
    with pytest.raises(FrozenInstanceError):
        s.last = Decimal("99")  # type: ignore[misc]


def test_snapshot_has_slots() -> None:
    assert "__slots__" in ScannerSnapshot.__dict__


def test_snapshot_accepts_none_baseline_and_float() -> None:
    """Optional fields tolerate missing data — caller (Scanner) decides what to do."""
    s = _snap(baseline_30d=None, float_record=None)
    assert s.baseline_30d is None
    assert s.float_record is None


def test_snapshot_accepts_empty_headlines() -> None:
    s = _snap(headlines=())
    assert s.headlines == ()
