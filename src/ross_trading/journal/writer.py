"""Concrete :class:`DecisionSink` backed by the SQLAlchemy session factory.

Phase 2 -- Atom A5 (#44). Implements the
:class:`ross_trading.scanner.decisions.DecisionSink` Protocol against the
journal models defined in :mod:`ross_trading.journal.models`. Two surfaces:

* :meth:`JournalWriter.emit` -- the A3 hot path. Each call opens a session,
  writes one decision row (plus a linked :class:`Pick` row for ``picked``),
  and commits. Each call is its own atomic 1-row transaction.
* :meth:`JournalWriter.record_scan` -- the batch API. Picks and rejections
  for a single tick land in one session and are committed together. This
  is the "one tick = one transaction" surface tested for atomic partial-
  failure rollback.

**Atomicity scope today.** A3's loop calls :meth:`emit` N times per tick
(one per pick, plus stale_feed / feed_gap as they occur), so a crash mid-
tick can leave a partial picked-set on disk -- under-recording, not
inconsistency. The current three kinds are structurally near-atomic
(stale_feed and feed_gap fire alone; only "pick #2 vs pick #3 of the same
tick" can split). A3 is stateless across ticks, so the next tick runs
fresh and downstream invariants hold. When #51 lands the fourth ``rejected``
kind, picks + rejections need true tick-atomicity -- splitting them would
systematically overstate scanner precision -- and #51 will migrate A3's
loop from N x :meth:`emit` to a single :meth:`record_scan` per tick.

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
    Pick,
    RejectionReason,
)
from ross_trading.journal.models import (
    ScannerDecision as ScannerDecisionRow,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

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
