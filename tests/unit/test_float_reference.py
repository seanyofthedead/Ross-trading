"""Atoms 4 & 11 — FloatReferenceProvider protocol + CachedFloatReference."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.float_reference import (
    CachedFloatReference,
    FloatReferenceProvider,
)
from ross_trading.data.types import FloatRecord


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_float(self, ticker: str, as_of: date) -> FloatRecord:
        self.calls += 1
        return FloatRecord(
            ticker=ticker.upper(),
            as_of=as_of,
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="counting",
        )


def test_counting_provider_satisfies_protocol() -> None:
    assert isinstance(_CountingProvider(), FloatReferenceProvider)


async def test_cache_hit_does_not_call_upstream() -> None:
    upstream = _CountingProvider()
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedFloatReference(upstream, clock=clock)
    await cache.get_float("AVTX", date(2026, 4, 26))
    await cache.get_float("avtx", date(2026, 4, 26))
    await cache.get_float("AVTX", date(2026, 4, 26))
    assert upstream.calls == 1


async def test_cache_misses_after_ttl() -> None:
    upstream = _CountingProvider()
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedFloatReference(upstream, clock=clock, ttl=timedelta(hours=1))
    await cache.get_float("AVTX", date(2026, 4, 26))
    clock.advance(3601)
    await cache.get_float("AVTX", date(2026, 4, 26))
    assert upstream.calls == 2


async def test_invalidate_drops_all_dates_for_ticker() -> None:
    upstream = _CountingProvider()
    cache = CachedFloatReference(upstream)
    await cache.get_float("AVTX", date(2026, 4, 26))
    await cache.get_float("AVTX", date(2026, 4, 25))
    await cache.get_float("BBAI", date(2026, 4, 26))
    dropped = cache.invalidate("AVTX")
    assert dropped == 2
    await cache.get_float("AVTX", date(2026, 4, 26))
    assert upstream.calls == 4  # initial 3 + 1 refetch after invalidate


def test_cache_rejects_zero_ttl() -> None:
    with pytest.raises(ValueError, match="ttl must be positive"):
        CachedFloatReference(_CountingProvider(), ttl=timedelta(0))
