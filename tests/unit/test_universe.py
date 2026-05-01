"""Atom A2 — UniverseProvider Protocol + CachedUniverseProvider (issue #41)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.universe import CachedUniverseProvider, UniverseProvider
from tests.fakes.universe import FakeUniverseProvider


def test_fake_satisfies_protocol() -> None:
    fake = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    assert isinstance(fake, UniverseProvider)


async def test_cached_returns_upstream_value() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX", "BBAI"])})
    cache = CachedUniverseProvider(upstream)
    result = await cache.list_symbols(date(2026, 4, 26))
    assert result == frozenset(["AVTX", "BBAI"])


async def test_cache_hit_does_not_call_upstream() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedUniverseProvider(upstream, clock=clock)
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 26))
    assert upstream.calls == [date(2026, 4, 26)]


async def test_cache_misses_after_ttl() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedUniverseProvider(upstream, clock=clock, ttl=timedelta(hours=1))
    await cache.list_symbols(date(2026, 4, 26))
    clock.advance(3601)
    await cache.list_symbols(date(2026, 4, 26))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 26)]


async def test_cache_per_date_separately() -> None:
    upstream = FakeUniverseProvider({
        date(2026, 4, 26): frozenset(["AVTX"]),
        date(2026, 4, 27): frozenset(["BBAI"]),
    })
    cache = CachedUniverseProvider(upstream)
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 27))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 27)]
    # Re-fetching either date should not re-call upstream.
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 27))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 27)]


def test_cache_rejects_zero_ttl() -> None:
    upstream = FakeUniverseProvider({})
    with pytest.raises(ValueError, match="ttl must be positive"):
        CachedUniverseProvider(upstream, ttl=timedelta(0))


def test_cache_rejects_negative_ttl() -> None:
    upstream = FakeUniverseProvider({})
    with pytest.raises(ValueError, match="ttl must be positive"):
        CachedUniverseProvider(upstream, ttl=timedelta(seconds=-1))
