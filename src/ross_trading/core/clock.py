"""Clock abstraction.

Production code uses :class:`RealClock`. Tests and historical replay use
:class:`VirtualClock` so deterministic playback doesn't depend on
wall-clock time. Every component that timestamps events or schedules
work should accept a ``Clock`` rather than calling ``datetime.now()``
or ``asyncio.sleep`` directly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo


@runtime_checkable
class Clock(Protocol):
    """Sourcing tz-aware UTC time + a sleep primitive."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Wall-clock implementation."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class VirtualClock:
    """In-memory clock for replay and tests.

    ``sleep`` does not block on wall time; it advances the virtual time
    by the requested duration and yields control once so cooperating
    coroutines can interleave.
    """

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            msg = "VirtualClock requires a tz-aware start time"
            raise ValueError(msg)
        self._now = start.astimezone(UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            msg = "sleep duration must be non-negative"
            raise ValueError(msg)
        self.advance(seconds)
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            msg = "advance duration must be non-negative"
            raise ValueError(msg)
        self._now = self._now + timedelta(seconds=seconds)
        self._monotonic += seconds

    def set_time(self, when: datetime) -> None:
        if when.tzinfo is None:
            msg = "set_time requires a tz-aware datetime"
            raise ValueError(msg)
        target = when.astimezone(UTC)
        delta = (target - self._now).total_seconds()
        if delta < 0:
            msg = f"clock cannot move backwards (current={self._now}, target={target})"
            raise ValueError(msg)
        self._now = target
        self._monotonic += delta


_NY_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = dt_time(7, 0)   # inclusive
_MARKET_CLOSE = dt_time(11, 0)  # exclusive


def is_market_hours(utc_dt: datetime) -> bool:
    """True iff ``utc_dt`` falls in [07:00, 11:00) America/New_York on a weekday.

    The window is wall-clock ET (matches Cameron's pre-market + first-hour
    momentum window per #38). DST is handled by zoneinfo. Holidays are out
    of scope -- the universe provider returns empty on those days, so
    out-of-band gating here is unnecessary.
    """
    if utc_dt.tzinfo is None:
        msg = "is_market_hours requires a tz-aware datetime"
        raise ValueError(msg)
    local = utc_dt.astimezone(_NY_TZ)
    if local.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    return _MARKET_OPEN <= local.time() < _MARKET_CLOSE
