"""Phase 2 -- Atom A8a (#74). Retrospective replay driver.

Walks recorded ticks for a single trading day through the existing
:class:`ScannerLoop`, populating the journal with the same decision
stream the live loop would emit. The driver is the journal-population
prerequisite for #70's Phase-2 recall gate.

Architecture:

1. Connect a :class:`ReplayProvider` against ``recordings_dir`` and pre-load
   every event for the day's universe (M1 bars, D1 bars, quotes, headlines,
   floats) into an in-memory :class:`_RecordingSnapshotAssembler`.
2. Wire the assembler into :class:`ScannerLoop` together with the journal-
   backed :class:`JournalWriter` and a :class:`VirtualClock` that advances
   from market open to market close (07:00-11:00 ET, gated by
   ``is_market_hours`` inside the loop).
3. Drive the loop until the clock reaches market close, then cancel.
4. Query the journal for picks/decisions counts and return a
   :class:`ReplaySummary`.

This is the A8a skeleton: no idempotency (reserved for A8b), no synthetic-
tick fallback (reserved for A8d), no multi-day range. The single-day path
is enough to populate the journal for one curated day.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import func, select

from ross_trading.core.clock import VirtualClock
from ross_trading.core.errors import MissingRecordingError
from ross_trading.data.providers.replay import ReplayMode, ReplayProvider
from ross_trading.data.types import Timeframe
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import Pick
from ross_trading.journal.models import ScannerDecision as ScannerDecisionRow
from ross_trading.journal.writer import JournalWriter
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.engine import Engine

    from ross_trading.data.types import Bar, FloatRecord, Headline, Quote

# Pad after the last recorded event so the loop fires at least one final
# tick that observes the freshest data and (if applicable) the freshest
# staleness window.
_REPLAY_TAIL_PAD = timedelta(seconds=10)


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Single-day replay outcome."""

    day: date
    picks_emitted: int
    decisions_emitted: int


class _StaticUniverseProvider:
    """A :class:`UniverseProvider` backed by a per-day JSON file.

    File layout: ``<universe_dir>/<YYYY-MM-DD>.json`` containing a JSON
    list of ticker strings. Missing file means "empty universe" -- the
    driver fails loudly later on; an empty frozenset is benign here so
    the loop's first call to ``list_symbols`` doesn't crash before the
    no-universe check fires.
    """

    def __init__(self, universe_dir: Path) -> None:
        self._dir = universe_dir

    async def list_symbols(self, as_of: date) -> frozenset[str]:
        path = self._dir / f"{as_of.isoformat()}.json"
        if not path.exists():
            return frozenset()
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(s).upper() for s in data)


_T = TypeVar("_T")


def _last_at_or_before(
    seq: Sequence[_T],
    target: datetime,
    key: Callable[[_T], datetime],
) -> _T | None:
    """Return the last element of ``seq`` with ``key(x) <= target``.

    Assumes ``seq`` is sorted ascending by ``key``. Linear time -- the
    recording is bounded and per-tick assembly stays sub-millisecond.
    """
    result: _T | None = None
    for item in seq:
        if key(item) <= target:
            result = item
        else:
            break
    return result


def _baseline_from(d1_prior: Sequence[Bar], window_days: int) -> Decimal | None:
    """Average volume across the trailing ``window_days`` daily bars.

    ``None`` means "no D1 history" -- the scanner will reject with
    ``missing_baseline`` for any ticker whose snapshot has this set.
    """
    if not d1_prior:
        return None
    window = d1_prior[-window_days:]
    total_volume = sum(b.volume for b in window)
    return Decimal(total_volume) / Decimal(len(window))


