"""Reusable SQLAlchemy ``TypeDecorator`` classes for the journal.

Two type decorators that mirror the precision/timezone rigor used in
``data/cache.py``:

* :class:`TzAwareUTC` -- datetime column that requires tz-aware values on
  bind, persists as ISO-8601 UTC TEXT, and re-attaches UTC on load.
  SQLite has no native tz type and SQLAlchemy ``DateTime(timezone=True)``
  is a no-op there -- silently stripping tzinfo would break replay
  determinism and the tz-aware invariants enforced upstream
  (e.g. :class:`ross_trading.scanner.decisions.ScannerDecision`).
* :class:`DecimalText` -- ``Decimal`` column persisted as TEXT to preserve
  precision. SQLAlchemy ``Numeric`` degrades to FLOAT on SQLite and
  silently loses precision on values like ``rel_volume`` and
  ``pct_change``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class TzAwareUTC(TypeDecorator[datetime]):
    """Datetime column round-tripped as ISO-8601 UTC TEXT.

    Naive datetimes are rejected on bind with a clear ``ValueError``.
    """

    impl = String
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Any,
    ) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "TzAwareUTC requires a tz-aware datetime; got naive"
            raise ValueError(msg)
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self,
        value: str | None,
        dialect: Any,
    ) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class DecimalText(TypeDecorator[Decimal]):
    """``Decimal`` column persisted as TEXT to preserve precision."""

    impl = String
    cache_ok = True

    def process_bind_param(
        self,
        value: Decimal | None,
        dialect: Any,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            msg = f"DecimalText requires Decimal; got {type(value).__name__}"
            raise TypeError(msg)
        return str(value)

    def process_result_value(
        self,
        value: str | None,
        dialect: Any,
    ) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)
