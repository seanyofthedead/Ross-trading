"""Async tick driver for the scanner.

Phase 2 -- Atom A3 (#42). Long-running coroutine that paces
:meth:`Scanner.scan` on a Clock and emits per-pick decisions to an
injected :class:`DecisionSink`. The loop owns no provider I/O --
the injected :class:`SnapshotAssembler` is the replay-determinism
boundary.

Cancellation: ``run()`` re-raises CancelledError. No drain on
shutdown, no upstream subscription cleanup. Outside-market-hours
ticks are no-ops, not exits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.clock import is_market_hours
from ross_trading.scanner.decisions import ScannerDecision

if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner


class ScannerLoop:
    """Drive Scanner.scan on a Clock-paced tick."""

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
        snapshot, _most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
        # Staleness self-check lands in Task 5; for now scan unconditionally.
        picks = self._scanner.scan(universe, snapshot)
        for pick in picks:
            self._sink.emit(
                ScannerDecision(
                    kind="picked",
                    decision_ts=anchor_ts,
                    ticker=pick.ticker,
                    pick=pick,
                    reason=None,
                    gap_start=None,
                    gap_end=None,
                )
            )
