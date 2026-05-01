"""Pure-function primitives for the Section 3.1 hard filters.

Atom A1 of Phase 2 (#40, tracked under #3). No I/O, no logging, no
module-level mutable state. Thresholds are passed as parameters so
the scanner can A/B test them later without surgery here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ross_trading.data.types import Bar


def rel_volume_ge(
    symbol: str,
    snapshot: Bar,
    baseline_30d: Decimal | None,
    threshold: float = 5.0,
) -> bool:
    """True iff ``snapshot.volume / baseline_30d >= threshold``.

    ``symbol`` documents per-symbol intent (matches issue #40's
    signature) but is not used in the body — the relevant volume is
    already on ``snapshot``. The project's ruff config does not
    enable ``ARG``, so no suppression is needed.

    Returns ``False`` when ``baseline_30d`` is ``None`` or zero — both
    mean "we don't have enough history to evaluate", and absence of
    evidence is not promotion.
    """
    if baseline_30d is None or baseline_30d == 0:
        return False
    ratio = Decimal(snapshot.volume) / baseline_30d
    return ratio >= Decimal(str(threshold))


def pct_change_ge(
    current: Decimal,
    reference: Decimal,
    threshold_pct: Decimal,
) -> bool:
    """True iff ``(current - reference) / reference >= threshold_pct / 100``.

    A pure two-price primitive — A1 stays ignorant of session
    boundaries. The caller (A2) supplies the right pair: pass
    ``current=quote.last, reference=prior_session_close`` for
    gainer-% on the day; pass ``current=bar.close,
    reference=bar.open`` for an intraday move check.

    ``threshold_pct`` is in **percent units** (``Decimal("10")``
    means 10%, not 0.10) so call sites read like the spec language
    of "≥ +10%".

    Returns ``False`` when ``reference == 0`` (avoid divide-by-zero).
    """
    if reference == 0:
        return False
    change = (current - reference) / reference
    return change >= threshold_pct / Decimal(100)


def price_in_band(
    symbol: str,
    snapshot: Bar,
    low: Decimal = Decimal("1"),
    high: Decimal = Decimal("20"),
) -> bool:
    """True iff ``low <= snapshot.close <= high`` (inclusive both ends).

    ``symbol`` is unused (kept for issue-spec parity); see notes on
    ``rel_volume_ge`` for the no-suppression-needed reasoning.
    """
    return low <= snapshot.close <= high
