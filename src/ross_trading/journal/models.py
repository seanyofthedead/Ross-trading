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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

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

    @validates("ticker")
    def _normalize_ticker(self, _key: str, value: str) -> str:
        # #58: enforce upper-case at the storage boundary so read sites
        # joining against ground truth (already upper-cased on load) cannot
        # silently miss a stray lowercase upstream.
        return value.strip().upper()


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

    @validates("ticker")
    def _normalize_ticker(self, _key: str, value: str) -> str:
        return value.strip().upper()


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

    @validates("ticker")
    def _normalize_ticker(self, _key: str, value: str | None) -> str | None:
        # ``stale_feed`` and ``feed_gap`` decisions have ticker=None.
        if value is None:
            return None
        return value.strip().upper()


# =====================================================================
# A3 / #91 -- trade-lifecycle observability tables.
#
# These tables capture the post-scanner half of the agent's life:
# orders placed (§3.6 execution / bracket orders), fills returned by the
# broker (partial fills first-class per #19), the positions they build
# into (§3.7 exit monitor watches these), the closed trades with realized
# P&L (§5 journaling fuels feedback), risk-supervisor events (§3.8 kill
# switch -- table only here, #90 owns emission), and regime snapshots
# (§3.10 regime detector).
#
# All money/price columns are ``DecimalText`` (FLOAT on SQLite loses
# precision on penny-stock prices); all timestamps are ``TzAwareUTC``;
# enums follow the ``StrEnum`` + ``Enum(values_callable=...)`` pattern so
# the ORM persists lowercase ``.value`` literals that match the migration
# and the CHECK constraints. ``__table_args__`` declares every
# constraint/index so ``Base.metadata.create_all`` (unit tests) yields
# the same schema as ``alembic upgrade head``.
# =====================================================================


class OrderSide(enum.StrEnum):
    """Direction of an order.

    The strategy is long-only momentum (§3.6), but ``SELL`` is needed for
    the scale-out / flatten legs of a bracket, so both are pinned now.
    """

    BUY = "buy"
    SELL = "sell"


class OrderType(enum.StrEnum):
    """Order routing type (§3.6).

    Entries are marketable-limit to bound slippage on low-float names;
    exits use market orders ("breakout or bailout"). ``STOP`` covers the
    hard-stop leg of the bracket.
    """

    MARKET = "market"
    MARKETABLE_LIMIT = "marketable_limit"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(enum.StrEnum):
    """Lifecycle status of an order.

    ``PARTIALLY_FILLED`` is first-class so requested-vs-filled is a stored
    fact rather than a derived one (#19).
    """

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderIntent(enum.StrEnum):
    """Why this order exists -- entry vs exit leg of a bracket (§3.6).

    The "no entry without a stop" invariant (§3.8 risk rule) is enforced
    at the schema level only for ``ENTRY`` orders, so this column is the
    discriminator the CHECK keys off.
    """

    ENTRY = "entry"
    EXIT = "exit"


class PositionStatus(enum.StrEnum):
    """Whether a position is still open or has been fully closed."""

    OPEN = "open"
    CLOSED = "closed"


class ExitReason(enum.StrEnum):
    """Why a trade was closed (§3.7 exit monitor triggers + §3.8 flatten).

    Vocabulary mirrors the exit-trigger table in §3.7 plus the
    risk-supervisor force-flatten path. New triggers require a migration.
    """

    TARGET_HIT = "target_hit"
    HARD_STOP = "hard_stop"
    JACKKNIFE = "jackknife"
    MACD_CROSS = "macd_cross"
    VOLUME_DRYUP = "volume_dryup"
    FIRST_RED_CANDLE = "first_red_candle"
    L2_WEAKNESS = "l2_weakness"
    DILUTIVE_NEWS = "dilutive_news"
    FORCE_FLATTEN = "force_flatten"


class RiskEventKind(enum.StrEnum):
    """Risk-supervisor event vocabulary (§3.8 kill switch).

    This module owns the TABLE only -- the supervisor (#90) emits the
    structured events and the supervisor-to-table wiring is integration
    work (D1). The vocabulary is the contract: renaming a value requires
    a migration.

    * ``ENTRY_BLOCKED`` -- a new entry was rejected (single-position rule,
      PDT cap, unsettled cash, regime trade-count cap).
    * ``LOCKOUT_TRIPPED`` -- trading disabled for the day.
    * ``FORCE_FLATTEN`` -- supervisor flattened all open positions.
    * ``DAILY_MAX_LOSS`` -- RULE 2: daily loss limit hit.
    * ``CONSECUTIVE_LOSERS`` -- RULE 3: N consecutive losers hit.
    """

    ENTRY_BLOCKED = "entry_blocked"
    LOCKOUT_TRIPPED = "lockout_tripped"
    FORCE_FLATTEN = "force_flatten"
    DAILY_MAX_LOSS = "daily_max_loss"
    CONSECUTIVE_LOSERS = "consecutive_losers"


class Regime(enum.StrEnum):
    """Market regime band (§3.10 regime detector)."""

    COLD = "cold"
    NEUTRAL = "neutral"
    WARM = "warm"
    HOT = "hot"


