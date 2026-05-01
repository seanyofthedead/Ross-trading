"""Atom A2 — UniverseProvider Protocol + CachedUniverseProvider (issue #41)."""

from __future__ import annotations

from datetime import date

from ross_trading.data.universe import UniverseProvider
from tests.fakes.universe import FakeUniverseProvider


def test_fake_satisfies_protocol() -> None:
    fake = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    assert isinstance(fake, UniverseProvider)
