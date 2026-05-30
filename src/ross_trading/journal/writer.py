"""Concrete :class:`DecisionSink` backed by the SQLAlchemy session factory.

Phase 2 -- Atom A5 (#44), extended in A8 (#51) to route picks +
rejections through :meth:`record_scan`. Implements the
:class:`ross_trading.scanner.decisions.DecisionSink` Protocol against the
journal models defined in :mod:`ross_trading.journal.models`. Two surfaces:

* :meth:`JournalWriter.emit` -- one-row writes for ``stale_feed`` and
  ``feed_gap``. Each call opens a session, writes one decision row, and
  commits as its own atomic 1-row transaction. ``picked`` and ``rejected``
  rows are not routed through ``emit`` in production code (the writer's
  ``_add`` method rejects ``rejected`` at runtime; ``picked`` is supported
  for backward compatibility but no longer used by the loop).
* :meth:`JournalWriter.record_scan` -- the per-tick batch API used by
  :class:`ross_trading.scanner.loop.ScannerLoop` since #51. Picks and
  rejections for a single tick land in one session and are committed
  together. This is the "one tick = one transaction" surface tested for
  atomic partial-failure rollback.

**Atomicity scope.** ``stale_feed`` and ``feed_gap`` fire alone, so their
``emit`` path has no atomicity requirement. ``picked`` and ``rejected``
travel together via ``record_scan`` -- splitting them would systematically
overstate scanner precision, so the loop calls ``record_scan`` exactly
once per non-stale tick.

**Transactional configuration.** This module intentionally does not issue
``BEGIN IMMEDIATE`` per call. The journal :class:`Engine` installs a
``begin``-event listener that emits ``BEGIN IMMEDIATE`` on every transaction
start; see :mod:`ross_trading.journal.engine` for the rationale (SQLAlchemy
2.x overrides the pysqlite ``isolation_level`` connect-arg at runtime, so
the engine-level listener is the substantive guarantee).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.journal.models import (
    DecisionKind,
    ExitReason,
    Fill,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Pick,
    Position,
    PositionStatus,
    Regime,
    RegimeSnapshot,
    RejectionReason,
    RiskEvent,
    RiskEventKind,
    Trade,
)
from ross_trading.journal.models import (
    ScannerDecision as ScannerDecisionRow,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from decimal import Decimal

    from sqlalchemy.orm import Session, sessionmaker

    from ross_trading.scanner.decisions import ScannerDecision
    from ross_trading.scanner.types import ScannerPick


class JournalWriter:
    """Concrete :class:`DecisionSink` backed by a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def emit(self, decision: ScannerDecision) -> None:
        """Write one decision row in its own transaction.

        See module docstring for the atomicity scope today vs. after #51.
        """
        with self._session_factory() as session:
            self._add(session, decision)
            session.commit()

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None:
        """Persist all picks and rejections for one scan tick atomically.

        The whole call is one transaction: a constraint violation on any
        row rolls back every row written in this call. ``session.begin()``
        ensures BEGIN/COMMIT pair fires even when both inputs are empty,
        keeping the "one tick = one transaction" property unconditional.
        """
        with self._session_factory() as session, session.begin():
            for pick in picks:
                self._add_picked(session, decision_ts=decision_ts, pick=pick)
            for ticker, reason in rejected.items():
                self._add_rejected(
                    session,
                    decision_ts=decision_ts,
                    ticker=ticker,
                    reason=reason,
                )

    # ------------------------------------------------ trade lifecycle (#91)
    #
    # Small, single-responsibility writers for the post-scanner lifecycle
    # (§3.6-3.8, §3.10). Each opens its own session and commits as one
    # atomic transaction, returning the new row's primary key so callers
    # can thread the FK chain (order -> fill, position -> order/trade).
    # Single-trade reconstruction (#91 acceptance) walks these rows back;
    # see ``tests/unit/test_journal_lifecycle_models.py``.

    def open_position(
        self,
        *,
        ticker: str,
        requested_shares: int,
        opened_ts: datetime,
        filled_shares: int = 0,
    ) -> int:
        """Insert an OPEN position and return its id."""
        with self._session_factory() as session, session.begin():
            position = Position(
                ticker=ticker,
                status=PositionStatus.OPEN,
                requested_shares=requested_shares,
                filled_shares=filled_shares,
                opened_ts=opened_ts,
                closed_ts=None,
            )
            session.add(position)
            session.flush()
            return position.id

    def record_order(
        self,
        *,
        ticker: str,
        side: OrderSide,
        order_type: OrderType,
        intent: OrderIntent,
        status: OrderStatus,
        requested_shares: int,
        created_ts: datetime,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        target_price: Decimal | None = None,
        position_id: int | None = None,
    ) -> int:
        """Insert an order and return its id.

        The schema enforces "entry order requires a stop" (§3.8); passing
        ``intent=ENTRY`` without a ``stop_price`` rolls back atomically.
        """
        with self._session_factory() as session, session.begin():
            order = Order(
                ticker=ticker,
                side=side,
                order_type=order_type,
                intent=intent,
                status=status,
                requested_shares=requested_shares,
                limit_price=limit_price,
                stop_price=stop_price,
                target_price=target_price,
                position_id=position_id,
                created_ts=created_ts,
            )
            session.add(order)
            session.flush()
            return order.id

    def record_fill(
        self,
        *,
        order_id: int,
        filled_shares: int,
        fill_price: Decimal,
        fill_ts: datetime,
    ) -> int:
        """Insert a (partial) fill for an order and return its id."""
        with self._session_factory() as session, session.begin():
            fill = Fill(
                order_id=order_id,
                filled_shares=filled_shares,
                fill_price=fill_price,
                fill_ts=fill_ts,
            )
            session.add(fill)
            session.flush()
            return fill.id

    def close_position(
        self,
        *,
        position_id: int,
        closed_ts: datetime,
        realized_pnl: Decimal,
        exit_reason: ExitReason,
        opened_ts: datetime,
    ) -> int:
        """Mark a position CLOSED and write its :class:`Trade`; return trade id.

        One atomic transaction so a position is never left CLOSED without a
        matching trade row (and vice versa).
        """
        with self._session_factory() as session, session.begin():
            position = session.get(Position, position_id)
            if position is None:
                msg = f"no Position with id {position_id}"
                raise ValueError(msg)
            position.status = PositionStatus.CLOSED
            position.closed_ts = closed_ts
            trade = Trade(
                position_id=position_id,
                realized_pnl=realized_pnl,
                opened_ts=opened_ts,
                closed_ts=closed_ts,
                exit_reason=exit_reason,
            )
            session.add(trade)
            session.flush()
            return trade.id

    def record_risk_event(
        self,
        *,
        event_ts: datetime,
        kind: RiskEventKind,
        reason: str,
        related_ticker: str | None = None,
    ) -> int:
        """Insert a risk-supervisor event (§3.8) and return its id."""
        with self._session_factory() as session, session.begin():
            event = RiskEvent(
                event_ts=event_ts,
                kind=kind,
                reason=reason,
                related_ticker=related_ticker,
            )
            session.add(event)
            session.flush()
            return event.id

    def record_regime_snapshot(
        self,
        *,
        snapshot_ts: datetime,
        regime: Regime,
        score: Decimal,
        components: str | None = None,
    ) -> int:
        """Insert a regime snapshot (§3.10) and return its id."""
        with self._session_factory() as session, session.begin():
            snapshot = RegimeSnapshot(
                snapshot_ts=snapshot_ts,
                regime=regime,
                score=score,
                components=components,
            )
            session.add(snapshot)
            session.flush()
            return snapshot.id

    # ----------------------------------------------------------- internals

    def _add(self, session: Session, decision: ScannerDecision) -> None:
        if decision.kind == "picked":
            assert decision.pick is not None  # noqa: S101 -- ScannerDecision invariant
            self._add_picked(
                session,
                decision_ts=decision.decision_ts,
                pick=decision.pick,
            )
            return
        if decision.kind == "stale_feed":
            session.add(
                ScannerDecisionRow(
                    kind=DecisionKind.STALE_FEED,
                    decision_ts=decision.decision_ts,
                    ticker=decision.ticker,
                    pick_id=None,
                    reason=decision.reason,
                    gap_start=None,
                    gap_end=None,
                    rejection_reason=None,
                )
            )
            return
        if decision.kind == "feed_gap":
            session.add(
                ScannerDecisionRow(
                    kind=DecisionKind.FEED_GAP,
                    decision_ts=decision.decision_ts,
                    ticker=decision.ticker,
                    pick_id=None,
                    reason=decision.reason,
                    gap_start=decision.gap_start,
                    gap_end=decision.gap_end,
                    rejection_reason=None,
                )
            )
            return
        msg = f"unknown ScannerDecision.kind: {decision.kind!r}"
        raise ValueError(msg)

    def _add_picked(
        self,
        session: Session,
        *,
        decision_ts: datetime,
        pick: ScannerPick,
    ) -> None:
        pick_row = Pick(
            ticker=pick.ticker,
            ts=pick.ts,
            rel_volume=pick.rel_volume,
            pct_change=pick.pct_change,
            price=pick.price,
            float_shares=pick.float_shares,
            news_present=pick.news_present,
            headline_count=pick.headline_count,
            rank=pick.rank,
        )
        session.add(pick_row)
        session.flush()  # populate pick_row.id for the FK below
        session.add(
            ScannerDecisionRow(
                kind=DecisionKind.PICKED,
                decision_ts=decision_ts,
                ticker=pick.ticker,
                pick_id=pick_row.id,
                reason=None,
                gap_start=None,
                gap_end=None,
                rejection_reason=None,
            )
        )

    def _add_rejected(
        self,
        session: Session,
        *,
        decision_ts: datetime,
        ticker: str,
        reason: RejectionReason,
    ) -> None:
        session.add(
            ScannerDecisionRow(
                kind=DecisionKind.REJECTED,
                decision_ts=decision_ts,
                ticker=ticker,
                pick_id=None,
                reason=None,
                gap_start=None,
                gap_end=None,
                rejection_reason=reason,
            )
        )
