"""Integration test for the live capture composition (#87).

Drives a flaky :class:`MarketDataProvider` through :func:`capture_session`,
then replays the resulting recording back through :func:`replay_day` and
asserts the FEED_GAP rung surfaces end-to-end -- closing the production
side of the loop that PR #86 opened on the replay side.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ross_trading.core.clock import VirtualClock
from ross_trading.core.errors import FeedDisconnected
from ross_trading.data.capture import capture_session
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.types import Bar, FloatRecord, Quote, Tape
from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import Base, DecisionKind
from ross_trading.journal.models import ScannerDecision as ScannerDecisionRow
from ross_trading.scanner.replay import replay_day
from tests.fakes.float_ref import FakeFloatReferenceProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from pathlib import Path

pytestmark = pytest.mark.integration


# Thursday, post-DST, no holiday. Cameron window 12:00-16:00 UTC.
DAY = date(2025, 1, 2)
PREV_TRADING_DAY = date(2024, 12, 31)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)


def _passing_d1() -> Bar:
    return Bar(
        symbol="AVTX",
        ts=datetime(
            PREV_TRADING_DAY.year, PREV_TRADING_DAY.month, PREV_TRADING_DAY.day,
            21, 0, tzinfo=UTC,
        ),
        timeframe="D1",
        open=Decimal("5.00"), high=Decimal("5.00"),
        low=Decimal("5.00"), close=Decimal("5.00"),
        volume=1_000_000,
    )


def _passing_m1(offset_s: int = 0) -> Bar:
    return Bar(
        symbol="AVTX",
        ts=WINDOW_OPEN + timedelta(seconds=offset_s),
        timeframe="M1",
        open=Decimal("5.00"), high=Decimal("5.55"),
        low=Decimal("4.95"), close=Decimal("5.50"),
        volume=5_000_000,
    )


def _passing_quote() -> Quote:
    return Quote(
        symbol="AVTX", ts=WINDOW_OPEN,
        bid=Decimal("5.49"), ask=Decimal("5.51"),
        bid_size=500, ask_size=500,
    )


def _passing_float() -> FloatRecord:
    return FloatRecord(
        ticker="AVTX", as_of=DAY,
        float_shares=8_500_000, shares_outstanding=12_000_000,
        source="test",
    )


class _FlakyDayMarket:
    """Yields one M1 bar, drops the bar stream once with backfilled bars,
    then resumes -- exercises the production gap-capture path.
    """

    def __init__(self) -> None:
        # Two bars: drop fires after the first yield; the wrapper reconnects
        # and the second bar arrives in the resumed stream so the gap window
        # contains both the marker and the recovered bar.
        self._remaining_m1 = [_passing_m1(0), _passing_m1(60)]
        self._d1 = [_passing_d1()]
        self._quotes = [_passing_quote()]
        self._dropped = False
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return frozenset({Timeframe.M1, Timeframe.D1})

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        del symbols
        for q in self._quotes:
            yield q

    async def subscribe_bars(
        self, symbols: Iterable[str], timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        del symbols
        if timeframe is Timeframe.D1:
            for b in self._d1:
                yield b
            return
        # M1 stream: emit one bar, then drop, then nothing else.
        emitted = 0
        while self._remaining_m1:
            if not self._dropped and emitted == 1:
                self._dropped = True
                raise FeedDisconnected("websocket-closed")
            yield self._remaining_m1.pop(0)
            emitted += 1

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        del symbols
        if False:
            yield  # pragma: no cover

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        del symbol, start, end, timeframe
        return ()


async def test_capture_then_replay_emits_feed_gap_decision(tmp_path: Path) -> None:
    """End-to-end: capture a flaky session, then replay -> journal must
    contain a FEED_GAP decision row sourced from the captured gap file."""
    recordings = tmp_path / "recordings"
    universe_dir = tmp_path / "universe"
    universe_dir.mkdir()
    (universe_dir / f"{DAY.isoformat()}.json").write_text(
        json.dumps(["AVTX"]), encoding="utf-8",
    )

    upstream = _FlakyDayMarket()
    float_provider = FakeFloatReferenceProvider(
        records={("AVTX", DAY): _passing_float()},
    )
    # VirtualClock keeps gap.end stamps inside the test day so replay's
    # day-window filter accepts the recorded gap.
    capture_clock = VirtualClock(WINDOW_OPEN)
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=float_provider,
        universe=["AVTX"],
        output_dir=recordings,
        timeframes=(Timeframe.M1, Timeframe.D1),
        as_of=DAY,
        clock=capture_clock,
    )

    # The capture path wrote both the bar and the feed_gap to disk.
    gap_path = recordings / DAY.isoformat() / "feed_gap.jsonl.gz"
    assert gap_path.exists()
    bar_path = recordings / DAY.isoformat() / "bar.jsonl.gz"
    assert bar_path.exists()
    float_path = recordings / DAY.isoformat() / "float.jsonl.gz"
    assert float_path.exists()

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
    assert row.reason is not None
    assert row.gap_start is not None
    assert row.gap_end is not None
    assert row.gap_start <= row.gap_end