class Order(Base):
    """An order placed with the broker (§3.6 execution / bracket orders).

    ``intent`` discriminates the entry leg from exit legs of a bracket.
    The risk invariant "no entry order without an attached stop" (§3.8) is
    enforced at the schema level: an ``ENTRY`` order must carry a
    ``stop_price``. ``requested_shares`` is separated from anything filled
    -- fills live in their own table so partial fills are first-class
    (#19).
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[OrderSide] = mapped_column(
        Enum(
            OrderSide,
            name="order_side",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    order_type: Mapped[OrderType] = mapped_column(
        Enum(
            OrderType,
            name="order_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    intent: Mapped[OrderIntent] = mapped_column(
        Enum(
            OrderIntent,
            name="order_intent",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    requested_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    position_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("positions.id"), nullable=True,
    )
    created_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)

    __table_args__ = (
        Index("ix_orders_ticker_created_ts", "ticker", "created_ts"),
        Index("ix_orders_position_id", "position_id"),
        # §3.8 risk rule: an entry order must have an attached stop.
        CheckConstraint(
            "intent != 'entry' OR stop_price IS NOT NULL",
            name="ck_orders_entry_requires_stop",
        ),
        CheckConstraint(
            "requested_shares > 0",
            name="ck_orders_requested_shares_positive",
        ),
    )

    @validates("ticker")
    def _normalize_ticker(self, _key: str, value: str) -> str:
        return value.strip().upper()


class Fill(Base):
    """A (partial) fill returned by the broker for an :class:`Order`.

    Requested vs filled is separated by design (#19): one order can have
    zero, one, or many fills. ``filled_shares`` is the shares filled on
    *this* fill event, ``fill_price`` its execution price.
    """

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=False,
    )
    filled_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    fill_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)

    order: Mapped[Order] = relationship(Order)

    __table_args__ = (
        Index("ix_fills_order_id", "order_id"),
        CheckConstraint(
            "filled_shares > 0",
            name="ck_fills_filled_shares_positive",
        ),
    )


class Position(Base):
    """An open or closed position in a single ticker (§3.7 exit monitor).

    ``requested_shares`` (intended size from the sizer, §3.5) is separated
    from ``filled_shares`` (what actually filled, #19). ``closed_ts`` is
    ``None`` while ``status=OPEN``; the biconditional is enforced by CHECK.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        Enum(
            PositionStatus,
            name="position_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    requested_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    closed_ts: Mapped[datetime | None] = mapped_column(TzAwareUTC, nullable=True)

    __table_args__ = (
        Index("ix_positions_ticker_opened_ts", "ticker", "opened_ts"),
        Index(
            "ix_positions_open_by_ticker",
            "ticker",
            sqlite_where=text("closed_ts IS NULL"),
            postgresql_where=text("closed_ts IS NULL"),
        ),
        # status <-> closed_ts biconditional: a position is closed iff it
        # has a closed_ts.
        CheckConstraint(
            "(status = 'closed') = (closed_ts IS NOT NULL)",
            name="ck_positions_closed_ts",
        ),
        CheckConstraint(
            "requested_shares > 0",
            name="ck_positions_requested_shares_positive",
        ),
        CheckConstraint(
            "filled_shares >= 0",
            name="ck_positions_filled_shares_nonneg",
        ),
    )

    @validates("ticker")
    def _normalize_ticker(self, _key: str, value: str) -> str:
        return value.strip().upper()


class Trade(Base):
    """A closed trade -- the realized-P&L unit of feedback (§5).

    One trade per closed :class:`Position`. ``realized_pnl`` is the net
    P&L in account currency (``DecimalText``). ``exit_reason`` records
    which §3.7 trigger (or §3.8 flatten) closed it. The single-trade
    reconstruction acceptance criterion (#91) walks
    Position -> Orders -> Fills and reconciles against this row.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("positions.id"), nullable=False,
    )
    realized_pnl: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    opened_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    closed_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    exit_reason: Mapped[ExitReason] = mapped_column(
        Enum(
            ExitReason,
            name="exit_reason",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )

    position: Mapped[Position] = relationship(Position)

    __table_args__ = (
        Index("ix_trades_position_id", "position_id"),
        Index("ix_trades_closed_ts", "closed_ts"),
        CheckConstraint(
            "closed_ts >= opened_ts",
            name="ck_trades_closed_after_opened",
        ),
    )


class RiskEvent(Base):
    """A risk-supervisor event (§3.8 kill switch).

    Table only -- emission lives in #90, wiring in D1. ``related_ticker``
    is set when the event is ticker-scoped (e.g. ``ENTRY_BLOCKED``) and
    ``None`` for account-wide events (e.g. ``DAILY_MAX_LOSS``).
    """

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    kind: Mapped[RiskEventKind] = mapped_column(
        Enum(
            RiskEventKind,
            name="risk_event_kind",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    related_ticker: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_risk_events_event_ts", "event_ts"),
        Index("ix_risk_events_kind_ts", "kind", "event_ts"),
    )

    @validates("related_ticker")
    def _normalize_ticker(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()


class RegimeSnapshot(Base):
    """A point-in-time market-regime reading (§3.10 regime detector).

    ``score`` is the composite hot-market score; ``regime`` is the band it
    maps to; ``components`` is the JSON breakdown of the score's
    contributing terms (stored as TEXT so SQLite and Postgres agree).
    """

    __tablename__ = "regime_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_ts: Mapped[datetime] = mapped_column(TzAwareUTC, nullable=False)
    regime: Mapped[Regime] = mapped_column(
        Enum(
            Regime,
            name="regime",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    score: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    components: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (Index("ix_regime_snapshots_snapshot_ts", "snapshot_ts"),)
