"""Atom A2 — rank_picks (issue #41)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ross_trading.scanner.ranking import rank_picks
from ross_trading.scanner.types import ScannerPick

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick(ticker: str, pct_change: str) -> ScannerPick:
    return ScannerPick(
        ticker=ticker,
        ts=T0,
        rel_volume=Decimal("8.0"),
        pct_change=Decimal(pct_change),
        price=Decimal("5.00"),
        float_shares=8_000_000,
        news_present=False,
        headline_count=0,
    )


def test_rank_empty_returns_empty() -> None:
    assert rank_picks([]) == []


def test_rank_assigns_1_through_n() -> None:
    picks = [_pick("A", "10"), _pick("B", "20"), _pick("C", "15")]
    ranked = rank_picks(picks, n=3)
    assert [p.ticker for p in ranked] == ["B", "C", "A"]
    assert [p.rank for p in ranked] == [1, 2, 3]


def test_rank_truncates_to_top_n() -> None:
    pcts = [10, 20, 15, 5, 25, 12, 18]
    picks = [_pick(t, str(p)) for t, p in zip("ABCDEFG", pcts, strict=True)]
    ranked = rank_picks(picks, n=5)
    # Sorted by pct desc: E=25, B=20, G=18, C=15, F=12 ; A=10 and D=5 dropped.
    assert [p.ticker for p in ranked] == ["E", "B", "G", "C", "F"]
    assert [p.rank for p in ranked] == [1, 2, 3, 4, 5]


def test_rank_tie_break_by_ticker_ascending() -> None:
    picks = [_pick("ZZZZ", "15"), _pick("AAAA", "15"), _pick("MMMM", "15")]
    ranked = rank_picks(picks, n=3)
    assert [p.ticker for p in ranked] == ["AAAA", "MMMM", "ZZZZ"]
    assert [p.rank for p in ranked] == [1, 2, 3]


def test_rank_tie_break_independent_of_input_order() -> None:
    """Same picks in two different input orders -> same output."""
    a = [_pick("ZZZZ", "15"), _pick("AAAA", "15"), _pick("MMMM", "15")]
    b = [_pick("MMMM", "15"), _pick("AAAA", "15"), _pick("ZZZZ", "15")]
    assert rank_picks(a) == rank_picks(b)


def test_rank_default_n_is_5() -> None:
    picks = [_pick(c, str(i)) for i, c in enumerate("ABCDEFG", start=1)]
    ranked = rank_picks(picks)
    assert len(ranked) == 5


def test_rank_zero_n_returns_empty() -> None:
    picks = [_pick("A", "10"), _pick("B", "20")]
    assert rank_picks(picks, n=0) == []


def test_rank_negative_n_returns_empty() -> None:
    picks = [_pick("A", "10")]
    assert rank_picks(picks, n=-1) == []


def test_rank_n_larger_than_input_returns_all_ranked() -> None:
    picks = [_pick("A", "10"), _pick("B", "20")]
    ranked = rank_picks(picks, n=100)
    assert [p.ticker for p in ranked] == ["B", "A"]
    assert [p.rank for p in ranked] == [1, 2]


def test_rank_overwrites_input_rank_field() -> None:
    """Pre-rank picks have rank=0; ranker overwrites regardless of input."""
    picks = [
        ScannerPick(
            ticker="A", ts=T0, rel_volume=Decimal("8"), pct_change=Decimal("10"),
            price=Decimal("5"), float_shares=8_000_000, news_present=False,
            headline_count=0, rank=99,
        ),
    ]
    ranked = rank_picks(picks)
    assert ranked[0].rank == 1
