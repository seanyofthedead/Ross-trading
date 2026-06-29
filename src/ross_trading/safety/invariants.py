"""Pure safety invariants for replayed entry decision streams.

The scanner can already replay deterministic pick/rejection streams. This
module defines the next safety boundary in a small, dependency-free shape:
entry attempts must carry a hard stop, must not occur during lockout, and
must not bypass a hard catalyst rejection. The same canonical serialization
also gives CI a stable hash for replay determinism checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

SafetyDecisionKind = Literal["entry", "reject", "lockout_started", "lockout_ended"]
CatalystStatus = Literal["approved", "hard_reject", "unknown"]


class SafetyInvariantViolation(ValueError):
    """Raised when a decision stream violates a required safety invariant."""


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """One replayable safety decision.

    ``entry_price`` and ``stop_price`` are required only for ``kind="entry"``.
    ``catalyst_status="hard_reject"`` represents the downstream catalyst
    classifier's hard veto for offerings, reverse splits, fake catalysts, or
    other non-tradeable news.
    """

    kind: SafetyDecisionKind
    decision_ts: datetime
    ticker: str | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    catalyst_status: CatalystStatus = "unknown"
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision_ts.tzinfo is None:
            msg = "decision_ts must be tz-aware"
            raise ValueError(msg)


def assert_safety_invariants(decisions: tuple[SafetyDecision, ...]) -> None:
    """Fail fast if any required safety invariant is violated."""

    lockout_active = False
    for decision in decisions:
        if decision.kind == "lockout_started":
            lockout_active = True
            continue
        if decision.kind == "lockout_ended":
            lockout_active = False
            continue
        if decision.kind != "entry":
            continue

        if lockout_active:
            _raise(decision, "entry during lockout")
        stop_price = decision.stop_price
        entry_price = decision.entry_price
        if stop_price is None:
            _raise(decision, "entry without stop")
        if entry_price is None:
            _raise(decision, "entry without entry price")
        if stop_price >= entry_price:
            _raise(decision, "entry stop must be below entry price")
        if decision.catalyst_status == "hard_reject":
            _raise(decision, "entry after catalyst hard reject")


def decision_stream_hash(decisions: tuple[SafetyDecision, ...]) -> str:
    """Return a stable SHA-256 for a replayed safety decision stream."""

    payload = [_to_wire(decision) for decision in decisions]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _raise(decision: SafetyDecision, reason: str) -> NoReturn:
    ticker = decision.ticker or "<none>"
    msg = f"{decision.decision_ts.isoformat()} {ticker}: {reason}"
    raise SafetyInvariantViolation(msg)


def _to_wire(decision: SafetyDecision) -> dict[str, str | None]:
    return {
        "kind": decision.kind,
        "decision_ts": decision.decision_ts.isoformat(),
        "ticker": decision.ticker,
        "entry_price": _decimal_to_wire(decision.entry_price),
        "stop_price": _decimal_to_wire(decision.stop_price),
        "catalyst_status": decision.catalyst_status,
        "reason": decision.reason,
    }


def _decimal_to_wire(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")
