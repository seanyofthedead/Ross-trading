"""Pure-function primitives for the Section 3.1 hard filters.

Atom A1 of Phase 2 (#40, tracked under #3). No I/O, no logging, no
module-level mutable state. Thresholds are passed as parameters so
the scanner can A/B test them later without surgery here.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ross_trading.data.news_feed import HeadlineDeduper

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, FloatRecord, Headline


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


def float_le(
    record: FloatRecord | None,
    threshold: int = 20_000_000,
) -> bool:
    """True iff ``record.float_shares <= threshold``.

    Returns ``False`` when ``record`` is ``None`` — absence of
    evidence is not promotion. The default threshold is 20M shares
    (Cameron's hard cap; ``<10M`` is the preferred soft target which
    the ranker (A2) will weigh separately).
    """
    if record is None:
        return False
    return record.float_shares <= threshold


def _within_lookback(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int,
) -> list[Headline]:
    """Return headlines for ``ticker`` with ``ts`` in
    ``[anchor_ts - lookback, anchor_ts]`` (inclusive both ends),
    sorted by ``ts`` ascending.

    Sorted ascending so ``HeadlineDeduper``'s OrderedDict eviction
    sees timestamps in monotonic order — eviction keys off
    ``headline.ts`` and assumes incoming events progress forward.
    """
    cutoff = anchor_ts - timedelta(hours=lookback_hours)
    upper = ticker.upper()
    matched = [
        h for h in headlines
        if h.ticker.upper() == upper and cutoff <= h.ts <= anchor_ts
    ]
    matched.sort(key=lambda h: h.ts)
    return matched


def news_present(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> bool:
    """True iff at least one ticker-matching headline falls in
    ``[anchor_ts - lookback_hours, anchor_ts]``.

    Anchor is the bar-open time, never ``datetime.now()`` — so live
    and replay produce identical answers.
    """
    return headline_count(ticker, headlines, anchor_ts, lookback_hours) >= 1


def headline_count(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> int:
    """Count distinct ticker-matching headlines after running them
    through a fresh ``HeadlineDeduper`` with a window matching
    ``lookback_hours``.

    A fresh deduper is constructed each call so scanner ticks do not
    leak state into each other.
    """
    window = timedelta(hours=lookback_hours)
    deduper = HeadlineDeduper(window=window)
    count = 0
    for headline in _within_lookback(ticker, headlines, anchor_ts, lookback_hours):
        if not deduper.is_duplicate(headline):
            count += 1
    return count
