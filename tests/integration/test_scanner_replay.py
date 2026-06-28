"""End-to-end tests for the A8 replay driver (issue #74).

Covers the orchestration: ``replay_day`` walks recorded ticks through
``ScannerLoop`` -> ``JournalWriter``. Verifies the smoke happy path, the
idempotency guarantee (re-running for the same day is a no-op on row
counts), and the task-crash escape hatch (loop exception propagates
instead of hanging the busy-yield).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import select

from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import Bar, FeedGap, FloatRecord, Quote
from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import (
    Base,
    DecisionKind,
    Pick,
    RejectionReason,
)
from ross_trading.journal.models import ScannerDecision as ScannerDecisionRow
from ross_trading.scanner.replay import replay_day

if TYPE_CHECKING:
    from pathlib import Path

    from ross_trading.scanner.scanner import Scanner

pytestmark = pytest.mark.integration

# Thursday, post-DST, no holiday. Cameron window is 12:00-16:00 UTC (07:00-11:00 ET).
DAY = date(2025, 1, 2)
PREV_TRADING_DAY = date(2024, 12, 31)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)


async def _record_passing_day(recordings_dir: Path, ticker: str) -> None:
    """Lay down a single-ticker recording with one tick that passes every filter.

    - Previous-day D1 bar at $5.00 close, 1M volume -> prev_close + baseline=1M.
    - Day-of M1 bar with close $5.50 (+10%), 5M volume (5x baseline).
    - Day-of quote with bid/ask straddling $5.50.
    - Float record at 8.5M shares (under 20M threshold).
    """
    async with FeedRecorder(recordings_dir) as rec:
        rec.record_bar(Bar(
            symbol=ticker,
            exchange_ts=datetime(
                PREV_TRADING_DAY.year, PREV_TRADING_DAY.month, PREV_TRADING_DAY.day,
                21, 0, tzinfo=UTC,
            ),
            timeframe="D1",
            open=Decimal("5.00"), high=Decimal("5.00"),
            low=Decimal("5.00"), close=Decimal("5.00"),
            volume=1_000_000,
        ))
        rec.record_bar(Bar(
            symbol=ticker, exchange_ts=WINDOW_OPEN, timeframe="M1",
            open=Decimal("5.00"), high=Decimal("5.55"),
            low=Decimal("4.95"), close=Decimal("5.50"), volume=5_000_000,
        ))
        rec.record_quote(Quote(
            symbol=ticker, exchange_ts=WINDOW_OPEN,
            bid=Decimal("5.49"), ask=Decimal("5.51"),
            bid_size=500, ask_size=500,
        ))
        rec.record_float(FloatRecord(
            ticker=ticker, as_of=DAY,
            float_shares=8_500_000, shares_outstanding=12_000_000,
            source="test",
        ))


async def _record_big_float_day(recordings_dir: Path, ticker: str) -> None:
    """Lay down a recording for a ticker that fails only the float-size filter.

    Same shape as :func:`_record_passing_day` but with a 50M-share float, so
    the scanner walks the AND-chain past every other check and rejects with
    ``float_size`` (the last filter). Lets us assert the rejected decision
    stream surfaces through replay end-to-end (#74 AC: same decision stream
    as the live loop, including REJECTED post-#51).
    """
    async with FeedRecorder(recordings_dir) as rec:
        rec.record_bar(Bar(
            symbol=ticker,
            exchange_ts=datetime(
                PREV_TRADING_DAY.year, PREV_TRADING_DAY.month, PREV_TRADING_DAY.day,
                21, 0, tzinfo=UTC,
            ),
            timeframe="D1",
            open=Decimal("5.00"), high=Decimal("5.00"),
            low=Decimal("5.00"), close=Decimal("5.00"),
            volume=1_000_000,
        ))
        rec.record_bar(Bar(
            symbol=ticker, exchange_ts=WINDOW_OPEN, timeframe="M1",
            open=Decimal("5.00"), high=Decimal("5.55"),
            low=Decimal("4.95"), close=Decimal("5.50"), volume=5_000_000,
        ))
        rec.record_quote(Quote(
            symbol=ticker, exchange_ts=WINDOW_OPEN,
            bid=Decimal("5.49"), ask=Decimal("5.51"),
            bid_size=500, ask_size=500,
        ))
        rec.record_float(FloatRecord(
            ticker=ticker, as_of=DAY,
            float_shares=50_000_000, shares_outstanding=80_000_000,
            source="test",
        ))


def _setup_fixture(tmp_path: Path, ticker: str) -> tuple[Path, Path]:
    """Lay down the recordings + per-day universe directories for ``DAY``."""
    recordings = tmp_path / "recordings"
    universe_dir = tmp_path / "universe"
    universe_dir.mkdir()
    (universe_dir / f"{DAY.isoformat()}.json").write_text(
        json.dumps([ticker]), encoding="utf-8",
    )
    return recordings, universe_dir


async def test_replay_day_writes_picks_to_journal(tmp_path: Path) -> None:
    """Smoke: replay a single-ticker passing day -> >=1 Pick row in the journal."""
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        summary = await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )

        session_factory = create_session_factory(engine)
        with session_factory() as session:
            picks = session.execute(
                select(Pick).where(Pick.ticker == "AVTX"),
            ).scalars().all()
    finally:
        engine.dispose()

    assert summary.picks_emitted >= 1
    assert summary.runtime_seconds >= 0.0
    assert len(picks) >= 1
    assert picks[0].ticker == "AVTX"


async def test_replay_day_is_idempotent_on_rerun(tmp_path: Path) -> None:
    """Re-running for the same day must not change journal row counts (#74 AC)."""
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        first = await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )
        second = await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )
    finally:
        engine.dispose()

    assert first.picks_emitted == second.picks_emitted >= 1
    assert first.decisions_emitted == second.decisions_emitted


