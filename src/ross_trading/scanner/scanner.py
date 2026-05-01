"""Scanner orchestrator: composes A1 filter primitives + ranker.

Phase 2 -- Atom A2 (#41). Pure-sync. No I/O, no logging, no
module-level mutable state. Thresholds are constructor parameters
so the caller can A/B test without surgery here.

Inputs are :class:`ScannerSnapshot` value objects keyed by ticker;
A3 (the loop) owns provider I/O and assembles the snapshot map.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from ross_trading.scanner.filters import (
    float_le,
    headline_count,
    news_present,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
from ross_trading.scanner.ranking import rank_picks
from ross_trading.scanner.types import ScannerPick

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ross_trading.data.types import FloatRecord
    from ross_trading.scanner.types import ScannerSnapshot


class Scanner:
    """Compose A1's hard filters + the ranker into top-N picks."""

    def __init__(
        self,
        rel_volume_threshold: float = 5.0,
        pct_change_threshold_pct: Decimal = Decimal("10"),
        price_low: Decimal = Decimal("1"),
        price_high: Decimal = Decimal("20"),
        float_threshold: int = 20_000_000,
        news_lookback_hours: int = 24,
        top_n: int = 5,
    ) -> None:
        self._rel_volume_threshold = rel_volume_threshold
        self._pct_change_threshold_pct = pct_change_threshold_pct
        self._price_low = price_low
        self._price_high = price_high
        self._float_threshold = float_threshold
        self._news_lookback_hours = news_lookback_hours
        self._top_n = top_n

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]:
        """Filter the universe by snapshot, then rank top-N.

        Universe members with no snapshot entry are silently skipped
        -- universe drift between enumeration and snapshot assembly
        is normal at the boundary of a session.
        """
        candidates: list[ScannerPick] = []
        for ticker in universe:
            snap = snapshot.get(ticker)
            if snap is None:
                continue
            baseline = snap.baseline_30d
            float_rec = snap.float_record
            if baseline is None or float_rec is None:
                continue
            if not (
                rel_volume_ge(ticker, snap.bar, baseline, self._rel_volume_threshold)
                and pct_change_ge(snap.last, snap.prev_close, self._pct_change_threshold_pct)
                and price_in_band(ticker, snap.bar, self._price_low, self._price_high)
                and float_le(float_rec, self._float_threshold)
            ):
                continue
            candidates.append(self._build_pick(ticker, snap, baseline, float_rec))
        return rank_picks(candidates, n=self._top_n)

    def _build_pick(
        self,
        ticker: str,
        snap: ScannerSnapshot,
        baseline_30d: Decimal,
        float_record: FloatRecord,
    ) -> ScannerPick:
        anchor_ts = snap.bar.ts
        return ScannerPick(
            ticker=ticker,
            ts=anchor_ts,
            rel_volume=Decimal(snap.bar.volume) / baseline_30d,
            pct_change=(snap.last - snap.prev_close) / snap.prev_close * Decimal(100),
            price=snap.last,
            float_shares=float_record.float_shares,
            news_present=news_present(
                ticker, snap.headlines, anchor_ts, self._news_lookback_hours,
            ),
            headline_count=headline_count(
                ticker, snap.headlines, anchor_ts, self._news_lookback_hours,
            ),
            rank=0,
        )
