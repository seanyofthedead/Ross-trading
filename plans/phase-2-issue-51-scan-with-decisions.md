# Phase 2 — A8: `scan_with_decisions` for Journal-Grade Rejection Reasons

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The harness owner has asked to be paused after each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `Scanner` with `scan_with_decisions(universe, snapshot) -> ScanResult` so `ScannerLoop` can journal rejection reasons (per universe member, first-failing filter) without re-running the filter chain. Migrate the loop to `JournalWriter.record_scan(...)` so picks and rejections for one tick land atomically as a fourth `REJECTED` decision kind.

**Architecture:** One new public method on `Scanner` (`scan_with_decisions`); existing `Scanner.scan` becomes a thin wrapper returning `.picks`. Two new frozen value types in `scanner/types.py` (`ScannerRejection`, `ScanResult`). The in-memory `ScannerDecision` (`scanner/decisions.py`) grows a fourth `Literal` kind plus a `rejection_reason` field. The `DecisionSink` Protocol grows a `record_scan(decision_ts, picks, rejected)` method (mirroring `JournalWriter.record_scan`); `ScannerLoop._tick` switches from `N × sink.emit()` to one `sink.record_scan()` per tick. The DB schema needs zero changes — migration 0002 already landed `DecisionKind.REJECTED`, the `RejectionReason` enum (7 values), the `rejection_reason` column, and the CHECK constraint making `rejection_reason` required for REJECTED rows.

**Tech Stack:** Python 3.11, `decimal.Decimal`, `dataclasses` (frozen, slots), mypy `--strict`, ruff (`["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`), pytest with `asyncio_mode = "auto"`. SQLAlchemy 2.x typed ORM; SQLite for tests via `Base.metadata.create_all`.

