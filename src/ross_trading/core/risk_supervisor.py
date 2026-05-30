"""Risk Supervisor -- the runtime kill-switch state machine.

Phase 2 -- Atom A2 (#90). Implements the *hard rules* from
``docs/architecture.md`` Section 3.8 as a pure, deterministic, synchronous
state machine. It owns the authority to deny new entries and to demand a
force-flatten once a hard stop has tripped.

**No I/O, no module-level mutable state.** Every input is passed in
explicitly (dollar amounts as :class:`decimal.Decimal`, timestamps as
tz-aware UTC). The supervisor is a plain object you construct, drive with
``record_fill`` / ``record_close``, and query with ``can_open_position`` /
``should_force_flatten``. This keeps it trivially testable and replayable.

**Hard rules (Section 3.8), parameterised via :class:`RiskLimits`:**

- ``max_risk_per_trade`` (default ``$50``) -- a single opening position may
  not risk more than this.
- ``daily_max_loss`` (default ``-$100``) -- once realised day P&L reaches or
  drops below this, the day is locked, no exceptions.
- ``max_consecutive_losers`` (default ``3``) -- a third consecutive loser
  locks the day.
- ``max_open_risk`` -- the sum of currently-open position risk may not
  exceed this cap (default ``$50`` -- the single-position ceiling, since
  Section 3.8 enforces one position at a time).

**Consecutive-loser semantics (Sections 3.5 / 3.8).** A losing close
increments the streak; a winner *that hit target* resets it to zero. We
treat "hit target" as ``trade_pnl > 0`` (a winning close) -- the architecture
does not give the supervisor candle-level target data, so a positive realised
P&L is the observable proxy for "winner that hit target". A scratch
(``trade_pnl == 0``) is neither a winner nor a loser: it leaves the streak
untouched.

**Journaling (decoupled from A3).** Per #90, blocked actions are journaled
with explicit reasons. To keep A2 independent of the journal ``risk_events``
table (built in parallel by #91), the supervisor emits structured
:class:`RiskEvent` value objects to an injected :class:`RiskEventSink`
Protocol. The concrete journal-backed sink is wired by #91 / D1; here we test
against an in-memory fake under ``tests/fakes/``.

**Out of scope.** The orchestrator / decision-path integration (Section 4)
is owned by D1 (#99). This module deliberately does *not* invent an
orchestrator -- it is the policy object the orchestrator will call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

# --- Reason / event-kind literals -------------------------------------------

DenyReason = Literal[
    "day_locked_daily_max_loss",
    "day_locked_consecutive_losers",
    "per_trade_risk_exceeded",
    "max_open_risk_exceeded",
]
"""Why ``can_open_position`` denied an entry. Mirrors the hard rules."""

LockReason = Literal["daily_max_loss", "consecutive_losers"]
"""Why the day was locked (which hard rule tripped)."""

RiskEventKind = Literal["entry_blocked", "lockout_tripped", "force_flatten"]
"""The kind of risk event carried to the sink."""


# --- Value objects ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Configurable hard-rule thresholds (Section 3.8 defaults).

    Dollar fields are :class:`~decimal.Decimal`. ``daily_max_loss`` is a
    *negative* number (a loss floor); ``max_risk_per_trade`` and
    ``max_open_risk`` are positive caps.
    """

    max_risk_per_trade: Decimal = Decimal("50")
    daily_max_loss: Decimal = Decimal("-100")
    max_consecutive_losers: int = 3
    max_open_risk: Decimal = Decimal("50")

    def __post_init__(self) -> None:
        if self.max_risk_per_trade <= 0:
            msg = "max_risk_per_trade must be positive"
            raise ValueError(msg)
        if self.daily_max_loss >= 0:
            msg = "daily_max_loss must be negative (it is a loss floor)"
            raise ValueError(msg)
        if self.max_consecutive_losers < 1:
            msg = "max_consecutive_losers must be >= 1"
            raise ValueError(msg)
        if self.max_open_risk <= 0:
            msg = "max_open_risk must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Outcome of a :meth:`RiskSupervisor.can_open_position` query.

    ``allowed`` is the gate. When denied, ``reason`` carries the specific
    hard rule that blocked the entry (never ``None`` on a deny; always
    ``None`` on an allow).
    """

    allowed: bool
    reason: DenyReason | None = None

    def __post_init__(self) -> None:
        if self.allowed and self.reason is not None:
            msg = "an allowed decision must not carry a deny reason"
            raise ValueError(msg)
        if not self.allowed and self.reason is None:
            msg = "a denied decision must carry a deny reason"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PositionRisk:
    """The dollar risk of one open position (entry-to-stop * shares).

    ``risk`` is the realisable loss if the stop is hit:
    ``(entry - stop) * shares`` for a long. It is stored so the supervisor
    can track aggregate open risk and release it on close.
    """

    position_id: str
    risk: Decimal

    def __post_init__(self) -> None:
        if self.risk < 0:
            msg = "position risk must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RiskEvent:
    """A structured, journalable risk event.

    Emitted to a :class:`RiskEventSink` for every blocked action, lockout
    trip, and force-flatten demand. ``reason`` is the machine-readable cause
    (a :data:`DenyReason` for ``entry_blocked``, a :data:`LockReason` for
    ``lockout_tripped`` / ``force_flatten``). ``detail`` is a human-readable
    string. ``day_pnl`` and ``consecutive_losers`` snapshot supervisor state
    at emit time for audit.
    """

    event_ts: datetime
    kind: RiskEventKind
    reason: str
    detail: str
    day_pnl: Decimal
    consecutive_losers: int

    def __post_init__(self) -> None:
        if self.event_ts.tzinfo is None:
            msg = "event_ts must be tz-aware"
            raise ValueError(msg)


# --- Sink Protocol (mirrors scanner/decisions.py::DecisionSink) -------------


@runtime_checkable
class RiskEventSink(Protocol):
    """Where the supervisor journals risk events.

    Mirrors :class:`ross_trading.scanner.decisions.DecisionSink`. The
    concrete journal-backed implementation (writing the ``risk_events``
    table) is wired by #91 / D1; A2 tests against an in-memory fake.
    """

    def record_risk_event(self, event: RiskEvent) -> None: ...


# --- The state machine ------------------------------------------------------


@dataclass(slots=True)
class RiskSupervisor:
    """Deterministic kill-switch state machine for one trading day.

    Construct one per trading day (state -- day P&L, loser streak, lockout,
    open positions -- is day-scoped). Drive it with :meth:`record_fill` and
    :meth:`record_close`; gate entries with :meth:`can_open_position`; ask
    :meth:`should_force_flatten` whether a hard stop demands flattening.

    All dollar math is :class:`~decimal.Decimal`. The supervisor is sync and
    does no I/O beyond emitting :class:`RiskEvent`s to the injected
    :class:`RiskEventSink`.
    """

    sink: RiskEventSink
    limits: RiskLimits = field(default_factory=RiskLimits)
    _day_pnl: Decimal = field(default=Decimal("0"), init=False)
    _consecutive_losers: int = field(default=0, init=False)
    _locked: bool = field(default=False, init=False)
    _lock_reason: LockReason | None = field(default=None, init=False)
    _force_flatten: bool = field(default=False, init=False)
    _open: dict[str, PositionRisk] = field(default_factory=dict, init=False)

    # -- read-only views ----------------------------------------------------

    @property
    def day_pnl(self) -> Decimal:
        """Realised P&L for the day so far."""
        return self._day_pnl

    @property
    def consecutive_losers(self) -> int:
        """Current consecutive-loser streak."""
        return self._consecutive_losers

    @property
    def open_risk(self) -> Decimal:
        """Sum of risk across currently-open positions."""
        return sum((p.risk for p in self._open.values()), Decimal("0"))

    def day_locked(self) -> bool:
        """True once a hard stop has locked the day (Section 4 gate)."""
        return self._locked

    @property
    def lock_reason(self) -> LockReason | None:
        """Which hard rule locked the day, or ``None`` if unlocked."""
        return self._lock_reason

    # -- gate ---------------------------------------------------------------

    def can_open_position(
        self,
        position_risk: Decimal,
        now: datetime,
    ) -> RiskDecision:
        """Decide whether a position risking ``position_risk`` may open.

        Denies (and journals an ``entry_blocked`` :class:`RiskEvent`) when:

        - the day is locked (daily max loss hit, or consecutive-loser
          lockout active), or
        - ``position_risk`` exceeds ``max_risk_per_trade``, or
        - opening it would push aggregate open risk above ``max_open_risk``.

        The lockout checks take precedence over the sizing checks: a locked
        day reports the lock reason regardless of the proposed size.
        """
        if position_risk < 0:
            msg = "position_risk must be non-negative"
            raise ValueError(msg)

        if self._locked:
            reason: DenyReason = (
                "day_locked_consecutive_losers"
                if self._lock_reason == "consecutive_losers"
                else "day_locked_daily_max_loss"
            )
            return self._deny(reason, now)

        if position_risk > self.limits.max_risk_per_trade:
            return self._deny("per_trade_risk_exceeded", now)

        if self.open_risk + position_risk > self.limits.max_open_risk:
            return self._deny("max_open_risk_exceeded", now)

        return RiskDecision(allowed=True)

    # -- mutations ----------------------------------------------------------

    def record_fill(self, position_id: str, position_risk: Decimal, now: datetime) -> None:
        """Register an opened position's dollar risk.

        Idempotency is the caller's concern: a duplicate ``position_id``
        raises rather than silently double-counting open risk.
        """
        if position_risk < 0:
            msg = "position_risk must be non-negative"
            raise ValueError(msg)
        if position_id in self._open:
            msg = f"position {position_id!r} already recorded as open"
            raise ValueError(msg)
        self._open[position_id] = PositionRisk(position_id=position_id, risk=position_risk)

    def record_close(
        self,
        trade_pnl: Decimal,
        now: datetime,
        position_id: str | None = None,
    ) -> None:
        """Settle a closed trade: update day P&L, the loser streak, lockout.

        ``trade_pnl`` is the realised P&L of the closed trade (positive =
        winner, negative = loser, zero = scratch). If ``position_id`` is
        given and known, its open risk is released. A loser increments the
        consecutive-loser streak; a winner resets it; a scratch leaves it
        untouched (Sections 3.5 / 3.8).

        Trips the lockout (and journals ``lockout_tripped`` +
        ``force_flatten``) when day P&L reaches ``daily_max_loss`` or the
        streak reaches ``max_consecutive_losers``.
        """
        if position_id is not None:
            self._open.pop(position_id, None)

        self._day_pnl += trade_pnl

        if trade_pnl < 0:
            self._consecutive_losers += 1
        elif trade_pnl > 0:
            self._consecutive_losers = 0
        # scratch (== 0): streak unchanged.

        if self._locked:
            return

        if self._day_pnl <= self.limits.daily_max_loss:
            self._trip("daily_max_loss", now)
        elif self._consecutive_losers >= self.limits.max_consecutive_losers:
            self._trip("consecutive_losers", now)

    def should_force_flatten(self) -> bool:
        """True once a hard stop has tripped that requires flattening.

        Set when the lockout trips. Stays true until queried/handled by the
        orchestrator (D1); the supervisor never silently clears it.
        """
        return self._force_flatten

    # -- internals ----------------------------------------------------------

    def _deny(self, reason: DenyReason, now: datetime) -> RiskDecision:
        self.sink.record_risk_event(
            RiskEvent(
                event_ts=now,
                kind="entry_blocked",
                reason=reason,
                detail=f"entry blocked: {reason}",
                day_pnl=self._day_pnl,
                consecutive_losers=self._consecutive_losers,
            )
        )
        return RiskDecision(allowed=False, reason=reason)

    def _trip(self, reason: LockReason, now: datetime) -> None:
        self._locked = True
        self._lock_reason = reason
        self._force_flatten = True
        self.sink.record_risk_event(
            RiskEvent(
                event_ts=now,
                kind="lockout_tripped",
                reason=reason,
                detail=f"day locked: {reason}",
                day_pnl=self._day_pnl,
                consecutive_losers=self._consecutive_losers,
            )
        )
        self.sink.record_risk_event(
            RiskEvent(
                event_ts=now,
                kind="force_flatten",
                reason=reason,
                detail=f"force flatten demanded: {reason}",
                day_pnl=self._day_pnl,
                consecutive_losers=self._consecutive_losers,
            )
        )
