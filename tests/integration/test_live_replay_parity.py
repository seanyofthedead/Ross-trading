"""Wave 0 -- live-vs-replay decision parity over an adversarial stream.

The architecture's whole thesis is that a decision is a deterministic
function of the *content* of the recorded events, never of their arrival
order. This test records the same logical day twice -- once with quotes
laid down in forward order, once reversed -- through an adversarial
stream that includes reordered quotes, a recorded feed gap (a silent
drop), a halt/resume, and a busted print, then asserts the two journals
are byte-identical.

A second assertion proves a bust deterministically rewrites rel-volume in
replay: the same day with vs without the bust yields a different pick
``rel_volume`` by exactly the busted size.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import (
    Bar,
    Correction,
    FeedGap,
    FloatRecord,
    Halt,
    Quote,
    Tape,
)
from ross_trading.journal.engine import create_journal_engine, create_session_factory
from ross_trading.journal.models import Base, Pick
from ross_trading.journal.models import ScannerDecision as ScannerDecisionRow
from ross_trading.scanner.replay import replay_day

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

# Thursday, post-DST, no holiday. Cameron window is 12:00-16:00 UTC.
DAY = date(2025, 1, 2)
PREV_TRADING_DAY = date(2024, 12, 31)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
TICKER = "AVTX"


def _d1_prior() -> Bar:
    return Bar(
        symbol=TICKER,
        exchange_ts=datetime(2024, 12, 31, 21, 0, tzinfo=UTC),
        seq=1,
        timeframe="D1",
        open=Decimal("5.00"),
        high=Decimal("5.00"),
        low=Decimal("5.00"),
        close=Decimal("5.00"),
        volume=1_000_000,
    )


def _m1() -> Bar:
    # +10% vs prev_close, 6M volume = 6x the 1M baseline before any bust.
    return Bar(
        symbol=TICKER,
        exchange_ts=WINDOW_OPEN,
        seq=1,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal("5.55"),
        low=Decimal("4.95"),
        close=Decimal("5.50"),
        volume=6_000_000,
    )


def _quote(seq: int, offset_s: int) -> Quote:
    return Quote(
        symbol=TICKER,
        exchange_ts=WINDOW_OPEN + timedelta(seconds=offset_s),
        seq=seq,
        bid=Decimal("5.49"),
        ask=Decimal("5.51"),
        bid_size=500,
        ask_size=500,
    )


# Quotes span before, during, and after the halt window. The post-resume
# quote (seq 4) makes the symbol tradeable again at the tail ticks.
def _quotes() -> list[Quote]:
    return [
        _quote(1, 0),
        _quote(2, 1),
        _quote(3, 2),
        _quote(4, 230),  # 12:03:50, after the 12:03 resume
    ]


_HALT = Halt(symbol=TICKER, state="halted", seq=1, exchange_ts=WINDOW_OPEN + timedelta(seconds=120))
_RESUME = Halt(
    symbol=TICKER, state="resumed", seq=2, exchange_ts=WINDOW_OPEN + timedelta(seconds=180),
)
_GAP = FeedGap(
    symbol=TICKER,
    start=WINDOW_OPEN + timedelta(seconds=20),
    end=WINDOW_OPEN + timedelta(seconds=25),
    reason="seq discontinuity on quote: expected 3, got 5 (1 missed)",
)
_BUSTED_PRINT = Tape(
    symbol=TICKER,
    exchange_ts=WINDOW_OPEN + timedelta(seconds=30),
    seq=7,
    price=Decimal("5.50"),
    size=1_000_000,
)
_BUST = Correction(
    symbol=TICKER,
    corrects_seq=7,
    new_size=0,
    new_price=None,
    seq=8,
    exchange_ts=WINDOW_OPEN + timedelta(seconds=40),
)


async def _record_day(
    recordings_dir: Path,
    *,
    quotes: list[Quote],
    with_bust: bool,
) -> None:
    async with FeedRecorder(recordings_dir) as rec:
        rec.record_bar(_d1_prior())
        rec.record_bar(_m1())
        for q in quotes:
            rec.record_quote(q)
        rec.record_halt(_HALT)
        rec.record_halt(_RESUME)
        rec.record_feed_gap(_GAP)
        rec.record_tape(_BUSTED_PRINT)
        if with_bust:
            rec.record_correction(_BUST)
        rec.record_float(
            FloatRecord(
                ticker=TICKER,
                as_of=DAY,
                float_shares=8_500_000,
                shares_outstanding=12_000_000,
                source="test",
            )
        )


def _universe(tmp_path: Path) -> Path:
    universe_dir = tmp_path / "universe"
    universe_dir.mkdir()
    (universe_dir / f"{DAY.isoformat()}.json").write_text(
        json.dumps([TICKER]), encoding="utf-8",
    )
    return universe_dir


async def _replay_into_fresh_journal(recordings: Path, universe_dir: Path) -> Engine:
    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    await replay_day(
        day=DAY,
        recordings_dir=recordings,
        universe_dir=universe_dir,
        journal_engine=engine,
    )
    return engine


def _decision_fingerprint(engine: Engine) -> tuple[tuple[object, ...], ...]:
    """Canonical, order-stable view of every Pick + ScannerDecision row."""
    factory = create_session_factory(engine)
    with factory() as session:
        picks = session.execute(select(Pick)).scalars().all()
        decisions = session.execute(select(ScannerDecisionRow)).scalars().all()
    pick_rows = sorted(
        (
            "pick",
            p.ticker,
            p.ts,
            str(p.rel_volume),
            str(p.pct_change),
            str(p.price),
            p.float_shares,
            p.rank,
        )
        for p in picks
    )
    decision_rows = sorted(
        (
            "decision",
            d.kind.value,
            d.decision_ts,
            d.ticker,
            d.rejection_reason.value if d.rejection_reason is not None else None,
            d.gap_start,
            d.gap_end,
        )
        for d in decisions
    )
    return tuple(pick_rows) + tuple(decision_rows)


def _min_pick_rel_volume(engine: Engine) -> Decimal:
    """Smallest rel-volume across the day's picks.

    The bust only lands on ticks whose anchor is at/after the correction's
    event time (no future-data leakage), so early-tick picks still carry
    the uncorrected rel-volume. The *minimum* across the day is the robust,
    anchor-order-independent signal that the bust was folded in.
    """
    factory = create_session_factory(engine)
    with factory() as session:
        rels = session.execute(select(Pick.rel_volume)).scalars().all()
    assert rels, "expected at least one pick"
    return min(rels)


async def test_decisions_are_invariant_to_arrival_order(tmp_path: Path) -> None:
    """Same logical day, quotes recorded forward vs reversed -> identical journal."""
    universe_dir = _universe(tmp_path)

    forward_dir = tmp_path / "forward"
    reversed_dir = tmp_path / "reversed"
    await _record_day(forward_dir, quotes=_quotes(), with_bust=True)
    await _record_day(reversed_dir, quotes=list(reversed(_quotes())), with_bust=True)

    forward_engine = await _replay_into_fresh_journal(forward_dir, universe_dir)
    reversed_engine = await _replay_into_fresh_journal(reversed_dir, universe_dir)
    try:
        forward_fp = _decision_fingerprint(forward_engine)
        reversed_fp = _decision_fingerprint(reversed_engine)
    finally:
        forward_engine.dispose()
        reversed_engine.dispose()

    # Bit-identical decision streams despite reversed quote arrival order,
    # across an adversarial stream (reorder + recorded gap + halt + bust).
    assert forward_fp == reversed_fp
    # And the adversarial day actually produced decisions (not a vacuous pass).
    assert any(row[0] == "pick" for row in forward_fp)


async def test_bust_rewrites_rel_volume_deterministically(tmp_path: Path) -> None:
    """A busted 1M-share print drops the M1 bar's rel-volume by exactly 1x.

    Once the bust is known (anchor at/after its event time), rel-volume on
    the covering bar drops from 6x to 5x; before then it is unchanged.
    """
    universe_dir = _universe(tmp_path)

    with_bust_dir = tmp_path / "with_bust"
    without_bust_dir = tmp_path / "without_bust"
    await _record_day(with_bust_dir, quotes=_quotes(), with_bust=True)
    await _record_day(without_bust_dir, quotes=_quotes(), with_bust=False)

    with_engine = await _replay_into_fresh_journal(with_bust_dir, universe_dir)
    without_engine = await _replay_into_fresh_journal(without_bust_dir, universe_dir)
    try:
        with_rel = _min_pick_rel_volume(with_engine)
        without_rel = _min_pick_rel_volume(without_engine)
    finally:
        with_engine.dispose()
        without_engine.dispose()

    # 6M volume / 1M baseline = 6x; busting 1M -> 5M / 1M = 5x (post-bust ticks).
    assert without_rel == Decimal("6")
    assert with_rel == Decimal("5")
