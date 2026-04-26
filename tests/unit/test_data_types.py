"""Atom 1 — sanity checks for data layer value types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.data import Bar, FeedGap, FloatRecord, Headline, Quote, Side, Tape

UTC_NOW = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def test_quote_is_frozen_and_hashable() -> None:
    quote = Quote(
        symbol="AVTX",
        ts=UTC_NOW,
        bid=Decimal("4.21"),
        ask=Decimal("4.22"),
        bid_size=100,
        ask_size=200,
    )
    with pytest.raises(FrozenInstanceError):
        quote.bid = Decimal("0")  # type: ignore[misc]
    assert hash(quote) == hash(quote)


def test_bar_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="tz-aware UTC"):
        Bar(
            symbol="AVTX",
            ts=datetime(2026, 4, 26, 14, 30),  # naive
            timeframe="M1",
            open=Decimal("4.20"),
            high=Decimal("4.30"),
            low=Decimal("4.18"),
            close=Decimal("4.25"),
            volume=12_345,
        )


def test_tape_defaults_to_unknown_side() -> None:
    tape = Tape(symbol="AVTX", ts=UTC_NOW, price=Decimal("4.22"), size=500)
    assert tape.side is Side.UNKNOWN


def test_headline_dedup_key_normalizes_title_and_ticker() -> None:
    a = Headline(
        ticker="avtx",
        ts=UTC_NOW,
        source="Benzinga",
        title="  AVTX Announces  Phase 3 Trial Success ",
    )
    b = Headline(
        ticker="AVTX",
        ts=UTC_NOW,
        source="Benzinga",
        title="AVTX Announces Phase 3 Trial Success",
    )
    assert a.dedup_key == b.dedup_key


def test_headline_dedup_key_distinguishes_sources() -> None:
    a = Headline(ticker="AVTX", ts=UTC_NOW, source="Benzinga", title="x")
    b = Headline(ticker="AVTX", ts=UTC_NOW, source="Polygon", title="x")
    assert a.dedup_key != b.dedup_key


def test_float_record_holds_date() -> None:
    rec = FloatRecord(
        ticker="AVTX",
        as_of=date(2026, 4, 26),
        float_shares=8_500_000,
        shares_outstanding=12_000_000,
        source="benzinga",
    )
    assert rec.as_of == date(2026, 4, 26)


def test_feed_gap_validates_both_ends_utc() -> None:
    with pytest.raises(ValueError, match="tz-aware UTC"):
        FeedGap(
            symbol="AVTX",
            start=UTC_NOW,
            end=datetime(2026, 4, 26, 14, 31),  # naive
            reason="websocket-closed",
        )
