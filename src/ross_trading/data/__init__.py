"""Read-side data layer: market quotes, bars, news, float reference.

See issue #2 (Phase 1 — Data plumbing) and architecture doc §3.1.
"""

from ross_trading.data.types import (
    Bar,
    FeedGap,
    FloatRecord,
    Headline,
    Quote,
    Side,
    Tape,
)

__all__ = [
    "Bar",
    "FeedGap",
    "FloatRecord",
    "Headline",
    "Quote",
    "Side",
    "Tape",
]
