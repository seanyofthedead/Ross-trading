"""Top-N ranker for scanner picks.

Phase 2 -- Atom A2 (#41). Pure function. Sorts by ``pct_change``
descending with stable tie-break on ``ticker`` ascending, takes the
first ``n``, and assigns final ``rank=1..N`` via
``dataclasses.replace`` (since :class:`ScannerPick` is frozen).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ross_trading.scanner.types import ScannerPick


def rank_picks(
    candidates: Sequence[ScannerPick],
    n: int = 5,
) -> list[ScannerPick]:
    """Sort ``candidates`` by ``-pct_change, ticker`` and return the top ``n``
    with ``rank`` overwritten to ``1..N``.

    Returns an empty list when ``n <= 0`` (a non-positive ``top_n``
    means "no slots available", not "unbounded").
    """
    if n <= 0:
        return []
    sorted_picks = sorted(candidates, key=lambda p: (-p.pct_change, p.ticker))
    top = sorted_picks[:n]
    return [replace(pick, rank=i + 1) for i, pick in enumerate(top)]
