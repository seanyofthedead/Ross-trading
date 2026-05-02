"""Atom A3 -- ScannerLoop unit tests (issue #42)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.types import Bar, FloatRecord
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.decision_sink import FakeDecisionSink
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler
from tests.fakes.universe import FakeUniverseProvider

# 2025-01-02 (Thursday, EST). 14:30 UTC = 09:30 ET (inside window).
INSIDE_TS = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
OUTSIDE_TS = datetime(2025, 1, 2, 6, 0, tzinfo=UTC)  # 01:00 ET


def _snap(symbol: str, last: str = "5.50", prev_close: str = "5.00") -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol,
        ts=INSIDE_TS,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal(last),
        low=Decimal("4.95"),
        close=Decimal(last),
        volume=5_000_000,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol,
            as_of=date(2025, 1, 2),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        headlines=(),
    )


def _build_loop(
    *,
    start: datetime,
    by_anchor: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]],
    universe: frozenset[str] = frozenset(["AVTX"]),
    tick_interval_s: float = 2.0,
) -> tuple[ScannerLoop, VirtualClock, FakeDecisionSink, FakeSnapshotAssembler]:
    clock = VirtualClock(start)
    sink = FakeDecisionSink()
    assembler = FakeSnapshotAssembler(by_anchor)
    upstream = FakeUniverseProvider({start.date(): universe})
    cached = CachedUniverseProvider(upstream, clock=clock)
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=cached,
        snapshot_assembler=assembler,
        decision_sink=sink,
        clock=clock,
        tick_interval_s=tick_interval_s,
    )
    return loop, clock, sink, assembler


async def _run_for_n_ticks(loop: ScannerLoop, n: int) -> None:
    """Spin the loop for exactly ``n`` ticks then cancel cleanly.

    CPython's asyncio runs all currently-ready callbacks per ``_run_once``
    iteration; new callbacks queued during processing land in the next
    iteration. So one ``await asyncio.sleep(0)`` advances the loop by
    exactly one tick (its first sleep puts the loop back on the ready
    queue, then alternates with the test's resume callback).
    """
    task = asyncio.create_task(loop.run())
    for _ in range(n):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ----------------------------------------------------------- market-hours gate


async def test_outside_market_hours_does_not_call_assembler() -> None:
    loop, _, sink, assembler = _build_loop(start=OUTSIDE_TS, by_anchor={})
    await _run_for_n_ticks(loop, n=3)
    assert assembler.calls == []
    assert sink.decisions == []


async def test_outside_market_hours_loop_keeps_running_not_exits() -> None:
    """Out-of-window ticks are no-ops, not termination."""
    loop, clock, _, assembler = _build_loop(start=OUTSIDE_TS, by_anchor={})
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not task.done()  # still running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    del clock, assembler


# ------------------------------------------------------ inside-window happy path


async def test_inside_market_hours_calls_assembler_and_emits_picked() -> None:
    snap = _snap("AVTX")
    loop, _, sink, assembler = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert assembler.calls == [INSIDE_TS]
    assert len(sink.decisions) == 1
    d = sink.decisions[0]
    assert d.kind == "picked"
    assert d.ticker == "AVTX"
    assert d.pick is not None
    assert d.pick.rank == 1
    assert d.decision_ts == INSIDE_TS


async def test_no_picks_emits_no_decisions() -> None:
    """Empty Scanner result -> empty decision stream for that tick."""
    # Use a snap that fails the rel-volume filter (volume too low).
    snap = ScannerSnapshot(
        bar=Bar(
            symbol="AVTX", ts=INSIDE_TS, timeframe="M1",
            open=Decimal("5"), high=Decimal("5.5"), low=Decimal("4.95"),
            close=Decimal("5.5"), volume=10_000,  # 0.01x baseline -> reject
        ),
        last=Decimal("5.50"),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker="AVTX", as_of=date(2025, 1, 2),
            float_shares=8_500_000, shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert sink.decisions == []


async def test_multiple_picks_emitted_in_rank_order() -> None:
    a, b, c = _snap("AAA", last="5.50"), _snap("BBB", last="6.50"), _snap("CCC", last="6.00")
    snapshot_map = {"AAA": a, "BBB": b, "CCC": c}
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: (snapshot_map, INSIDE_TS)},
        universe=frozenset(["AAA", "BBB", "CCC"]),
    )
    await _run_for_n_ticks(loop, n=1)
    # Sorted by pct_change desc: BBB (+30%), CCC (+20%), AAA (+10%).
    assert [d.ticker for d in sink.decisions] == ["BBB", "CCC", "AAA"]
    assert [d.pick.rank for d in sink.decisions if d.pick is not None] == [1, 2, 3]


# ----------------------------------------------------------------- cancellation


async def test_cancellation_reraises_cancelled_error() -> None:
    loop, _, _, _ = _build_loop(start=OUTSIDE_TS, by_anchor={})
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancellation_does_not_swallow() -> None:
    """Even mid-tick cancellation propagates without try/except suppression."""
    snap = _snap("AVTX")
    loop, _, _, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ------------------------------------------------------------------ injection


async def test_loop_uses_injected_clock_sleep_not_asyncio_sleep() -> None:
    """VirtualClock.sleep advances virtual time. If the loop used asyncio.sleep
    directly, virtual time would not advance and the second tick would re-fire
    at the same anchor_ts -- this would surface as a duplicate calls[0] entry
    or a KeyError on the un-scripted second anchor.
    """
    snap = _snap("AVTX")
    snap_t2 = _snap("AVTX")
    loop, _, sink, assembler = _build_loop(
        start=INSIDE_TS,
        by_anchor={
            INSIDE_TS: ({"AVTX": snap}, INSIDE_TS),
            INSIDE_TS.replace(second=2): ({"AVTX": snap_t2}, INSIDE_TS.replace(second=2)),
        },
    )
    await _run_for_n_ticks(loop, n=2)
    assert assembler.calls == [INSIDE_TS, INSIDE_TS.replace(second=2)]
    assert len(sink.decisions) == 2
