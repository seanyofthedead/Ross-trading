"""Market data provider interface.

Concrete vendor implementations live under ``data/providers/``.
Anything that streams quotes, bars, or tape prints — including
recordings replayed from disk — implements :class:`MarketDataProvider`.

Note on protocol shape: ``subscribe_*`` methods are declared as plain
``def`` returning :class:`AsyncIterator`. Implementations are
typically async generator functions (``async def`` with ``yield``);
this is intentional — calling ``provider.subscribe_quotes(...)``
returns an iterator directly without needing ``await``, which is the
canonical pattern for streaming sources in Python (PEP 525).
``inspect.iscoroutinefunction`` will return ``False`` for these
methods; ``inspect.isasyncgenfunction`` will return ``True``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ross_trading.data.types import Timeframe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, Halt, Quote, Tape


__all__ = ["MarketDataProvider", "Timeframe"]


@runtime_checkable
class MarketDataProvider(Protocol):
    """Streaming + historical access to NBBO quotes, bars, and tape prints.

    Sequence contract. Every streamed :class:`Quote`/:class:`Bar`/
    :class:`Tape`/:class:`Halt` carries a ``seq`` that is **monotonic
    per ``(symbol, channel)``** -- "channel" being quotes, tape, or
    bars-at-a-given-timeframe. A consumer detects silent vendor drops as
    a forward jump in ``seq`` (see :class:`~ross_trading.data.reconnect.
    ReconnectingProvider`) rather than waiting for a socket disconnect.
    Implementations that cannot supply real sequence numbers must leave
    ``seq`` at ``0``; downstream gap detection then no-ops for that
    stream. Events also carry ``exchange_ts``/``vendor_ts``/``ingest_ts``
    (see :mod:`ross_trading.data.types`); as-of ordering keys on
    ``(exchange_ts, seq)``.
    """

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]: ...

    def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]: ...

    def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]: ...

    def subscribe_halts(self, symbols: Iterable[str]) -> AsyncIterator[Halt]:
        """Stream typed trading-halt / resume events.

        Distinct from a feed gap: a halt is the venue suspending trading,
        not the feed dropping data. Consumers must not fire an entry on
        the resume off a stale pre-halt quote.
        """
        ...

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]: ...