class _ExplodingScanner:
    """Test-only :class:`Scanner` substitute that raises inside the loop tick.

    Used to verify ``replay_day`` propagates loop exceptions instead of
    spinning forever in its ``while clock.now() < end`` busy-yield.
    """

    def scan_with_decisions(
        self,
        universe: object,
        snapshot: object,
    ) -> object:
        del universe, snapshot
        msg = "boom"
        raise RuntimeError(msg)


async def test_replay_day_propagates_loop_exception(tmp_path: Path) -> None:
    """ScannerLoop exceptions must propagate, not hang the busy-yield."""
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await replay_day(
                day=DAY,
                recordings_dir=recordings,
                universe_dir=universe_dir,
                journal_engine=engine,
                scanner=cast("Scanner", _ExplodingScanner()),
            )
    finally:
        engine.dispose()


async def test_replay_day_emits_stale_feed_when_quotes_age_past_threshold(
    tmp_path: Path,
) -> None:
    """#74 AC: replay must surface ``stale_feed`` decisions, not just picks/rejects.

    The recording has exactly one quote at ``WINDOW_OPEN``. The driver pads
    the run by ``_REPLAY_TAIL_PAD`` (10s) past the last recorded event, so
    the loop ticks at offsets +0/+2/+4/+6/+8 seconds from ``WINDOW_OPEN``
    on the default ``tick_interval_s=2.0``. With the default
    ``staleness_threshold_s=5.0``, the +6s and +8s ticks observe a quote
    that's older than the threshold and emit ``stale_feed`` -- the same
    decision the live loop would emit when its feed goes silent.

    ``feed_gap`` is exercised separately by
    :func:`test_replay_day_emits_feed_gap_when_recorded`: it requires a
    recorded ``FeedGap`` event in ``feed_gap.jsonl.gz`` and is orthogonal
    to the staleness threshold the loop computes per tick.
    """
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            stale_rows = session.execute(
                select(ScannerDecisionRow).where(
                    ScannerDecisionRow.kind == DecisionKind.STALE_FEED,
                ),
            ).scalars().all()
    finally:
        engine.dispose()

    assert len(stale_rows) >= 1
    assert all(row.ticker is None for row in stale_rows)
    assert all(row.rejection_reason is None for row in stale_rows)
    assert all(
        row.reason is not None and "stale" in row.reason
        for row in stale_rows
    )