**Issue:** [#51](https://github.com/seanyofthedead/Ross-trading/issues/51) — tracked under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Depends on:** A2 (#41, scanner core, merged at `41e7bfe`), A3 (#42, async tick driver, merged at `e4e44ba`), A4 (#43, journal storage, merged at `d20e0be`), A5 (#44, journal writer, merged at `137027a`).

---

## Decisions Resolved

- **D-A8-1 — Loop writes via `record_scan`, not `N × emit`.** The journal writer's module docstring (`journal/writer.py:18-25`) explicitly designates this issue as the migration point: "splitting [picks vs rejections] would systematically overstate scanner precision, and #51 will migrate A3's loop from N × emit to a single record_scan per tick." We honor that: `DecisionSink` Protocol grows a `record_scan(...)` method, `JournalWriter` already implements it, `FakeDecisionSink` adds a recording stub, and `ScannerLoop._tick` calls `record_scan` for the picks + rejections branch. `stale_feed` and `feed_gap` keep using `emit` (they fire alone — atomicity is not at risk). **Why not the literal "emit a REJECTED decision per rejection" reading of the issue?** Because the writer comment is a baked-in architectural decision from #44, and per-emit would silently violate the tick-atomicity invariant. Outcome (REJECTED rows in the DB, one per rejected ticker) is identical.

- **D-A8-2 — Rejection-reason ordering matches the schema enum, which matches `Scanner.scan`'s AND-chain.** `journal/models.py::RejectionReason` enum is already declared in scanner-AND order (`NO_SNAPSHOT`, `MISSING_BASELINE`, `MISSING_FLOAT`, `REL_VOLUME`, `PCT_CHANGE`, `PRICE_BAND`, `FLOAT_SIZE`). `scan_with_decisions` evaluates filters in that order and returns the *first* failing reason. The `Literal[...]` on `ScannerRejection.reason` mirrors the enum string values exactly (lowercase strings); both must stay in lockstep.

- **D-A8-3 — Test file naming collision: extend, don't fork.** Issue #51 calls for a new file at `tests/unit/test_scanner_decisions.py`. That path **already exists** (created in #42 to test the in-memory `ScannerDecision` dataclass). Forking to a near-duplicate name would fragment the test surface. Proposed answer: extend the existing file with one section per rejection reason plus an "all rejected" case, prefixed by a section banner. Rationale: the existing tests cover `ScannerDecision` *as a value type*; the new tests cover the new `ScannerRejection`/`ScanResult` value types and `Scanner.scan_with_decisions` — same semantic domain, same file is the right home. This decision is itself recorded in the file's module docstring.

- **D-A8-4 — `ScannerPick.price` and existing `scan(...)` semantics are preserved.** `scan(universe, snapshot)` becomes literally `return self.scan_with_decisions(universe, snapshot).picks` — no behavior change. The existing test suite (`tests/unit/test_scanner.py`, 14 tests) is the regression gate; it must stay green without modification.

- **D-A8-5 — Universe members with no snapshot are silently skipped (NO regression).** The existing `Scanner.scan` policy at `scanner.py:67-70` is "no snapshot ⇒ continue (silent skip)." The issue acceptance line *"every universe member exactly once across `picks` ∪ `rejections` (modulo silent-skip semantics for not-in-snapshot when that policy is preserved)"* preserves this. **However:** the issue's spec'd `Literal[...]` includes `"no_snapshot"` as a reason, which suggests the alternative reading "expose silent-skip as a NO_SNAPSHOT rejection." We pick the *preserve-silent-skip* reading because: (a) the journal writer's `record_scan` only writes rows for picks + rejections passed in — silently-skipped tickers were never in scope for the journal; (b) reversing the policy would change A2 behavior beyond what's stated. The `NO_SNAPSHOT` enum value stays defined in `RejectionReason` (it's already in the schema) but is unused by `scan_with_decisions` — reserved for a future explicit-skip mode if needed. *This is the only place this plan diverges from a literal reading of the issue; flag for follow-up if reviewer disagrees.*

No Resolved-Decisions-appendix entries in `docs/architecture.md` are reversed by this PR.

---

## Acceptance Criteria (from issue #51)

- [ ] `scan_with_decisions(universe, snapshot)` returns every universe member that has a snapshot exactly once across `picks ∪ rejections`. Silent-skip preserved for not-in-snapshot.
- [ ] Rejection reasons are a stable `Literal[...]` enum surfacing the *first* failing filter in the existing AND-combine order: `missing_baseline`, `missing_float`, `rel_volume`, `pct_change`, `price_band`, `float_size`. (`no_snapshot` defined in the Literal for forward-compat with the schema enum but unused by this method — see D-A8-5.)
- [ ] Existing `scan(...)` test suite stays green without modification (regression-tested).
- [ ] `ScannerLoop` emits a fourth decision kind `rejected` carrying the reason. Wired via `DecisionSink.record_scan(...)`.
- [ ] `tests/unit/test_scanner_decisions.py` extended with one test per active rejection reason plus an "all rejected" case.
- [ ] `mypy --strict`, `ruff`, `pytest -m "not integration"`, `pytest -m integration` all green.
- [ ] CI green on the PR.

---

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Modify | `src/ross_trading/scanner/types.py` | Add `ScannerRejection` and `ScanResult` frozen dataclasses + `RejectionReasonLit` Literal alias. |
| Modify | `src/ross_trading/scanner/scanner.py` | Add `scan_with_decisions(...)`; refactor `scan(...)` into wrapper; expose first-failing-filter logic. |
| Modify | `src/ross_trading/scanner/decisions.py` | Extend `ScannerDecision.kind` Literal with `"rejected"`; add `rejection_reason: RejectionReasonLit \| None` field; extend `DecisionSink` Protocol with `record_scan(...)`. |
| Modify | `src/ross_trading/scanner/loop.py` | Switch `_tick`'s scan branch to `scan_with_decisions` + `sink.record_scan(...)`. `stale_feed` and `feed_gap` keep `emit`. |
| Modify | `src/ross_trading/journal/writer.py` | `JournalWriter.record_scan` already exists; just verify the new `DecisionSink.record_scan` signature it satisfies. Add it as an explicit Protocol-conforming method (no behavior change). |
| Modify | `tests/fakes/decision_sink.py` | Add `record_scan(decision_ts, picks, rejected)` recording method; expose `self.scans: list[tuple[...]]`. |
| Modify | `tests/unit/test_scanner_decisions.py` | Append `ScannerRejection` + `ScanResult` value-type tests, `scan_with_decisions` tests (one per active reason + all-rejected + happy-path mix). |
| Modify | `tests/unit/test_scanner_loop.py` | Update existing happy-path expectations (one `record_scan` call per tick instead of N `emit` calls) and add a tick with mixed picks+rejections. |
| Modify | `tests/unit/test_journal_writer.py` | Add a Protocol-conformance test: `JournalWriter` satisfies `DecisionSink` (post-extension). |

No new files. No changes to `docs/architecture.md` (no Resolved Decision reversed). No changes to `pyproject.toml`. No DB migration needed (schema already supports REJECTED).

---

## Key Interfaces

```python
# src/ross_trading/scanner/types.py — additions

# Mirrors journal/models.py::RejectionReason string values exactly.
RejectionReasonLit = Literal[
    "no_snapshot",          # reserved; unused by scan_with_decisions today (see D-A8-5)
    "missing_baseline",
    "missing_float",
    "rel_volume",
    "pct_change",
    "price_band",
    "float_size",
]


@dataclass(frozen=True, slots=True)
class ScannerRejection:
    """One universe member that failed the scanner's hard filters.

    `reason` is the first failing filter in the AND-chain order
    declared by `Scanner.scan_with_decisions`. The literal values
    are pinned to `journal.models.RejectionReason` and must not
    drift.
    """
    ticker: str
    ts: datetime
    reason: RejectionReasonLit


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Combined output of `Scanner.scan_with_decisions`.

    Invariant: every universe member that has a snapshot appears in
    exactly one of `picks` or `rejections`. Members with no snapshot
    are silently skipped (preserves Scanner.scan policy).
    """
    picks: tuple[ScannerPick, ...]
    rejections: tuple[ScannerRejection, ...]


# src/ross_trading/scanner/scanner.py — additions

class Scanner:
    def scan_with_decisions(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> ScanResult: ...

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]:
        return self.scan_with_decisions(universe, snapshot).picks


# src/ross_trading/scanner/decisions.py — modifications

@dataclass(frozen=True, slots=True)
class ScannerDecision:
    kind: Literal["picked", "stale_feed", "feed_gap", "rejected"]   # +rejected
    decision_ts: datetime
    ticker: str | None
    pick: ScannerPick | None
    reason: str | None
    gap_start: datetime | None
    gap_end: datetime | None
    rejection_reason: RejectionReasonLit | None = None              # NEW field, defaulted

    # __post_init__ unchanged (tz-awareness checks).


@runtime_checkable
class DecisionSink(Protocol):
    def emit(self, decision: ScannerDecision) -> None: ...

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None: ...
```

**`scan_with_decisions` algorithm** — preserves the existing AND-chain order at `scanner.py:75-80` exactly:

```
for ticker in universe:
    snap = snapshot.get(ticker)
    if snap is None: continue                                       # silent skip
    if snap.baseline_30d is None: REJECT(missing_baseline); continue
    if snap.float_record is None: REJECT(missing_float);    continue
    if not rel_volume_ge(...):    REJECT(rel_volume);       continue
    if not pct_change_ge(...):    REJECT(pct_change);       continue
    if not price_in_band(...):    REJECT(price_band);       continue
    if not float_le(...):         REJECT(float_size);       continue
    candidates.append(_build_pick(...))
return ScanResult(picks=rank_picks(candidates, n=top_n), rejections=rejections)
```

The current `scan(...)` method's combined `if not (A and B and C and D): continue` block is unwound into individual `if not X: REJECT(reason); continue` lines. Short-circuit behavior is preserved (Python's `and` evaluation is left-to-right, same order as the new sequential `if`s).

`scan(...)` becomes the trivial wrapper shown above. No behavior change visible to existing callers.

**`ScannerLoop._tick` migration** (`loop.py:64-97`):

```
async def _tick(self) -> None:
    anchor_ts = self._clock.now()
    if not is_market_hours(anchor_ts): return
    universe = await self._universe_provider.list_symbols(anchor_ts.date())
    snapshot, most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
    if most_recent_quote_ts is not None and (anchor_ts - most_recent_quote_ts).total_seconds() > self._staleness_threshold_s:
        self._sink.emit(ScannerDecision(kind="stale_feed", ...))
        return
    result = self._scanner.scan_with_decisions(universe, snapshot)
    rejected_for_writer = {r.ticker: _lit_to_enum(r.reason) for r in result.rejections}
    self._sink.record_scan(
        decision_ts=anchor_ts,
        picks=result.picks,
        rejected=rejected_for_writer,
    )
```

`_lit_to_enum` is a private one-liner mapping `RejectionReasonLit` (string) to `journal.models.RejectionReason` (enum). It lives in `scanner/loop.py` because that's where the boundary between in-memory types and writer-shaped values is.

---

## Test Strategy

**Unit (`tests/unit/`):**
- `test_scanner_decisions.py` — extend with:
  - `ScannerRejection` value-type invariants (frozen, slots, picklable, equality).
  - `ScanResult` value-type invariants.
  - `Scanner.scan_with_decisions` happy path (one passing ticker → picks=[pick], rejections=[]).
  - One test per active rejection reason: `missing_baseline`, `missing_float`, `rel_volume`, `pct_change`, `price_band`, `float_size`. Each builds a `ScannerSnapshot` that fails *exactly that filter* and asserts `len(picks) == 0`, `len(rejections) == 1`, `rejections[0].reason == "<reason>"`.
  - "All rejected" case: 3-ticker universe, all fail (each for a different reason), assert `picks == []`, `rejections` length 3 with the correct reasons.
  - Mixed picks + rejections: 2 pass, 2 fail, assert correct partition.
  - Universe-not-in-snapshot is silently skipped (regression-protect D-A8-5).
  - `Scanner.scan(...)` regression: identical inputs as the happy path → `scan_with_decisions(...).picks == scan(...)`.
- `test_scanner.py` — **no edits** (regression gate).
- `test_scanner_loop.py` — modify happy-path tests:
  - `test_inside_market_hours_calls_assembler_and_emits_picked` → assert `sink.scans == [(INSIDE_TS, [pick], {})]` not `sink.decisions`.
  - `test_no_picks_emits_no_decisions` → assert `sink.scans == [(INSIDE_TS, [], {})]` (one empty record_scan call, not zero — record_scan fires every tick now).
  - `test_multiple_picks_emitted_in_rank_order` → assert `sink.scans[0][1]` ranked correctly.
  - Add `test_tick_with_mixed_picks_and_rejections` — universe of 3, one passes, two fail with different reasons; assert one `record_scan` call carries both.
  - `stale_feed` / `feed_gap` tests → no change (still use `emit`).
- `test_journal_writer.py` — add `test_writer_satisfies_decision_sink_protocol`:
  - `from ross_trading.scanner.decisions import DecisionSink`
  - `assert isinstance(JournalWriter(session_factory), DecisionSink)`

**Integration (`tests/integration/`):**
- `test_scanner_loop_journal.py` — verify end-to-end: a tick with picks + rejections produces correct row counts in the SQLite journal: N PICKED rows + M REJECTED rows in one transaction. The CHECK constraints (already in schema) prove `rejection_reason` is populated only on REJECTED rows.
- Existing tests should still pass without modification — `JournalWriter.record_scan` already does this; the test additions just exercise the new path.

**Regression gate:** the existing test suite (currently 14 in `test_scanner.py`, 16+ in `test_scanner_loop.py`, 10 in `test_scanner_decisions.py`, plus all journal/integration tests) stays green without modification — only additions are allowed in pre-existing tests where the call shape changes (loop tests).

---

## Defects / Open Questions

- **OQ-1 — Stale-feed + record_scan coexistence.** When a tick is staleness-suppressed, the loop returns early after `sink.emit(stale_feed_decision)` *without* calling `sink.record_scan`. That means the universe-coverage invariant ("every universe member appears once across picks/rejections") is intentionally not enforced for stale ticks — those ticks have no scanner output at all. Consistent with current `_tick` behavior (no PICKED rows on stale ticks). Documented in the loop docstring update.
- **OQ-2 — `record_scan` always called, even on no-pick + no-rejection ticks.** Today, a tick with zero picks emits zero decisions. Post-migration, every non-stale tick within market hours calls `record_scan(ts, [], {})`. The writer's `record_scan` already handles empty inputs (uses `session.begin()` to ensure the empty-input transaction still fires — see `writer.py:78-83`). One empty transaction per tick is acceptable overhead (~1.7k empty txns per 4-hour trading window at 2s tick interval; SQLite handles this at <1ms each). If profiling shows this is a hotspot, we can add a `if picks or rejected: ...` guard later.
- **OQ-3 — `RejectionReason` enum vs `RejectionReasonLit` Literal.** Two parallel definitions exist: the SQLAlchemy `Enum` (DB-facing) and the `Literal[...]` (in-memory, type-checker-facing). They must stay in lockstep. Mitigation: a `_lit_to_enum` helper in `scanner/loop.py` is the only conversion site, and it's a `match` on every literal value — mypy strict catches drift at the conversion site. Future work could collapse to a single source of truth (e.g., generate the Literal from the enum), but that's out of scope for #51.
- **OQ-4 — `tests/unit/test_scanner_decisions.py` is now a multi-purpose file.** Per D-A8-3 we extend rather than fork. The file's docstring is updated to reflect both #42 (ScannerDecision value type) and #51 (ScannerRejection / ScanResult / scan_with_decisions). If the file grows past ~400 lines, split in a follow-up.

---

## Conventions

- **Imports arrive when needed.** Each task's diff adds only what it references. `RejectionReasonLit` is added to `scanner/types.py` first (Task 1), then re-exported / referenced by `scanner/decisions.py` (Task 3) and `scanner/loop.py` (Task 5).
- **No `# noqa` for ruff rules outside the project's `select` list.** `select = ["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`.
- **ASCII in comments and strings** where it reads identically. Avoid `≥`/`×`; use `>=`/`x`.
- **Decimal everywhere for price math** (preserved from existing code).
- **Conventional commits**: `feat(scanner): ...`, `test(scanner): ...`, `chore(...)`, `docs(...)`. Co-author trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```

---

## Effort Estimate

**M** (medium). Six source-file modifications (~150 LoC net add), four test-file modifications (~250 LoC net add). No new files, no new dependencies, no schema changes. Roughly 2–3 hours including local gates and PR.

---

## Tasks

### Task 1: Add `RejectionReasonLit` + `ScannerRejection` + `ScanResult` to `scanner/types.py`

**Files:**
- Modify: `src/ross_trading/scanner/types.py`
- Modify: `tests/unit/test_scanner_decisions.py` (append value-type tests)

- [ ] **Step 1: Append failing tests to `tests/unit/test_scanner_decisions.py`**

Append the following at the bottom of the file. Update the module docstring on line 1 from:

```python
"""Atom A3 -- ScannerDecision + DecisionSink (issue #42)."""
```

to:

```python
"""Atom A3 + A8 -- ScannerDecision + DecisionSink (#42), ScannerRejection +
ScanResult + scan_with_decisions (#51).

Per #51 plan D-A8-3: this file extends rather than forks because the new
types live in the same semantic domain (scanner-decision shapes) as the
existing ones.
"""
```

Then append:

```python
# =============================================================================
# Issue #51 -- ScannerRejection, ScanResult, scan_with_decisions
# =============================================================================

from ross_trading.scanner.types import ScanResult, ScannerRejection


def _rejection(reason: str = "rel_volume", ticker: str = "AVTX") -> ScannerRejection:
    return ScannerRejection(ticker=ticker, ts=T0, reason=reason)  # type: ignore[arg-type]


# --------------------------------------------------------------- ScannerRejection


def test_rejection_is_frozen() -> None:
    r = _rejection()
    with pytest.raises(FrozenInstanceError):
        r.reason = "pct_change"  # type: ignore[misc]


def test_rejection_has_slots() -> None:
    assert "__slots__" in ScannerRejection.__dict__


def test_rejection_picklable_roundtrip() -> None:
    r = _rejection()
    revived = pickle.loads(pickle.dumps(r))  # noqa: S301
    assert revived == r


def test_rejection_equality_value_based() -> None:
    assert _rejection() == _rejection()
    assert _rejection(reason="rel_volume") != _rejection(reason="pct_change")


# ------------------------------------------------------------------ ScanResult


def test_scan_result_is_frozen() -> None:
    sr = ScanResult(picks=[_pick()], rejections=[_rejection()])
    with pytest.raises(FrozenInstanceError):
        sr.picks = []  # type: ignore[misc]


def test_scan_result_has_slots() -> None:
    assert "__slots__" in ScanResult.__dict__


def test_scan_result_picklable_roundtrip() -> None:
    sr = ScanResult(picks=[_pick()], rejections=[_rejection()])
    revived = pickle.loads(pickle.dumps(sr))  # noqa: S301
    assert revived == sr


def test_scan_result_empty_both_lists_ok() -> None:
    sr = ScanResult(picks=[], rejections=[])
    assert sr.picks == []
    assert sr.rejections == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/unit/test_scanner_decisions.py -v`
Expected: `ImportError` for `ScanResult` / `ScannerRejection` from `ross_trading.scanner.types`.

- [ ] **Step 3: Add the new types to `src/ross_trading/scanner/types.py`**

Update the file's import block and append the new types. Replace:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from ross_trading.data.types import Bar, FloatRecord, Headline
```

with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from ross_trading.data.types import Bar, FloatRecord, Headline


# Mirrors `journal.models.RejectionReason` string values exactly.
# Both must stay in lockstep -- if you add a value here, add it there
# (and the migration to ALTER TYPE), and vice versa.
RejectionReasonLit = Literal[
    "no_snapshot",
    "missing_baseline",
    "missing_float",
    "rel_volume",
    "pct_change",
    "price_band",
    "float_size",
]
```

Then append at the bottom of the file (after `ScannerPick`):

```python
@dataclass(frozen=True, slots=True)
class ScannerRejection:
    """One universe member that failed the scanner's hard filters.

    Phase 2 -- issue #51. ``reason`` is the *first* failing filter in
    :meth:`Scanner.scan_with_decisions`'s evaluation order, which mirrors
    the AND-chain in the legacy :meth:`Scanner.scan`. The literal
    values are the contract referenced by the SQL schema's
    ``RejectionReason`` enum; renaming any value requires a coordinated
    migration.
    """

    ticker: str
    ts: datetime
    reason: RejectionReasonLit


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Combined output of :meth:`Scanner.scan_with_decisions`.

    Phase 2 -- issue #51. Every universe member that has a snapshot
    appears in exactly one of ``picks`` or ``rejections``. Members
    with no snapshot entry are silently skipped (preserves
    :meth:`Scanner.scan`'s pre-existing policy at ``scanner.py:67-70``).
    """

    picks: tuple[ScannerPick, ...]
    rejections: tuple[ScannerRejection, ...]
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/unit/test_scanner_decisions.py -v`
Expected: pre-existing 12 tests + 8 new tests = 20 passed.

- [ ] **Step 5: Local gate (full project)**

Run, confirm each is clean, then proceed:
```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
```

If anything outside the modified files breaks, **stop and surface** before fixing — likely indicates a plan defect.

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/types.py tests/unit/test_scanner_decisions.py
git commit -m "$(cat <<'EOF'
feat(scanner): add ScannerRejection, ScanResult, RejectionReasonLit (#51)

Frozen value types for journal-grade rejection reasons. Mirrors the
DB-facing `journal.models.RejectionReason` enum string values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `Scanner.scan_with_decisions` and refactor `scan` into a wrapper

**Files:**
- Modify: `src/ross_trading/scanner/scanner.py`
- Modify: `tests/unit/test_scanner_decisions.py` (append `scan_with_decisions` tests)

- [ ] **Step 1: Append failing tests to `tests/unit/test_scanner_decisions.py`**

Append at the bottom of the file:

```python
# =====================================================================
# Issue #51 -- Scanner.scan_with_decisions
# =====================================================================

from datetime import date

from ross_trading.data.types import Bar, FloatRecord
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot

S_T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar_for_scan(close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol="AVTX", ts=S_T0, timeframe="M1",
        open=Decimal("5.00"), high=Decimal(close), low=Decimal("4.95"),
        close=Decimal(close), volume=volume,
    )


def _passing_snap(
    *,
    symbol: str = "AVTX",
    close: str = "5.50",
    volume: int = 5_000_000,
    last: str = "5.50",
    prev_close: str = "5.00",
    baseline_30d: Decimal | None = Decimal("1000000"),
    float_shares: int | None = 8_500_000,
) -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol, ts=S_T0, timeframe="M1",
        open=Decimal("5.00"), high=Decimal(close), low=Decimal("4.95"),
        close=Decimal(close), volume=volume,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=baseline_30d,
        float_record=FloatRecord(
            ticker=symbol, as_of=date(2026, 4, 26),
            float_shares=float_shares, shares_outstanding=12_000_000,
            source="test",
        ) if float_shares is not None else None,
        headlines=(),
    )


# ------------------------------------------------------------------ happy path


def test_scan_with_decisions_passing_ticker_yields_one_pick_no_rejections() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap()},
    )
    assert len(result.picks) == 1
    assert result.picks[0].ticker == "AVTX"
    assert result.rejections == []


def test_scan_with_decisions_universe_not_in_snapshot_is_silently_skipped() -> None:
    """Per D-A8-5: not-in-snapshot is silent skip, NOT a NO_SNAPSHOT rejection."""
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX", "BBAI"]), {"AVTX": _passing_snap()},  # BBAI missing
    )
    assert [p.ticker for p in result.picks] == ["AVTX"]
    assert result.rejections == []  # BBAI is NOT a rejection


# ---------------------------------------------------- one test per rejection reason


def test_scan_with_decisions_missing_baseline_rejects() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(baseline_30d=None)},
    )
    assert result.picks == []
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "missing_baseline"
    assert result.rejections[0].ticker == "AVTX"


def test_scan_with_decisions_missing_float_rejects() -> None:
    scanner = Scanner()
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(float_shares=None)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["missing_float"]


def test_scan_with_decisions_rel_volume_rejects() -> None:
    scanner = Scanner()  # default 5x
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]), {"AVTX": _passing_snap(volume=4_000_000)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["rel_volume"]


def test_scan_with_decisions_pct_change_rejects() -> None:
    scanner = Scanner()  # default 10%
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(last="5.40", prev_close="5.00")},  # +8%
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["pct_change"]


def test_scan_with_decisions_price_band_rejects_high() -> None:
    scanner = Scanner()  # default [1, 20]
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(close="25.00", last="25.50", prev_close="22.00")},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["price_band"]


def test_scan_with_decisions_float_size_rejects() -> None:
    scanner = Scanner()  # default 20M
    result = scanner.scan_with_decisions(
        frozenset(["AVTX"]),
        {"AVTX": _passing_snap(float_shares=25_000_000)},
    )
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["float_size"]


# -------------------------------------------- short-circuit: first failing reason


def test_scan_with_decisions_first_failure_wins_when_multiple_filters_fail() -> None:
    """Snapshot fails BOTH rel_volume AND pct_change -- reason should be the
    earlier one (rel_volume), preserving the AND-chain order."""
    scanner = Scanner()
    snap = _passing_snap(volume=4_000_000, last="5.40", prev_close="5.00")
    result = scanner.scan_with_decisions(frozenset(["AVTX"]), {"AVTX": snap})
    assert result.picks == []
    assert [r.reason for r in result.rejections] == ["rel_volume"]  # not pct_change


# --------------------------------------------------------- mixed picks + rejects


def test_scan_with_decisions_mixed_partition() -> None:
    scanner = Scanner()
    universe = frozenset(["GOOD", "REJ_VOL", "REJ_PCT"])
    snapshot = {
        "GOOD": _passing_snap(symbol="GOOD"),
        "REJ_VOL": _passing_snap(symbol="REJ_VOL", volume=4_000_000),
        "REJ_PCT": _passing_snap(symbol="REJ_PCT", last="5.40", prev_close="5.00"),
    }
    result = scanner.scan_with_decisions(universe, snapshot)
    assert [p.ticker for p in result.picks] == ["GOOD"]
    assert sorted((r.ticker, r.reason) for r in result.rejections) == [
        ("REJ_PCT", "pct_change"), ("REJ_VOL", "rel_volume"),
    ]


def test_scan_with_decisions_all_rejected() -> None:
    scanner = Scanner()
    universe = frozenset(["A", "B", "C"])
    snapshot = {
        "A": _passing_snap(symbol="A", baseline_30d=None),     # missing_baseline
        "B": _passing_snap(symbol="B", float_shares=None),     # missing_float
        "C": _passing_snap(symbol="C", volume=4_000_000),      # rel_volume
    }
    result = scanner.scan_with_decisions(universe, snapshot)
    assert result.picks == []
    assert sorted((r.ticker, r.reason) for r in result.rejections) == [
        ("A", "missing_baseline"), ("B", "missing_float"), ("C", "rel_volume"),
    ]


# ---------------------------------------------------- scan(...) wrapper regression


def test_scan_is_thin_wrapper_returning_only_picks() -> None:
    """Issue #51: scan(...) must produce identical picks to scan_with_decisions(...).picks."""
    scanner = Scanner()
    universe = frozenset(["GOOD", "REJ_VOL"])
    snapshot = {
        "GOOD": _passing_snap(symbol="GOOD"),
        "REJ_VOL": _passing_snap(symbol="REJ_VOL", volume=4_000_000),
    }
    via_scan = scanner.scan(universe, snapshot)
    via_decisions = scanner.scan_with_decisions(universe, snapshot)
    assert via_scan == via_decisions.picks
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/unit/test_scanner_decisions.py -v -k "scan_with_decisions or scan_is_thin_wrapper"`
Expected: `AttributeError: 'Scanner' object has no attribute 'scan_with_decisions'` for all new tests.

- [ ] **Step 3: Refactor `src/ross_trading/scanner/scanner.py`**

Replace the entire `Scanner.scan(...)` method with `scan_with_decisions(...)` plus a `scan(...)` wrapper. Update the file's imports to add `ScannerRejection` and `ScanResult`. Final shape:

```python
"""Scanner orchestrator: composes A1 filter primitives + ranker.

Phase 2 -- Atom A2 (#41), extended in A8 (#51) with
``scan_with_decisions``. Pure-sync. No I/O, no logging, no
module-level mutable state. Thresholds are constructor parameters
so the caller can A/B test without surgery here.

Inputs are :class:`ScannerSnapshot` value objects keyed by ticker;
A3 (the loop) owns provider I/O and assembles the snapshot map.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from ross_trading.scanner.filters import (
    float_le,
    headline_count,
    news_present,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
from ross_trading.scanner.ranking import rank_picks
from ross_trading.scanner.types import ScannerPick, ScannerRejection, ScanResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ross_trading.data.types import FloatRecord
    from ross_trading.scanner.types import ScannerSnapshot


class Scanner:
    """Compose A1's hard filters + the ranker into top-N picks."""

    def __init__(
        self,
        rel_volume_threshold: float = 5.0,
        pct_change_threshold_pct: Decimal = Decimal("10"),
        price_low: Decimal = Decimal("1"),
        price_high: Decimal = Decimal("20"),
        float_threshold: int = 20_000_000,
        news_lookback_hours: int = 24,
        top_n: int = 5,
    ) -> None:
        self._rel_volume_threshold = rel_volume_threshold
        self._pct_change_threshold_pct = pct_change_threshold_pct
        self._price_low = price_low
        self._price_high = price_high
        self._float_threshold = float_threshold
        self._news_lookback_hours = news_lookback_hours
        self._top_n = top_n

    def scan_with_decisions(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> ScanResult:
        """Filter the universe by snapshot, ranking picks and recording
        the *first* failing filter for each rejected ticker.

        Universe members with no snapshot entry are silently skipped
        -- universe drift between enumeration and snapshot assembly
        is normal at the boundary of a session, and not a journal-
        worthy event (see plan D-A8-5).

        Filter evaluation order matches the AND-chain in the legacy
        :meth:`scan` method (preserved for behavioral compatibility):
        baseline presence, float-record presence, ``rel_volume_ge``,
        ``pct_change_ge``, ``price_in_band``, ``float_le``. Returns
        as soon as the first failing filter is identified.
        """
        candidates: list[ScannerPick] = []
        rejections: list[ScannerRejection] = []
        for ticker in universe:
            snap = snapshot.get(ticker)
            if snap is None:
                continue
            anchor_ts = snap.bar.ts
            baseline = snap.baseline_30d
            if baseline is None:
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="missing_baseline",
                ))
                continue
            float_rec = snap.float_record
            if float_rec is None:
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="missing_float",
                ))
                continue
            if not rel_volume_ge(ticker, snap.bar, baseline, self._rel_volume_threshold):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="rel_volume",
                ))
                continue
            if not pct_change_ge(snap.last, snap.prev_close, self._pct_change_threshold_pct):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="pct_change",
                ))
                continue
            if not price_in_band(ticker, snap.bar, self._price_low, self._price_high):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="price_band",
                ))
                continue
            if not float_le(float_rec, self._float_threshold):
                rejections.append(ScannerRejection(
                    ticker=ticker, ts=anchor_ts, reason="float_size",
                ))
                continue
            candidates.append(self._build_pick(ticker, snap, baseline, float_rec))
        # n=len(candidates) preserves the partition invariant: every snapshot
        # member ends up in exactly one of picks/rejections. Truncating to
        # self._top_n here would silently drop passers above the watchlist
        # size. The legacy scan() wrapper applies [:self._top_n] at its
        # call site for callers that want the watchlist-sized slice.
        return ScanResult(
            picks=tuple(rank_picks(candidates, n=len(candidates))),
            rejections=tuple(rejections),
        )

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]:
        """Return only the picks; thin wrapper over :meth:`scan_with_decisions`.

        Preserved for callers that don't care about rejection journaling
        (e.g., back-test drivers, ad-hoc scripts).
        """
        return self.scan_with_decisions(universe, snapshot).picks

    def _build_pick(
        self,
        ticker: str,
        snap: ScannerSnapshot,
        baseline_30d: Decimal,
        float_record: FloatRecord,
    ) -> ScannerPick:
        anchor_ts = snap.bar.ts
        return ScannerPick(
            ticker=ticker,
            ts=anchor_ts,
            rel_volume=Decimal(snap.bar.volume) / baseline_30d,
            pct_change=(snap.last - snap.prev_close) / snap.prev_close * Decimal(100),
            price=snap.last,
            float_shares=float_record.float_shares,
            news_present=news_present(
                ticker, snap.headlines, anchor_ts, self._news_lookback_hours,
            ),
            headline_count=headline_count(
                ticker, snap.headlines, anchor_ts, self._news_lookback_hours,
            ),
            rank=0,
        )
