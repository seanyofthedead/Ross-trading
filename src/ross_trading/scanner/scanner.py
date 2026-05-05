"""Scanner orchestrator: composes A1 filter primitives + ranker.

Phase 2 -- Atom A2 (#41), extended in A8 (#51) with
``scan_with_decisions``. Pure-sync. No I/O, no logging, no
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
from ross_trading.scanner.types import ScannerPick, ScannerRejection, ScanResult

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

    def scan_with_decisions(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> ScanResult:
        """Filter the universe by snapshot, ranking picks and recording
        the *first* failing filter for each rejected ticker.

        Universe members with no snapshot entry are silently skipped
        -- universe drift between enumeration and snapshot assembly
        is normal at the boundary of a session, and not a journal-
        worthy event (see plan D-A8-5).

        Filter evaluation order matches the AND-chain in the legacy
        :meth:`scan` method (preserved for behavioral compatibility):
        baseline presence, float-record presence, ``rel_volume_ge``,
        ``pct_change_ge``, ``price_in_band``, ``float_le``. Returns
        as soon as the first failing filter is identified.
        """
        candidates: list[ScannerPick] = []
        rejections: list[ScannerRejection] = []
        for ticker in universe:
            snap = snapshot.get(ticker)
            if snap is None:
                continue
            anchor_ts = snap.bar.ts
            baseline = snap.baseline_30d
            if baseline is None:
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="missing_baseline",
                ))
                continue
            float_rec = snap.float_record
            if float_rec is None:
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="missing_float",
                ))
                continue
            if not rel_volume_ge(ticker, snap.bar, baseline, self._rel_volume_threshold):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="rel_volume",
                ))
                continue
            if not pct_change_ge(snap.last, snap.prev_close, self._pct_change_threshold_pct):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="pct_change",
                ))
                continue
            if not price_in_band(ticker, snap.bar, self._price_low, self._price_high):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="price_band",
                ))
                continue
            if not float_le(float_rec, self._float_threshold):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="float_size",
                ))
                continue
            candidates.append(self._build_pick(ticker, snap, baseline, float_rec))
        # `scan_with_decisions` returns ALL ranked passers (no top_n truncation)
        # so the partition contract holds: every universe member with a
        # snapshot is in exactly one of `picks` or `rejections`. The
        # back-compat `scan(...)` wrapper applies the `[:top_n]` slice for
        # callers that only want the watchlist-sized result.
        return ScanResult(
            picks=tuple(rank_picks(candidates, n=len(candidates))),
            rejections=tuple(rejections),
        )

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]:
        """Return top-N picks; thin wrapper over :meth:`scan_with_decisions`.

        Preserved for callers that only care about the watchlist-sized
        slice (e.g., back-test drivers, ad-hoc scripts). The full
        partition is in :meth:`scan_with_decisions`'s :class:`ScanResult`.

        The returned ``list`` is a fresh copy of the tuple-backed
        :attr:`ScanResult.picks` -- callers may mutate it without
        affecting the underlying immutable scan result.
        """
        return list(self.scan_with_decisions(universe, snapshot).picks[: self._top_n])

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
