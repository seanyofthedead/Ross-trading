"""Atom A1 — scanner filter primitives (issue #40)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.filters import (
    float_le,
    headline_count,
    news_present,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar(
    *,
    symbol: str = "AVTX",
    ts: datetime | None = None,
    open_: str = "5.00",
    high: str = "5.50",
    low: str = "4.95",
    close: str = "5.50",
    volume: int = 1_000_000,
) -> Bar:
    return Bar(
        symbol=symbol,
        ts=ts or T0,
        timeframe="D1",
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


# ---------------------------------------------------------------- rel_volume_ge


@pytest.mark.parametrize(
    ("today_volume", "baseline", "threshold", "expected"),
    [
        (5_000_000, Decimal("1_000_000"), 5.0, True),   # exact 5.0x
        (5_000_001, Decimal("1_000_000"), 5.0, True),   # just above
        (4_999_999, Decimal("1_000_000"), 5.0, False),  # just below
        (10_000_000, Decimal("1_000_000"), 5.0, True),  # well above
    ],
)
def test_rel_volume_ge_boundaries(
    today_volume: int,
    baseline: Decimal,
    threshold: float,
    expected: bool,
) -> None:
    snapshot = _bar(volume=today_volume)
    assert rel_volume_ge("AVTX", snapshot, baseline, threshold) is expected


def test_rel_volume_ge_missing_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), None) is False


def test_rel_volume_ge_zero_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), Decimal("0")) is False


# ----------------------------------------------------------------- pct_change_ge


@pytest.mark.parametrize(
    ("current", "reference", "threshold_pct", "expected"),
    [
        ("5.50", "5.00", "10",  True),    # exact +10%
        ("5.501", "5.00", "10", True),    # just above
        ("5.499", "5.00", "10", False),   # just below
        ("10.00", "5.00", "10", True),    # well above
        ("4.50", "5.00", "10",  False),   # negative move
        ("5.50", "5.00", "5",   True),    # lower threshold passes
        ("5.50", "5.00", "20",  False),   # higher threshold fails
    ],
)
def test_pct_change_ge_boundaries(
    current: str, reference: str, threshold_pct: str, expected: bool,
) -> None:
    assert pct_change_ge(
        Decimal(current), Decimal(reference), Decimal(threshold_pct)
    ) is expected


def test_pct_change_ge_zero_reference_is_false() -> None:
    """Avoid divide-by-zero — return False rather than raising."""
    assert pct_change_ge(Decimal("1.00"), Decimal("0"), Decimal("10")) is False


# ----------------------------------------------------------------- price_in_band


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        ("1.00", True),    # exact low
        ("0.99", False),   # just below low
        ("1.01", True),    # just above low
        ("19.99", True),   # just below high
        ("20.00", True),   # exact high
        ("20.01", False),  # just above high
        ("5.50", True),    # mid-band
    ],
)
def test_price_in_band_default_bounds(close: str, expected: bool) -> None:
    snapshot = _bar(close=close)
    assert price_in_band("AVTX", snapshot) is expected


def test_price_in_band_custom_bounds() -> None:
    snapshot = _bar(close="50.00")
    assert price_in_band("AVTX", snapshot, low=Decimal("10"), high=Decimal("100")) is True
    assert price_in_band("AVTX", snapshot, low=Decimal("60"), high=Decimal("100")) is False


# --------------------------------------------------------------------- float_le


def _float(shares: int, ticker: str = "AVTX") -> FloatRecord:
    return FloatRecord(
        ticker=ticker,
        as_of=date(2026, 4, 26),
        float_shares=shares,
        shares_outstanding=shares * 2,
        source="test",
    )


@pytest.mark.parametrize(
    ("shares", "threshold", "expected"),
    [
        (20_000_000, 20_000_000, True),   # exact
        (19_999_999, 20_000_000, True),   # just below
        (20_000_001, 20_000_000, False),  # just above
        (5_000_000, 20_000_000, True),    # well below
    ],
)
def test_float_le_boundaries(shares: int, threshold: int, expected: bool) -> None:
    assert float_le(_float(shares), threshold) is expected


def test_float_le_missing_record_is_false() -> None:
    assert float_le(None) is False


# -------------------------------------------------------- news_present / count


def _h(
    *,
    title: str = "AVTX up on FDA approval",
    source: str = "Benzinga",
    ticker: str = "AVTX",
    ts: datetime | None = None,
) -> Headline:
    return Headline(ticker=ticker, ts=ts or T0, source=source, title=title)


def test_news_present_empty_is_false() -> None:
    assert news_present("AVTX", [], anchor_ts=T0) is False
    assert headline_count("AVTX", [], anchor_ts=T0) == 0


def test_news_present_within_window_is_true() -> None:
    headlines = [_h(ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_at_exact_window_edge_is_inclusive() -> None:
    """24h ago exactly is still in the window."""
    headlines = [_h(ts=T0 - timedelta(hours=24))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_at_exact_anchor_is_inclusive() -> None:
    """A headline timestamped at anchor_ts itself is included."""
    headlines = [_h(ts=T0)]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_outside_window_is_false() -> None:
    headlines = [_h(ts=T0 - timedelta(hours=24, seconds=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_future_headlines_excluded() -> None:
    """Strictly look backward from anchor_ts. A headline with ts > anchor_ts is ignored."""
    headlines = [_h(ts=T0 + timedelta(seconds=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_wrong_ticker_excluded() -> None:
    headlines = [_h(ticker="OTHER", ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_lowercase_ticker_query_matches() -> None:
    """Casing must not matter — Headline.dedup_key already upper-cases."""
    headlines = [_h(ticker="avtx", ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert news_present("avtx", headlines, anchor_ts=T0) is True


def test_headline_count_dedup_same_source_same_title() -> None:
    """Same (source, normalized_title, ticker) twice should count as 1."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_distinct_sources_not_deduped() -> None:
    """dedup_key includes source — Benzinga + Polygon are two distinct entries."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Polygon",  title="AVTX up on FDA approval", ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 2


def test_headline_count_normalized_title_dedupes() -> None:
    """HeadlineDeduper normalizes case + whitespace within the title."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Benzinga", title="  avtx UP  ON  fda APPROVAL ",
           ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_uses_fresh_deduper_per_call() -> None:
    """Two consecutive calls with the same headlines must each return 1."""
    headlines = [_h(ts=T0 - timedelta(hours=1))]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_custom_lookback_uses_matching_dedup_window() -> None:
    """If lookback_hours=2, the deduper window is also 2h."""
    headlines = [
        _h(source="Benzinga", title="story A", ts=T0 - timedelta(hours=3)),
        _h(source="Benzinga", title="story B", ts=T0 - timedelta(minutes=30)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0, lookback_hours=2) == 1