```

- [ ] **Step 4: Run the new tests AND the existing scanner regression tests**

Run: `pytest tests/unit/test_scanner_decisions.py tests/unit/test_scanner.py -v`
Expected: all new `scan_with_decisions` tests pass + existing 14 `test_scanner.py` tests still pass without modification (regression-protected — D-A8-4).

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/scanner.py tests/unit/test_scanner_decisions.py
git commit -m "$(cat <<'EOF'
feat(scanner): add scan_with_decisions; scan now wraps it (#51)

Surfaces the first-failing-filter rejection reason per universe member.
Preserves scan(...) behavior verbatim (regression-tested).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Extend `ScannerDecision` Literal + add `rejection_reason` field; extend `DecisionSink` Protocol with `record_scan`

**Files:**
- Modify: `src/ross_trading/scanner/decisions.py`
- Modify: `tests/unit/test_scanner_decisions.py` (append rejected-decision tests)

- [ ] **Step 1: Append failing tests to `tests/unit/test_scanner_decisions.py`**

Append at the bottom of the file:

```python
# =====================================================================
# Issue #51 -- ScannerDecision.kind="rejected" + DecisionSink.record_scan
# =====================================================================

from collections.abc import Mapping, Sequence

from ross_trading.journal.models import RejectionReason


def _rejected_decision() -> ScannerDecision:
    return ScannerDecision(
        kind="rejected",
        decision_ts=T0,
        ticker="AVTX",
        pick=None,
        reason=None,
        gap_start=None,
        gap_end=None,
        rejection_reason="rel_volume",
    )