class _RecordingSnapshotAssembler:
    """In-memory :class:`SnapshotAssembler` built from recorded events.

    Loaded once at startup by :func:`_load_assembler`; every per-tick
    ``assemble`` is a pure read against pre-sorted lists.
    """

    def __init__(
        self,
        *,
        m1_by_ticker: Mapping[str, Sequence[Bar]],
        d1_by_ticker: Mapping[str, Sequence[Bar]],
        quotes_by_ticker: Mapping[str, Sequence[Quote]],
        headlines_by_ticker: Mapping[str, Sequence[Headline]],
        floats_by_ticker: Mapping[str, FloatRecord],
        baseline_window_days: int = 30,
        news_lookback_hours: int = 24,
    ) -> None:
        self._m1 = {
            t.upper(): tuple(sorted(bs, key=lambda b: b.ts))
            for t, bs in m1_by_ticker.items()
        }
        self._d1 = {
            t.upper(): tuple(sorted(bs, key=lambda b: b.ts))
            for t, bs in d1_by_ticker.items()
        }
        self._quotes = {
            t.upper(): tuple(sorted(qs, key=lambda q: q.ts))
            for t, qs in quotes_by_ticker.items()
        }
        self._headlines = {
            t.upper(): tuple(sorted(hs, key=lambda h: h.ts))
            for t, hs in headlines_by_ticker.items()
        }
        self._floats = {t.upper(): rec for t, rec in floats_by_ticker.items()}
        self._baseline_window = baseline_window_days
        self._news_lookback = timedelta(hours=news_lookback_hours)

    def day_event_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        """Min/max event ts (M1 bars + quotes) that fall within ``day``.

        Returns ``None`` if the recording has no intraday events for the
        requested day -- the driver shortcuts to a no-op summary.
        """
        day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        timestamps: list[datetime] = []
        for bars in self._m1.values():
            for b in bars:
                if day_start <= b.ts < day_end:
                    timestamps.append(b.ts)
        for quotes in self._quotes.values():
            for q in quotes:
                if day_start <= q.ts < day_end:
                    timestamps.append(q.ts)
        if not timestamps:
            return None
        return min(timestamps), max(timestamps)

    async def assemble(
        self,
        universe: frozenset[str],
        anchor_ts: datetime,
    ) -> tuple[Mapping[str, ScannerSnapshot], datetime | None]:
        snapshots: dict[str, ScannerSnapshot] = {}
        latest_quote_ts: datetime | None = None
        anchor_date = anchor_ts.astimezone(UTC).date()
        news_floor = anchor_ts - self._news_lookback
        for ticker in universe:
            t = ticker.upper()
            bar = _last_at_or_before(
                self._m1.get(t, ()), anchor_ts, lambda b: b.ts,
            )
            if bar is None:
                continue
            quote = _last_at_or_before(
                self._quotes.get(t, ()), anchor_ts, lambda q: q.ts,
            )
            if quote is not None:
                last = (quote.bid + quote.ask) / Decimal(2)
                if latest_quote_ts is None or quote.ts > latest_quote_ts:
                    latest_quote_ts = quote.ts
            else:
                last = bar.close
            d1_prior = tuple(
                b for b in self._d1.get(t, ()) if b.ts.date() < anchor_date
            )
            prev_close = d1_prior[-1].close if d1_prior else bar.close
            snapshots[t] = ScannerSnapshot(
                bar=bar,
                last=last,
                prev_close=prev_close,
                baseline_30d=_baseline_from(d1_prior, self._baseline_window),
                float_record=self._floats.get(t),
                headlines=tuple(
                    h for h in self._headlines.get(t, ())
                    if news_floor <= h.ts <= anchor_ts
                ),
            )
        return snapshots, latest_quote_ts


async def _load_assembler(
    provider: ReplayProvider,
    universe: frozenset[str],
    day: date,
) -> _RecordingSnapshotAssembler:
    """Drain the provider for ``universe`` and return a populated assembler."""
    m1: dict[str, list[Bar]] = {t: [] for t in universe}
    d1: dict[str, list[Bar]] = {t: [] for t in universe}
    quotes: dict[str, list[Quote]] = {t: [] for t in universe}
    headlines: dict[str, list[Headline]] = {t: [] for t in universe}
    floats: dict[str, FloatRecord] = {}

    async for bar in provider.subscribe_bars(universe, Timeframe.M1):
        m1.setdefault(bar.symbol.upper(), []).append(bar)
    async for bar in provider.subscribe_bars(universe, Timeframe.D1):
        d1.setdefault(bar.symbol.upper(), []).append(bar)
    async for q in provider.subscribe_quotes(universe):
        quotes.setdefault(q.symbol.upper(), []).append(q)
    async for h in provider.subscribe_headlines(universe):
        headlines.setdefault(h.ticker.upper(), []).append(h)
    for t in universe:
        try:
            floats[t.upper()] = await provider.get_float(t, day)
        except MissingRecordingError:
            continue

    return _RecordingSnapshotAssembler(
        m1_by_ticker=m1,
        d1_by_ticker=d1,
        quotes_by_ticker=quotes,
        headlines_by_ticker=headlines,
        floats_by_ticker=floats,
    )


