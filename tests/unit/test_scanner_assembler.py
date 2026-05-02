"""Atom A3 -- SnapshotAssembler protocol (issue #42)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord
from ross_trading.scanner.assembler import SnapshotAssembler
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _snap(symbol: str = "AVTX") -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol,
        ts=T0,
        timeframe="M1",
        open=Decimal("5.30"),
        high=Decimal("5.55"),
        low=Decimal("5.25"),
        close=Decimal("5.50"),
        volume=900_000,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal("5.52"),
        prev_close=Decimal("4.80"),
        baseline_30d=Decimal("100000"),
        float_record=FloatRecord(
            ticker=symbol,
            as_of=date(2026, 4, 26),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        headlines=(),
    )


def test_fake_satisfies_protocol() -> None:
    fake = FakeSnapshotAssembler({})
    assert isinstance(fake, SnapshotAssembler)


async def test_fake_returns_scripted_map_at_anchor_ts() -> None:
    snap = _snap()
    fake = FakeSnapshotAssembler({T0: ({"AVTX": snap}, T0)})
    universe = frozenset(["AVTX", "BBAI"])
    snapshot, most_recent = await fake.assemble(universe, T0)
    assert snapshot == {"AVTX": snap}
    assert most_recent == T0


async def test_fake_supports_pre_first_quote() -> None:
    """most_recent_quote_ts is None until the first quote arrives."""
    fake = FakeSnapshotAssembler({T0: ({}, None)})
    snapshot, most_recent = await fake.assemble(frozenset(["AVTX"]), T0)
    assert snapshot == {}
    assert most_recent is None


async def test_fake_raises_on_unscripted_anchor() -> None:
    """Any anchor_ts the test forgot to script is a programming error."""
    fake = FakeSnapshotAssembler({})
    with pytest.raises(KeyError):
        await fake.assemble(frozenset(["AVTX"]), T0)
