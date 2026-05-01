"""Scripted UniverseProvider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.errors import FeedError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date


class FakeUniverseProvider:
    """Returns canned symbol sets keyed on as_of date.

    Records every call in ``self.calls`` (in order) so cache tests
    can assert hit / miss behavior on the wrapping
    :class:`CachedUniverseProvider`.
    """

    def __init__(self, by_date: Mapping[date, frozenset[str]]) -> None:
        self._by_date = dict(by_date)
        self.calls: list[date] = []

    async def list_symbols(self, as_of: date) -> frozenset[str]:
        self.calls.append(as_of)
        result = self._by_date.get(as_of)
        if result is None:
            msg = f"no fake universe for {as_of}"
            raise FeedError(msg)
        return result