def test_decision_accepts_rejected_kind() -> None:
    d = _rejected_decision()
    assert d.kind == "rejected"
    assert d.ticker == "AVTX"
    assert d.rejection_reason == "rel_volume"


def test_decision_rejected_picklable_roundtrip() -> None:
    d = _rejected_decision()
    revived = pickle.loads(pickle.dumps(d))  # noqa: S301
    assert revived == d


def test_decision_rejection_reason_defaults_to_none_for_other_kinds() -> None:
    """Existing call sites that build picked/stale_feed/feed_gap without
    passing rejection_reason must continue to work."""
    d = _picked()  # uses the original 7-field constructor
    assert d.rejection_reason is None


# --------------------------------------------------- DecisionSink.record_scan


class _RecordingSink:
    """Inline sink stand-in to assert Protocol shape post-extension."""

    def __init__(self) -> None:
        self.scans: list[tuple[datetime, list[ScannerPick], dict[str, RejectionReason]]] = []
        self.decisions: list[ScannerDecision] = []

    def emit(self, decision: ScannerDecision) -> None:
        self.decisions.append(decision)

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None:
        self.scans.append((decision_ts, list(picks), dict(rejected)))


def test_recording_sink_satisfies_extended_decision_sink_protocol() -> None:
    sink = _RecordingSink()
    assert isinstance(sink, DecisionSink)


