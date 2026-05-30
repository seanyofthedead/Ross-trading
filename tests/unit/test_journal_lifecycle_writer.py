"""A3 (#91) -- JournalWriter trade-lifecycle methods.

Drives the small writer surface (open_position, record_order, record_fill,
close_position, record_risk_event, record_regime_snapshot) and reconstructs
a single trade end-to-end through the writer API alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ross_trading.journal.engine import create_journal_engine, create_session_factory
from ross_trading.journal.models import (
    Base,
    ExitReason,
    Fill,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    Regime,
    RegimeSnapshot,
    RiskEvent,
    RiskEventKind,
    Trade,
)
from ross_trading.journal.writer import JournalWriter

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

_OPEN_TS = datetime(2026, 5, 4, 14, 30, tzinfo=UTC)
_CLOSE_TS = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_journal_engine("sqlite://")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def writer(session_factory: sessionmaker[Session]) -> JournalWriter:
    return JournalWriter(session_factory)


def test_open_position_persists_open_row(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    pos_id = writer.open_position(
        ticker="abcd", requested_shares=500, opened_ts=_OPEN_TS
    )
    with session_factory() as session:
        pos = session.get(Position, pos_id)
    assert pos is not None
    assert pos.ticker == "ABCD"
    assert pos.status is PositionStatus.OPEN
    assert pos.closed_ts is None


def test_record_order_and_fill_chain(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    order_id = writer.record_order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKETABLE_LIMIT,
        intent=OrderIntent.ENTRY,
        status=OrderStatus.SUBMITTED,
        requested_shares=500,
        created_ts=_OPEN_TS,
        limit_price=Decimal("3.50"),
        stop_price=Decimal("3.20"),
        target_price=Decimal("4.10"),
    )
    fill_id = writer.record_fill(
        order_id=order_id,
        filled_shares=500,
        fill_price=Decimal("3.51"),
        fill_ts=_OPEN_TS,
    )
    with session_factory() as session:
        order = session.get(Order, order_id)
        fill = session.get(Fill, fill_id)
    assert order is not None
    assert order.stop_price == Decimal("3.20")
    assert fill is not None
    assert fill.order_id == order_id
    assert fill.filled_shares == 500


def test_close_position_writes_trade_and_flips_status(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    pos_id = writer.open_position(
        ticker="ABCD", requested_shares=500, opened_ts=_OPEN_TS, filled_shares=500
    )
    trade_id = writer.close_position(
        position_id=pos_id,
        closed_ts=_CLOSE_TS,
        realized_pnl=Decimal("285.00"),
        exit_reason=ExitReason.TARGET_HIT,
        opened_ts=_OPEN_TS,
    )
    with session_factory() as session:
        pos = session.get(Position, pos_id)
        trade = session.get(Trade, trade_id)
    assert pos is not None
    assert pos.status is PositionStatus.CLOSED
    assert pos.closed_ts == _CLOSE_TS
    assert trade is not None
    assert trade.position_id == pos_id
    assert trade.realized_pnl == Decimal("285.00")
    assert trade.exit_reason is ExitReason.TARGET_HIT


def test_close_position_unknown_id_raises(writer: JournalWriter) -> None:
    with pytest.raises(ValueError, match="no Position with id"):
        writer.close_position(
            position_id=4242,
            closed_ts=_CLOSE_TS,
            realized_pnl=Decimal("0"),
            exit_reason=ExitReason.HARD_STOP,
            opened_ts=_OPEN_TS,
        )


def test_record_risk_event(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    event_id = writer.record_risk_event(
        event_ts=_OPEN_TS,
        kind=RiskEventKind.DAILY_MAX_LOSS,
        reason="daily loss -100",
    )
    with session_factory() as session:
        event = session.get(RiskEvent, event_id)
    assert event is not None
    assert event.kind is RiskEventKind.DAILY_MAX_LOSS
    assert event.related_ticker is None


def test_record_regime_snapshot(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    snap_id = writer.record_regime_snapshot(
        snapshot_ts=_OPEN_TS,
        regime=Regime.HOT,
        score=Decimal("3.10"),
        components='{"gappers": 2.0}',
    )
    with session_factory() as session:
        snap = session.get(RegimeSnapshot, snap_id)
    assert snap is not None
    assert snap.regime is Regime.HOT
    assert snap.score == Decimal("3.10")


def test_full_trade_reconstruction_through_writer(
    writer: JournalWriter, session_factory: sessionmaker[Session]
) -> None:
    """End-to-end via the writer: open -> entry+fills -> exit+fill -> close.

    Then reconstruct realized P&L from the stored fills and reconcile
    against the trade row -- the #91 acceptance criterion, exercised
    through the public writer API.
    """
    pos_id = writer.open_position(
        ticker="ABCD", requested_shares=500, opened_ts=_OPEN_TS
    )
    entry_id = writer.record_order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKETABLE_LIMIT,
        intent=OrderIntent.ENTRY,
        status=OrderStatus.FILLED,
        requested_shares=500,
        created_ts=_OPEN_TS,
        limit_price=Decimal("3.50"),
        stop_price=Decimal("3.20"),
        target_price=Decimal("4.10"),
        position_id=pos_id,
    )
    writer.record_fill(
        order_id=entry_id, filled_shares=300, fill_price=Decimal("3.50"),
        fill_ts=_OPEN_TS,
    )
    writer.record_fill(
        order_id=entry_id, filled_shares=200, fill_price=Decimal("3.55"),
        fill_ts=_OPEN_TS,
    )
    exit_id = writer.record_order(
        ticker="ABCD",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        status=OrderStatus.FILLED,
        requested_shares=500,
        created_ts=_CLOSE_TS,
        position_id=pos_id,
    )
    writer.record_fill(
        order_id=exit_id, filled_shares=500, fill_price=Decimal("4.10"),
        fill_ts=_CLOSE_TS,
    )
    # 500*4.10 - (300*3.50 + 200*3.55) = 2050 - 1760 = 290.00
    trade_id = writer.close_position(
        position_id=pos_id,
        closed_ts=_CLOSE_TS,
        realized_pnl=Decimal("290.00"),
        exit_reason=ExitReason.TARGET_HIT,
        opened_ts=_OPEN_TS,
    )

    with session_factory() as session:
        trade = session.get(Trade, trade_id)
        assert trade is not None
        orders = session.scalars(
            select(Order).where(Order.position_id == pos_id)
        ).all()
        order_ids = [o.id for o in orders]
        side_by_order = {o.id: o.side for o in orders}
        fills = session.scalars(
            select(Fill).where(Fill.order_id.in_(order_ids))
        ).all()

    recomputed = Decimal("0")
    for f in fills:
        notional = Decimal(f.filled_shares) * f.fill_price
        if side_by_order[f.order_id] is OrderSide.SELL:
            recomputed += notional
        else:
            recomputed -= notional

    assert recomputed == trade.realized_pnl == Decimal("290.00")
    assert trade.exit_reason is ExitReason.TARGET_HIT
