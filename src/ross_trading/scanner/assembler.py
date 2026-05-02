"""Per-tick snapshot assembler protocol.

Phase 2 -- Atom A3 (#42). The replay-determinism boundary: A3's loop
asks the assembler for an as-of view of the world at ``anchor_ts``,
and the assembler returns the per-symbol snapshot map plus the
freshest quote timestamp it has on hand. Concrete vendor wiring
(which provider feeds bars / quotes / news / floats / baselines) is
out of scope for #42 -- a later atom composes those into a real
:class:`SnapshotAssembler`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from ross_trading.scanner.types import ScannerSnapshot


@runtime_checkable
class SnapshotAssembler(Protocol):
    """Read data as of ``anchor_ts`` and return a Scanner-ready bundle.

    Returns ``(snapshot_map, most_recent_quote_ts)``:
    - ``snapshot_map`` -- per-symbol ScannerSnapshot for every symbol
      in ``universe`` for which the assembler has data; symbols not
      yet observed are omitted.
    - ``most_recent_quote_ts`` -- ts of the freshest quote across all
      symbols, used by the loop for the staleness self-check. ``None``
      means "no quote ever observed" -- the loop arms staleness only
      after the first non-None reply.
    """

    async def assemble(
        self,
        universe: frozenset[str],
        anchor_ts: datetime,
    ) -> tuple[Mapping[str, ScannerSnapshot], datetime | None]: ...