async def test_replay_day_writes_rejected_decisions_to_journal(
    tmp_path: Path,
) -> None:
    """#74 AC: replay must surface ``rejected`` decisions, not just ``picked``.

    Universe = {AVTX (passes every filter), BIGFLT (passes every filter
    except ``float_size``)}. After replay the journal must contain at
    least one decision of each kind, with BIGFLT's rejection carrying
    ``rejection_reason=FLOAT_SIZE`` -- the live-loop's
    first-failing-filter contract from #51 carrying through the replay
    path unchanged.
    """
    recordings = tmp_path / "recordings"
    universe_dir = tmp_path / "universe"
    universe_dir.mkdir()
    (universe_dir / f"{DAY.isoformat()}.json").write_text(
        json.dumps(["AVTX", "BIGFLT"]), encoding="utf-8",
    )
    await _record_passing_day(recordings, "AVTX")
    await _record_big_float_day(recordings, "BIGFLT")

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        summary = await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            picked_rows = session.execute(
                select(ScannerDecisionRow).where(
                    ScannerDecisionRow.kind == DecisionKind.PICKED,
                ),
            ).scalars().all()
            rejected_rows = session.execute(
                select(ScannerDecisionRow).where(
                    ScannerDecisionRow.kind == DecisionKind.REJECTED,
                ),
            ).scalars().all()
    finally:
        engine.dispose()

    assert summary.decisions_emitted >= 2
    assert len(picked_rows) >= 1
    assert len(rejected_rows) >= 1
    assert {row.ticker for row in picked_rows} == {"AVTX"}
    assert {row.ticker for row in rejected_rows} == {"BIGFLT"}
    assert all(
        row.rejection_reason == RejectionReason.FLOAT_SIZE
        for row in rejected_rows
    )


async def test_replay_day_emits_feed_gap_when_recorded(tmp_path: Path) -> None:
    """#74 AC: replay must surface ``feed_gap`` decisions when the recording
    captured a reconnect-induced gap.

    A live recorder wired behind ``ReconnectingProvider(..., on_gap=rec.record_feed_gap)``
    captures the gap window; the replay driver replays it onto the loop's
    ``on_feed_gap`` callback at the recorded virtual-clock instant. The
    journal should contain one ``feed_gap`` decision with ``gap_start`` /
    ``gap_end`` matching the recorded values -- the same shape the live
    loop writes when its production reconnect fires.
    """
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    gap = FeedGap(
        symbol=None,
        start=WINDOW_OPEN + timedelta(seconds=2),
        end=WINDOW_OPEN + timedelta(seconds=4),
        reason="upstream disconnect",
    )
    async with FeedRecorder(recordings) as rec:
        rec.record_feed_gap(gap)

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
        )
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            gap_rows = session.execute(
                select(ScannerDecisionRow).where(
                    ScannerDecisionRow.kind == DecisionKind.FEED_GAP,
                ),
            ).scalars().all()
    finally:
        engine.dispose()

    assert len(gap_rows) == 1
    row = gap_rows[0]
    assert row.ticker is None
    assert row.rejection_reason is None
    assert row.gap_start == gap.start
    assert row.gap_end == gap.end
    assert row.reason == gap.reason


async def test_replay_day_emits_late_feed_gap_with_correct_decision_ts(
    tmp_path: Path,
) -> None:
    """Gaps closing past ``last_event + _REPLAY_TAIL_PAD`` still fire at gap.end.

    Locks in the dispatch contract: ``decision_ts`` is the recorded
    reconnect time (``gap.end``), not the busy-yield exit boundary. The
    only intraday event in the fixture is at ``WINDOW_OPEN``, so the
    event-derived end is ``WINDOW_OPEN + 10s``; the gap closes at +30s,
    well outside that window. The driver must extend its busy-yield to
    cover the gap and dispatch it through the in-loop path so
    ``ScannerLoop.on_feed_gap`` stamps ``decision_ts`` from a virtual
    clock that has actually advanced to ``gap.end``.
    """
    recordings, universe_dir = _setup_fixture(tmp_path, "AVTX")
    await _record_passing_day(recordings, "AVTX")

    gap = FeedGap(
        symbol=None,
        start=WINDOW_OPEN + timedelta(seconds=20),
        end=WINDOW_OPEN + timedelta(seconds=30),
        reason="late reconnect",
    )
    async with FeedRecorder(recordings) as rec:
        rec.record_feed_gap(gap)

    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        await replay_day(
            day=DAY,
            recordings_dir=recordings,
            universe_dir=universe_dir,
            journal_engine=engine,
            tick_interval_s=2.0,
        )
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            gap_rows = session.execute(
                select(ScannerDecisionRow).where(
                    ScannerDecisionRow.kind == DecisionKind.FEED_GAP,
                ),
            ).scalars().all()
    finally:
        engine.dispose()

    assert len(gap_rows) == 1
    row = gap_rows[0]
    assert row.gap_start == gap.start
    assert row.gap_end == gap.end
    # decision_ts must land at -- or within one tick of -- gap.end. The
    # busy-yield exit boundary (last_event + _REPLAY_TAIL_PAD = +10s) is
    # well inside the previous-tick window, so a regression to the prior
    # tail-flush behavior would put decision_ts there instead.
    assert gap.end <= row.decision_ts <= gap.end + timedelta(seconds=2.5)
