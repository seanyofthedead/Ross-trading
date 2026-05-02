"""Scripted SnapshotAssembler for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from ross_trading.scanner.types import ScannerSnapshot

ScriptValue = tuple["Mapping[str, ScannerSnapshot]", "datetime | None"]


class FakeSnapshotAssembler:
    """Returns canned ``(snapshot_map, most_recent_quote_ts)`` keyed on anchor_ts.

    Records every call in ``self.calls`` (in order) so loop tests can
    assert exactly which anchor_ts values fired during a run.
    """

    def __init__(self, by_anchor: Mapping[datetime, ScriptValue]) -> None:
        self._by_anchor = dict(by_anchor)
        self.calls: list[datetime] = []

    async def assemble(
        self,
        universe: frozenset[str],
        anchor_ts: datetime,
    ) -> ScriptValue:
        del universe  # fake ignores universe; tests script per-anchor only
        self.calls.append(anchor_ts)
        return self._by_anchor[anchor_ts]
