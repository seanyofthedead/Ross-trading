"""Atom A5 (#44) -- end-to-end smoke test: ScannerLoop + real JournalWriter.

Drives the loop for a few ticks against an in-memory engine with a real
:class:`JournalWriter` as the :class:`DecisionSink`, then asserts rows
landed. The Protocol seam is the test boundary, so existing A3 tests stay
on :class:`FakeDecisionSink`; this test specifically covers the wiring.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ross_trading.core.clock import VirtualClock
from ross_trading.data.types import Bar, FloatRecord
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import (
    Base,
    DecisionKind,
    Pick,
)
from ross_trading.journal.models import (
    ScannerDecision as ScannerDecisionRow,
)
from ross_trading.journal.writer import JournalWriter
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler
from tests.fakes.universe import FakeUniverseProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

DAY = date(2025, 1, 2)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)


def _bar(symbol: str, ts: datetime) -> Bar:
    return Bar(
        symbol=symbol, ts=ts, timeframe="M1",
        open=Decimal("5.00"), high=Decimal("5.50"),
        low=Decimal("4.95"), close=Decimal("5.50"), volume=5_000_000,
    )


def _snap(symbol: str, ts: datetime) -> ScannerSnapshot:
    return ScannerSnapshot(
        bar=_bar(symbol, ts),
        last=Decimal("5.50"),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol, as_of=DAY, float_shares=8_500_000,
            shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_journal_engine("sqlite://")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


async def test_scanner_loop_with_real_journal_writer_persists_picks(
    engine: Engine,
) -> None:
    """Three qualifying ticks => three Pick + three ScannerDecision rows on disk."""
    session_factory = create_session_factory(engine)
    writer = JournalWriter(session_factory)

    snap = _snap("AVTX", WINDOW_OPEN)
    script = {
        WINDOW_OPEN: ({"AVTX": snap}, WINDOW_OPEN),
        WINDOW_OPEN + timedelta(seconds=2): (
            {"AVTX": snap}, WINDOW_OPEN + timedelta(seconds=2),
        ),
        WINDOW_OPEN + timedelta(seconds=4): (
            {"AVTX": snap}, WINDOW_OPEN + timedelta(seconds=4),
        ),
    }
    clock = VirtualClock(WINDOW_OPEN)
    upstream = FakeUniverseProvider({DAY: frozenset(["AVTX"])})
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=CachedUniverseProvider(upstream, clock=clock),
        snapshot_assembler=FakeSnapshotAssembler(script),
        decision_sink=writer,
        clock=clock,
        tick_interval_s=2.0,
        staleness_threshold_s=5.0,
    )

    task = asyncio.create_task(loop.run())
    while clock.now() < WINDOW_OPEN + timedelta(seconds=6):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with session_factory() as session:
        decisions = session.execute(
            select(ScannerDecisionRow).order_by(ScannerDecisionRow.id)
        ).scalars().all()
        picks = session.execute(select(Pick)).scalars().all()

    assert len(decisions) == 3, "expected one decision per tick"
    assert all(d.kind is DecisionKind.PICKED for d in decisions)
    assert {d.ticker for d in decisions} == {"AVTX"}
    assert len(picks) == 3, "each emit('picked') writes a fresh Pick row"
    assert {p.ticker for p in picks} == {"AVTX"}
    # Decision rows reference the Pick rows they were emitted with.
    assert {d.pick_id for d in decisions} == {p.id for p in picks}


async def test_scanner_loop_with_real_journal_writer_persists_stale_feed(
    engine: Engine,
) -> None:
    """A stale tick lands as a stale_feed row through the real writer."""
    session_factory = create_session_factory(engine)
    writer = JournalWriter(session_factory)

    # Quote ts is 10s before the anchor -> staleness 10s > threshold 5s.
    stale_quote_ts = WINDOW_OPEN - timedelta(seconds=10)
    script = {WINDOW_OPEN: ({"AVTX": _snap("AVTX", WINDOW_OPEN)}, stale_quote_ts)}

    clock = VirtualClock(WINDOW_OPEN)
    upstream = FakeUniverseProvider({DAY: frozenset(["AVTX"])})
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=CachedUniverseProvider(upstream, clock=clock),
        snapshot_assembler=FakeSnapshotAssembler(script),
        decision_sink=writer,
        clock=clock,
        tick_interval_s=2.0,
        staleness_threshold_s=5.0,
    )

    task = asyncio.create_task(loop.run())
    while clock.now() < WINDOW_OPEN + timedelta(seconds=2):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with session_factory() as session:
        decisions = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert len(decisions) == 1
    row = decisions[0]
    assert row.kind is DecisionKind.STALE_FEED
    assert row.ticker is None
    assert row.pick_id is None
    assert row.reason is not None
    assert "stale" in row.reason
