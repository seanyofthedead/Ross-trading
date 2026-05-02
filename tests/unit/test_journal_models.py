"""Atom A4 (#43) -- round-trip every journal model against in-memory SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

from ross_trading.journal.engine import create_journal_engine
from ross_trading.journal.models import (
    Base,
    DecisionKind,
    Pick,
    RejectionReason,
    ScannerDecision,
    WatchlistEntry,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _make_pick(
    *,
    ticker: str = "ABCD",
    rank: int = 1,
    headline_count: int = 4,
) -> Pick:
    return Pick(
        ticker=ticker,
        ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        rel_volume=Decimal("12.5"),
        pct_change=Decimal("18.75"),
        price=Decimal("3.42"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=headline_count,
        rank=rank,
    )


def test_pick_round_trip(session: Session) -> None:
    pick = _make_pick()
    session.add(pick)
    session.commit()

    fetched = session.get(Pick, pick.id)
    assert fetched is not None
    assert fetched.ticker == "ABCD"
    assert fetched.ts == datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    assert fetched.rel_volume == Decimal("12.5")
    assert fetched.pct_change == Decimal("18.75")
    assert fetched.price == Decimal("3.42")
    assert fetched.float_shares == 8_500_000
    assert fetched.news_present is True
    assert fetched.headline_count == 4
    assert fetched.rank == 1


def test_pick_preserves_decimal_precision(session: Session) -> None:
    """SQLAlchemy ``Numeric`` would degrade these to FLOAT on SQLite."""
    pick = _make_pick()
    pick.rel_volume = Decimal("12.123456789012345678")
    pick.pct_change = Decimal("0.000000000000000001")
    session.add(pick)
    session.commit()

    fetched = session.get(Pick, pick.id)
    assert fetched is not None
    assert fetched.rel_volume == Decimal("12.123456789012345678")
    assert fetched.pct_change == Decimal("0.000000000000000001")


def test_pick_normalizes_non_utc_tz_to_utc(session: Session) -> None:
    """Non-UTC tz inputs are stored as UTC and load back as UTC."""
    et = timezone(timedelta(hours=-4))
    pick = _make_pick()
    pick.ts = datetime(2026, 5, 2, 10, 30, tzinfo=et)
    session.add(pick)
    session.commit()

    fetched = session.get(Pick, pick.id)
    assert fetched is not None
    assert fetched.ts == datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    assert fetched.ts.tzinfo == UTC


def test_pick_rejects_naive_datetime(session: Session) -> None:
    pick = _make_pick()
    pick.ts = datetime(2026, 5, 2, 14, 30)  # naive
    session.add(pick)
    with pytest.raises(StatementError, match="tz-aware"):
        session.flush()


def test_watchlist_entry_open_membership(session: Session) -> None:
    pick = _make_pick()
    session.add(pick)
    session.flush()

    entry = WatchlistEntry(
        ticker="ABCD",
        pick_id=pick.id,
        added_at=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
    )
    session.add(entry)
    session.commit()

    fetched = session.get(WatchlistEntry, entry.id)
    assert fetched is not None
    assert fetched.ticker == "ABCD"
    assert fetched.pick_id == pick.id
    assert fetched.added_at == datetime(2026, 5, 2, 14, 31, tzinfo=UTC)
    assert fetched.removed_at is None
    assert fetched.pick.id == pick.id


def test_watchlist_entry_closed_membership(session: Session) -> None:
    pick = _make_pick()
    session.add(pick)
    session.flush()

    entry = WatchlistEntry(
        ticker="ABCD",
        pick_id=pick.id,
        added_at=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
        removed_at=datetime(2026, 5, 2, 15, 0, tzinfo=UTC),
    )
    session.add(entry)
    session.commit()

    fetched = session.get(WatchlistEntry, entry.id)
    assert fetched is not None
    assert fetched.removed_at == datetime(2026, 5, 2, 15, 0, tzinfo=UTC)


def test_active_watchlist_partial_index_query(session: Session) -> None:
    """The lookup A7 (#46) will run -- "ABCD currently on the watchlist?"."""
    pick = _make_pick()
    session.add(pick)
    session.flush()

    closed = WatchlistEntry(
        ticker="ABCD",
        pick_id=pick.id,
        added_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        removed_at=datetime(2026, 5, 2, 14, 45, tzinfo=UTC),
    )
    open_ = WatchlistEntry(
        ticker="ABCD",
        pick_id=pick.id,
        added_at=datetime(2026, 5, 2, 15, 0, tzinfo=UTC),
    )
    session.add_all([closed, open_])
    session.commit()

    active = session.scalars(
        select(WatchlistEntry).where(
            WatchlistEntry.ticker == "ABCD",
            WatchlistEntry.removed_at.is_(None),
        ),
    ).all()
    assert len(active) == 1
    assert active[0].id == open_.id


def test_decision_picked_round_trip(session: Session) -> None:
    pick = _make_pick()
    session.add(pick)
    session.flush()

    decision = ScannerDecision(
        kind=DecisionKind.PICKED,
        decision_ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ticker="ABCD",
        pick_id=pick.id,
    )
    session.add(decision)
    session.commit()

    fetched = session.get(ScannerDecision, decision.id)
    assert fetched is not None
    assert fetched.kind == DecisionKind.PICKED
    assert fetched.ticker == "ABCD"
    assert fetched.pick_id == pick.id
    assert fetched.pick is not None
    assert fetched.pick.ticker == "ABCD"
    assert fetched.reason is None
    assert fetched.gap_start is None
    assert fetched.gap_end is None
    assert fetched.rejection_reason is None


def test_decision_stale_feed_round_trip(session: Session) -> None:
    decision = ScannerDecision(
        kind=DecisionKind.STALE_FEED,
        decision_ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ticker=None,
        reason="quote feed stale (5.2s since last tick)",
    )
    session.add(decision)
    session.commit()

    fetched = session.get(ScannerDecision, decision.id)
    assert fetched is not None
    assert fetched.kind == DecisionKind.STALE_FEED
    assert fetched.ticker is None
    assert fetched.pick_id is None
    assert fetched.reason == "quote feed stale (5.2s since last tick)"


def test_decision_feed_gap_round_trip(session: Session) -> None:
    decision = ScannerDecision(
        kind=DecisionKind.FEED_GAP,
        decision_ts=datetime(2026, 5, 2, 14, 35, tzinfo=UTC),
        gap_start=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        gap_end=datetime(2026, 5, 2, 14, 33, tzinfo=UTC),
    )
    session.add(decision)
    session.commit()

    fetched = session.get(ScannerDecision, decision.id)
    assert fetched is not None
    assert fetched.kind == DecisionKind.FEED_GAP
    assert fetched.gap_start == datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    assert fetched.gap_end == datetime(2026, 5, 2, 14, 33, tzinfo=UTC)


@pytest.mark.parametrize(
    "reason",
    list(RejectionReason),
)
def test_decision_rejected_round_trip(
    session: Session,
    reason: RejectionReason,
) -> None:
    decision = ScannerDecision(
        kind=DecisionKind.REJECTED,
        decision_ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ticker="WXYZ",
        rejection_reason=reason,
    )
    session.add(decision)
    session.commit()

    fetched = session.get(ScannerDecision, decision.id)
    assert fetched is not None
    assert fetched.kind == DecisionKind.REJECTED
    assert fetched.rejection_reason == reason
    assert fetched.pick_id is None


def test_rejection_reason_order_matches_filter_chain() -> None:
    """The enum order encodes 'first failing filter' priority -- A4 contract.

    A rename or reorder is a contract break; lock it down here so #51's
    wiring lands without surprises.
    """
    assert [r.value for r in RejectionReason] == [
        "no_snapshot",
        "missing_baseline",
        "missing_float",
        "rel_volume",
        "pct_change",
        "price_band",
        "float_size",
    ]


def test_decision_kind_includes_reserved_rejected() -> None:
    assert {k.value for k in DecisionKind} == {
        "picked",
        "stale_feed",
        "feed_gap",
        "rejected",
    }