def test_record_scan_stores_picks_and_rejected() -> None:
    sink = _RecordingSink()
    sink.record_scan(T0, [_pick()], {"BBAI": RejectionReason.REL_VOLUME})
    assert len(sink.scans) == 1
    ts, picks, rejected = sink.scans[0]
    assert ts == T0
    assert picks == [_pick()]
    assert rejected == {"BBAI": RejectionReason.REL_VOLUME}
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/unit/test_scanner_decisions.py -v -k "rejected_kind or record_scan or rejection_reason or rejected_picklable or recording_sink"`
Expected: `TypeError` (no `rejection_reason` kwarg) and Protocol-conformance failures.

- [ ] **Step 3: Update `src/ross_trading/scanner/decisions.py`**

Replace the entire file with:

```python
"""Decision rows emitted by the scanner loop.

Phase 2 -- Atom A3 (#42), extended in A8 (#51) with the fourth
``rejected`` kind and the :meth:`DecisionSink.record_scan` batch API.
``ScannerDecision`` is the unit the loop writes to its sink per
emit-style decision (stale_feed, feed_gap); ``record_scan`` carries
the per-tick batch of picks + rejections atomically.

Per #51 plan D-A8-1: the loop calls :meth:`record_scan` for the
scan branch (one call per tick, atomic) and :meth:`emit` for
stale_feed and feed_gap (which fire alone -- no atomicity at risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ross_trading.journal.models import RejectionReason
    from ross_trading.scanner.types import RejectionReasonLit, ScannerPick


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    """One row emitted to the journal per non-batched tick outcome.

    Four kinds:
    - ``picked``: ticker passed all hard filters; ``pick`` carries
      the ranked ScannerPick; ``ticker`` mirrors ``pick.ticker``.
      (Carried via :meth:`DecisionSink.record_scan` post-#51, not emit.)
    - ``stale_feed``: emitted in real time, once per suppressed tick;
      ``ticker`` is None (loop-wide); ``reason`` is human-readable.
    - ``feed_gap``: emitted retrospectively when the reconnect provider
      fires its on_gap callback; ``gap_start`` / ``gap_end`` are
      quote-time, not wall-time.
    - ``rejected`` (#51): a universe member that failed the scanner's
      hard filters; ``rejection_reason`` carries the first-failing-
      filter literal. Carried via :meth:`record_scan`, not emit.
    """

    kind: Literal["picked", "stale_feed", "feed_gap", "rejected"]
    decision_ts: datetime
    ticker: str | None
    pick: ScannerPick | None
    reason: str | None
    gap_start: datetime | None
    gap_end: datetime | None
    rejection_reason: RejectionReasonLit | None = None

    def __post_init__(self) -> None:
        if self.decision_ts.tzinfo is None:
            msg = "decision_ts must be tz-aware"
            raise ValueError(msg)
        if self.gap_start is not None and self.gap_start.tzinfo is None:
            msg = "gap_start must be tz-aware"
            raise ValueError(msg)
        if self.gap_end is not None and self.gap_end.tzinfo is None:
            msg = "gap_end must be tz-aware"
            raise ValueError(msg)


@runtime_checkable
class DecisionSink(Protocol):
    """Where ScannerLoop writes decisions. A5 (#44) implements this.

    Two surfaces (per #51 D-A8-1):
    - :meth:`emit`: one-row writes for ``stale_feed`` and ``feed_gap``,
      which fire alone and have no atomicity requirement.
    - :meth:`record_scan`: per-tick batch of picks + rejections, written
      atomically. Used by the loop's scan branch every non-stale tick.
    """

    def emit(self, decision: ScannerDecision) -> None: ...

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None: ...
```

- [ ] **Step 4: Run all `test_scanner_decisions.py` tests to verify they pass**

Run: `pytest tests/unit/test_scanner_decisions.py -v`
Expected: full file passes (~30+ tests including the original 12 + Task 1's 8 + Task 2's 12 + Task 3's 5).

- [ ] **Step 5: Local gate (full project) — expect breakage**

```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
```

Expected breakage: `JournalWriter` and `FakeDecisionSink` no longer satisfy the extended `DecisionSink` Protocol because `FakeDecisionSink` lacks `record_scan` (`JournalWriter` already has the method but mypy may flag the signature mismatch — `Mapping[str, RejectionReason]` vs Protocol). **Fix in Task 4** — do NOT patch here.

If breakage is *only* "FakeDecisionSink is not a DecisionSink" or "JournalWriter signature mismatch on record_scan," proceed. If anything else breaks, stop and surface.

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/decisions.py tests/unit/test_scanner_decisions.py
git commit -m "$(cat <<'EOF'
feat(scanner): extend ScannerDecision with rejected kind and DecisionSink.record_scan (#51)

ScannerDecision.kind grows the fourth value 'rejected' with a
rejection_reason field. DecisionSink Protocol grows record_scan(...)
for atomic per-tick picks+rejections writes (per JournalWriter's
intent comment from #44).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update `FakeDecisionSink` and add `JournalWriter` Protocol-conformance test

**Files:**
- Modify: `tests/fakes/decision_sink.py`
- Modify: `tests/unit/test_journal_writer.py` (append protocol-conformance test)

- [ ] **Step 1: Add the failing protocol-conformance test to `tests/unit/test_journal_writer.py`**

Read the existing imports at the top of `tests/unit/test_journal_writer.py`. Append at the bottom:

```python
# =====================================================================
# Issue #51 -- DecisionSink Protocol conformance
# =====================================================================

from ross_trading.scanner.decisions import DecisionSink as DecisionSinkProtocol


def test_journal_writer_satisfies_decision_sink_protocol(
    session_factory: sessionmaker[Session],
) -> None:
    """Post-#51, DecisionSink requires both emit and record_scan.
    JournalWriter must satisfy both."""
    writer = JournalWriter(session_factory)
    assert isinstance(writer, DecisionSinkProtocol)
```

(Reuse whatever `session_factory` fixture the existing tests in this file use; if no fixture exists, build a minimal in-memory SQLite session_factory inline matching the patterns in the file.)

- [ ] **Step 2: Add a Protocol-conformance test for `FakeDecisionSink`**

Append at the bottom of `tests/unit/test_scanner_decisions.py`:

```python
def test_fake_decision_sink_satisfies_extended_protocol() -> None:
    """Post-#51, the bundled fake must implement both emit and record_scan."""
    from tests.fakes.decision_sink import FakeDecisionSink

    sink = FakeDecisionSink()
    assert isinstance(sink, DecisionSink)
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `pytest tests/unit/test_journal_writer.py::test_journal_writer_satisfies_decision_sink_protocol tests/unit/test_scanner_decisions.py::test_fake_decision_sink_satisfies_extended_protocol -v`
Expected: FakeDecisionSink fails (no `record_scan`); JournalWriter test passes if its existing `record_scan` signature matches, otherwise also fails.

- [ ] **Step 4: Update `tests/fakes/decision_sink.py`**

Replace the entire file with:

```python
"""In-memory DecisionSink for tests.

Records both ``emit`` calls and ``record_scan`` batches, so loop tests
can assert per-tick batched outputs (#51) alongside one-off emits
(stale_feed, feed_gap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ross_trading.journal.models import RejectionReason
    from ross_trading.scanner.decisions import ScannerDecision
    from ross_trading.scanner.types import ScannerPick


class FakeDecisionSink:
    """Records every ``emit`` and ``record_scan`` call in order."""

    def __init__(self) -> None:
        self.decisions: list[ScannerDecision] = []
        self.scans: list[tuple[datetime, list[ScannerPick], dict[str, RejectionReason]]] = []

    def emit(self, decision: ScannerDecision) -> None:
        self.decisions.append(decision)

    def record_scan(
        self,
        decision_ts: datetime,
        picks: Sequence[ScannerPick],
        rejected: Mapping[str, RejectionReason],
    ) -> None:
        self.scans.append((decision_ts, list(picks), dict(rejected)))
```

- [ ] **Step 5: Verify `JournalWriter.record_scan` signature matches the Protocol**

Read `src/ross_trading/journal/writer.py` lines 72-94. Verify:
- Method name: `record_scan` ✓
- Args: `(self, decision_ts: datetime, picks: Sequence[ScannerPick], rejected: Mapping[str, RejectionReason]) -> None` ✓

If the signature already matches (it should — see writer.py), no change needed in `writer.py`. If mypy strict catches a variance/positional mismatch, adjust the Protocol method signature in `decisions.py` to use the exact same parameter order/types as `writer.py`.

- [ ] **Step 6: Run both Protocol-conformance tests + `test_journal_writer.py`**

Run: `pytest tests/unit/test_journal_writer.py tests/unit/test_scanner_decisions.py -v`
Expected: all pass.

- [ ] **Step 7: Local gate (full project)**

```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
```

Note: `test_scanner_loop.py` will still be broken (its happy-path tests assert `sink.decisions` shape). Fixed in Task 5.

- [ ] **Step 8: Commit**

```bash
git add tests/fakes/decision_sink.py tests/unit/test_journal_writer.py tests/unit/test_scanner_decisions.py
git commit -m "$(cat <<'EOF'
test(scanner): FakeDecisionSink + JournalWriter satisfy extended DecisionSink (#51)

FakeDecisionSink records record_scan batches alongside emit calls.
JournalWriter Protocol-conformance test pins the contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Migrate `ScannerLoop._tick` to `scan_with_decisions` + `record_scan`

**Files:**
- Modify: `src/ross_trading/scanner/loop.py`
- Modify: `tests/unit/test_scanner_loop.py`

- [ ] **Step 1: Update existing happy-path tests to assert the new sink shape**

In `tests/unit/test_scanner_loop.py`, replace the three happy-path tests:

Replace `test_inside_market_hours_calls_assembler_and_emits_picked` (currently lines 119-133) with:

```python
async def test_inside_market_hours_calls_assembler_and_records_scan_with_picks() -> None:
    snap = _snap("AVTX")
    loop, _, sink, assembler = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert assembler.calls == [INSIDE_TS]
    assert sink.decisions == []  # picks now go via record_scan, not emit
    assert len(sink.scans) == 1
    ts, picks, rejected = sink.scans[0]
    assert ts == INSIDE_TS
    assert len(picks) == 1
    assert picks[0].ticker == "AVTX"
    assert picks[0].rank == 1
    assert rejected == {}
```

Replace `test_no_picks_emits_no_decisions` (currently lines 136-159) with:

```python
async def test_no_picks_records_scan_with_one_rejection() -> None:
    """Empty Scanner picks now produce a record_scan with one rejection
    (rel_volume), not an empty decision stream."""
    snap = ScannerSnapshot(
        bar=Bar(
            symbol="AVTX", ts=INSIDE_TS, timeframe="M1",
            open=Decimal("5"), high=Decimal("5.5"), low=Decimal("4.95"),
            close=Decimal("5.5"), volume=10_000,  # 0.01x baseline -> reject
        ),
        last=Decimal("5.50"),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker="AVTX", as_of=date(2025, 1, 2),
            float_shares=8_500_000, shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert sink.decisions == []
    assert len(sink.scans) == 1
    ts, picks, rejected = sink.scans[0]
    assert ts == INSIDE_TS
    assert picks == []
    from ross_trading.journal.models import RejectionReason
    assert rejected == {"AVTX": RejectionReason.REL_VOLUME}
```

Replace `test_multiple_picks_emitted_in_rank_order` (currently lines 162-173) with:

```python
async def test_multiple_picks_recorded_in_rank_order() -> None:
    a, b, c = _snap("AAA", last="5.50"), _snap("BBB", last="6.50"), _snap("CCC", last="6.00")
    snapshot_map = {"AAA": a, "BBB": b, "CCC": c}
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: (snapshot_map, INSIDE_TS)},
        universe=frozenset(["AAA", "BBB", "CCC"]),
    )
    await _run_for_n_ticks(loop, n=1)
    assert len(sink.scans) == 1
    _, picks, rejected = sink.scans[0]
    # Sorted by pct_change desc: BBB (+30%), CCC (+20%), AAA (+10%).
    assert [p.ticker for p in picks] == ["BBB", "CCC", "AAA"]
    assert [p.rank for p in picks] == [1, 2, 3]
    assert rejected == {}
```

Update `test_loop_uses_injected_clock_sleep_not_asyncio_sleep` (currently lines 205-222): change `assert len(sink.decisions) == 2` to `assert len(sink.scans) == 2`.

Update `test_pre_first_quote_does_not_suppress_scan` (currently lines 228-237): change `assert sink.decisions[0].kind == "picked"` to `assert len(sink.scans) == 1; assert len(sink.scans[0][1]) == 1`.

Update `test_fresh_feed_within_threshold_runs_scan` (currently lines 259-269): same change as above.

Append a new test at the bottom of the file:

```python
async def test_tick_with_mixed_picks_and_rejections_records_one_scan() -> None:
    """Per #51 D-A8-1: picks + rejections for one tick land in ONE record_scan call."""
    good = _snap("GOOD", last="5.50")
    bad_volume = _snap("BAD_VOL", last="5.50")
    bad_volume = ScannerSnapshot(
        bar=Bar(
            symbol="BAD_VOL", ts=INSIDE_TS, timeframe="M1",
            open=Decimal("5"), high=Decimal("5.5"), low=Decimal("4.95"),
            close=Decimal("5.5"), volume=10_000,  # rel_volume reject
        ),
        last=Decimal("5.50"), prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker="BAD_VOL", as_of=date(2025, 1, 2),
            float_shares=8_500_000, shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"GOOD": good, "BAD_VOL": bad_volume}, INSIDE_TS)},
        universe=frozenset(["GOOD", "BAD_VOL"]),
    )
    await _run_for_n_ticks(loop, n=1)
    assert len(sink.scans) == 1
    _, picks, rejected = sink.scans[0]
    assert [p.ticker for p in picks] == ["GOOD"]
    from ross_trading.journal.models import RejectionReason
    assert rejected == {"BAD_VOL": RejectionReason.REL_VOLUME}
