"""A3 (#91) -- round-trip the trade-lifecycle models against in-memory SQLite.

Mirrors ``tests/unit/test_journal_models.py``: model construction,
``@validates`` ticker normalization, enum round-trips, and a single-trade
reconstruction walk (orders -> fills -> position -> trade) proving P&L /
exit are recoverable from stored rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

_TS = datetime(2026, 5, 4, 14, 30, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


# --------------------------------------------------------------- positions


def test_position_open_round_trip(session: Session) -> None:
    pos = Position(
        ticker="ABCD",
        status=PositionStatus.OPEN,
        requested_shares=1000,
        filled_shares=600,
        opened_ts=_TS,
    )
    session.add(pos)
    session.commit()

    fetched = session.get(Position, pos.id)
    assert fetched is not None
    assert fetched.ticker == "ABCD"
    assert fetched.status is PositionStatus.OPEN
    assert fetched.requested_shares == 1000
    assert fetched.filled_shares == 600
    assert fetched.opened_ts == _TS
    assert fetched.closed_ts is None


def test_position_normalizes_ticker(session: Session) -> None:
    pos = Position(
        ticker="  abcd\t",
        status=PositionStatus.OPEN,
        requested_shares=100,
        filled_shares=0,
        opened_ts=_TS,
    )
    assert pos.ticker == "ABCD"


# ------------------------------------------------------------------- orders


def test_order_entry_round_trip(session: Session) -> None:
    order = Order(
        ticker="abcd",
        side=OrderSide.BUY,
        order_type=OrderType.MARKETABLE_LIMIT,
        intent=OrderIntent.ENTRY,
        status=OrderStatus.SUBMITTED,
        requested_shares=500,
        limit_price=Decimal("3.50"),
        stop_price=Decimal("3.20"),
        target_price=Decimal("4.10"),
        created_ts=_TS,
    )
    assert order.ticker == "ABCD"  # normalized at __init__
    session.add(order)
    session.commit()

    fetched = session.get(Order, order.id)
    assert fetched is not None
    assert fetched.side is OrderSide.BUY
    assert fetched.order_type is OrderType.MARKETABLE_LIMIT
    assert fetched.intent is OrderIntent.ENTRY
    assert fetched.status is OrderStatus.SUBMITTED
    assert fetched.stop_price == Decimal("3.20")
    assert fetched.target_price == Decimal("4.10")


def test_order_rejects_non_decimal_price(session: Session) -> None:
    order = Order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        status=OrderStatus.PENDING,
        requested_shares=100,
        created_ts=_TS,
    )
    order.limit_price = 3.42  # type: ignore[assignment]  # float, not Decimal
    session.add(order)
    with pytest.raises(StatementError, match="DecimalText requires Decimal"):
        session.flush()


def test_order_rejects_naive_datetime(session: Session) -> None:
    order = Order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        status=OrderStatus.PENDING,
        requested_shares=100,
        created_ts=datetime(2026, 5, 4, 14, 30),  # naive
    )
    session.add(order)
    with pytest.raises(StatementError, match="tz-aware"):
        session.flush()


# -------------------------------------------------------------------- fills


def test_fill_round_trip(session: Session) -> None:
    order = Order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        status=OrderStatus.FILLED,
        requested_shares=300,
        created_ts=_TS,
    )
    session.add(order)
    session.flush()

    fill = Fill(
        order_id=order.id,
        filled_shares=120,
        fill_price=Decimal("3.515"),
        fill_ts=_TS,
    )
    session.add(fill)
    session.commit()

    fetched = session.get(Fill, fill.id)
    assert fetched is not None
    assert fetched.order_id == order.id
    assert fetched.filled_shares == 120
    assert fetched.fill_price == Decimal("3.515")
    assert fetched.order.ticker == "ABCD"


# ------------------------------------------------------------------- trades


def test_trade_round_trip(session: Session) -> None:
    pos = Position(
        ticker="ABCD",
        status=PositionStatus.CLOSED,
        requested_shares=500,
        filled_shares=500,
        opened_ts=_TS,
        closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
    )
    session.add(pos)
    session.flush()

    trade = Trade(
        position_id=pos.id,
        realized_pnl=Decimal("145.50"),
        opened_ts=_TS,
        closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
        exit_reason=ExitReason.TARGET_HIT,
    )
    session.add(trade)
    session.commit()

    fetched = session.get(Trade, trade.id)
    assert fetched is not None
    assert fetched.realized_pnl == Decimal("145.50")
    assert fetched.exit_reason is ExitReason.TARGET_HIT
    assert fetched.position.id == pos.id


@pytest.mark.parametrize("reason", list(ExitReason))
def test_trade_exit_reason_round_trip(session: Session, reason: ExitReason) -> None:
    pos = Position(
        ticker="ABCD",
        status=PositionStatus.CLOSED,
        requested_shares=100,
        filled_shares=100,
        opened_ts=_TS,
        closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
    )
    session.add(pos)
    session.flush()
    trade = Trade(
        position_id=pos.id,
        realized_pnl=Decimal("-10.00"),
        opened_ts=_TS,
        closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
        exit_reason=reason,
    )
    session.add(trade)
    session.commit()
    fetched = session.get(Trade, trade.id)
    assert fetched is not None
    assert fetched.exit_reason is reason


# -------------------------------------------------------------- risk events


@pytest.mark.parametrize("kind", list(RiskEventKind))
def test_risk_event_round_trip(session: Session, kind: RiskEventKind) -> None:
    event = RiskEvent(
        event_ts=_TS,
        kind=kind,
        reason="rule tripped",
        related_ticker="abcd",
    )
    assert event.related_ticker == "ABCD"
    session.add(event)
    session.commit()
    fetched = session.get(RiskEvent, event.id)
    assert fetched is not None
    assert fetched.kind is kind
    assert fetched.related_ticker == "ABCD"


def test_risk_event_account_wide_has_no_ticker(session: Session) -> None:
    event = RiskEvent(
        event_ts=_TS,
        kind=RiskEventKind.DAILY_MAX_LOSS,
        reason="daily loss -100",
        related_ticker=None,
    )
    assert event.related_ticker is None
    session.add(event)
    session.commit()
    fetched = session.get(RiskEvent, event.id)
    assert fetched is not None
    assert fetched.related_ticker is None


# --------------------------------------------------------- regime snapshots


@pytest.mark.parametrize("regime", list(Regime))
def test_regime_snapshot_round_trip(session: Session, regime: Regime) -> None:
    snap = RegimeSnapshot(
        snapshot_ts=_TS,
        regime=regime,
        score=Decimal("2.75"),
        components='{"gappers": 1.2, "range": 0.9}',
    )
    session.add(snap)
    session.commit()
    fetched = session.get(RegimeSnapshot, snap.id)
    assert fetched is not None
    assert fetched.regime is regime
    assert fetched.score == Decimal("2.75")
    assert fetched.components == '{"gappers": 1.2, "range": 0.9}'


# --------------------------- enum vocabulary contracts (rename = migration)


def test_exit_reason_vocabulary() -> None:
    assert [r.value for r in ExitReason] == [
        "target_hit",
        "hard_stop",
        "jackknife",
        "macd_cross",
        "volume_dryup",
        "first_red_candle",
        "l2_weakness",
        "dilutive_news",
        "force_flatten",
    ]


def test_risk_event_kind_vocabulary() -> None:
    assert [k.value for k in RiskEventKind] == [
        "entry_blocked",
        "lockout_tripped",
        "force_flatten",
        "daily_max_loss",
        "consecutive_losers",
    ]


def test_regime_vocabulary() -> None:
    assert [r.value for r in Regime] == ["cold", "neutral", "warm", "hot"]


# ----------------------------------- single-trade reconstruction (#91 AC)


def test_single_trade_reconstruction_from_stored_events(session: Session) -> None:
    """Write orders -> fills -> position -> trade, then reconstruct P&L/exit.

    Acceptance criterion: a single trade's economics must be recoverable
    from the stored rows alone. We seed a long entry (two partial fills),
    a sell exit (one fill), close the position, and recompute realized
    P&L from the fills -- then assert it reconciles with the stored
    ``Trade.realized_pnl`` and that the exit reason survived the trip.
    """
    pos = Position(
        ticker="ABCD",
        status=PositionStatus.OPEN,
        requested_shares=500,
        filled_shares=0,
        opened_ts=_TS,
    )
    session.add(pos)
    session.flush()

    entry = Order(
        ticker="ABCD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKETABLE_LIMIT,
        intent=OrderIntent.ENTRY,
        status=OrderStatus.FILLED,
        requested_shares=500,
        limit_price=Decimal("3.50"),
        stop_price=Decimal("3.20"),
        target_price=Decimal("4.10"),
        position_id=pos.id,
        created_ts=_TS,
    )
    session.add(entry)
    session.flush()
    # Two partial fills (#19) totalling 500 shares.
    session.add_all([
        Fill(order_id=entry.id, filled_shares=300, fill_price=Decimal("3.50"),
             fill_ts=_TS),
        Fill(order_id=entry.id, filled_shares=200, fill_price=Decimal("3.55"),
             fill_ts=_TS),
    ])

    exit_ts = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)
    exit_order = Order(
        ticker="ABCD",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        status=OrderStatus.FILLED,
        requested_shares=500,
        position_id=pos.id,
        created_ts=exit_ts,
    )
    session.add(exit_order)
    session.flush()
    session.add(
        Fill(order_id=exit_order.id, filled_shares=500, fill_price=Decimal("4.10"),
             fill_ts=exit_ts)
    )

    pos.filled_shares = 500
    pos.status = PositionStatus.CLOSED
    pos.closed_ts = exit_ts

    # Compute realized P&L from fills: SELL proceeds - BUY cost.
    buy_cost = Decimal("300") * Decimal("3.50") + Decimal("200") * Decimal("3.55")
    sell_proceeds = Decimal("500") * Decimal("4.10")
    realized = sell_proceeds - buy_cost

    trade = Trade(
        position_id=pos.id,
        realized_pnl=realized,
        opened_ts=_TS,
        closed_ts=exit_ts,
        exit_reason=ExitReason.TARGET_HIT,
    )
    session.add(trade)
    session.commit()

    # --- reconstruction walk: start from the trade, recover everything ---
    stored_trade = session.scalars(
        select(Trade).where(Trade.position_id == pos.id)
    ).one()
    stored_pos = session.get(Position, stored_trade.position_id)
    assert stored_pos is not None
    assert stored_pos.status is PositionStatus.CLOSED

    orders = session.scalars(
        select(Order).where(Order.position_id == stored_pos.id)
    ).all()
    order_ids = [o.id for o in orders]
    fills = session.scalars(
        select(Fill).where(Fill.order_id.in_(order_ids))
    ).all()

    side_by_order = {o.id: o.side for o in orders}
    recomputed = Decimal("0")
    for f in fills:
        notional = Decimal(f.filled_shares) * f.fill_price
        if side_by_order[f.order_id] is OrderSide.SELL:
            recomputed += notional
        else:
            recomputed -= notional

    assert recomputed == stored_trade.realized_pnl == realized
    # 500*4.10 - (300*3.50 + 200*3.55) = 2050 - 1760 = 290.00
    assert stored_trade.realized_pnl == Decimal("290.00")
    assert stored_trade.exit_reason is ExitReason.TARGET_HIT
