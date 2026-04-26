"""Exponential moving average.

Initialization uses the SMA of the first ``period`` values, which is
the convention TA-Lib uses (and what most charting platforms display).
Returned series is the same length as the input; positions before the
initialization window are filled with the running prefix mean so
callers don't need to worry about ``None`` slots.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def ema_alpha(period: int) -> Decimal:
    if period <= 0:
        msg = "period must be positive"
        raise ValueError(msg)
    return Decimal(2) / (Decimal(period) + Decimal(1))


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    if period <= 0:
        msg = "period must be positive"
        raise ValueError(msg)
    if not values:
        return []
    alpha = ema_alpha(period)
    one_minus_alpha = Decimal(1) - alpha
    out: list[Decimal] = []
    running_sum = Decimal(0)
    seed: Decimal | None = None
    for i, v in enumerate(values):
        running_sum += v
        if i < period - 1:
            out.append(running_sum / Decimal(i + 1))
            continue
        if seed is None:
            seed = running_sum / Decimal(period)
            out.append(seed)
            continue
        prev = out[-1]
        out.append(alpha * v + one_minus_alpha * prev)
    return out
