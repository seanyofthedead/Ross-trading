"""Top-N ranker for scanner picks.

Phase 2 -- Atom A2 (#41). Pure function. Sorts by ``pct_change``
descending, then by float-tier weight descending (preferred floats
ahead of acceptable floats), with stable tie-break on ``ticker``
ascending; takes the first ``n`` and assigns final ``rank=1..N`` via
``dataclasses.replace`` (since :class:`ScannerPick` is frozen).

The float-tier weight encodes the architecture's tiered float policy
(docs/architecture.md §3.1):

* ``< 10_000_000`` shares -- *preferred* (weight 2).
* ``10_000_000 <= float <= 20_000_000`` -- *acceptable* (weight 1).
* ``> 20_000_000`` -- *Gap-and-Go window* (weight 0). The scanner's
  hard ``float_le`` filter caps at 20M today, so weight-0 picks never
  reach the ranker; the band exists so the policy is uniform when
  Phase-3 Gap-and-Go pattern detection raises the cap.

See ``docs/architecture.md`` "Resolved Decisions" appendix for the
ADR-style rationale (ISSUE-008 / Finding 7).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ross_trading.scanner.types import ScannerPick


_PREFERRED_FLOAT_MAX = 10_000_000
_ACCEPTABLE_FLOAT_MAX = 20_000_000


def float_tier_weight(float_shares: int) -> int:
    """Return the tier weight for *float_shares*.

    Higher is better:

    * ``2`` -- ``< 10M`` shares (preferred).
    * ``1`` -- ``10M <= float <= 20M`` (acceptable).
    * ``0`` -- ``> 20M`` (Gap-and-Go-only; outside the scanner's
      current 20M hard cap).
    """
    if float_shares < _PREFERRED_FLOAT_MAX:
        return 2
    if float_shares <= _ACCEPTABLE_FLOAT_MAX:
        return 1
    return 0


def rank_picks(
    candidates: Sequence[ScannerPick],
    n: int = 5,
) -> list[ScannerPick]:
    """Sort ``candidates`` by ``-pct_change, -float_tier_weight, ticker`` and
    return the top ``n`` with ``rank`` overwritten to ``1..N``.

    Returns an empty list when ``n <= 0`` (a non-positive ``top_n``
    means "no slots available", not "unbounded").
    """
    if n <= 0:
        return []
    sorted_picks = sorted(
        candidates,
        key=lambda p: (-p.pct_change, -float_tier_weight(p.float_shares), p.ticker),
    )
    top = sorted_picks[:n]
    return [replace(pick, rank=i + 1) for i, pick in enumerate(top)]
