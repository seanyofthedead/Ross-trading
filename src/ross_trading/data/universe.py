"""Universe provider interface and daily-cache wrapper.

Phase 2 issue #41 -- A2. The scanner consumes ``UniverseProvider``
implementations to enumerate the day's NMS-listed common-stock
universe. Concrete vendor implementations live under
``data/providers/``; the cache wrapper (added in Task 5 of #41's
plan) keeps a daily TTL.

Decisions resolved:
- #35 (D1: universe source) -- daily NMS enumeration is the source
  of truth. Vendor gainers/snapshot endpoints are an internal
  optimization inside concrete providers (skip polling 8k symbols
  with no movement) but never a substitute for full enumeration.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import date

DEFAULT_CACHE_TTL = timedelta(hours=24)


@runtime_checkable
class UniverseProvider(Protocol):
    """Daily symbol-universe enumeration for one trading date."""

    async def list_symbols(self, as_of: date) -> frozenset[str]: ...
