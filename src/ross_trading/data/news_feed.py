"""News provider interface and headline deduplication.

The dedup window is sliding and clock-driven so replay is
deterministic — see :class:`HeadlineDeduper`.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ross_trading.core.clock import Clock, RealClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence

    from ross_trading.data.types import Headline

DEFAULT_DEDUP_WINDOW = timedelta(hours=24)


@runtime_checkable
class NewsProvider(Protocol):
    """Streaming + historical headline access."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def subscribe_headlines(
        self,
        symbols: Iterable[str] | None = None,
    ) -> AsyncIterator[Headline]: ...

    async def recent_headlines(
        self,
        symbol: str,
        since: datetime,
    ) -> Sequence[Headline]: ...


class HeadlineDeduper:
    """Sliding-window deduper keyed on ``(source, normalized_title, ticker)``.

    Independent of wall-clock time: callers must provide a :class:`Clock`
    so replay produces the same dedup decisions as the live run.
    """

    def __init__(
        self,
        window: timedelta = DEFAULT_DEDUP_WINDOW,
        clock: Clock | None = None,
        max_entries: int = 100_000,
    ) -> None:
        if window <= timedelta(0):
            msg = "dedup window must be positive"
            raise ValueError(msg)
        if max_entries <= 0:
            msg = "max_entries must be positive"
            raise ValueError(msg)
        self._window = window
        self._clock: Clock = clock if clock is not None else RealClock()
        self._max_entries = max_entries
        self._seen: OrderedDict[tuple[str, str, str], datetime] = OrderedDict()

    def is_duplicate(self, headline: Headline) -> bool:
        """Return True if a matching headline was seen within the window.

        Calling this *records* the headline as seen — call once per
        incoming headline.
        """
        self._evict_expired()
        key = headline.dedup_key
        if key in self._seen:
            self._seen.move_to_end(key)
            self._seen[key] = headline.ts
            return True
        self._seen[key] = headline.ts
        if len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return False

    def _evict_expired(self) -> None:
        cutoff = self._clock.now() - self._window
        while self._seen:
            oldest_key = next(iter(self._seen))
            if self._seen[oldest_key] < cutoff:
                self._seen.pop(oldest_key)
            else:
                break
