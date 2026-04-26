"""Error hierarchy.

Phase 1 introduces the data-layer errors below. Other phases extend
``TradingError`` with their own subclasses (risk, execution, etc.).
"""

from __future__ import annotations


class TradingError(Exception):
    """Root of all domain-specific exceptions in the agent."""


class FeedError(TradingError):
    """Anything that goes wrong inside the read-side data layer."""


class FeedDisconnected(FeedError):
    """The provider's transport dropped; reconnect logic should engage."""


class FeedGapError(FeedError):
    """A gap window could not be backfilled within the configured budget."""


class MissingRecordingError(FeedError):
    """Replay was asked for data that is not present on disk."""


class RateLimitError(FeedError):
    """The vendor signalled a rate-limit response; backoff is required."""
