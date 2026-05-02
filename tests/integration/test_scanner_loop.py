"""Atom A3 -- ScannerLoop integration / replay test (issue #42).

Mirrors tests/integration/test_replay_day.py: drives the loop on a
VirtualClock through a synthetic 4-hour-window day, asserts no scans
outside the window, asserts byte-identical decision streams across
two runs (replay-determinism contract), and exercises the
pre-first-quote / mid-window-disconnect boundary cases from #42's
acceptance.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.types import Bar, FeedGap, FloatRecord
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.decision_sink import FakeDecisionSink
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler
from tests.fakes.universe import FakeUniverseProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.integration

# 2025-01-02 (Thursday, EST, no DST). Window: 12:00 UTC (07:00 ET) to 16:00 UTC (11:00 ET).
DAY = date(2025, 1, 2)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)   # 07:00 ET
WINDOW_CLOSE = datetime(2025, 1, 2, 16, 0, tzinfo=UTC)  # 11:00 ET (exclusive)


def _bar(symbol: str, ts: datetime, close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol=symbol, ts=ts, timeframe="M1",
        open=Decimal("5.00"), high=Decimal(close),
        low=Decimal("4.95"), close=Decimal(close), volume=volume,
    )


def _snap(symbol: str, ts: datetime, last: str = "5.50") -> ScannerSnapshot:
    return ScannerSnapshot(
        bar=_bar(symbol, ts, close=last),
        last=Decimal(last),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol, as_of=DAY, float_shares=8_500_000,
            shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )


def _script_window(
    *,
    start: datetime,
    end: datetime,
    tick_s: float,
    snap_for: dict[datetime, dict[str, ScannerSnapshot]],
    quote_ts_for: dict[datetime, datetime | None],
) -> dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]]:
    """Build an anchor->(snapshot_map, most_recent_quote_ts) script for every tick in [start, end).

    Tests pass per-anchor overrides via ``snap_for`` / ``quote_ts_for``;
    unspecified anchors get an empty snapshot and None quote_ts.
    """
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    cur = start
    while cur < end:
        script[cur] = (snap_for.get(cur, {}), quote_ts_for.get(cur))
        cur = cur + timedelta(seconds=tick_s)
    return script


async def _drive_until(loop: ScannerLoop, clock: VirtualClock, until: datetime) -> None:
    """Run the loop until clock.now() >= ``until``, then cancel cleanly.

    The driver and the loop alternate on the asyncio ready queue: each
    ``clock.sleep`` inside the loop calls ``await asyncio.sleep(0)``,
    which yields back here, and the busy-yield re-checks ``clock.now()``.
    Correctness depends on CPython's ``_run_once`` draining ready
    callbacks in FIFO order -- the same scheduling assumption documented
    on the unit-test ``_run_for_n_ticks`` helper.
    """
    task = asyncio.create_task(loop.run())
    while clock.now() < until:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _build(
    *,
    start: datetime,
    script: Mapping[datetime, tuple[Mapping[str, ScannerSnapshot], datetime | None]],
    universe: frozenset[str] = frozenset(["AVTX"]),
    tick_s: float = 2.0,
    staleness_threshold_s: float = 5.0,
) -> tuple[ScannerLoop, VirtualClock, FakeDecisionSink]:
    clock = VirtualClock(start)
    sink = FakeDecisionSink()
    upstream = FakeUniverseProvider({DAY: universe})
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=CachedUniverseProvider(upstream, clock=clock),
        snapshot_assembler=FakeSnapshotAssembler(script),
        decision_sink=sink,
        clock=clock,
        tick_interval_s=tick_s,
        staleness_threshold_s=staleness_threshold_s,
    )
    return loop, clock, sink


# ------------------------------------------------------------- market boundary


async def test_no_scans_outside_07_to_11_et() -> None:
    """Start a few ticks before window open, drive past close. No scans outside.

    Tight 30-second buffer on each end keeps this fast (~7200 ticks of the
    full 4-hour window) while still asserting both boundaries (open
    inclusive, close exclusive). is_market_hours' DST / weekday correctness
    is covered separately in test_clock.py.
    """
    pre_open = WINDOW_OPEN - timedelta(seconds=30)
    post_close = WINDOW_CLOSE + timedelta(seconds=30)
    # Inside the window every tick, return a passing snap with fresh quote.
    snap_for: dict[datetime, dict[str, ScannerSnapshot]] = {}
    quote_ts_for: dict[datetime, datetime | None] = {}
    cur = WINDOW_OPEN
    while cur < WINDOW_CLOSE:
        snap_for[cur] = {"AVTX": _snap("AVTX", cur)}
        quote_ts_for[cur] = cur
        cur = cur + timedelta(seconds=2)
    script = _script_window(
        start=pre_open, end=post_close, tick_s=2.0,
        snap_for=snap_for, quote_ts_for=quote_ts_for,
    )
    loop, clock, sink = _build(start=pre_open, script=script)
    await _drive_until(loop, clock, post_close)
    # Every decision must fall in [WINDOW_OPEN, WINDOW_CLOSE).
    for d in sink.decisions:
        assert WINDOW_OPEN <= d.decision_ts < WINDOW_CLOSE
    # And we should have many picks (window contains 7200 ticks, all qualifying).
    assert any(d.kind == "picked" for d in sink.decisions)
    # Sanity: first pick is at WINDOW_OPEN (inclusive), last is at WINDOW_CLOSE - 2s (exclusive).
    picked_ts = [d.decision_ts for d in sink.decisions if d.kind == "picked"]
    assert picked_ts[0] == WINDOW_OPEN
    assert picked_ts[-1] == WINDOW_CLOSE - timedelta(seconds=2)


# ------------------------------------------------------------- pre-first-quote


async def test_pre_first_quote_no_stale_feed() -> None:
    """Before the first quote, no staleness suppression should fire."""
    # Drive 5 ticks at the open with most_recent_quote_ts=None on each.
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    for i in range(5):
        t = WINDOW_OPEN + timedelta(seconds=2 * i)
        script[t] = ({}, None)  # empty snapshot, pre-first-quote
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    await _drive_until(loop, clock, WINDOW_OPEN + timedelta(seconds=10))
    assert all(d.kind != "stale_feed" for d in sink.decisions)


# ------------------------------------------------------------- mid-window disconnect


async def test_mid_window_disconnect_emits_feed_gap() -> None:
    """A FeedGap delivered via on_feed_gap during the window emits one feed_gap row."""
    snap = _snap("AVTX", WINDOW_OPEN)
    script = {WINDOW_OPEN: ({"AVTX": snap}, WINDOW_OPEN)}
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    # Tick once, then synchronously deliver the gap.
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    loop.on_feed_gap(FeedGap(
        symbol=None,
        start=WINDOW_OPEN - timedelta(seconds=30),
        end=WINDOW_OPEN,
        reason="upstream socket reset",
    ))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    del clock
    feed_gaps = [d for d in sink.decisions if d.kind == "feed_gap"]
    assert len(feed_gaps) == 1
    assert feed_gaps[0].gap_start is not None
    assert feed_gaps[0].gap_end is not None
    assert feed_gaps[0].gap_end - feed_gaps[0].gap_start == timedelta(seconds=30)


# ------------------------------------------------------------- byte-identical replay


async def test_two_runs_produce_byte_identical_decision_streams() -> None:
    """The replay-determinism contract: same inputs -> equal decision lists."""
    snap_pass = _snap("AVTX", WINDOW_OPEN, last="5.50")
    snap_fail = _snap("AVTX", WINDOW_OPEN.replace(second=4), last="5.05")  # +1% only -> reject
    script = {
        WINDOW_OPEN: ({"AVTX": snap_pass}, WINDOW_OPEN),
        WINDOW_OPEN.replace(second=2): ({"AVTX": snap_pass}, WINDOW_OPEN.replace(second=2)),
        WINDOW_OPEN.replace(second=4): ({"AVTX": snap_fail}, WINDOW_OPEN.replace(second=4)),
    }
    loop_a, clock_a, sink_a = _build(start=WINDOW_OPEN, script=dict(script))
    loop_b, clock_b, sink_b = _build(start=WINDOW_OPEN, script=dict(script))
    await _drive_until(loop_a, clock_a, WINDOW_OPEN + timedelta(seconds=6))
    await _drive_until(loop_b, clock_b, WINDOW_OPEN + timedelta(seconds=6))
    assert sink_a.decisions == sink_b.decisions
    # Plus a positive sanity: at least one pick fired in the run.
    assert any(d.kind == "picked" for d in sink_a.decisions)


# ----------------------------------------------------- steady-state memory shape


async def test_steady_state_no_unbounded_growth() -> None:
    """Sanity: 100-tick run produces exactly the expected count of decisions.

    The loop must not buffer or coalesce. One pick per qualifying tick;
    one stale_feed per stale tick; nothing else is queued.
    """
    snap = _snap("AVTX", WINDOW_OPEN)
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    for i in range(100):
        t = WINDOW_OPEN + timedelta(seconds=2 * i)
        script[t] = ({"AVTX": snap}, t)
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    # Script has 100 anchors at seconds 0, 2, ..., 198. Driving to second 200
    # ensures the 100th tick (at 198s) completes; the would-be 101st tick at
    # 200s is unscripted and would KeyError, but the cancel fires first
    # because clock.now() == 200 >= 200 after the 100th tick's sleep.
    await _drive_until(loop, clock, WINDOW_OPEN + timedelta(seconds=200))
    assert len(sink.decisions) == 100  # one picked per tick
    assert all(d.kind == "picked" for d in sink.decisions)
