"""Atom A2 — rank_picks (issue #41)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ross_trading.scanner.ranking import float_tier_weight, rank_picks
from ross_trading.scanner.types import ScannerPick

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick(ticker: str, pct_change: str, float_shares: int = 8_000_000) -> ScannerPick:
    return ScannerPick(
        ticker=ticker,
        ts=T0,
        rel_volume=Decimal("8.0"),
        pct_change=Decimal(pct_change),
        price=Decimal("5.00"),
        float_shares=float_shares,
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


# --- Float-tier policy (ISSUE-008 / arch §3.1) ---------------------------


def test_float_tier_weight_boundaries() -> None:
    """Just-below-10M is preferred; exactly-10M is acceptable; >20M is OOS."""
    assert float_tier_weight(9_999_999) == 2
    assert float_tier_weight(10_000_000) == 1
    assert float_tier_weight(10_000_001) == 1
    assert float_tier_weight(20_000_000) == 1
    assert float_tier_weight(20_000_001) == 0
    assert float_tier_weight(50_000_000) == 0


def test_rank_prefers_smaller_float_at_equal_pct_change() -> None:
    """Equal pct_change: preferred (<10M) ranks ahead of acceptable (10-20M)."""
    preferred = _pick("ZZZZ", "15", float_shares=5_000_000)
    acceptable = _pick("AAAA", "15", float_shares=15_000_000)
    ranked = rank_picks([acceptable, preferred], n=2)
    assert [p.ticker for p in ranked] == ["ZZZZ", "AAAA"]
    assert [p.rank for p in ranked] == [1, 2]


def test_rank_pct_change_dominates_tier() -> None:
    """A higher pct_change beats a smaller float -- tier is the secondary key."""
    higher_pct = _pick("BIG", "30", float_shares=18_000_000)
    smaller_float = _pick("TINY", "20", float_shares=4_000_000)
    ranked = rank_picks([smaller_float, higher_pct], n=2)
    assert [p.ticker for p in ranked] == ["BIG", "TINY"]


def test_rank_tier_then_ticker_tiebreak() -> None:
    """Equal pct + equal tier falls back to ticker ascending."""
    a = _pick("BBBB", "15", float_shares=8_000_000)
    b = _pick("AAAA", "15", float_shares=9_000_000)
    ranked = rank_picks([a, b], n=2)
    assert [p.ticker for p in ranked] == ["AAAA", "BBBB"]


def test_rank_preferred_below_10m_boundary() -> None:
    """9_999_999 is preferred (weight 2); 10_000_000 is acceptable (weight 1)."""
    just_below = _pick("PREF", "12", float_shares=9_999_999)
    exactly_at = _pick("ACCT", "12", float_shares=10_000_000)
    ranked = rank_picks([exactly_at, just_below], n=2)
    assert [p.ticker for p in ranked] == ["PREF", "ACCT"]
