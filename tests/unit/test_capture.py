"""Unit tests for the live capture composition (#87).

The composition wires a ``MarketDataProvider`` (and optional news / float
providers) behind :class:`ReconnectingProvider` and pipes every event into
a :class:`FeedRecorder`. These tests cover per-stream round-trip and the
optional-provider branches; the integration test covers the gap loop.
"""

from __future__ import annotations

import gzip
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.core.errors import FeedDisconnected, FeedError
from ross_trading.data.capture import capture_session
from ross_trading.data.market_feed import Timeframe
from ross_trading.data.types import (
    Bar,
    FloatRecord,
    Headline,
    Quote,
    Side,
    Tape,
)
from tests.fakes.float_ref import FakeFloatReferenceProvider
from tests.fakes.market import FakeMarketDataProvider
from tests.fakes.news import FakeNewsProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from pathlib import Path

T0 = datetime(2026, 5, 4, 13, 30, tzinfo=UTC)
DAY = date(2026, 5, 4)


def _quote(symbol: str, offset: int) -> Quote:
    return Quote(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        bid=Decimal("1.00"),
        ask=Decimal("1.01"),
        bid_size=100,
        ask_size=100,
    )


def _bar(symbol: str, offset: int, timeframe: str = "M1") -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        timeframe=timeframe,
        open=Decimal("1.00"),
        high=Decimal("1.05"),
        low=Decimal("0.95"),
        close=Decimal("1.02"),
        volume=10_000,
    )


def _tape(symbol: str, offset: int) -> Tape:
    return Tape(
        symbol=symbol,
        ts=T0 + timedelta(seconds=offset),
        price=Decimal("1.00"),
        size=100,
        side=Side.BUY,
    )


def _headline(symbol: str, offset: int) -> Headline:
    return Headline(
        ticker=symbol,
        ts=T0 + timedelta(seconds=offset),
        source="Benzinga",
        title=f"{symbol} headline {offset}",
    )


def _float(symbol: str) -> FloatRecord:
    return FloatRecord(
        ticker=symbol,
        as_of=DAY,
        float_shares=8_500_000,
        shares_outstanding=12_000_000,
        source="test",
    )


def _read_gz_lines(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


async def test_capture_records_quotes_and_bars(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider(
        quotes=[_quote("AVTX", 0), _quote("AVTX", 1)],
        bars=[_bar("AVTX", 0), _bar("AVTX", 60)],
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=None,
        universe=["AVTX"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
    )
    assert upstream.connect_calls == 1
    assert upstream.disconnect_calls == 1
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "quote.jsonl.gz")) == 2
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "bar.jsonl.gz")) == 2


async def test_capture_subscribes_each_requested_timeframe(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider(
        bars=[
            _bar("AVTX", 0, "M1"),
            _bar("AVTX", 60, "M1"),
            _bar("AVTX", 0, "D1"),
        ],
        timeframes=(Timeframe.M1, Timeframe.D1),
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=None,
        universe=["AVTX"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1, Timeframe.D1),
    )
    bar_lines = _read_gz_lines(tmp_path / DAY.isoformat() / "bar.jsonl.gz")
    assert len(bar_lines) == 3


async def test_capture_records_tape(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider(
        tape=[_tape("AVTX", 0), _tape("AVTX", 1), _tape("BBAI", 2)],
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=None,
        universe=["AVTX", "BBAI"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
    )
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "tape.jsonl.gz")) == 3


async def test_capture_records_headlines_when_news_provider_given(
    tmp_path: Path,
) -> None:
    upstream = FakeMarketDataProvider()
    news = FakeNewsProvider(
        headlines=[_headline("AVTX", 0), _headline("AVTX", 5)]
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=news,
        upstream_float=None,
        universe=["AVTX"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
    )
    assert news.connect_calls == 1
    assert news.disconnect_calls == 1
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "headline.jsonl.gz")) == 2