def _journal_summary(engine: Engine, day: date) -> tuple[int, int]:
    """Return ``(picks_count, decisions_count)`` for ``day`` from the journal."""
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    factory = create_session_factory(engine)
    with factory() as session:
        picks_count = session.execute(
            select(func.count()).select_from(Pick).where(
                Pick.ts >= day_start, Pick.ts < day_end,
            ),
        ).scalar_one()
        decisions_count = session.execute(
            select(func.count()).select_from(ScannerDecisionRow).where(
                ScannerDecisionRow.decision_ts >= day_start,
                ScannerDecisionRow.decision_ts < day_end,
            ),
        ).scalar_one()
    return int(picks_count), int(decisions_count)


async def replay_day(
    *,
    day: date,
    recordings_dir: Path,
    universe_dir: Path,
    journal_engine: Engine,
    scanner: Scanner | None = None,
    tick_interval_s: float = 2.0,
    staleness_threshold_s: float = 5.0,
) -> ReplaySummary:
    """Replay a single calendar day through ``ScannerLoop`` -> journal.

    Pre-loads the day's universe into memory, drives the loop on a
    :class:`VirtualClock` from start-of-day to end-of-day UTC, and queries
    the journal for the resulting row counts. ``ScannerLoop``'s
    ``is_market_hours`` gate keeps scans within Cameron's 07:00-11:00 ET
    window; ticks outside the window are no-ops on the loop side.

    No idempotency guard yet (A8b). No synthetic-tick fallback (A8d). If
    ``recordings_dir`` has no events for ``day`` the assembler returns
    empty snapshots and no picks fire -- the journal stays untouched.
    """
    universe_provider = _StaticUniverseProvider(universe_dir)
    universe = await universe_provider.list_symbols(day)

    provider = ReplayProvider(recordings_dir, mode=ReplayMode.AS_FAST_AS_POSSIBLE)
    await provider.connect()
    try:
        assembler = await _load_assembler(provider, universe, day)
    finally:
        await provider.disconnect()

    bounds = assembler.day_event_bounds(day)
    if bounds is None:
        # Nothing recorded for this day -- no scans to drive.
        return ReplaySummary(day=day, picks_emitted=0, decisions_emitted=0)
    start, last_event = bounds
    end = last_event + _REPLAY_TAIL_PAD

    clock = VirtualClock(start)
    session_factory = create_session_factory(journal_engine)
    writer = JournalWriter(session_factory)
    loop = ScannerLoop(
        scanner=scanner if scanner is not None else Scanner(),
        universe_provider=CachedUniverseProvider(universe_provider, clock=clock),
        snapshot_assembler=assembler,
        decision_sink=writer,
        clock=clock,
        tick_interval_s=tick_interval_s,
        staleness_threshold_s=staleness_threshold_s,
    )

    task = asyncio.create_task(loop.run())
    try:
        while clock.now() < end:
            await asyncio.sleep(0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    picks_count, decisions_count = _journal_summary(journal_engine, day)
    return ReplaySummary(
        day=day,
        picks_emitted=picks_count,
        decisions_emitted=decisions_count,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ross_trading.scanner.replay",
        description="Replay a recorded trading day through the scanner -> journal.",
    )
    parser.add_argument(
        "--date", required=True, type=date.fromisoformat,
        help="Calendar day (YYYY-MM-DD) to replay.",
    )
    parser.add_argument(
        "--recordings-dir", required=True, type=Path,
        help="Recordings root: <recordings-dir>/<YYYY-MM-DD>/<event>.jsonl.gz",
    )
    parser.add_argument(
        "--universe-dir", required=True, type=Path,
        help="Per-day universe dir: <universe-dir>/<YYYY-MM-DD>.json",
    )
    parser.add_argument(
        "--db-url", default="sqlite:///journal.sqlite",
        help="SQLAlchemy URL for the journal database (default: %(default)s).",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    engine = create_journal_engine(args.db_url)
    try:
        summary = asyncio.run(
            replay_day(
                day=args.date,
                recordings_dir=args.recordings_dir,
                universe_dir=args.universe_dir,
                journal_engine=engine,
            ),
        )
    finally:
        engine.dispose()
    print(
        f"{summary.day} picks={summary.picks_emitted} "
        f"decisions={summary.decisions_emitted}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
