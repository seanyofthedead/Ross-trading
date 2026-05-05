"""Async tick driver for the scanner.

Phase 2 -- Atom A3 (#42), extended in A8 (#51) to migrate the scan
branch from N x ``emit`` to a single ``record_scan`` per tick (atomic
picks + rejections). Long-running coroutine that paces
:meth:`Scanner.scan_with_decisions` on a Clock and emits per-tick
batches to an injected :class:`DecisionSink`. The loop owns no
provider I/O -- the injected :class:`SnapshotAssembler` is the
replay-determinism boundary.

Cancellation: ``run()`` re-raises CancelledError. No drain on
shutdown, no upstream subscription cleanup. Outside-market-hours
ticks are no-ops, not exits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from ross_trading.core.clock import is_market_hours
from ross_trading.journal.models import RejectionReason
from ross_trading.scanner.decisions import ScannerDecision

if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.types import FeedGap
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner
    from ross_trading.scanner.types import RejectionReasonLit


# Mirrors the Literal -> Enum mapping. The Literal values are the contract
# pinned by `scanner/types.py::RejectionReasonLit`; the Enum is the DB-
# facing twin from `journal/models.py::RejectionReason`. The wildcard
# arm calls `assert_never`, which is the canonical exhaustive-match marker:
# mypy strict fails at type-check time if the Literal grows and a case is
# missed; at runtime it raises AssertionError instead of silently returning
# None.
def _lit_to_enum(reason: RejectionReasonLit) -> RejectionReason:
    match reason:
        case "no_snapshot":
            return RejectionReason.NO_SNAPSHOT
        case "missing_baseline":
            return RejectionReason.MISSING_BASELINE
        case "missing_float":
            return RejectionReason.MISSING_FLOAT
        case "rel_volume":
            return RejectionReason.REL_VOLUME
        case "pct_change":
            return RejectionReason.PCT_CHANGE
        case "price_band":
            return RejectionReason.PRICE_BAND
        case "float_size":
            return RejectionReason.FLOAT_SIZE
        case _:  # pragma: no cover -- unreachable by type, defensive at runtime
            assert_never(reason)


class ScannerLoop:
    """Drive Scanner.scan_with_decisions on a Clock-paced tick."""

    def __init__(
        self,
        scanner: Scanner,
        universe_provider: UniverseProvider,
        snapshot_assembler: SnapshotAssembler,
        decision_sink: DecisionSink,
        clock: Clock,
        *,
        tick_interval_s: float = 2.0,
        staleness_threshold_s: float = 5.0,
    ) -> None:
        if tick_interval_s <= 0:
            msg = "tick_interval_s must be positive"
            raise ValueError(msg)
        if staleness_threshold_s <= 0:
            msg = "staleness_threshold_s must be positive"
            raise ValueError(msg)
        self._scanner = scanner
        self._universe_provider = universe_provider
        self._assembler = snapshot_assembler
        self._sink = decision_sink
        self._clock = clock
        self._tick_interval_s = tick_interval_s
        self._staleness_threshold_s = staleness_threshold_s

    async def run(self) -> None:
        """Tick forever until cancelled. All waits via injected Clock."""
        while True:
            await self._tick()
            await self._clock.sleep(self._tick_interval_s)

    async def _tick(self) -> None:
        anchor_ts = self._clock.now()
        if not is_market_hours(anchor_ts):
            return
        universe = await self._universe_provider.list_symbols(anchor_ts.date())
        snapshot, most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
        if most_recent_quote_ts is not None:
            staleness_s = (anchor_ts - most_recent_quote_ts).total_seconds()
            if staleness_s > self._staleness_threshold_s:
                self._sink.emit(
                    ScannerDecision(
                        kind="stale_feed",
                        decision_ts=anchor_ts,
                        ticker=None,
                        pick=None,
                        reason=f"feed stale by {staleness_s:.1f}s",
                        gap_start=None,
                        gap_end=None,
                    )
                )
                return
        result = self._scanner.scan_with_decisions(universe, snapshot)
        rejected = {r.ticker: _lit_to_enum(r.reason) for r in result.rejections}
        self._sink.record_scan(
            decision_ts=anchor_ts,
            picks=result.picks,
            rejected=rejected,
        )

    def on_feed_gap(self, gap: FeedGap) -> None:
        """Receive a retrospective FeedGap and emit a feed_gap decision.

        Wired by callers as ``ReconnectingProvider(upstream, on_gap=loop.on_feed_gap)``.
        Sync because ReconnectingProvider's callback runs synchronously
        inside its FeedDisconnected handler -- emit-and-return is correct.

        Must be called from within the asyncio event-loop thread. The
        event loop serializes ``_tick`` and this callback, so they cannot
        race on ``self._sink``. If a future ReconnectingProvider moves
        to threaded I/O, callers must marshal the call back to the loop
        thread (e.g., ``loop.call_soon_threadsafe(loop_inst.on_feed_gap, gap)``).
        """
        self._sink.emit(
            ScannerDecision(
                kind="feed_gap",
                decision_ts=self._clock.now(),
                ticker=None,
                pick=None,
                reason=gap.reason,
                gap_start=gap.start,
                gap_end=gap.end,
            )
        )
