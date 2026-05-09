"""Daily-chart strength filter (architecture §3.3).

Phase 3 — Atom A1 (#72, tracks under #4). Pure-function consumer of
the ``daily_emas`` cache populated by
``data.historical.precompute_daily_emas``.

Scoring (per Cameron's Gap and Go, TA Series p.4):

* ``score = (close > EMA20) + (close > EMA50) + (close > EMA200)``
  + breakout flag + turnaround flag.
* Range today: 0..3 (the breakout / turnaround inputs are not yet
  wired; they are emitted as ``None`` until follow-up atom #73 lands
  and the score range expands to 0..5).
* Threshold semantics live with the consumer (not here): score ≥ 2 →
  *keep*, score 0-1 → *demote*.

Cache-as-source-of-truth: a missing ``daily_emas`` row for any of the
three periods returns ``score=None`` and the corresponding
``above_emaXX=None`` (matching ``filters.float_le`` "absence of
evidence is not promotion" semantics). Recomputing on the fly is the
job of ``precompute_daily_emas``.

Strict greater-than: ``close == EMA`` returns ``False`` for that EMA,
mirroring the architecture-doc pseudocode exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from ross_trading.data.cache import HistoricalCache


@dataclass(frozen=True, slots=True)
class DailyStrengthScore:
    """Strength-filter result for a single (symbol, day).

    A field set to ``None`` means "no evidence" (cache miss or flag
    not yet wired). Consumers must treat ``None`` as a demotion
    signal, not as a permissive default.
    """

    score: int | None
    above_ema20: bool | None
    above_ema50: bool | None
    above_ema200: bool | None
    breakout: bool | None
    turnaround: bool | None


_PERIODS = (20, 50, 200)


def score_daily_strength(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: HistoricalCache,
) -> DailyStrengthScore:
    """Score *symbol* against its EMA20 / EMA50 / EMA200 for *as_of*.

    Returns a :class:`DailyStrengthScore`. ``score`` is the sum of
    ``daily_close > EMA{20,50,200}`` when all three rows exist in the
    cache; if any row is missing, ``score`` is ``None`` and the
    corresponding ``above_emaXX`` flag is ``None``. ``breakout`` and
    ``turnaround`` are always ``None`` until the follow-up atom (#73)
    wires their inputs.
    """
    flags: dict[int, bool | None] = {
        period: None if (ema := cache.ema(symbol, as_of, period)) is None
        else daily_close > ema
        for period in _PERIODS
    }
    score = (
        sum(int(flag) for flag in flags.values() if flag is not None)
        if all(flag is not None for flag in flags.values())
        else None
    )
    return DailyStrengthScore(
        score=score,
        above_ema20=flags[20],
        above_ema50=flags[50],
        above_ema200=flags[200],
        breakout=None,
        turnaround=None,
    )
