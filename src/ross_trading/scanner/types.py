"""Frozen value types for the scanner.

Phase 2 — Atom A2 (#41). ``ScannerPick`` is the output unit;
``ScannerSnapshot`` is the per-symbol input bag the scanner needs to
evaluate the Section 3.1 filters. Keeping inputs and outputs as
value objects lets ``Scanner.scan`` stay pure-sync — A3 (the loop)
owns provider I/O and assembles the snapshot map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from ross_trading.data.types import Bar, FloatRecord, Headline


# Mirrors `journal.models.RejectionReason` string values exactly.
# Both must stay in lockstep -- if you add a value here, add it there
# (and the migration to ALTER TYPE), and vice versa.
RejectionReasonLit = Literal[
    "no_snapshot",
    "missing_baseline",
    "missing_float",
    "rel_volume",
    "pct_change",
    "price_band",
    "float_size",
]


@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    """Per-symbol inputs needed to evaluate the scanner's hard filters.

    Assembled by A3 from provider calls and consumed by
    :meth:`Scanner.scan` as a deterministic value. Keeps the scanner
    I/O-free so live and replay produce identical answers.

    - ``bar`` -- last completed bar; provides ``volume`` (rel-vol input),
      ``close`` (price-band input), and ``ts`` (the bar's open time)
      as the anchor for news lookback.
    - ``last`` -- latest quote price; reference for the gainer-% check
      (``pct_change_ge`` vs ``prev_close``) and the value surfaced as
      ``ScannerPick.price``.
    - ``prev_close`` -- previous session's closing price; reference for
      gainer-%.
    - ``baseline_30d`` -- 30-day average daily volume; ``None`` means
      "insufficient history" and the scanner rejects.
    - ``float_record`` -- daily float record; ``None`` means "no float
      data" and the scanner rejects.
    - ``headlines`` -- ticker-relevant headlines for the news soft
      signals. An empty sequence is fine (``news_present=False``,
      ``headline_count=0``); since news is non-gating per #39, the
      pick still survives if the hard filters pass.
    """

    bar: Bar
    last: Decimal
    prev_close: Decimal
    baseline_30d: Decimal | None
    float_record: FloatRecord | None
    headlines: Sequence[Headline]


@dataclass(frozen=True, slots=True)
class ScannerPick:
    """A symbol that passed the scanner's hard filters.

    Frozen, slots-enabled, picklable (per #41 acceptance). ``rank=0``
    is the pre-rank sentinel produced by the filter step;
    :func:`rank_picks` assigns final ``rank`` values ``1..N`` via
    ``dataclasses.replace``. Pre-rank picks never escape
    :meth:`Scanner.scan` -- external callers only see ranked output.
    """

    ticker: str
    ts: datetime
    rel_volume: Decimal
    pct_change: Decimal
    price: Decimal
    float_shares: int
    news_present: bool
    headline_count: int
    rank: int = 0


@dataclass(frozen=True, slots=True)
class ScannerRejection:
    """One universe member that failed the scanner's hard filters.

    Phase 2 -- issue #51. ``reason`` is the *first* failing filter in
    :meth:`Scanner.scan_with_decisions`'s evaluation order, which mirrors
    the AND-chain in the legacy :meth:`Scanner.scan`. The literal
    values are the contract referenced by the SQL schema's
    ``RejectionReason`` enum; renaming any value requires a coordinated
    migration.
    """

    ticker: str
    ts: datetime
    reason: RejectionReasonLit


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Combined output of :meth:`Scanner.scan_with_decisions`.

    Phase 2 -- issue #51. Every universe member that has a snapshot
    appears in exactly one of ``picks`` or ``rejections``. Members
    with no snapshot entry are silently skipped (preserves
    :meth:`Scanner.scan`'s pre-existing policy at ``scanner.py:67-70``).
    """

    picks: list[ScannerPick]
    rejections: list[ScannerRejection]
