"""Multi-stream feed recorder.

Each event class gets its own gzip-compressed JSON-Lines file under
``<output_dir>/<UTC-date>/<type>.jsonl.gz``. The recorder is the
producer side; the replay provider is the consumer.

Example::

    async with FeedRecorder(Path("./recordings")) as rec:
        async for q in provider.subscribe_quotes(["AVTX"]):
            rec.record_quote(q)

The recorder is *best-effort*: an unflushed crash may lose the tail
of the current gzip block. Call :meth:`flush` periodically if that
matters; :meth:`close` always flushes.
"""

from __future__ import annotations

import gzip
from typing import IO, TYPE_CHECKING, Self

from ross_trading.core.clock import Clock, RealClock
from ross_trading.data._codec import (
    EventType,
    encode_bar,
    encode_event,
    encode_float,
    encode_headline,
    encode_quote,
    encode_tape,
)

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path
    from types import TracebackType

    from ross_trading.data.types import Bar, FloatRecord, Headline, Quote, Tape


class FeedRecorder:
    """Append-only writer with one file per (UTC-date, event-type)."""

    def __init__(
        self,
        output_dir: Path,
        clock: Clock | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._clock: Clock = clock if clock is not None else RealClock()
        self._files: dict[tuple[str, EventType], IO[str]] = {}
        self._closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def record_quote(self, q: Quote) -> None:
        self._write(EventType.QUOTE, q.ts.date(), encode_quote(q))

    def record_bar(self, b: Bar) -> None:
        self._write(EventType.BAR, b.ts.date(), encode_bar(b))

    def record_tape(self, t: Tape) -> None:
        self._write(EventType.TAPE, t.ts.date(), encode_tape(t))

    def record_headline(self, h: Headline) -> None:
        self._write(EventType.HEADLINE, h.ts.date(), encode_headline(h))

    def record_float(self, r: FloatRecord) -> None:
        self._write(EventType.FLOAT, r.as_of, encode_float(r))

    def flush(self) -> None:
        for f in self._files.values():
            f.flush()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for f in self._files.values():
            f.close()
        self._files.clear()

    def _write(self, event_type: EventType, day: date, payload: dict[str, object]) -> None:
        if self._closed:
            msg = "recorder is closed"
            raise RuntimeError(msg)
        key = (day.isoformat(), event_type)
        handle = self._files.get(key)
        if handle is None:
            path = self._output_dir / day.isoformat() / f"{event_type.value}.jsonl.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = gzip.open(path, "at", encoding="utf-8")  # noqa: SIM115  (closed in self.close())
            self._files[key] = handle
        line = encode_event(event_type, payload, self._clock.now())
        handle.write(line)
        handle.write("\n")
