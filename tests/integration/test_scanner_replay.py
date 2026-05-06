"""End-to-end tests for the A8 replay driver (issue #74).

A8a -- skeleton: ``replay_day`` orchestrates ``ReplayProvider`` + a recording-
backed ``SnapshotAssembler`` + ``ScannerLoop`` against the journal writer for
a single calendar day. No idempotency, no synthetic-tick fallback yet (those
land in A8b / A8d).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import Bar, FloatRecord, Quote
from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import Base, Pick
from ross_trading.scanner.replay import replay_day

if TYPE_CHECKING:
    from pathlib import Path

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
            ts=datetime(
                PREV_TRADING_DAY.year, PREV_TRADING_DAY.month, PREV_TRADING_DAY.day,
                21, 0, tzinfo=UTC,
            ),
            timeframe="D1",
            open=Decimal("5.00"), high=Decimal("5.00"),
            low=Decimal("5.00"), close=Decimal("5.00"),
            volume=1_000_000,
        ))
        rec.record_bar(Bar(
            symbol=ticker, ts=WINDOW_OPEN, timeframe="M1",
            open=Decimal("5.00"), high=Decimal("5.55"),
            low=Decimal("4.95"), close=Decimal("5.50"), volume=5_000_000,
        ))
        rec.record_quote(Quote(
            symbol=ticker, ts=WINDOW_OPEN,
            bid=Decimal("5.49"), ask=Decimal("5.51"),
            bid_size=500, ask_size=500,
        ))
        rec.record_float(FloatRecord(
            ticker=ticker, as_of=DAY,
            float_shares=8_500_000, shares_outstanding=12_000_000,
            source="test",
        ))


async def test_replay_day_writes_picks_to_journal(tmp_path: Path) -> None:
    """Smoke: replay a single-ticker passing day -> >=1 Pick row in the journal."""
    recordings = tmp_path / "recordings"
    universe_dir = tmp_path / "universe"
    universe_dir.mkdir()
    (universe_dir / f"{DAY.isoformat()}.json").write_text(
        json.dumps(["AVTX"]), encoding="utf-8",
    )

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
    assert len(picks) >= 1
    assert picks[0].ticker == "AVTX"
