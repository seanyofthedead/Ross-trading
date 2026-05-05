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


# =====================================================================
# Issue #51 -- end-to-end picks + rejections in one transaction
# =====================================================================


def _failing_snap_rel_volume(symbol: str, ts: datetime) -> ScannerSnapshot:
    """Snapshot that fails the rel_volume filter (volume too low for 5x)."""
    return ScannerSnapshot(
        bar=Bar(
            symbol=symbol, ts=ts, timeframe="M1",
            open=Decimal("5.00"), high=Decimal("5.50"),
            low=Decimal("4.95"), close=Decimal("5.50"),
            volume=10_000,  # 0.01x baseline -> rel_volume reject
        ),
        last=Decimal("5.50"),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol, as_of=DAY, float_shares=8_500_000,
            shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )


def _failing_snap_pct_change(symbol: str, ts: datetime) -> ScannerSnapshot:
    """Snapshot that passes rel_volume but fails pct_change (+8% < 10% default)."""
    return ScannerSnapshot(
        bar=Bar(
            symbol=symbol, ts=ts, timeframe="M1",
            open=Decimal("5.00"), high=Decimal("5.40"),
            low=Decimal("4.95"), close=Decimal("5.40"), volume=5_000_000,
        ),
        last=Decimal("5.40"),
        prev_close=Decimal("5.00"),  # +8% < 10% threshold
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol, as_of=DAY, float_shares=8_500_000,
            shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )


async def test_scanner_loop_writes_picks_and_rejections_atomically(
    engine: Engine,
) -> None:
    """One tick with mixed picks + rejections produces correct PICKED and
    REJECTED rows in one transaction. Schema CHECK constraints (migration
    0002) enforce field-population invariants per kind.
    """
    from ross_trading.journal.models import RejectionReason
    session_factory = create_session_factory(engine)
    writer = JournalWriter(session_factory)

    snapshots = {
        "GOOD": _snap("GOOD", WINDOW_OPEN),
        "BAD_VOL": _failing_snap_rel_volume("BAD_VOL", WINDOW_OPEN),
        "BAD_PCT": _failing_snap_pct_change("BAD_PCT", WINDOW_OPEN),
    }
    script = {WINDOW_OPEN: (snapshots, WINDOW_OPEN)}

    clock = VirtualClock(WINDOW_OPEN)
    upstream = FakeUniverseProvider({DAY: frozenset(["GOOD", "BAD_VOL", "BAD_PCT"])})
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
        decisions = session.execute(
            select(ScannerDecisionRow).order_by(ScannerDecisionRow.id)
        ).scalars().all()
        picks = session.execute(select(Pick)).scalars().all()

    # 1 PICKED + 2 REJECTED = 3 decision rows; 1 Pick row.
    assert len(decisions) == 3
    assert len(picks) == 1
    assert picks[0].ticker == "GOOD"

    by_kind: dict[DecisionKind, list[ScannerDecisionRow]] = {d.kind: [] for d in decisions}
    for d in decisions:
        by_kind[d.kind].append(d)
    assert set(by_kind) == {DecisionKind.PICKED, DecisionKind.REJECTED}
    assert len(by_kind[DecisionKind.PICKED]) == 1
    assert len(by_kind[DecisionKind.REJECTED]) == 2

    # PICKED row invariants.
    picked = by_kind[DecisionKind.PICKED][0]
    assert picked.ticker == "GOOD"
    assert picked.pick_id == picks[0].id
    assert picked.rejection_reason is None

    # REJECTED row invariants.
    rejected_by_ticker = {d.ticker: d for d in by_kind[DecisionKind.REJECTED]}
    assert set(rejected_by_ticker) == {"BAD_VOL", "BAD_PCT"}
    assert rejected_by_ticker["BAD_VOL"].rejection_reason is RejectionReason.REL_VOLUME
    assert rejected_by_ticker["BAD_PCT"].rejection_reason is RejectionReason.PCT_CHANGE
    for r in by_kind[DecisionKind.REJECTED]:
        assert r.pick_id is None
        assert r.reason is None
        assert r.gap_start is None
        assert r.gap_end is None

    # All three rows share the same decision_ts (one record_scan call).
    assert {d.decision_ts for d in decisions} == {WINDOW_OPEN}
