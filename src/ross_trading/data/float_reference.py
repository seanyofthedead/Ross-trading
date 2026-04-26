"""Float-reference provider interface and daily-cache wrapper.

Phase 1 issue #2 requires the *cache layer* to be vendor-agnostic.
The concrete vendor implementation is blocked on decision issue #33.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ross_trading.core.clock import Clock, RealClock

if TYPE_CHECKING:
    from ross_trading.data.types import FloatRecord

DEFAULT_CACHE_TTL = timedelta(hours=24)


@runtime_checkable
class FloatReferenceProvider(Protocol):
    """Daily float lookups for a ticker."""

    async def get_float(self, ticker: str, as_of: date) -> FloatRecord: ...


@dataclass(frozen=True, slots=True)
class _Entry:
    record: FloatRecord
    fetched_at: datetime


class CachedFloatReference:
    """24-hour in-memory cache in front of any :class:`FloatReferenceProvider`.

    The cache is *opt-in invalidated* via :meth:`invalidate` — risk
    issue #23 (same-day dilution detection) is the home for the
    decision logic that fires the invalidation; this layer just
    exposes the hook.
    """

    def __init__(
        self,
        upstream: FloatReferenceProvider,
        clock: Clock | None = None,
        ttl: timedelta = DEFAULT_CACHE_TTL,
    ) -> None:
        if ttl <= timedelta(0):
            msg = "cache ttl must be positive"
            raise ValueError(msg)
        self._upstream = upstream
        self._clock: Clock = clock if clock is not None else RealClock()
        self._ttl = ttl
        self._cache: dict[tuple[str, date], _Entry] = {}

    async def get_float(self, ticker: str, as_of: date) -> FloatRecord:
        key = (ticker.upper(), as_of)
        now = self._clock.now()
        cached = self._cache.get(key)
        if cached is not None and now - cached.fetched_at < self._ttl:
            return cached.record
        record = await self._upstream.get_float(ticker, as_of)
        self._cache[key] = _Entry(record=record, fetched_at=now)
        return record

    def invalidate(self, ticker: str) -> int:
        """Drop every cached entry for ``ticker``. Returns count dropped."""
        upper = ticker.upper()
        keys = [k for k in self._cache if k[0] == upper]
        for k in keys:
            del self._cache[k]
        return len(keys)
