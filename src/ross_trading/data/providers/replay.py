"""Replay provider — reads recordings from disk and emits them.

Two timing modes:

* :attr:`ReplayMode.AS_FAST_AS_POSSIBLE` — for tests and backfills,
  events are yielded immediately in timestamp order.
* :attr:`ReplayMode.REALTIME` — events are paced to honor the
  original inter-arrival times against the supplied clock.

Pacing is per-subscription: the first event of each stream anchors
that stream's wall-clock baseline. All streams started under the
same provider session will therefore stay coherent with each other
within one wall-clock anchor.

``_read_lines`` accepts an optional date range so callers like
:meth:`historical_bars` only open the files for the days they care
about, avoiding a full archive scan per query.
"""

from __future__ import annotations

import gzip
from datetime import date as _date_type
from enum import StrEnum
from typing import TYPE_CHECKING

from ross_trading.core.clock import Clock, RealClock
from ross_trading.core.errors import MissingRecordingError
from ross_trading.data._codec import (
    EventType,
    decode_bar,
    decode_envelope,
    decode_float,
    decode_headline,
    decode_quote,
    decode_tape,
)
from ross_trading.data.types import Timeframe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence
    from datetime import date, datetime
    from pathlib import Path

    from ross_trading.data.types import Bar, FloatRecord, Headline, Quote, Tape


class ReplayMode(StrEnum):
    AS_FAST_AS_POSSIBLE = "fast"
    REALTIME = "realtime"


class ReplayProvider:
    """Implements :class:`MarketDataProvider`, :class:`NewsProvider`, and
    :class:`FloatReferenceProvider` from a recordings directory."""

    def __init__(
        self,
        recordings_dir: Path,
        mode: ReplayMode = ReplayMode.AS_FAST_AS_POSSIBLE,
        clock: Clock | None = None,
        timeframes: Iterable[Timeframe] = (
            Timeframe.S1,
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.D1,
        ),
    ) -> None:
        self._dir = recordings_dir
        self._mode = mode
        self._clock: Clock = clock if clock is not None else RealClock()
        self._timeframes = frozenset(timeframes)
        self._float_records: dict[tuple[str, date], FloatRecord] = {}
        self._connected = False

    @property
    def supported_timeframes(self) -> frozenset[Timeframe]:
        return self._timeframes

    async def connect(self) -> None:
        self._load_float_records()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def subscribe_quotes(self, symbols: Iterable[str]) -> AsyncIterator[Quote]:
        wanted = {s.upper() for s in symbols}
        anchor = _Anchor()
        for line in self._read_lines(EventType.QUOTE):
            _, payload = decode_envelope(line)
            quote = decode_quote(payload)
            if quote.symbol.upper() not in wanted:
                continue
            await self._maybe_pace(quote.ts, anchor)
            yield quote

    async def subscribe_bars(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Bar]:
        wanted = {s.upper() for s in symbols}
        anchor = _Anchor()
        for line in self._read_lines(EventType.BAR):
            _, payload = decode_envelope(line)
            bar = decode_bar(payload)
            if bar.symbol.upper() not in wanted or bar.timeframe != timeframe.value:
                continue
            await self._maybe_pace(bar.ts, anchor)
            yield bar

    async def subscribe_tape(self, symbols: Iterable[str]) -> AsyncIterator[Tape]:
        wanted = {s.upper() for s in symbols}
        anchor = _Anchor()
        for line in self._read_lines(EventType.TAPE):
            _, payload = decode_envelope(line)
            tape = decode_tape(payload)
            if tape.symbol.upper() not in wanted:
                continue
            await self._maybe_pace(tape.ts, anchor)
            yield tape

    async def historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Bar]:
        upper = symbol.upper()
        result: list[Bar] = []
        for line in self._read_lines(
            EventType.BAR, date_from=start.date(), date_to=end.date()
        ):
            _, payload = decode_envelope(line)
            bar = decode_bar(payload)
            if (
                bar.symbol.upper() == upper
                and bar.timeframe == timeframe.value
                and start <= bar.ts < end
            ):
                result.append(bar)
        return result

    async def subscribe_headlines(
        self,
        symbols: Iterable[str] | None = None,
    ) -> AsyncIterator[Headline]:
        wanted = None if symbols is None else {s.upper() for s in symbols}
        anchor = _Anchor()
        for line in self._read_lines(EventType.HEADLINE):
            _, payload = decode_envelope(line)
            headline = decode_headline(payload)
            if wanted is not None and headline.ticker.upper() not in wanted:
                continue
            await self._maybe_pace(headline.ts, anchor)
            yield headline

    async def recent_headlines(
        self,
        symbol: str,
        since: datetime,
    ) -> Sequence[Headline]:
        upper = symbol.upper()
        result: list[Headline] = []
        for line in self._read_lines(EventType.HEADLINE, date_from=since.date()):
            _, payload = decode_envelope(line)
            headline = decode_headline(payload)
            if headline.ticker.upper() == upper and headline.ts >= since:
                result.append(headline)
        return result

    async def get_float(self, ticker: str, as_of: date) -> FloatRecord:
        if not self._float_records:
            self._load_float_records()
        key = (ticker.upper(), as_of)
        rec = self._float_records.get(key)
        if rec is None:
            msg = f"no recorded float for {ticker} on {as_of}"
            raise MissingRecordingError(msg)
        return rec

    def _load_float_records(self) -> None:
        for line in self._read_lines(EventType.FLOAT):
            _, payload = decode_envelope(line)
            rec = decode_float(payload)
            self._float_records[(rec.ticker.upper(), rec.as_of)] = rec

    def _read_lines(
        self,
        event_type: EventType,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Iterable[str]:
        if not self._dir.exists():
            return
        for day_dir in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            try:
                day = _date_type.fromisoformat(day_dir.name)
            except ValueError:
                # Non-date directory (e.g. a backup folder); skip silently.
                continue
            if date_from is not None and day < date_from:
                continue
            if date_to is not None and day > date_to:
                continue
            path = day_dir / f"{event_type.value}.jsonl.gz"
            if not path.exists():
                continue
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if line:
                        yield line

    async def _maybe_pace(self, event_ts: datetime, anchor: _Anchor) -> None:
        if self._mode is not ReplayMode.REALTIME:
            return
        if anchor.event_ts is None:
            anchor.event_ts = event_ts
            anchor.monotonic_at_anchor = self._clock.monotonic()
            return
        target_elapsed = (event_ts - anchor.event_ts).total_seconds()
        actual_elapsed = self._clock.monotonic() - anchor.monotonic_at_anchor
        delay = target_elapsed - actual_elapsed
        if delay > 0:
            await self._clock.sleep(delay)


class _Anchor:
    """Per-subscription pacing anchor."""

    __slots__ = ("event_ts", "monotonic_at_anchor")

    def __init__(self) -> None:
        self.event_ts: datetime | None = None
        self.monotonic_at_anchor: float = 0.0
