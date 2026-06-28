"""Wave 0 -- ingestion-contract invariants, each tested in isolation.

Covers: the three-timestamp split + defaults on the value objects, seq
ordering/dedup in the assembler, feed-gap-on-seq-discontinuity (socket
still up), typed halts suppressing a symbol, and correction/bust deltas
adjusting recorded volume deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.reconnect import ReconnectingProvider
from ross_trading.data.types import (
    Bar,
    Correction,
    FeedGap,
    Halt,
    Quote,
    Tape,
)
from ross_trading.scanner.replay import _RecordingSnapshotAssembler
from tests.fakes.market import FakeMarketDataProvider

if TYPE_CHECKING:
    from datetime import datetime as _dt

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _quote(seq: int, *, offset: int = 0, bid: str = "5.00", ask: str = "5.02") -> Quote:
    ts = T0 + timedelta(seconds=offset)
    return Quote(
        symbol="AVTX",
        exchange_ts=ts,
        seq=seq,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=100,
        ask_size=100,
    )


def _m1(seq: int, *, offset_min: int, close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol="AVTX",
        exchange_ts=T0 + timedelta(minutes=offset_min),
        seq=seq,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal("5.55"),
        low=Decimal("4.95"),
        close=Decimal(close),
        volume=volume,
    )


# --- three timestamps + defaults ------------------------------------------


def test_derived_timestamps_default_to_exchange_ts() -> None:
    q = _quote(1)
    assert q.vendor_ts == q.exchange_ts
    assert q.ingest_ts == q.exchange_ts
    assert q.ts == q.exchange_ts  # legacy alias


def test_explicit_three_timestamps_are_preserved() -> None:
    q = Quote(
        symbol="AVTX",
        exchange_ts=T0,
        vendor_ts=T0 + timedelta(milliseconds=5),
        ingest_ts=T0 + timedelta(milliseconds=20),
        seq=9,
        bid=Decimal("5.00"),
        ask=Decimal("5.02"),
        bid_size=1,
        ask_size=1,
    )
    assert q.vendor_ts == T0 + timedelta(milliseconds=5)
    assert q.ingest_ts == T0 + timedelta(milliseconds=20)


def test_naive_derived_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware UTC"):
        Quote(
            symbol="AVTX",
            exchange_ts=T0,
            ingest_ts=datetime(2026, 4, 26, 14, 30),  # naive
            seq=1,
            bid=Decimal("5.00"),
            ask=Decimal("5.02"),
            bid_size=1,
            ask_size=1,
        )


def test_correction_bust_uses_zero_size() -> None:
    bust = Correction(
        symbol="AVTX",
        corrects_seq=4,
        new_size=0,
        new_price=None,
        seq=10,
        exchange_ts=T0,
    )
    assert bust.new_size == 0
    assert bust.ingest_ts == T0


# --- seq ordering + scoped dedup in the assembler -------------------------


async def _assemble_last(assembler: _RecordingSnapshotAssembler, anchor: _dt) -> Decimal:
    snaps, _ = await assembler.assemble(frozenset({"AVTX"}), anchor)
    return snaps["AVTX"].last


async def test_assembler_orders_by_exchange_ts_then_seq() -> None:
    # Two quotes at the SAME exchange_ts; seq breaks the tie deterministically
    # regardless of arrival order.
    a = _quote(1, bid="5.00", ask="5.00")
    b = _quote(2, bid="6.00", ask="6.00")
    forward = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0)]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [a, b]},
        headlines_by_ticker={},
        floats_by_ticker={},
    )
    shuffled = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0)]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [b, a]},  # reversed arrival
        headlines_by_ticker={},
        floats_by_ticker={},
    )
    anchor = T0 + timedelta(minutes=2)
    # Highest seq at the tied timestamp wins as the as-of value, either way.
    assert await _assemble_last(forward, anchor) == Decimal("6.00")
    assert await _assemble_last(shuffled, anchor) == Decimal("6.00")


async def test_assembler_dedups_duplicate_seq_within_scope() -> None:
    dup = _quote(5, offset=1, bid="7.00", ask="7.00")
    assembler = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0)]},
        d1_by_ticker={},
        # Same logical event delivered twice (same scoped (symbol, seq)).
        quotes_by_ticker={"AVTX": [dup, dup]},
        headlines_by_ticker={},
        floats_by_ticker={},
    )
    quotes = assembler._quotes["AVTX"]  # asserting scoped dedup directly
    assert len(quotes) == 1


# --- feed gap from seq discontinuity (socket still up) --------------------


async def test_seq_discontinuity_emits_feed_gap_without_disconnect() -> None:
    # Quotes 1, 2, 4 -> the missing seq 3 must surface a FeedGap even though
    # the socket never disconnected.
    quotes = [_quote(1, offset=0), _quote(2, offset=1), _quote(4, offset=2)]
    upstream = FakeMarketDataProvider(quotes=quotes)
    gaps: list[FeedGap] = []
    wrapper = ReconnectingProvider(
        upstream, on_gap=gaps.append, clock=VirtualClock(T0),
    )
    received = [q async for q in wrapper.subscribe_quotes(["AVTX"])]
    assert [q.seq for q in received] == [1, 2, 4]  # stream is not interrupted
    assert len(gaps) == 1
    assert gaps[0].symbol == "AVTX"
    assert "seq discontinuity" in gaps[0].reason


async def test_contiguous_seq_emits_no_gap() -> None:
    quotes = [_quote(1, offset=0), _quote(2, offset=1), _quote(3, offset=2)]
    upstream = FakeMarketDataProvider(quotes=quotes)
    gaps: list[FeedGap] = []
    wrapper = ReconnectingProvider(
        upstream, on_gap=gaps.append, clock=VirtualClock(T0),
    )
    _ = [q async for q in wrapper.subscribe_quotes(["AVTX"])]
    assert gaps == []


# --- typed halt suppresses the symbol -------------------------------------


async def test_halted_symbol_is_omitted_from_snapshot() -> None:
    halt = Halt(symbol="AVTX", state="halted", seq=1, exchange_ts=T0 + timedelta(minutes=1))
    assembler = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0)]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [_quote(1, offset=0)]},
        headlines_by_ticker={},
        floats_by_ticker={},
        halts_by_ticker={"AVTX": [halt]},
    )
    snaps, _ = await assembler.assemble(
        frozenset({"AVTX"}), T0 + timedelta(minutes=2),
    )
    assert "AVTX" not in snaps


async def test_resume_does_not_price_off_pre_halt_quote() -> None:
    # Pre-halt quote (seq 1) -> halt -> resume, but no fresh post-resume
    # quote yet. The assembler must NOT price ``last`` off the stale quote;
    # it falls back to the bar close instead.
    halt = Halt(symbol="AVTX", state="halted", seq=2, exchange_ts=T0 + timedelta(minutes=1))
    resume = Halt(
        symbol="AVTX", state="resumed", seq=3, exchange_ts=T0 + timedelta(minutes=2),
    )
    assembler = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0, close="5.50")]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [_quote(1, offset=0, bid="9.00", ask="9.00")]},
        headlines_by_ticker={},
        floats_by_ticker={},
        halts_by_ticker={"AVTX": [halt, resume]},
    )
    snaps, latest = await assembler.assemble(
        frozenset({"AVTX"}), T0 + timedelta(minutes=3),
    )
    # Stale pre-halt quote ignored: last == bar close, not the 9.00 quote mid.
    assert snaps["AVTX"].last == Decimal("5.50")
    # And it does not count toward the staleness watermark.
    assert latest is None


# --- correction / bust adjusts recorded volume ----------------------------


async def test_bust_reduces_covering_bar_volume() -> None:
    # A 1,000,000-share print (seq 7) inside the M1 bar is busted; rel-vol
    # input (bar volume) must drop by exactly that size, deterministically.
    print_seq = 7
    trade = Tape(
        symbol="AVTX",
        exchange_ts=T0 + timedelta(seconds=10),
        seq=print_seq,
        price=Decimal("5.50"),
        size=1_000_000,
    )
    bust = Correction(
        symbol="AVTX",
        corrects_seq=print_seq,
        new_size=0,
        new_price=None,
        seq=99,
        exchange_ts=T0 + timedelta(seconds=11),
    )
    assembler = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0, volume=5_000_000)]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [_quote(1, offset=0)]},
        headlines_by_ticker={},
        floats_by_ticker={},
        tape_by_ticker={"AVTX": [trade]},
        corrections_by_ticker={"AVTX": [bust]},
    )
    snaps, _ = await assembler.assemble(
        frozenset({"AVTX"}), T0 + timedelta(minutes=2),
    )
    assert snaps["AVTX"].bar.volume == 4_000_000


async def test_correction_resizes_covering_bar_volume() -> None:
    # A partial correction (1,000,000 -> 250,000) trims volume by the delta.
    trade = Tape(
        symbol="AVTX",
        exchange_ts=T0 + timedelta(seconds=10),
        seq=7,
        price=Decimal("5.50"),
        size=1_000_000,
    )
    corr = Correction(
        symbol="AVTX",
        corrects_seq=7,
        new_size=250_000,
        new_price=Decimal("5.50"),
        seq=99,
        exchange_ts=T0 + timedelta(seconds=11),
    )
    assembler = _RecordingSnapshotAssembler(
        m1_by_ticker={"AVTX": [_m1(1, offset_min=0, volume=5_000_000)]},
        d1_by_ticker={},
        quotes_by_ticker={"AVTX": [_quote(1, offset=0)]},
        headlines_by_ticker={},
        floats_by_ticker={},
        tape_by_ticker={"AVTX": [trade]},
        corrections_by_ticker={"AVTX": [corr]},
    )
    snaps, _ = await assembler.assemble(
        frozenset({"AVTX"}), T0 + timedelta(minutes=2),
    )
    assert snaps["AVTX"].bar.volume == 4_250_000
