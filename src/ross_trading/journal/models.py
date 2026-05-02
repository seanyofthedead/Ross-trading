"""SQLAlchemy 2.x typed ORM models for the scanner journal.

Mirrors :class:`ross_trading.scanner.types.ScannerPick` and
:class:`ross_trading.scanner.decisions.ScannerDecision`. ``WatchlistEntry``
captures open-ended watchlist membership for a Pick (added_at /
removed_at), so survivorship is a temporal fact rather than a per-tick
snapshot.

Two enum vocabularies are pinned at the schema level today even though
A3 only emits a subset:

* :class:`DecisionKind` -- four values. ``rejected`` is reserved for #51
  (rejected-decision wiring); A3 only emits ``picked`` / ``stale_feed`` /
  ``feed_gap``. Landing it now makes #51 a wiring-only change.
* :class:`RejectionReason` -- seven values. The order matches the
  AND-chain in :func:`ross_trading.scanner.scanner.Scanner.scan` so the
  enum order itself encodes "first failing filter" priority. The literal
  values are the contract referenced by #51 and must not be renamed
  without a matching migration.
"""

from __future__ import annotations

import enum
from datetime import datetime  # noqa: TC003 -- SA 2.x evals Mapped[datetime] at runtime
from decimal import Decimal  # noqa: TC003 -- SA 2.x evals Mapped[Decimal] at runtime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ross_trading.journal.types import DecimalText, TzAwareUTC


class Base(DeclarativeBase):
    """Declarative base for all journal models."""


class DecisionKind(enum.StrEnum):
    """Kind of :class:`ScannerDecision` row.

    ``REJECTED`` is reserved for #51 -- A3 only emits the other three.
    """

    PICKED = "picked"
    STALE_FEED = "stale_feed"
    FEED_GAP = "feed_gap"
    REJECTED = "rejected"


class RejectionReason(enum.StrEnum):
    """First-failing-filter reason for a ``REJECTED`` decision.

    Order matches the AND-chain in
    :func:`ross_trading.scanner.scanner.Scanner.scan` (snapshot presence
    -> baseline -> float record -> rel_volume -> pct_change ->
    price_band -> float_size). Literal values are the contract referenced
    by #51 and must not be renamed without a matching migration.
    """

    NO_SNAPSHOT = "no_snapshot"
    MISSING_BASELINE = "missing_baseline"
    MISSING_FLOAT = "missing_float"
    REL_VOLUME = "rel_volume"
    PCT_CHANGE = "pct_change"
    PRICE_BAND = "price_band"
    FLOAT_SIZE = "float_size"


class Pick(Base):
    """A symbol that passed the scanner's hard filters (ranked output).

    Mirrors :class:`ross_trading.scanner.types.ScannerPick`.
    ``headline_count`` is an explicit ``Integer`` column -- first-class,
    not derived -- so Phase 3 retrospectives can correlate outcome
    quality with headline volume.
    """

    __tablename__ = "picks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    rel_volume: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    pct_change: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    price: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    float_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    news_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    headline_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_picks_ticker_ts", "ticker", "ts"),)


class WatchlistEntry(Base):
    """Open-ended watchlist membership for a :class:`Pick`.

    ``added_at`` marks promotion onto the watchlist; ``removed_at`` is
    ``None`` while membership is active. The partial index on
    ``(ticker)`` filtered by ``removed_at IS NULL`` keeps "currently on
    watchlist" lookups (A7 / #46) cheap.
    """

    __tablename__ = "watchlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    pick_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("picks.id"), nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(TzAwareUTC, nullable=True)

    pick: Mapped[Pick] = relationship(Pick)

    __table_args__ = (
        Index(
            "ix_watchlist_active_by_ticker",
            "ticker",
            sqlite_where=text("removed_at IS NULL"),
            postgresql_where=text("removed_at IS NULL"),
        ),
    )


class ScannerDecision(Base):
    """One row per tick outcome from the scanner loop.

    Mirrors :class:`ross_trading.scanner.decisions.ScannerDecision`.
    ``pick_id`` is set only when ``kind=PICKED``; ``rejection_reason`` is
    set only when ``kind=REJECTED``.
    """

    __tablename__ = "scanner_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[DecisionKind] = mapped_column(
        Enum(
            DecisionKind,
            name="decision_kind",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    decision_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    pick_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("picks.id"), nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    gap_start: Mapped[datetime | None] = mapped_column(TzAwareUTC, nullable=True)
    gap_end: Mapped[datetime | None] = mapped_column(TzAwareUTC, nullable=True)
    rejection_reason: Mapped[RejectionReason | None] = mapped_column(
        Enum(
            RejectionReason,
            name="rejection_reason",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
    )

    pick: Mapped[Pick | None] = relationship(Pick)

    __table_args__ = (
        Index("ix_scanner_decisions_decision_ts", "decision_ts"),
        Index("ix_scanner_decisions_kind_ts", "kind", "decision_ts"),
        # Kind -> field-population invariants. Mirrors migration 0002
        # (P2 review item from #43 / PR #54). Declared here so
        # ``Base.metadata.create_all`` -- used by unit tests -- produces
        # the same schema as ``alembic upgrade head``.
        CheckConstraint(
            "(kind = 'picked') = (pick_id IS NOT NULL)",
            name="ck_scanner_decisions_picked_pick_id",
        ),
        CheckConstraint(
            "(kind = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_scanner_decisions_rejected_reason",
        ),
        CheckConstraint(
            "(kind = 'feed_gap') = (gap_start IS NOT NULL)",
            name="ck_scanner_decisions_feed_gap_start",
        ),
        CheckConstraint(
            "(kind = 'feed_gap') = (gap_end IS NOT NULL)",
            name="ck_scanner_decisions_feed_gap_end",
        ),
        CheckConstraint(
            "kind IN ('stale_feed', 'feed_gap') OR ticker IS NOT NULL",
            name="ck_scanner_decisions_ticker_required",
        ),
    )
