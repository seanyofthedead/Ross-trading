"""Phase 2 -- Atom A8 (#74). Retrospective replay driver.

Walks recorded ticks for a trading day (or a date range) through the
existing :class:`ScannerLoop`, populating the journal with the same
decision stream the live loop would emit. The driver is the journal-
population prerequisite for #70's Phase-2 recall gate.

Architecture:

1. Connect a :class:`ReplayProvider` against ``recordings_dir`` and pre-load
   every event for the day's universe (M1 bars, D1 bars, quotes, headlines,
   floats) into an in-memory :class:`_RecordingSnapshotAssembler`.
2. Wire the assembler into :class:`ScannerLoop` together with the journal-
   backed :class:`JournalWriter` and a :class:`VirtualClock` that advances
   over the day's recorded event span. ``ScannerLoop``'s ``is_market_hours``
   gate keeps scans inside Cameron's 07:00-11:00 ET window even when the
   recording extends beyond it.
3. Drive the loop until the clock reaches the recording's last-event
   timestamp + a tail pad, propagating any task exception, then cancel.
4. Query the journal for picks/decisions counts and return a
   :class:`ReplaySummary`.

Idempotency. Re-running for the same ``day`` is a no-op on the journal:
:func:`_purge_day` clears the day's :class:`Pick` and
:class:`ScannerDecision` rows in one transaction before the new run
begins. The plan suggested a unique index on ``picks(ticker, ts)``; that
choice would have broken the existing live-loop contract that emits one
``Pick`` row per qualifying tick (multiple ticks within a single M1 bar
share the same ``Pick.ts``). Pre-flight DELETE is the equivalent of the
plan's documented fallback ("``DELETE WHERE day = ?``") and the only one
of the two options that's compatible with how the loop actually writes.

Synthetic-tick fallback (the issue's "no recordings" risk) is reserved
for a follow-up atom. Today, if ``recordings_dir`` has no events for
``day`` the assembler returns empty bounds and the journal stays clean.

FEED_GAP decisions surface from replay when the recording captured them.
A live capture path wires the recorder behind the reconnect provider
(``ReconnectingProvider(upstream, on_gap=recorder.record_feed_gap)``),
so reconnect-induced gap windows land in the per-day
``feed_gap.jsonl.gz`` file alongside the other event streams. The driver
loads that file once at startup and dispatches each gap to
``ScannerLoop.on_feed_gap`` once virtual time reaches ``gap.end``,
producing the same decision row a live loop would have written when the
upstream reconnect actually fired. Recordings that pre-date the gap-
capture path simply omit the file; the driver behaves as before.
STALE_FEED still surfaces naturally -- the loop fires it whenever
``anchor_ts - latest_quote_ts`` exceeds ``staleness_threshold_s``, which
happens during the ``_REPLAY_TAIL_PAD`` window after the recording's
last quote.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import delete, func, select

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
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from sqlalchemy.engine import Engine

    from ross_trading.data.types import (
        Bar,
        Correction,
        FeedGap,
        FloatRecord,
        Halt,
        Headline,
        Quote,
        Tape,
    )

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
    runtime_seconds: float


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
_Seqd = TypeVar("_Seqd", "Bar", "Quote", "Tape")

# M1 bars cover a 60-second interval [open, open + 60s); used to attribute
# a trade correction to the bar whose window contained the amended print.
_M1_INTERVAL = timedelta(minutes=1)


def _ordered_unique(events: Iterable[_Seqd]) -> tuple[_Seqd, ...]:
    """Sort sequenced market events by ``(exchange_ts, seq)`` and dedup.

    The as-of contract (Wave 0): order strictly on ``(exchange_ts,
    seq)`` so identical inputs in any arrival order assemble to identical
    snapshots, and drop true duplicates on the scoped ``seq`` key (the
    caller passes one ``(symbol, channel)`` stream at a time, so ``seq``
    alone is the in-scope identity). ``seq == 0`` is the "unsequenced"
    sentinel -- legacy/unsequenced events are never collapsed, since a
    producer that supplies no sequence numbers gives us no duplicate key
    to trust.
    """
    ordered = sorted(events, key=lambda e: (e.exchange_ts, e.seq))
    seen: set[int] = set()
    out: list[_Seqd] = []
    for event in ordered:
        if event.seq > 0:
            if event.seq in seen:
                continue
            seen.add(event.seq)
        out.append(event)
    return tuple(out)


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


def _halt_state_at(
    halts: Sequence[Halt],
    anchor_ts: datetime,
) -> tuple[str | None, datetime | None]:
    """Resolve the active halt state for one symbol as-of ``anchor_ts``.

    Returns ``(state, boundary_ts)`` of the latest halt/resume event at or
    before the anchor: ``("halted", ts)`` while suspended, ``("resumed",
    ts)`` after the resume, or ``(None, None)`` if no halt event applies.
    """
    latest = _last_at_or_before(halts, anchor_ts, lambda h: h.exchange_ts)
    if latest is None:
        return None, None
    return latest.state, latest.exchange_ts


def _apply_corrections(
    m1: Mapping[str, tuple[Bar, ...]],
    tape: Mapping[str, tuple[Tape, ...]],
    corrections: Mapping[str, tuple[Correction, ...]],
) -> dict[str, tuple[Bar, ...]]:
    """Fold trade corrections/busts into the volume of the covering M1 bar.

    The volume delta is ``new_size - original_size`` where the original
    size is looked up from the amended tape print (by ``corrects_seq``);
    a bust is just ``new_size == 0``. The correction is attributed to the
    M1 bar whose ``[open, open + 60s)`` window contains the correction's
    ``exchange_ts``. Deterministic and append-only: the recorded bars and
    prints are never mutated on disk -- this recomputes the adjusted view.
    """
    if not corrections:
        return dict(m1)
    result: dict[str, list[Bar]] = {sym: list(bars) for sym, bars in m1.items()}
    for sym, corrs in corrections.items():
        bars = result.get(sym)
        if not bars:
            continue
        by_seq = {t.seq: t for t in tape.get(sym, ()) if t.seq > 0}
        for corr in corrs:
            original = by_seq.get(corr.corrects_seq)
            original_size = original.size if original is not None else 0
            new_size = corr.new_size if corr.new_size is not None else 0
            delta = new_size - original_size
            if delta == 0:
                continue
            idx = _bar_index_covering(bars, corr.exchange_ts)
            if idx is None:
                continue
            target = bars[idx]
            bars[idx] = replace(target, volume=max(0, target.volume + delta))
    return {sym: tuple(bars) for sym, bars in result.items()}


def _bar_index_covering(bars: Sequence[Bar], when: datetime) -> int | None:
    """Index of the M1 bar whose ``[open, open + 60s)`` window holds ``when``."""
    for idx, bar in enumerate(bars):
        if bar.exchange_ts <= when < bar.exchange_ts + _M1_INTERVAL:
            return idx
    return None


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
        tape_by_ticker: Mapping[str, Sequence[Tape]] | None = None,
        halts_by_ticker: Mapping[str, Sequence[Halt]] | None = None,
        corrections_by_ticker: Mapping[str, Sequence[Correction]] | None = None,
        baseline_window_days: int = 30,
        news_lookback_hours: int = 24,
    ) -> None:
        ordered_m1 = {
            t.upper(): _ordered_unique(bs) for t, bs in m1_by_ticker.items()
        }
        ordered_tape = {
            t.upper(): _ordered_unique(ts)
            for t, ts in (tape_by_ticker or {}).items()
        }
        ordered_corrections = {
            t.upper(): tuple(sorted(cs, key=lambda c: (c.exchange_ts, c.seq)))
            for t, cs in (corrections_by_ticker or {}).items()
        }
        # Corrections/busts adjust the volume of the M1 bar that covered
        # the amended print, so rel-vol reflects the bust deterministically
        # in replay. The originals stay untouched in the recording; the
        # adjustment is recomputed here from the append-only delta.
        self._m1 = _apply_corrections(ordered_m1, ordered_tape, ordered_corrections)
        self._d1 = {
            t.upper(): _ordered_unique(bs) for t, bs in d1_by_ticker.items()
        }
        self._quotes = {
            t.upper(): _ordered_unique(qs) for t, qs in quotes_by_ticker.items()
        }
        self._halts = {
            t.upper(): tuple(sorted(hs, key=lambda h: (h.exchange_ts, h.seq)))
            for t, hs in (halts_by_ticker or {}).items()
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
            halt_state, halt_boundary = _halt_state_at(
                self._halts.get(t, ()), anchor_ts,
            )
            if halt_state == "halted":
                # Symbol is in a venue halt as-of the anchor -- it is not
                # tradeable, so omit it entirely rather than letting the
                # scanner act on a stale pre-halt price.
                continue
            bar = _last_at_or_before(
                self._m1.get(t, ()), anchor_ts, lambda b: b.ts,
            )
            if bar is None:
                continue
            quote = _last_at_or_before(
                self._quotes.get(t, ()), anchor_ts, lambda q: q.ts,
            )
            # After a resume, refuse to bridge a pre-halt quote: a quote
            # whose exchange_ts predates the resume boundary is stale, so
            # don't price ``last`` off it and don't count it toward the
            # staleness watermark until a fresh post-resume quote arrives.
            if (
                quote is not None
                and halt_state == "resumed"
                and halt_boundary is not None
                and quote.exchange_ts < halt_boundary
            ):
                quote = None
            if quote is not None:
                last = (quote.bid + quote.ask) / Decimal(2)
                # Staleness keys on ingest_ts (local receipt), not the
                # exchange/as-of timestamp -- the loop compares against
                # wall-clock now.
                if latest_quote_ts is None or quote.ingest_ts > latest_quote_ts:
                    latest_quote_ts = quote.ingest_ts
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


async def _load_feed_gaps(
    provider: ReplayProvider,
    day: date,
) -> list[FeedGap]:
    """Drain recorded ``FeedGap`` events that fall within ``day``.

    Returned list is sorted by ``end`` so the driver can dispatch gaps in
    closure order: each one fires the moment virtual time reaches its
    ``end``, mirroring the live model where ``ReconnectingProvider`` calls
    ``loop.on_feed_gap`` after the reconnect completes. Gaps that began
    earlier or end later than ``day`` are filtered out -- a gap straddling
    midnight gets dispatched only by the day in which it ends, not twice.

    Older recordings without ``feed_gap.jsonl.gz`` produce an empty list;
    the driver's per-tick dispatch loop is a no-op and replay behavior is
    unchanged for those days.
    """
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    gaps: list[FeedGap] = []
    async for gap in provider.subscribe_feed_gaps():
        if day_start <= gap.end < day_end:
            gaps.append(gap)
    gaps.sort(key=lambda g: g.end)
    return gaps


async def _load_assembler(
    provider: ReplayProvider,
    universe: frozenset[str],
    day: date,
) -> _RecordingSnapshotAssembler:
    """Drain the provider for ``universe`` and return a populated assembler."""
    m1: dict[str, list[Bar]] = {t: [] for t in universe}
    d1: dict[str, list[Bar]] = {t: [] for t in universe}
    quotes: dict[str, list[Quote]] = {t: [] for t in universe}
    tape: dict[str, list[Tape]] = {t: [] for t in universe}
    halts: dict[str, list[Halt]] = {t: [] for t in universe}
    corrections: dict[str, list[Correction]] = {t: [] for t in universe}
    headlines: dict[str, list[Headline]] = {t: [] for t in universe}
    floats: dict[str, FloatRecord] = {}

    async for bar in provider.subscribe_bars(universe, Timeframe.M1):
        m1.setdefault(bar.symbol.upper(), []).append(bar)
    async for bar in provider.subscribe_bars(universe, Timeframe.D1):
        d1.setdefault(bar.symbol.upper(), []).append(bar)
    async for q in provider.subscribe_quotes(universe):
        quotes.setdefault(q.symbol.upper(), []).append(q)
    async for trade in provider.subscribe_tape(universe):
        tape.setdefault(trade.symbol.upper(), []).append(trade)
    async for halt in provider.subscribe_halts(universe):
        halts.setdefault(halt.symbol.upper(), []).append(halt)
    async for corr in provider.subscribe_corrections(universe):
        corrections.setdefault(corr.symbol.upper(), []).append(corr)
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
        tape_by_ticker=tape,
        halts_by_ticker=halts,
        corrections_by_ticker=corrections,
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


def _purge_day(engine: Engine, day: date) -> None:
    """Delete every ``Pick`` and ``ScannerDecision`` row that belongs to ``day``.

    The two deletes share one transaction so a crash mid-purge can't leave
    a half-cleared day on disk. Called by :func:`replay_day` before the
    loop drives, so re-running for the same day is idempotent at the
    row-count level (the AC in #74).

    Note on the alternative. The plan recommended a unique index on
    ``picks(ticker, ts)`` for idempotency. Existing live-loop tests
    (``test_scanner_loop_with_real_journal_writer_persists_picks``) write
    multiple ``Pick`` rows that share ``(ticker, ts)`` -- one per scan
    tick within a single M1 bar -- so a unique constraint there would
    break the documented loop contract. Pre-flight DELETE is the plan's
    documented fallback and the only one of the two options that's
    compatible with how the loop actually writes.
    """
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        session.execute(
            delete(ScannerDecisionRow).where(
                ScannerDecisionRow.decision_ts >= day_start,
                ScannerDecisionRow.decision_ts < day_end,
            ),
        )
        session.execute(
            delete(Pick).where(Pick.ts >= day_start, Pick.ts < day_end),
        )


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
    :class:`VirtualClock` over the recording's intraday event span, and
    queries the journal for the resulting row counts. ``ScannerLoop``'s
    ``is_market_hours`` gate keeps scans within Cameron's 07:00-11:00 ET
    window even when the recording extends beyond it.

    Idempotent: any existing journal rows for ``day`` are deleted before
    the loop drives (see :func:`_purge_day`).

    Synthetic-tick fallback (the issue's "no recordings" risk) is reserved
    for a follow-up atom. If ``recordings_dir`` has no events for ``day``
    the assembler returns empty bounds and the journal stays clean.
    """
    universe_provider = _StaticUniverseProvider(universe_dir)
    universe = await universe_provider.list_symbols(day)

    provider = ReplayProvider(recordings_dir, mode=ReplayMode.AS_FAST_AS_POSSIBLE)
    await provider.connect()
    try:
        assembler = await _load_assembler(provider, universe, day)
        feed_gaps = await _load_feed_gaps(provider, day)
    finally:
        await provider.disconnect()

    started = time.monotonic()
    _purge_day(journal_engine, day)
    bounds = assembler.day_event_bounds(day)
    if bounds is None:
        # Nothing recorded for this day -- no scans to drive.
        return ReplaySummary(
            day=day, picks_emitted=0, decisions_emitted=0,
            runtime_seconds=time.monotonic() - started,
        )
    start, last_event = bounds
    end = last_event + _REPLAY_TAIL_PAD
    if feed_gaps:
        # Make sure the busy-yield runs at least one tick past the latest
        # recorded gap close, so the in-loop dispatch fires it at virtual
        # time ``gap.end`` (decision_ts is stamped from clock.now() inside
        # on_feed_gap). Without this, a gap whose end falls outside the
        # event-derived window would either be missed entirely or, with a
        # post-loop tail flush, be journaled at last_event + pad rather
        # than the recorded reconnect time.
        latest_gap_end = max(g.end for g in feed_gaps)
        end = max(end, latest_gap_end + timedelta(seconds=tick_interval_s))

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
    gap_idx = 0
    try:
        while clock.now() < end:
            if task.done():
                # Loop crashed before we reached the recording's tail. The
                # busy-yield would otherwise spin forever -- propagate the
                # exception (or swallow a clean exit) by reading .result().
                task.result()
                break
            # Fire any recorded gaps whose end has been reached. Dispatch
            # is monotonic in ``gap.end`` so the journal preserves the live
            # ordering of feed_gap rows, and decision_ts (stamped via
            # ``clock.now()`` inside ``on_feed_gap``) lands at the recorded
            # reconnect time rather than at the busy-yield exit boundary.
            # ``on_feed_gap`` is sync and the event loop serializes it with
            # ``_tick`` -- same contract the production reconnect path
            # relies on.
            while gap_idx < len(feed_gaps) and feed_gaps[gap_idx].end <= clock.now():
                loop.on_feed_gap(feed_gaps[gap_idx])
                gap_idx += 1
            await asyncio.sleep(0)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    picks_count, decisions_count = _journal_summary(journal_engine, day)
    return ReplaySummary(
        day=day,
        picks_emitted=picks_count,
        decisions_emitted=decisions_count,
        runtime_seconds=time.monotonic() - started,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ross_trading.scanner.replay",
        description="Replay a recorded trading day through the scanner -> journal.",
    )
    day_group = parser.add_mutually_exclusive_group(required=True)
    day_group.add_argument(
        "--date", type=date.fromisoformat,
        help="Single calendar day (YYYY-MM-DD) to replay.",
    )
    day_group.add_argument(
        "--from", dest="date_from", type=date.fromisoformat,
        help="First day of an inclusive range (use with --to).",
    )
    parser.add_argument(
        "--to", dest="date_to", type=date.fromisoformat,
        help="Last day of an inclusive range (use with --from).",
    )
    parser.add_argument(
        "--source", required=True, type=Path, dest="recordings_dir",
        metavar="DIR",
        help="Recordings root: <DIR>/<YYYY-MM-DD>/<event>.jsonl.gz",
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


def _resolve_days(args: argparse.Namespace) -> list[date]:
    if args.date is not None:
        return [args.date]
    if args.date_to is None:
        msg = "--from requires --to"
        raise SystemExit(msg)
    if args.date_to < args.date_from:
        msg = f"--to ({args.date_to}) is before --from ({args.date_from})"
        raise SystemExit(msg)
    days: list[date] = []
    cursor = args.date_from
    while cursor <= args.date_to:
        days.append(cursor)
        cursor = cursor + timedelta(days=1)
    return days


async def _run_days(
    days: list[date],
    recordings_dir: Path,
    universe_dir: Path,
    journal_engine: Engine,
) -> list[ReplaySummary]:
    summaries: list[ReplaySummary] = []
    for day in days:
        if not (recordings_dir / day.isoformat()).exists():
            print(
                f"WARN: no recordings for {day.isoformat()}; skipping",
                file=sys.stderr,
            )
            continue
        summaries.append(
            await replay_day(
                day=day,
                recordings_dir=recordings_dir,
                universe_dir=universe_dir,
                journal_engine=journal_engine,
            ),
        )
    return summaries


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    days = _resolve_days(args)
    engine = create_journal_engine(args.db_url)
    try:
        summaries = asyncio.run(
            _run_days(days, args.recordings_dir, args.universe_dir, engine),
        )
    finally:
        engine.dispose()
    for summary in summaries:
        print(
            f"{summary.day} picks={summary.picks_emitted} "
            f"decisions={summary.decisions_emitted} "
            f"runtime={summary.runtime_seconds:.2f}s",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
