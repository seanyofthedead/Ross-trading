"""Universe provider interface and daily-cache wrapper.

Phase 2 issue #41 -- A2. The scanner consumes ``UniverseProvider``
implementations to enumerate the day's NMS-listed common-stock
universe. Concrete vendor implementations live under
``data/providers/``; the cache wrapper here keeps a daily TTL.

Decisions resolved:
- #35 (D1: universe source) -- daily NMS enumeration is the source
  of truth. Vendor gainers/snapshot endpoints are an internal
  optimization inside concrete providers (skip polling 8k symbols
  with no movement) but never a substitute for full enumeration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ross_trading.core.clock import Clock, RealClock

if TYPE_CHECKING:
    from datetime import date

DEFAULT_CACHE_TTL = timedelta(hours=24)


@runtime_checkable
class UniverseProvider(Protocol):
    """Daily symbol-universe enumeration for one trading date."""

    async def list_symbols(self, as_of: date) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class _Entry:
    symbols: frozenset[str]
    fetched_at: datetime


class CachedUniverseProvider:
    """24-hour in-memory cache in front of any :class:`UniverseProvider`.

    Modeled on :class:`CachedFloatReference` (``data/float_reference.py``).
    The universe changes at most once per session day; this cache
    avoids re-enumerating the NMS list on every scanner tick.
    """

    def __init__(
        self,
        upstream: UniverseProvider,
        clock: Clock | None = None,
        ttl: timedelta = DEFAULT_CACHE_TTL,
    ) -> None:
        if ttl <= timedelta(0):
            msg = "cache ttl must be positive"
            raise ValueError(msg)
        self._upstream = upstream
        self._clock: Clock = clock if clock is not None else RealClock()
        self._ttl = ttl
        self._cache: dict[date, _Entry] = {}

    async def list_symbols(self, as_of: date) -> frozenset[str]:
        now = self._clock.now()
        cached = self._cache.get(as_of)
        if cached is not None and now - cached.fetched_at < self._ttl:
            return cached.symbols
        symbols = await self._upstream.list_symbols(as_of)
        self._cache[as_of] = _Entry(symbols=symbols, fetched_at=now)
        return symbols
