"""Scripted NewsProvider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import datetime

    from ross_trading.data.types import Headline


class FakeNewsProvider:
    """Replays a fixed list of headlines."""

    def __init__(self, headlines: Sequence[Headline] = ()) -> None:
        self._headlines = list(headlines)
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def subscribe_headlines(
        self,
        symbols: Iterable[str] | None = None,
    ) -> AsyncIterator[Headline]:
        wanted = None if symbols is None else {s.upper() for s in symbols}
        for h in self._headlines:
            if wanted is None or h.ticker.upper() in wanted:
                yield h

    async def recent_headlines(
        self,
        symbol: str,
        since: datetime,
    ) -> Sequence[Headline]:
        upper = symbol.upper()
        return [h for h in self._headlines if h.ticker.upper() == upper and h.ts >= since]