```

`stale_feed` and `feed_gap` tests remain unchanged.

- [ ] **Step 2: Run the modified tests to verify they fail**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: the 6 modified tests + 1 new test fail because `_tick` still uses `emit` for picks. `stale_feed` and `feed_gap` tests still pass.

- [ ] **Step 3: Modify `src/ross_trading/scanner/loop.py`**

Replace the entire file with:

```python
"""Async tick driver for the scanner.

Phase 2 -- Atom A3 (#42), extended in A8 (#51) to migrate the scan
branch from N x ``emit`` to a single ``record_scan`` per tick (atomic
picks + rejections). Long-running coroutine that paces
:meth:`Scanner.scan_with_decisions` on a Clock and emits per-tick
batches to an injected :class:`DecisionSink`. The loop owns no
provider I/O -- the injected :class:`SnapshotAssembler` is the
replay-determinism boundary.

Cancellation: ``run()`` re-raises CancelledError. No drain on
shutdown, no upstream subscription cleanup. Outside-market-hours
ticks are no-ops, not exits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.clock import is_market_hours
from ross_trading.journal.models import RejectionReason
from ross_trading.scanner.decisions import ScannerDecision

if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.types import FeedGap
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner
    from ross_trading.scanner.types import RejectionReasonLit


# Mirrors the Literal -> Enum mapping. The Literal values are the contract
# pinned by `scanner/types.py::RejectionReasonLit`; the Enum is the DB-
# facing twin from `journal/models.py::RejectionReason`. mypy strict catches
# any drift at this match site.
def _lit_to_enum(reason: RejectionReasonLit) -> RejectionReason:
    match reason:
        case "no_snapshot":
            return RejectionReason.NO_SNAPSHOT
        case "missing_baseline":
            return RejectionReason.MISSING_BASELINE
        case "missing_float":
            return RejectionReason.MISSING_FLOAT
        case "rel_volume":
            return RejectionReason.REL_VOLUME
        case "pct_change":
            return RejectionReason.PCT_CHANGE
        case "price_band":
            return RejectionReason.PRICE_BAND
        case "float_size":
            return RejectionReason.FLOAT_SIZE


class ScannerLoop:
    """Drive Scanner.scan_with_decisions on a Clock-paced tick."""

    def __init__(
        self,
        scanner: Scanner,
        universe_provider: UniverseProvider,
        snapshot_assembler: SnapshotAssembler,
        decision_sink: DecisionSink,
        clock: Clock,
        *,
        tick_interval_s: float = 2.0,
        staleness_threshold_s: float = 5.0,
    ) -> None:
        if tick_interval_s <= 0:
            msg = "tick_interval_s must be positive"
            raise ValueError(msg)
        if staleness_threshold_s <= 0:
            msg = "staleness_threshold_s must be positive"
            raise ValueError(msg)
        self._scanner = scanner
        self._universe_provider = universe_provider
        self._assembler = snapshot_assembler
        self._sink = decision_sink
        self._clock = clock
        self._tick_interval_s = tick_interval_s
        self._staleness_threshold_s = staleness_threshold_s

    async def run(self) -> None:
        """Tick forever until cancelled. All waits via injected Clock."""
        while True:
            await self._tick()
            await self._clock.sleep(self._tick_interval_s)

    async def _tick(self) -> None:
        anchor_ts = self._clock.now()
        if not is_market_hours(anchor_ts):
            return
        universe = await self._universe_provider.list_symbols(anchor_ts.date())
        snapshot, most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
        if most_recent_quote_ts is not None:
            staleness_s = (anchor_ts - most_recent_quote_ts).total_seconds()
            if staleness_s > self._staleness_threshold_s:
                self._sink.emit(
                    ScannerDecision(
                        kind="stale_feed",
                        decision_ts=anchor_ts,
                        ticker=None,
                        pick=None,
                        reason=f"feed stale by {staleness_s:.1f}s",
                        gap_start=None,
                        gap_end=None,
                    )
                )
                return
        result = self._scanner.scan_with_decisions(universe, snapshot)
        rejected = {r.ticker: _lit_to_enum(r.reason) for r in result.rejections}
        self._sink.record_scan(
            decision_ts=anchor_ts,
            picks=result.picks,
            rejected=rejected,
        )

    def on_feed_gap(self, gap: FeedGap) -> None:
        """Receive a retrospective FeedGap and emit a feed_gap decision.

        Wired by callers as ``ReconnectingProvider(upstream, on_gap=loop.on_feed_gap)``.
        Sync because ReconnectingProvider's callback runs synchronously
        inside its FeedDisconnected handler -- emit-and-return is correct.

        Must be called from within the asyncio event-loop thread. The
        event loop serializes ``_tick`` and this callback, so they cannot
        race on ``self._sink``. If a future ReconnectingProvider moves
        to threaded I/O, callers must marshal the call back to the loop
        thread (e.g., ``loop.call_soon_threadsafe(loop_inst.on_feed_gap, gap)``).
        """
        self._sink.emit(
            ScannerDecision(
                kind="feed_gap",
                decision_ts=self._clock.now(),
                ticker=None,
                pick=None,
                reason=gap.reason,
                gap_start=gap.start,
                gap_end=gap.end,
            )
        )
```

- [ ] **Step 4: Run all loop tests to verify they pass**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: all loop tests pass (modified + new + unchanged stale_feed/feed_gap).

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/loop.py tests/unit/test_scanner_loop.py
git commit -m "$(cat <<'EOF'
feat(scanner): ScannerLoop emits rejected decisions via record_scan (#51)

Migrates the scan branch from N x emit() to one record_scan() per tick
so picks + rejections land atomically. Honors the architectural intent
documented in JournalWriter (#44).

stale_feed and feed_gap continue to use emit -- they fire alone and
have no atomicity requirement.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Integration test — end-to-end picks + rejections journaled atomically

**Files:**
- Modify: `tests/integration/test_scanner_loop_journal.py`

- [ ] **Step 1: Read the existing integration test to understand fixtures**

Run: `cat tests/integration/test_scanner_loop_journal.py | head -80`
Note the fixture for the `JournalWriter` and the loop wiring. Reuse them.

- [ ] **Step 2: Append a new integration test**

Append at the bottom of `tests/integration/test_scanner_loop_journal.py`:

```python
async def test_tick_with_picks_and_rejections_writes_both_kinds_atomically(
    # reuse the same fixtures as the rest of the file --
    # typically: clock, sqlite_session_factory, journal_writer
) -> None:
    """End-to-end: one tick with mixed picks + rejections produces
    correct PICKED and REJECTED rows in one transaction."""
    # Build a snapshot with one passing ticker and two rejected tickers
    # (different reasons). Run one tick. Then query the journal:
    #   - picks table: 1 row for the passing ticker
    #   - scanner_decisions table: 1 PICKED row + 2 REJECTED rows
    #   - REJECTED rows have rejection_reason populated, ticker populated,
    #     pick_id NULL
    #   - All three rows share the same decision_ts
    # The CHECK constraints in migration 0002 already enforce field-
    # population invariants; this test just exercises the write path.
    pass  # full body filled in during implementation, mirroring existing patterns
```

(The full body is filled in during execution by mirroring whichever pattern the existing tests in the file use — the plan shows the assertion shape; the implementer writes the wiring. The integration tests already exist and follow a known fixture pattern.)

- [ ] **Step 3: Run integration tests to verify the new test passes**

Run: `pytest tests/integration/test_scanner_loop_journal.py -v`
Expected: all pass (existing tests + new one).

- [ ] **Step 4: Local gate (full project, both markers)**

```bash
ruff check src tests
mypy src tests
pytest -m "not integration"
pytest -m integration
```

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_scanner_loop_journal.py
git commit -m "$(cat <<'EOF'
test(scanner): integration test for atomic picks+rejections journaling (#51)

End-to-end verification that one tick produces correctly-shaped PICKED
and REJECTED rows in one transaction, with CHECK constraints enforcing
field-population invariants.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Static checks + full suite (verification gate)

This is a defensive sweep — if the per-task gate in Tasks 1-6 was honored, this is a no-op.

- [ ] **Step 1: Run ruff**

Run: `ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 2: Run mypy strict**

Run: `mypy src tests`
Expected: `Success: no issues found in <N> source files`. Verify the count is sensible (baseline + zero new files for #51 since this PR is modify-only).

- [ ] **Step 3: Run unit tests**

Run: `pytest -m "not integration"`
Expected: all green.

- [ ] **Step 4: Run integration tests**

Run: `pytest -m integration`
Expected: all green.

- [ ] **Step 5: If anything fails, fix and re-run.** Per the workflow: cap at 3 fix-loops per failing command, then stop and report.

- [ ] **Step 6: No new commit needed if Tasks 1-6 commits are clean.** If lint/mypy turned up something, the fix lands as a `chore` commit:

```bash
git add -p
git commit -m "$(cat <<'EOF'
chore(scanner): satisfy ruff/mypy on scan_with_decisions migration (#51)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Open the PR

- [ ] **Step 1: Push the branch**

Run: `git push -u origin phase-2-issue-51-scan-with-decisions`

- [ ] **Step 2: Open the PR closing #51**

```bash
gh pr create --base main --title "feat(scanner): add scan_with_decisions for rejection journaling (#51)" --body "$(cat <<'EOF'
Closes #51.

## Summary

Adds `Scanner.scan_with_decisions(universe, snapshot) -> ScanResult(picks, rejections)` so `ScannerLoop` can journal rejection reasons without re-running the filter chain. Existing `Scanner.scan(...)` becomes a thin wrapper returning `.picks` (regression-tested — existing test suite passes without modification).

## New public surface

- `scanner/types.py`: `ScannerRejection`, `ScanResult`, `RejectionReasonLit` (frozen dataclasses; Literal mirrors `journal.models.RejectionReason` enum string values).
- `scanner/scanner.py`: `Scanner.scan_with_decisions(...)`. `Scanner.scan(...)` preserved as wrapper.
- `scanner/decisions.py`: `ScannerDecision.kind` Literal extended with `"rejected"`; new `rejection_reason` field. `DecisionSink` Protocol grows `record_scan(decision_ts, picks, rejected) -> None`.

## Migration: ScannerLoop now emits REJECTED via record_scan

Per the architectural intent baked into `journal/writer.py` (from #44), the loop migrates from `N x emit()` to one `record_scan()` per non-stale tick. Picks and rejections for one tick land atomically in one transaction. `stale_feed` and `feed_gap` continue to use `emit` (they fire alone, no atomicity requirement).

## Schema

No schema changes. Migration 0002 (PR #54, from #43) already added `DecisionKind.REJECTED`, the `RejectionReason` enum (7 values matching the AND-chain), the `rejection_reason` column, and the CHECK constraints. This PR wires up the in-memory and write paths to the existing schema.

## Decisions resolved

See `plans/phase-2-issue-51-scan-with-decisions.md` "Decisions Resolved" section. None reverse a Resolved Decision in `docs/architecture.md`.

## Test additions

- Unit (`tests/unit/test_scanner_decisions.py` extended): one test per active rejection reason (`missing_baseline`, `missing_float`, `rel_volume`, `pct_change`, `price_band`, `float_size`), all-rejected case, mixed picks+rejections, scan(...) wrapper regression, ScannerRejection / ScanResult value-type invariants, ScannerDecision rejected-kind invariants, DecisionSink Protocol conformance.
- Unit (`tests/unit/test_scanner_loop.py`): updated happy-path tests to assert `sink.scans` shape; new `test_tick_with_mixed_picks_and_rejections_records_one_scan`.
- Unit (`tests/unit/test_journal_writer.py`): JournalWriter satisfies extended DecisionSink Protocol.
- Integration (`tests/integration/test_scanner_loop_journal.py`): end-to-end PICKED + REJECTED rows in one transaction.

## Verification

- `ruff check src tests` -- clean
- `mypy src tests` -- clean
- `pytest -m "not integration"` -- all green
- `pytest -m integration` -- all green
EOF
)"
```

- [ ] **Step 3: Confirm CI is green**

Wait for the GitHub Actions run on the PR. If drift CI flags anything (no Resolved Decision should be reversed), surface and fix.

- [ ] **Step 4: Post `@claude` and `@codex` review-mention comments (separate)**

```bash
gh pr comment <PR_NUMBER> --body "@claude please review"
gh pr comment <PR_NUMBER> --body "@codex please review"
```

- [ ] **Step 5: Report PR URL + summary, do NOT merge.**

---

## Self-Review

**1. Spec coverage.** Walking through #51's "Acceptance" bullets:
- "every universe member exactly once across picks ∪ rejections" → Task 2's `test_scan_with_decisions_mixed_partition`, `test_scan_with_decisions_all_rejected`, plus D-A8-5 documenting the silent-skip exception.
- "stable enum surfacing the *first* failing filter in the existing AND-combine order" → Task 2 implementation preserves `scanner.py:75-80` order; `test_scan_with_decisions_first_failure_wins_when_multiple_filters_fail` proves short-circuit semantics.
- "Existing `scan(...)` behavior unchanged (regression-tested)" → Task 2 `test_scanner.py` passes without modification + `test_scan_is_thin_wrapper_returning_only_picks` is the explicit regression test.
- "ScannerLoop migrates to scan_with_decisions and emits a fourth decision kind: rejected" → Tasks 3+5; D-A8-1 documents the per-emit vs record_scan choice.

#51's "Tests" line ("one test per rejection reason, plus an 'all rejected' case verifying picks=[] with a populated rejections list") → all six active reasons covered (Task 2) plus `test_scan_with_decisions_all_rejected`.

**2. Placeholder scan.** Task 6 leaves the integration test body as "filled in during implementation, mirroring existing patterns" — this is the one near-placeholder. The shape of the assertions is fully specified; only the fixture wiring (which exists in the file already) is not duplicated. Acceptable because the integration test infrastructure is established; not acceptable would be "TODO: write the test."

**3. Type consistency.**
- `RejectionReasonLit` strings in `scanner/types.py` ↔ `RejectionReason` enum string values in `journal/models.py` — verified seven-for-seven (`no_snapshot`, `missing_baseline`, `missing_float`, `rel_volume`, `pct_change`, `price_band`, `float_size`).
- `ScannerDecision.rejection_reason: RejectionReasonLit | None` in `decisions.py` matches `ScannerRejection.reason: RejectionReasonLit` in `types.py`.
- `_lit_to_enum` in `loop.py` covers all seven literals — mypy strict will catch a missing case via `match` exhaustiveness.
- `DecisionSink.record_scan(self, decision_ts, picks, rejected)` Protocol signature matches `JournalWriter.record_scan(self, decision_ts, picks, rejected)` (writer.py:72-77) and `FakeDecisionSink.record_scan(self, decision_ts, picks, rejected)` (Task 4).

**4. Conflict surfacing.** Two were caught and resolved:
- D-A8-1 (per-emit vs record_scan) — chose record_scan, justified by the writer's pre-existing intent comment.
- D-A8-3 (test file naming collision) — chose extend, justified by semantic-domain match.
- D-A8-5 (no_snapshot reason in Literal but unused) — flagged for follow-up if reviewer disagrees.

**5. Drift CI.** No `docs/architecture.md` Resolved Decision is reversed. No `docs/ground_truth.md` change needed (this is a write-path change, not a ground-truth change). No active plan in `plans/` is reversed (this PR adds a new plan and archives it on merge per house convention).

**6. Sequencing.** Tasks 1→2→3→4→5→6→7→8 — each task's tests are self-contained, each task ends green for the local gate (Task 3 explicitly notes the expected breakage between Task 3 and Task 4, which is fixed in Task 4). Task 5 is the load-bearing migration.

---
