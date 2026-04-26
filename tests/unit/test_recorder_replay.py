"""Atoms 5 & 6 — FeedRecorder + ReplayProvider roundtrip."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.clock import RealClock, VirtualClock
from ross_trading.core.errors import MissingRecordingError
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.providers.replay import ReplayMode, ReplayProvider
from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import Bar, FloatRecord, Headline, Quote, Side, Tape

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 4, 26, 13, 30, tzinfo=UTC)


def _quote(symbol: str, offset: int, bid: str, ask: str) -> Quote:
    return Quote(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=100,
        ask_size=100,
    )


def _bar(symbol: str, offset: int, close: str) -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        timeframe="M1",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=10_000,
    )


async def _record_fixture(out: Path) -> None:
    async with FeedRecorder(out) as rec:
        rec.record_quote(_quote("AVTX", 0, "4.21", "4.22"))
        rec.record_quote(_quote("AVTX", 1, "4.23", "4.24"))
        rec.record_quote(_quote("BBAI", 0, "9.50", "9.52"))
        rec.record_bar(_bar("AVTX", 0, "4.21"))
        rec.record_bar(_bar("AVTX", 60, "4.30"))
        rec.record_tape(
            Tape(symbol="AVTX", ts=T0, price=Decimal("4.22"), size=500, side=Side.BUY)
        )
        rec.record_headline(
            Headline(
                ticker="AVTX",
                ts=T0 + timedelta(seconds=2),
                source="Benzinga",
                title="AVTX Phase 3 Trial Hits Primary Endpoint",
                url="https://example.com/a",
            )
        )
        rec.record_float(
            FloatRecord(
                ticker="AVTX",
                as_of=date(2026, 4, 26),
                float_shares=8_500_000,
                shares_outstanding=12_000_000,
                source="benzinga",
            )
        )


async def test_quotes_roundtrip(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path, mode=ReplayMode.AS_FAST_AS_POSSIBLE)
    await replay.connect()
    out: list[Quote] = [q async for q in replay.subscribe_quotes(["AVTX"])]
    assert [q.symbol for q in out] == ["AVTX", "AVTX"]
    assert out[0].bid == Decimal("4.21")
    assert out[1].ask == Decimal("4.24")


async def test_bars_filter_by_timeframe(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    bars: list[Bar] = [
        b async for b in replay.subscribe_bars(["AVTX"], Timeframe.M1)
    ]
    assert len(bars) == 2
    bars_d1: list[Bar] = [
        b async for b in replay.subscribe_bars(["AVTX"], Timeframe.D1)
    ]
    assert bars_d1 == []


async def test_tape_roundtrip_preserves_side(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    tapes: list[Tape] = [t async for t in replay.subscribe_tape(["AVTX"])]
    assert len(tapes) == 1
    assert tapes[0].side is Side.BUY
    assert tapes[0].price == Decimal("4.22")


async def test_headline_roundtrip(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    heads: list[Headline] = [
        h async for h in replay.subscribe_headlines(["AVTX"])
    ]
    assert len(heads) == 1
    assert heads[0].url == "https://example.com/a"


async def test_float_roundtrip(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    rec = await replay.get_float("AVTX", date(2026, 4, 26))
    assert rec.float_shares == 8_500_000


async def test_float_missing_raises(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    with pytest.raises(MissingRecordingError):
        await replay.get_float("ZZZZ", date(2026, 4, 26))


async def test_historical_bars_window(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    replay = ReplayProvider(tmp_path)
    await replay.connect()
    bars = await replay.historical_bars(
        "AVTX",
        start=T0,
        end=T0 + timedelta(seconds=30),
        timeframe=Timeframe.M1,
    )
    assert len(bars) == 1
    assert bars[0].ts == T0


async def test_realtime_mode_paces_with_virtual_clock(tmp_path: Path) -> None:
    await _record_fixture(tmp_path)
    clock = VirtualClock(T0)
    replay = ReplayProvider(tmp_path, mode=ReplayMode.REALTIME, clock=clock)
    await replay.connect()
    real = RealClock()
    real_t0 = real.monotonic()
    out = [q async for q in replay.subscribe_quotes(["AVTX"])]
    real_elapsed = real.monotonic() - real_t0
    # Two AVTX quotes at offsets 0 and 1 second; virtual clock should
    # advance by 1s. Real wall time stays small because VirtualClock
    # short-circuits sleep.
    assert clock.monotonic() == pytest.approx(1.0, abs=0.01)
    assert real_elapsed < 0.5
    assert len(out) == 2


async def test_recorder_is_idempotent_on_double_close(tmp_path: Path) -> None:
    rec = FeedRecorder(tmp_path)
    rec.record_quote(_quote("AVTX", 0, "1.00", "1.01"))
    await rec.close()
    await rec.close()  # must not raise
