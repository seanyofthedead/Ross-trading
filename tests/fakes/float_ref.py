"""Scripted FloatReferenceProvider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.errors import FeedError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from ross_trading.data.types import FloatRecord


class FakeFloatReferenceProvider:
    """Returns canned float records keyed on (ticker, as_of)."""

    def __init__(self, records: Mapping[tuple[str, date], FloatRecord]) -> None:
        self._records = {(t.upper(), d): r for (t, d), r in records.items()}
        self.calls: list[tuple[str, date]] = []

    async def get_float(self, ticker: str, as_of: date) -> FloatRecord:
        key = (ticker.upper(), as_of)
        self.calls.append(key)
        record = self._records.get(key)
        if record is None:
            msg = f"no fake float record for {key}"
            raise FeedError(msg)
        return record