async def test_capture_skips_news_when_provider_is_none(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider()
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=None,
        universe=["AVTX"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
    )
    assert not (tmp_path / DAY.isoformat() / "headline.jsonl.gz").exists()


async def test_capture_records_floats_when_float_provider_given(
    tmp_path: Path,
) -> None:
    upstream = FakeMarketDataProvider()
    float_provider = FakeFloatReferenceProvider(
        records={("AVTX", DAY): _float("AVTX"), ("BBAI", DAY): _float("BBAI")}
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=float_provider,
        universe=["AVTX", "BBAI"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
        as_of=DAY,
    )
    assert sorted(float_provider.calls) == [("AVTX", DAY), ("BBAI", DAY)]
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "float.jsonl.gz")) == 2


async def test_capture_tolerates_missing_float_record(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider()
    float_provider = FakeFloatReferenceProvider(
        records={("AVTX", DAY): _float("AVTX")}  # ZZZZ deliberately missing
    )
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=float_provider,
        universe=["AVTX", "ZZZZ"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
        as_of=DAY,
    )
    # AVTX was recorded; ZZZZ raised FeedError and was skipped without
    # crashing the capture session.
    assert len(_read_gz_lines(tmp_path / DAY.isoformat() / "float.jsonl.gz")) == 1


async def test_capture_records_feed_gap_on_reconnect(tmp_path: Path) -> None:
    """Capture composition wires recorder behind the reconnect provider, so a
    disconnect/reconnect cycle persists a ``feed_gap`` row to disk."""

    class _FlakyMarket:
        @property
        def supported_timeframes(self) -> frozenset[Timeframe]:
            return frozenset({Timeframe.M1})

        def __init__(self) -> None:
            self._remaining_bars = [_bar("AVTX", 0), _bar("AVTX", 60)]
            self._dropped = False
            self.connect_calls = 0
            self.disconnect_calls = 0

        async def connect(self) -> None:
            self.connect_calls += 1

        async def disconnect(self) -> None:
            self.disconnect_calls += 1

        async def subscribe_quotes(
            self, symbols: Iterable[str]
        ) -> AsyncIterator[Quote]:
            del symbols
            if False:
                yield  # pragma: no cover

        async def subscribe_bars(
            self, symbols: Iterable[str], timeframe: Timeframe
        ) -> AsyncIterator[Bar]:
            del symbols, timeframe
            emitted = 0
            while self._remaining_bars:
                if not self._dropped and emitted == 1:
                    self._dropped = True
                    raise FeedDisconnected("websocket-closed")
                yield self._remaining_bars.pop(0)
                emitted += 1

        async def subscribe_tape(
            self, symbols: Iterable[str]
        ) -> AsyncIterator[Tape]:
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

    upstream = _FlakyMarket()
    await capture_session(
        upstream_market_data=upstream,
        upstream_news=None,
        upstream_float=None,
        universe=["AVTX"],
        output_dir=tmp_path,
        timeframes=(Timeframe.M1,),
    )
    gap_path = tmp_path / DAY.isoformat() / "feed_gap.jsonl.gz"
    assert gap_path.exists()
    gap_lines = _read_gz_lines(gap_path)
    assert len(gap_lines) == 1
    bar_lines = _read_gz_lines(tmp_path / DAY.isoformat() / "bar.jsonl.gz")
    assert len(bar_lines) == 2  # both bars survived the disconnect+reconnect


async def test_capture_rejects_unsupported_timeframe(tmp_path: Path) -> None:
    upstream = FakeMarketDataProvider(timeframes=(Timeframe.M1,))
    with pytest.raises(ValueError, match="not supported"):
        await capture_session(
            upstream_market_data=upstream,
            upstream_news=None,
            upstream_float=None,
            universe=["AVTX"],
            output_dir=tmp_path,
            timeframes=(Timeframe.S10,),
        )


async def test_capture_propagates_unrelated_errors(tmp_path: Path) -> None:
    """A non-FeedDisconnected error from a stream surfaces; capture does not hang."""

    class _ExplodingMarket(FakeMarketDataProvider):
        async def subscribe_quotes(
            self, symbols: Iterable[str]
        ) -> AsyncIterator[Quote]:
            del symbols
            msg = "boom"
            raise FeedError(msg)
            yield  # pragma: no cover

    upstream = _ExplodingMarket()
    with pytest.raises(FeedError, match="boom"):
        await capture_session(
            upstream_market_data=upstream,
            upstream_news=None,
            upstream_float=None,
            universe=["AVTX"],
            output_dir=tmp_path,
            timeframes=(Timeframe.M1,),
        )
