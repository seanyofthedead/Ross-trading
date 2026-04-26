"""Scripted fakes used across the data-layer test suite.

These are *test helpers*, not production code — concrete vendor
providers belong under ``ross_trading.data.providers``.
"""

from tests.fakes.float_ref import FakeFloatReferenceProvider
from tests.fakes.market import FakeMarketDataProvider
from tests.fakes.news import FakeNewsProvider

__all__ = [
    "FakeFloatReferenceProvider",
    "FakeMarketDataProvider",
    "FakeNewsProvider",
]
