> Status: merged in PR #50 (commit 41e7bfe)

# Phase 2 — A2: Scanner Core and Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The harness owner has asked to be paused after each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose A1's six filter primitives into a deterministic, sync `Scanner.scan()` that returns the top-N momentum picks, plus a daily-cached `UniverseProvider` for the day's NMS-listed common-stock list.

**Architecture:** Two new package surfaces. (1) `scanner/` grows three modules — `types.py` (`ScannerPick`, `ScannerSnapshot` value objects), `ranking.py` (`rank_picks` pure function), `scanner.py` (`Scanner` orchestrator that composes A1 filters + ranker). The orchestrator is **pure sync, no I/O** — it consumes a pre-assembled `Mapping[str, ScannerSnapshot]` so live and replay produce identical answers. (2) `data/universe.py` adds the async `UniverseProvider` Protocol and the daily-TTL `CachedUniverseProvider`, modeled on the existing `FloatReferenceProvider` / `CachedFloatReference` pattern.

**Tech Stack:** Python 3.11, `decimal.Decimal` arithmetic, `datetime` UTC timestamps, `dataclasses.replace` for rank assignment on frozen picks, mypy `--strict`, ruff (`["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`), pytest with `asyncio_mode = "auto"`.

**Issue:** [#41](https://github.com/seanyofthedead/Ross-trading/issues/41) — tracked under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Depends on:** A1 (#40) — filter primitives. Merged in PR #48 at `61eb4a1`.

**Decisions resolved:**
- [#35](https://github.com/seanyofthedead/Ross-trading/issues/35) (D1: universe source) — daily NMS enumeration with vendor gainers as optional pre-filter inside the concrete provider; `CachedUniverseProvider` wraps with daily TTL.
- [#39](https://github.com/seanyofthedead/Ross-trading/issues/39) (D5: catalyst treatment) — `news_present` + `headline_count` recorded on the pick, not gating. Already honored by A1's filter implementations; A2 just needs to record without AND-combining.

---

## Acceptance Criteria (from issue #41)

- [ ] Deterministic output given a fixed snapshot.
- [ ] `ScannerPick` is frozen, slots-enabled, picklable.
- [ ] No DB or network access in this layer (the `Scanner` itself; `UniverseProvider` is async by interface but the scanner does not call it).
- [ ] `UniverseProvider` is a `@runtime_checkable Protocol`, mockable via `tests/fakes/`.
- [ ] `CachedUniverseProvider` exposes the same protocol surface as the underlying provider.
- [ ] `mypy --strict` passes.
- [ ] All A1 + Phase-1 tests still pass (no regression).

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/scanner/types.py` | `ScannerPick`, `ScannerSnapshot` frozen dataclasses. |
| Create | `src/ross_trading/scanner/ranking.py` | `rank_picks(candidates, n=5) -> list[ScannerPick]` pure function. |
| Create | `src/ross_trading/scanner/scanner.py` | `Scanner` orchestrator composing A1 filters + ranker. |
| Create | `src/ross_trading/data/universe.py` | `UniverseProvider` Protocol + `CachedUniverseProvider`. |
| Create | `tests/fakes/universe.py` | `FakeUniverseProvider` for `tests/unit/test_universe.py`. |
| Create | `tests/unit/test_scanner_types.py` | Frozen / slots / picklable / default-rank invariants. |
| Create | `tests/unit/test_scanner_ranking.py` | Sort / tie-break / top-N / rank-assignment behavior. |
| Create | `tests/unit/test_scanner.py` | Orchestrator integration: filters AND-combined, news non-gating, deterministic. |
| Create | `tests/unit/test_universe.py` | Protocol conformance + cache TTL. |

No modifications to existing modules. No dependency changes in `pyproject.toml`. The `scanner/__init__.py` package marker already exists from A1.

## Key Interfaces

All input types live in `src/ross_trading/data/types.py` (`Bar`, `FloatRecord`, `Headline`). A1's filter primitives live in `src/ross_trading/scanner/filters.py`.

```python
# src/ross_trading/scanner/types.py — public surface

@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    """Per-symbol bag of inputs the scanner needs to evaluate filters."""
    bar: Bar
    last: Decimal
    prev_close: Decimal
    baseline_30d: Decimal | None
    float_record: FloatRecord | None
    headlines: Sequence[Headline]


@dataclass(frozen=True, slots=True)
class ScannerPick:
    """A symbol that passed the scanner's hard filters."""
    ticker: str
    ts: datetime
    rel_volume: Decimal
    pct_change: Decimal
    price: Decimal
    float_shares: int
    news_present: bool
    headline_count: int
    rank: int = 0  # 0 ⇒ pre-rank; rank_picks assigns 1..N


# src/ross_trading/scanner/ranking.py
def rank_picks(
    candidates: Sequence[ScannerPick],
    n: int = 5,
) -> list[ScannerPick]: ...


# src/ross_trading/scanner/scanner.py
class Scanner:
    def __init__(
        self,
        rel_volume_threshold: float = 5.0,
        pct_change_threshold_pct: Decimal = Decimal("10"),
        price_low: Decimal = Decimal("1"),
        price_high: Decimal = Decimal("20"),
        float_threshold: int = 20_000_000,
        news_lookback_hours: int = 24,
        top_n: int = 5,
    ) -> None: ...

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]: ...


# src/ross_trading/data/universe.py
@runtime_checkable
class UniverseProvider(Protocol):
    async def list_symbols(self, as_of: date) -> frozenset[str]: ...


class CachedUniverseProvider:
    def __init__(
        self,
        upstream: UniverseProvider,
        clock: Clock | None = None,
        ttl: timedelta = DEFAULT_CACHE_TTL,  # 24h
    ) -> None: ...

    async def list_symbols(self, as_of: date) -> frozenset[str]: ...
```

**Snapshot semantics:** `ScannerSnapshot` is the value-object answer to the spec's ambiguity (see Defects). It carries everything one-symbol filtering needs:
- `bar` — last completed bar; provides `volume` (rel-vol input), `close` (price-band input), `ts` (anchor for news lookback).
- `last` — latest quote price; `pct_change` reference vs `prev_close`; surfaced as `ScannerPick.price`.
- `prev_close` — previous session's close; `pct_change` reference.
- `baseline_30d` — 30-day average daily volume; `None` ⇒ insufficient history ⇒ reject.
- `float_record` — daily float; `None` ⇒ no data ⇒ reject.
- `headlines` — ticker-relevant headlines for the news soft signals.

**Pick construction order:** `Scanner.scan()` returns ranked picks in one call. Internally:
1. For each `ticker in universe`, look up the snapshot. Skip if missing.
2. Reject if `baseline_30d is None` or `float_record is None` (mypy-narrows for the build step).
3. AND-combine the four hard filters: `rel_volume_ge`, `pct_change_ge`, `price_in_band`, `float_le`. Reject on any False.
4. Build a `ScannerPick` with `rank=0`, recording `news_present` and `headline_count` (non-gating).
5. Pass the candidate list through `rank_picks(candidates, n=top_n)`, which uses `dataclasses.replace` to assign final `rank=1..N`.

**Ranker semantics:** Sort by `(-pct_change, ticker)` so primary is %-change descending, tie-break is ticker ascending. Stable. Returns first N (`n<=0` ⇒ empty list).

**Cache semantics:** `CachedUniverseProvider` mirrors `CachedFloatReference` (`src/ross_trading/data/float_reference.py:34`). 24-hour default TTL. Keyed on `as_of: date`. Constructor injects `Clock` for test determinism (matches `VirtualClock` use in `test_float_reference.py`).

**Missing-data conventions** (reusing A1's "absence of evidence is not promotion" rule):
- Universe member with no snapshot entry → silently skipped (universe drift between enumeration and snapshot assembly is normal).
- Snapshot with `baseline_30d=None` → reject before evaluating filters.
- Snapshot with `float_record=None` → reject before evaluating filters.
- Snapshot with empty `headlines` → `news_present=False`, `headline_count=0` (still produces a pick if hard filters pass, since news is non-gating).

## Defects / Open Questions

These three planning decisions diverge from the literal text of #41 because the spec leaves them under-specified. Each is named here so the executor doesn't paper them over; each gets a one-line proposed answer and a follow-up commitment to file a single bundled spec-fix issue against #41 once A2 ships (matching A1's pattern with #40).

**D-A2-1 — `Scanner.scan(universe, snapshot)` shape.** #41 lists the signature as `(universe, snapshot) -> list[ScannerPick]` without specifying what `snapshot` is for a multi-symbol universe. A momentum scanner needs per-symbol bars + per-symbol prev_close + per-symbol baseline + per-symbol float + per-symbol headlines. **Proposed answer:** introduce a `ScannerSnapshot` value object (one per symbol) and make `snapshot: Mapping[str, ScannerSnapshot]`. This keeps `Scanner.scan` pure-sync, deterministic, and testable with synthetic dicts — no fakes needed for the orchestrator itself.

**D-A2-2 — Source of `baseline_30d`.** A1's `rel_volume_ge` takes `baseline_30d: Decimal | None` as a value parameter (per A1's plan §"Snapshot semantics"). A2 has to decide where that value comes from at scan time: precomputed and passed in, or fetched per-symbol via a provider. **Proposed answer:** `ScannerSnapshot.baseline_30d` carries it. Provider concern is upstream of A2 (A3 / loop assembles the snapshot). Keeps the scanner I/O-free.

**D-A2-3 — Headlines source per tick.** A1's `news_present` / `headline_count` take `Sequence[Headline]` per call. A2 has to decide whether the scanner pulls all headlines once and dispatches per ticker, or holds a `NewsFeed` provider and queries per ticker. **Proposed answer:** `ScannerSnapshot.headlines` carries the per-ticker subsequence, pre-filtered by the loop. Same pattern as the other inputs — scanner stays sync.

**D-A2-4 — `ScannerPick.price` definition.** #41 lists `price` on the pick without saying which price (the bar's close that satisfied the band check, or the live quote that triggered the gainer-% check). **Proposed answer:** `ScannerPick.price = snap.last` (the live quote). That's the price a human reader of the pick would expect ("AVTX flagged at $5.50"). The `bar.close` used in `price_in_band` stays internal to the filter step.

**D-A2-5 — Pre-rank vs post-rank pick representation.** `ScannerPick.rank` is required per #41, but the filter step naturally produces unranked candidates. **Proposed answer:** keep one `ScannerPick` type with `rank: int = 0` default; the filter step constructs with `rank=0`, the ranker calls `dataclasses.replace(pick, rank=i+1)` for the surviving top-N. Trade-off: a sentinel `0` value, but no second type. Acceptable because pre-rank picks never escape `Scanner.scan` (callers only see ranked output).

These will be bundled into one spec-fix issue against #41 after the PR ships, matching A1's pattern with #40.

## Conventions (applies to all tasks)

The same three patterns A1 codified, restated for A2:

- **Imports arrive when needed (not pre-emptively).** Each task's test/source files start with only what that task references; later tasks add to the same files. For multi-task files (`test_universe.py`, `data/universe.py`), the Import Evolution Pattern below tracks the growth alphabetically. Rationale: every intermediate state stays lint-clean (no `F401` from premature imports, no `E402` from scattered mid-file imports), so each task's red→green cycle is self-contained.
- **Do not add `# noqa` for ruff rules outside the project's `select` list.** `pyproject.toml` selects `["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`. Notably absent: `ARG` (unused arguments are fine, no suppression needed), `C901` (complexity is fine), `D` (no docstring style enforcement). A suppression like `# noqa: ARG002` will trip `RUF100` (unused noqa).
- **Use ASCII in comments and strings where it reads identically.** Ruff `RUF001`/`RUF002`/`RUF003` flag visually-ambiguous Unicode (e.g., `×` MULTIPLICATION SIGN vs `x`). Prefer `5.0x` over `5.0×`, `>=` over `≥`.

## Import Evolution Pattern (test_universe.py, data/universe.py)

These two files are touched in both Task 4 (Protocol + fake) and Task 5 (CachedUniverseProvider). Their growth-bearing import lines evolve alphabetically as follows:

| File | After Task 4 | After Task 5 |
|---|---|---|
| `data/universe.py` (top-level) | `from datetime import timedelta`<br>`from typing import TYPE_CHECKING, Protocol, runtime_checkable`<br>`if TYPE_CHECKING: from datetime import date` | `from dataclasses import dataclass`<br>`from datetime import datetime, timedelta`<br>`from typing import TYPE_CHECKING, Protocol, runtime_checkable`<br>`from ross_trading.core.clock import Clock, RealClock`<br>`if TYPE_CHECKING: from datetime import date` |
| `test_universe.py` | `from datetime import date`<br>`from ross_trading.data.universe import UniverseProvider`<br>`from tests.fakes.universe import FakeUniverseProvider` | `from datetime import UTC, date, datetime, timedelta`<br>`import pytest`<br>`from ross_trading.core.clock import VirtualClock`<br>`from ross_trading.data.universe import CachedUniverseProvider, UniverseProvider`<br>`from tests.fakes.universe import FakeUniverseProvider` |

The other six new files (`scanner/types.py`, `scanner/ranking.py`, `scanner/scanner.py`, `tests/fakes/universe.py`, `test_scanner_types.py`, `test_scanner_ranking.py`, `test_scanner.py`) are each born complete in one task — no in-task evolution.

## Effort Estimate

**M** (medium). Four source files, four test files, one fake file. ~400 LoC source, ~500 LoC tests. Roughly 2-3 hours for an engineer who has read this plan, including running ruff/mypy/pytest after each task.

---

## Tasks

### Task 1: `ScannerPick` + `ScannerSnapshot` value objects

**Files:**
- Create: `src/ross_trading/scanner/types.py`
- Create: `tests/unit/test_scanner_types.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Atom A2 — ScannerPick + ScannerSnapshot value types (issue #41)."""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.types import ScannerPick, ScannerSnapshot

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick(**overrides: object) -> ScannerPick:
    base: dict[str, object] = {
        "ticker": "AVTX",
        "ts": T0,
        "rel_volume": Decimal("8.5"),
        "pct_change": Decimal("18.4"),
        "price": Decimal("5.50"),
        "float_shares": 8_500_000,
        "news_present": True,
        "headline_count": 2,
        "rank": 1,
    }
    base.update(overrides)
    return ScannerPick(**base)  # type: ignore[arg-type]


def _snap(**overrides: object) -> ScannerSnapshot:
    bar = Bar(
        symbol="AVTX",
        ts=T0,
        timeframe="M1",
        open=Decimal("5.30"),
        high=Decimal("5.55"),
        low=Decimal("5.25"),
        close=Decimal("5.50"),
        volume=900_000,
    )
    base: dict[str, object] = {
        "bar": bar,
        "last": Decimal("5.52"),
        "prev_close": Decimal("4.80"),
        "baseline_30d": Decimal("100_000"),
        "float_record": FloatRecord(
            ticker="AVTX",
            as_of=date(2026, 4, 26),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        "headlines": (
            Headline(ticker="AVTX", ts=T0, source="Benzinga", title="story"),
        ),
    }
    base.update(overrides)
    return ScannerSnapshot(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------- ScannerPick


def test_pick_is_frozen() -> None:
    p = _pick()
    with pytest.raises(FrozenInstanceError):
        p.rank = 99  # type: ignore[misc]


def test_pick_has_slots() -> None:
    assert "__slots__" in ScannerPick.__dict__


def test_pick_picklable_roundtrip() -> None:
    p = _pick()
    revived = pickle.loads(pickle.dumps(p))  # noqa: S301
    assert revived == p
    assert revived is not p


def test_pick_default_rank_is_zero() -> None:
    p = ScannerPick(
        ticker="AVTX",
        ts=T0,
        rel_volume=Decimal("8.5"),
        pct_change=Decimal("18.4"),
        price=Decimal("5.50"),
        float_shares=8_500_000,
        news_present=False,
        headline_count=0,
    )
    assert p.rank == 0


def test_pick_equality_value_based() -> None:
    assert _pick() == _pick()
    assert _pick(rank=1) != _pick(rank=2)


# ------------------------------------------------------------- ScannerSnapshot


def test_snapshot_is_frozen() -> None:
    s = _snap()
    with pytest.raises(FrozenInstanceError):
        s.last = Decimal("99")  # type: ignore[misc]


def test_snapshot_has_slots() -> None:
    assert "__slots__" in ScannerSnapshot.__dict__


def test_snapshot_accepts_none_baseline_and_float() -> None:
    """Optional fields tolerate missing data — caller (Scanner) decides what to do."""
    s = _snap(baseline_30d=None, float_record=None)
    assert s.baseline_30d is None
    assert s.float_record is None


def test_snapshot_accepts_empty_headlines() -> None:
    s = _snap(headlines=())
    assert s.headlines == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_types.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `ross_trading.scanner.types` (the source file does not exist yet).

- [ ] **Step 3: Create `src/ross_trading/scanner/types.py`**

```python
"""Frozen value types for the scanner.

Phase 2 — Atom A2 (#41). ``ScannerPick`` is the output unit;
``ScannerSnapshot`` is the per-symbol input bag the scanner needs to
evaluate the Section 3.1 filters. Keeping inputs and outputs as
value objects lets ``Scanner.scan`` stay pure-sync — A3 (the loop)
owns provider I/O and assembles the snapshot map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from ross_trading.data.types import Bar, FloatRecord, Headline


@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    """Per-symbol inputs needed to evaluate the scanner's hard filters.

    Assembled by A3 from provider calls and consumed by
    :meth:`Scanner.scan` as a deterministic value. Keeps the scanner
    I/O-free so live and replay produce identical answers.

    - ``bar`` — last completed bar; provides ``volume`` (rel-vol input),
      ``close`` (price-band input), and ``ts`` (the bar's open time)
      as the anchor for news lookback.
    - ``last`` — latest quote price; reference for the gainer-% check
      (``pct_change_ge`` vs ``prev_close``) and the value surfaced as
      ``ScannerPick.price``.
    - ``prev_close`` — previous session's closing price; reference for
      gainer-%.
    - ``baseline_30d`` — 30-day average daily volume; ``None`` means
      "insufficient history" and the scanner rejects.
    - ``float_record`` — daily float record; ``None`` means "no float
      data" and the scanner rejects.
    - ``headlines`` — ticker-relevant headlines for the news soft
      signals. An empty sequence is fine (``news_present=False``,
      ``headline_count=0``); since news is non-gating per #39, the
      pick still survives if the hard filters pass.
    """

    bar: Bar
    last: Decimal
    prev_close: Decimal
    baseline_30d: Decimal | None
    float_record: FloatRecord | None
    headlines: Sequence[Headline]


@dataclass(frozen=True, slots=True)
class ScannerPick:
    """A symbol that passed the scanner's hard filters.

    Frozen, slots-enabled, picklable (per #41 acceptance). ``rank=0``
    is the pre-rank sentinel produced by the filter step;
    :func:`rank_picks` assigns final ``rank`` values ``1..N`` via
    ``dataclasses.replace``. Pre-rank picks never escape
    :meth:`Scanner.scan` — external callers only see ranked output.
    """

    ticker: str
    ts: datetime
    rel_volume: Decimal
    pct_change: Decimal
    price: Decimal
    float_shares: int
    news_present: bool
    headline_count: int
    rank: int = 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_types.py -v`
Expected: 9 passed.

- [ ] **Step 5: Local gate (full project)**

Run, confirm each is clean, then proceed:
```bash
ruff check .
mypy src tests
pytest
```

If anything outside the new files breaks, **stop and surface** before fixing — likely indicates a plan defect.

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/types.py tests/unit/test_scanner_types.py
git commit -m "feat(scanner): ScannerPick and ScannerSnapshot value types (#41)"
```

---

### Task 2: `rank_picks`

**Files:**
- Create: `src/ross_trading/scanner/ranking.py`
- Create: `tests/unit/test_scanner_ranking.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Atom A2 — rank_picks (issue #41)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ross_trading.scanner.ranking import rank_picks
from ross_trading.scanner.types import ScannerPick

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick(ticker: str, pct_change: str) -> ScannerPick:
    return ScannerPick(
        ticker=ticker,
        ts=T0,
        rel_volume=Decimal("8.0"),
        pct_change=Decimal(pct_change),
        price=Decimal("5.00"),
        float_shares=8_000_000,
        news_present=False,
        headline_count=0,
    )


def test_rank_empty_returns_empty() -> None:
    assert rank_picks([]) == []


def test_rank_assigns_1_through_n() -> None:
    picks = [_pick("A", "10"), _pick("B", "20"), _pick("C", "15")]
    ranked = rank_picks(picks, n=3)
    assert [p.ticker for p in ranked] == ["B", "C", "A"]
    assert [p.rank for p in ranked] == [1, 2, 3]


def test_rank_truncates_to_top_n() -> None:
    pcts = [10, 20, 15, 5, 25, 12, 18]
    picks = [_pick(t, str(p)) for t, p in zip("ABCDEFG", pcts, strict=True)]
    ranked = rank_picks(picks, n=5)
    # Sorted by pct desc: E=25, B=20, G=18, C=15, F=12 ; A=10 and D=5 dropped.
    assert [p.ticker for p in ranked] == ["E", "B", "G", "C", "F"]
    assert [p.rank for p in ranked] == [1, 2, 3, 4, 5]


def test_rank_tie_break_by_ticker_ascending() -> None:
    picks = [_pick("ZZZZ", "15"), _pick("AAAA", "15"), _pick("MMMM", "15")]
    ranked = rank_picks(picks, n=3)
    assert [p.ticker for p in ranked] == ["AAAA", "MMMM", "ZZZZ"]
    assert [p.rank for p in ranked] == [1, 2, 3]


def test_rank_tie_break_independent_of_input_order() -> None:
    """Same picks in two different input orders → same output."""
    a = [_pick("ZZZZ", "15"), _pick("AAAA", "15"), _pick("MMMM", "15")]
    b = [_pick("MMMM", "15"), _pick("AAAA", "15"), _pick("ZZZZ", "15")]
    assert rank_picks(a) == rank_picks(b)


def test_rank_default_n_is_5() -> None:
    picks = [_pick(c, str(i)) for i, c in enumerate("ABCDEFG", start=1)]
    ranked = rank_picks(picks)
    assert len(ranked) == 5


def test_rank_zero_n_returns_empty() -> None:
    picks = [_pick("A", "10"), _pick("B", "20")]
    assert rank_picks(picks, n=0) == []


def test_rank_negative_n_returns_empty() -> None:
    picks = [_pick("A", "10")]
    assert rank_picks(picks, n=-1) == []


def test_rank_n_larger_than_input_returns_all_ranked() -> None:
    picks = [_pick("A", "10"), _pick("B", "20")]
    ranked = rank_picks(picks, n=100)
    assert [p.ticker for p in ranked] == ["B", "A"]
    assert [p.rank for p in ranked] == [1, 2]


def test_rank_overwrites_input_rank_field() -> None:
    """Pre-rank picks have rank=0; ranker overwrites regardless of input."""
    picks = [
        ScannerPick(
            ticker="A", ts=T0, rel_volume=Decimal("8"), pct_change=Decimal("10"),
            price=Decimal("5"), float_shares=8_000_000, news_present=False,
            headline_count=0, rank=99,  # bogus input rank
        ),
    ]
    ranked = rank_picks(picks)
    assert ranked[0].rank == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_ranking.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `ross_trading.scanner.ranking`.

- [ ] **Step 3: Create `src/ross_trading/scanner/ranking.py`**

```python
"""Top-N ranker for scanner picks.

Phase 2 — Atom A2 (#41). Pure function. Sorts by ``pct_change``
descending with stable tie-break on ``ticker`` ascending, takes the
first ``n``, and assigns final ``rank=1..N`` via
``dataclasses.replace`` (since :class:`ScannerPick` is frozen).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ross_trading.scanner.types import ScannerPick


def rank_picks(
    candidates: Sequence[ScannerPick],
    n: int = 5,
) -> list[ScannerPick]:
    """Sort ``candidates`` by ``-pct_change, ticker`` and return the top ``n``
    with ``rank`` overwritten to ``1..N``.

    Returns an empty list when ``n <= 0`` (a non-positive ``top_n``
    means "no slots available", not "unbounded").
    """
    if n <= 0:
        return []
    sorted_picks = sorted(candidates, key=lambda p: (-p.pct_change, p.ticker))
    top = sorted_picks[:n]
    return [replace(pick, rank=i + 1) for i, pick in enumerate(top)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_ranking.py -v`
Expected: 9 passed.

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/ranking.py tests/unit/test_scanner_ranking.py
git commit -m "feat(scanner): rank_picks top-N selector (#41)"
```

---

### Task 3: `Scanner` orchestrator

**Files:**
- Create: `src/ross_trading/scanner/scanner.py`
- Create: `tests/unit/test_scanner.py`

This is the load-bearing task — it composes A1's six primitives + the ranker into the public `Scanner.scan()` surface. Test cases cover each individual filter rejection, news-non-gating, deterministic output, and threshold customization.

- [ ] **Step 1: Write the failing test file**

```python
"""Atom A2 — Scanner orchestrator (issue #41)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar(*, symbol: str = "AVTX", close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal(close),
        low=Decimal("4.95"),
        close=Decimal(close),
        volume=volume,
    )


def _float(shares: int, ticker: str = "AVTX") -> FloatRecord:
    return FloatRecord(
        ticker=ticker,
        as_of=date(2026, 4, 26),
        float_shares=shares,
        shares_outstanding=shares * 2,
        source="test",
    )


def _snap(
    *,
    symbol: str = "AVTX",
    close: str = "5.50",
    volume: int = 5_000_000,
    last: str = "5.50",
    prev_close: str = "5.00",
    baseline_30d: Decimal | None = Decimal("1_000_000"),
    float_shares: int | None = 8_500_000,
    headlines: tuple[Headline, ...] = (),
) -> ScannerSnapshot:
    return ScannerSnapshot(
        bar=_bar(symbol=symbol, close=close, volume=volume),
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=baseline_30d,
        float_record=_float(float_shares, symbol) if float_shares is not None else None,
        headlines=headlines,
    )


# -------------------------------------------------------------------- happy path


def test_passes_all_filters_yields_one_pick() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX"])
    snapshot = {"AVTX": _snap()}
    picks = scanner.scan(universe, snapshot)
    assert len(picks) == 1
    pick = picks[0]
    assert pick.ticker == "AVTX"
    assert pick.rank == 1
    assert pick.ts == T0
    assert pick.rel_volume == Decimal("5")
    assert pick.pct_change == Decimal("10")
    assert pick.price == Decimal("5.50")  # ScannerPick.price is snap.last
    assert pick.float_shares == 8_500_000
    assert pick.news_present is False
    assert pick.headline_count == 0


# ------------------------------------------------------------- universe handling


def test_universe_member_with_no_snapshot_is_skipped() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX", "BBAI"])
    snapshot = {"AVTX": _snap()}  # BBAI missing
    picks = scanner.scan(universe, snapshot)
    assert [p.ticker for p in picks] == ["AVTX"]


def test_empty_universe_yields_empty() -> None:
    scanner = Scanner()
    assert scanner.scan(frozenset(), {}) == []


# ------------------------------------------------------------------ each filter


def test_missing_baseline_rejects() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(baseline_30d=None)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_missing_float_record_rejects() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(float_shares=None)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_rel_volume_below_threshold_rejects() -> None:
    scanner = Scanner()  # default 5x
    snapshot = {"AVTX": _snap(volume=4_000_000)}  # 4x baseline
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_pct_change_below_threshold_rejects() -> None:
    scanner = Scanner()  # default 10%
    snapshot = {"AVTX": _snap(last="5.40", prev_close="5.00")}  # +8%
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_price_outside_band_rejects_low() -> None:
    scanner = Scanner()  # default [1, 20]
    snapshot = {"AVTX": _snap(close="0.50", last="0.55", prev_close="0.45")}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_price_outside_band_rejects_high() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(close="25.00", last="25.50", prev_close="22.00")}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_float_above_threshold_rejects() -> None:
    scanner = Scanner()  # default 20M
    snapshot = {"AVTX": _snap(float_shares=25_000_000)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


# ---------------------------------------------------------------- non-gating news


def test_news_recorded_but_not_gating() -> None:
    """Per D5/#39: news_present is recorded on the pick but does NOT gate selection."""
    scanner = Scanner()
    headlines = (
        Headline(ticker="AVTX", ts=T0 - timedelta(hours=1), source="Benzinga", title="story"),
    )
    snap_with_news = _snap(headlines=headlines)
    snap_without_news = _snap()
    picks_with = scanner.scan(frozenset(["AVTX"]), {"AVTX": snap_with_news})
    picks_without = scanner.scan(frozenset(["AVTX"]), {"AVTX": snap_without_news})
    # Both qualify (news non-gating); both produce one pick.
    assert len(picks_with) == 1
    assert len(picks_without) == 1
    assert picks_with[0].news_present is True
    assert picks_with[0].headline_count == 1
    assert picks_without[0].news_present is False
    assert picks_without[0].headline_count == 0


# ---------------------------------------------------------------------- top-N


def test_top_n_truncates_and_orders_by_pct_change() -> None:
    scanner = Scanner()  # default n=5
    universe = frozenset(["A", "B", "C", "D", "E", "F", "G"])
    pct_changes = {"A": 10, "B": 20, "C": 15, "D": 11, "E": 25, "F": 12, "G": 18}
    snapshot = {}
    for sym, pct in pct_changes.items():
        new_last = Decimal("5.00") + Decimal("5.00") * Decimal(pct) / Decimal("100")
        snapshot[sym] = _snap(symbol=sym, last=str(new_last), prev_close="5.00")
    picks = scanner.scan(universe, snapshot)
    assert [p.ticker for p in picks] == ["E", "B", "G", "C", "F"]
    assert [p.rank for p in picks] == [1, 2, 3, 4, 5]


# --------------------------------------------------------------- determinism


def test_deterministic_same_inputs_same_output() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX", "BBAI"])
    snapshot = {
        "AVTX": _snap(symbol="AVTX"),
        "BBAI": _snap(symbol="BBAI", last="5.55"),
    }
    out_a = scanner.scan(universe, snapshot)
    out_b = scanner.scan(universe, snapshot)
    assert out_a == out_b


# ------------------------------------------------------------ custom thresholds


def test_custom_thresholds_let_a_b_test_without_surgery() -> None:
    scanner = Scanner(rel_volume_threshold=10.0)  # tighter rel-vol
    snapshot = {"AVTX": _snap(volume=4_000_000)}  # 4x — fails 10x cutoff
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_custom_top_n() -> None:
    scanner = Scanner(top_n=2)
    universe = frozenset(["A", "B", "C"])
    snapshot = {
        sym: _snap(symbol=sym, last=str(Decimal("5") + Decimal(i)), prev_close="5.00")
        for i, sym in enumerate(universe, start=1)
    }
    picks = scanner.scan(universe, snapshot)
    assert len(picks) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `ross_trading.scanner.scanner`.

- [ ] **Step 3: Create `src/ross_trading/scanner/scanner.py`**

```python
"""Scanner orchestrator: composes A1 filter primitives + ranker.

Phase 2 — Atom A2 (#41). Pure-sync. No I/O, no logging, no
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
from ross_trading.scanner.types import ScannerPick

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

    def scan(
        self,
        universe: frozenset[str],
        snapshot: Mapping[str, ScannerSnapshot],
    ) -> list[ScannerPick]:
        """Filter the universe by snapshot, then rank top-N.

        Universe members with no snapshot entry are silently skipped
        — universe drift between enumeration and snapshot assembly
        is normal at the boundary of a session.
        """
        candidates: list[ScannerPick] = []
        for ticker in universe:
            snap = snapshot.get(ticker)
            if snap is None:
                continue
            baseline = snap.baseline_30d
            float_rec = snap.float_record
            if baseline is None or float_rec is None:
                continue
            if not (
                rel_volume_ge(ticker, snap.bar, baseline, self._rel_volume_threshold)
                and pct_change_ge(snap.last, snap.prev_close, self._pct_change_threshold_pct)
                and price_in_band(ticker, snap.bar, self._price_low, self._price_high)
                and float_le(float_rec, self._float_threshold)
            ):
                continue
            candidates.append(self._build_pick(ticker, snap, baseline, float_rec))
        return rank_picks(candidates, n=self._top_n)

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner.py -v`
Expected: 14 passed.

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/scanner.py tests/unit/test_scanner.py
git commit -m "feat(scanner): Scanner orchestrator composes filters and ranker (#41)"
```

---

### Task 4: `UniverseProvider` Protocol + `FakeUniverseProvider`

**Files:**
- Create: `src/ross_trading/data/universe.py` (Protocol only; cache wrapper arrives in Task 5)
- Create: `tests/fakes/universe.py`
- Create: `tests/unit/test_universe.py` (Protocol-conformance test only; cache tests arrive in Task 5)

- [ ] **Step 1: Write the failing test file**

```python
"""Atom A2 — UniverseProvider Protocol + CachedUniverseProvider (issue #41)."""

from __future__ import annotations

from datetime import date

from ross_trading.data.universe import UniverseProvider
from tests.fakes.universe import FakeUniverseProvider


def test_fake_satisfies_protocol() -> None:
    fake = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    assert isinstance(fake, UniverseProvider)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_universe.py -v`
Expected: `ImportError` for `ross_trading.data.universe` (and `tests.fakes.universe`).

- [ ] **Step 3: Create the fake at `tests/fakes/universe.py`**

```python
"""Scripted UniverseProvider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.errors import FeedError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date


class FakeUniverseProvider:
    """Returns canned symbol sets keyed on as_of date.

    Records every call in ``self.calls`` (in order) so cache tests
    can assert hit / miss behavior on the wrapping
    :class:`CachedUniverseProvider`.
    """

    def __init__(self, by_date: Mapping[date, frozenset[str]]) -> None:
        self._by_date = dict(by_date)
        self.calls: list[date] = []

    async def list_symbols(self, as_of: date) -> frozenset[str]:
        self.calls.append(as_of)
        result = self._by_date.get(as_of)
        if result is None:
            msg = f"no fake universe for {as_of}"
            raise FeedError(msg)
        return result
```

- [ ] **Step 4: Create the Protocol at `src/ross_trading/data/universe.py`**

```python
"""Universe provider interface and daily-cache wrapper.

Phase 2 issue #41 — A2. The scanner consumes ``UniverseProvider``
implementations to enumerate the day's NMS-listed common-stock
universe. Concrete vendor implementations live under
``data/providers/``; the cache wrapper (added in Task 5 of #41's
plan) keeps a daily TTL.

Decisions resolved:
- #35 (D1: universe source) — daily NMS enumeration is the source
  of truth. Vendor gainers/snapshot endpoints are an internal
  optimization inside concrete providers (skip polling 8k symbols
  with no movement) but never a substitute for full enumeration.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import date

DEFAULT_CACHE_TTL = timedelta(hours=24)


@runtime_checkable
class UniverseProvider(Protocol):
    """Daily symbol-universe enumeration for one trading date."""

    async def list_symbols(self, as_of: date) -> frozenset[str]: ...
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/unit/test_universe.py -v`
Expected: 1 passed.

- [ ] **Step 6: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 7: Commit**

```bash
git add src/ross_trading/data/universe.py tests/fakes/universe.py tests/unit/test_universe.py
git commit -m "feat(data): UniverseProvider protocol and fake (#41)"
```

---

### Task 5: `CachedUniverseProvider`

**Files:**
- Modify: `src/ross_trading/data/universe.py` (append cache wrapper, grow imports per Import Evolution Pattern)
- Modify: `tests/unit/test_universe.py` (grow imports + append cache tests)

- [ ] **Step 1: Update `test_universe.py` imports, then append the failing tests**

Replace the file's import block from:

```python
from datetime import date

from ross_trading.data.universe import UniverseProvider
from tests.fakes.universe import FakeUniverseProvider
```

to:

```python
from datetime import UTC, date, datetime, timedelta

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.universe import CachedUniverseProvider, UniverseProvider
from tests.fakes.universe import FakeUniverseProvider
```

Then append the new tests at the bottom of the file:

```python
async def test_cached_returns_upstream_value() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX", "BBAI"])})
    cache = CachedUniverseProvider(upstream)
    result = await cache.list_symbols(date(2026, 4, 26))
    assert result == frozenset(["AVTX", "BBAI"])


async def test_cache_hit_does_not_call_upstream() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedUniverseProvider(upstream, clock=clock)
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 26))
    assert upstream.calls == [date(2026, 4, 26)]


async def test_cache_misses_after_ttl() -> None:
    upstream = FakeUniverseProvider({date(2026, 4, 26): frozenset(["AVTX"])})
    clock = VirtualClock(datetime(2026, 4, 26, tzinfo=UTC))
    cache = CachedUniverseProvider(upstream, clock=clock, ttl=timedelta(hours=1))
    await cache.list_symbols(date(2026, 4, 26))
    clock.advance(3601)
    await cache.list_symbols(date(2026, 4, 26))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 26)]


async def test_cache_per_date_separately() -> None:
    upstream = FakeUniverseProvider({
        date(2026, 4, 26): frozenset(["AVTX"]),
        date(2026, 4, 27): frozenset(["BBAI"]),
    })
    cache = CachedUniverseProvider(upstream)
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 27))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 27)]
    # Re-fetching either date should not re-call upstream.
    await cache.list_symbols(date(2026, 4, 26))
    await cache.list_symbols(date(2026, 4, 27))
    assert upstream.calls == [date(2026, 4, 26), date(2026, 4, 27)]


def test_cache_rejects_zero_ttl() -> None:
    upstream = FakeUniverseProvider({})
    with pytest.raises(ValueError, match="ttl must be positive"):
        CachedUniverseProvider(upstream, ttl=timedelta(0))


def test_cache_rejects_negative_ttl() -> None:
    upstream = FakeUniverseProvider({})
    with pytest.raises(ValueError, match="ttl must be positive"):
        CachedUniverseProvider(upstream, ttl=timedelta(seconds=-1))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_universe.py -v`
Expected: failures with `ImportError: cannot import name 'CachedUniverseProvider'`.

- [ ] **Step 3: Update `data/universe.py` imports, then append the cache wrapper**

Replace the file's import block from:

```python
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import date
```

to:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ross_trading.core.clock import Clock, RealClock

if TYPE_CHECKING:
    from datetime import date
```

Then append the cache wrapper at the bottom of the file:

```python
@dataclass(frozen=True, slots=True)
class _Entry:
    symbols: frozenset[str]
    fetched_at: datetime


class CachedUniverseProvider:
    """24-hour in-memory cache in front of any :class:`UniverseProvider`.

    Modeled on :class:`CachedFloatReference` (``data/float_reference.py``).
    The universe changes at most once per session day; this cache
    avoids re-enumerating the NMS list on every scanner tick.
    """

    def __init__(
        self,
        upstream: UniverseProvider,
        clock: Clock | None = None,
        ttl: timedelta = DEFAULT_CACHE_TTL,
    ) -> None:
        if ttl <= timedelta(0):
            msg = "cache ttl must be positive"
            raise ValueError(msg)
        self._upstream = upstream
        self._clock: Clock = clock if clock is not None else RealClock()
        self._ttl = ttl
        self._cache: dict[date, _Entry] = {}

    async def list_symbols(self, as_of: date) -> frozenset[str]:
        now = self._clock.now()
        cached = self._cache.get(as_of)
        if cached is not None and now - cached.fetched_at < self._ttl:
            return cached.symbols
        symbols = await self._upstream.list_symbols(as_of)
        self._cache[as_of] = _Entry(symbols=symbols, fetched_at=now)
        return symbols
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_universe.py -v`
Expected: 7 passed (1 from Task 4 + 6 cache tests).

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/data/universe.py tests/unit/test_universe.py
git commit -m "feat(data): CachedUniverseProvider 24h-TTL wrapper (#41)"
```

---

### Task 6: Static checks + full suite (verification gate)

This is a defensive sweep — if the per-task gate in Tasks 1-5 was honored, this is a no-op. If anything drifted, fix here before opening the PR.

- [ ] **Step 1: Run ruff**

Run: `ruff check .`
Expected: `All checks passed!`

- [ ] **Step 2: Run mypy strict**

Run: `mypy src tests`
Expected: `Success: no issues found in <N> source files` where `<N>` is the A1-baseline count plus the eight new files. Verify the count is sensible rather than asserting an exact number — the baseline can drift between branches.

- [ ] **Step 3: Run the full pytest suite**

Run: `pytest`
Expected: every Phase-1 and A1 test still passes, plus the new A2 cases. No regressions, no skips, no warnings.

- [ ] **Step 4: If anything fails, fix and re-run.** A failure here likely indicates a plan defect — surface to the harness owner before patching.

- [ ] **Step 5: No new commit needed if Tasks 1-5 commits are clean.** If lint or mypy turned up something, the fix lands as a `chore` commit:

```bash
git add -p
git commit -m "chore(scanner): satisfy ruff/mypy on scanner core (#41)"
```

---

### Task 7: Open the PR

- [ ] **Step 1: Push the branch**

Run: `git push -u origin phase-2-a2-scanner-core-and-ranking`

- [ ] **Step 2: Open the PR closing #41**

Use `gh pr create --base main`. Title: `Phase 2 — A2: scanner core and ranking`. Body must include:

- `Closes #41.` on its own line so the issue auto-closes on merge.
- A one-paragraph summary linking back to the parent #3.
- The four files added under `src/` and four under `tests/` (plus `tests/fakes/universe.py`).
- "Decisions resolved: #35 (D1, universe source via daily NMS + cache), #39 (D5, news non-gating)."
- The full Acceptance Criteria checklist from #41 with each item checked.
- Spec-fix notice: "Five planning decisions diverged from #41's literal text — see plan §Defects/Open Questions; will be bundled into a follow-up spec-fix issue."
- Verification block: `ruff check .`, `mypy src tests`, `pytest` — all green, with counts.
- Tag `@claude` and `@codex` per project review convention.

- [ ] **Step 3: Confirm CI is green**, then hand off to reviewer. Do NOT merge.

---

## Self-Review

**1. Spec coverage.** Walking through #41's "Files (new)" list:
- `scanner/scanner.py` (`Scanner.scan`) → Task 3.
- `scanner/ranking.py` (`rank_picks`) → Task 2.
- `scanner/types.py` (`ScannerPick`) → Task 1, plus `ScannerSnapshot` added per defect D-A2-1.
- `data/universe.py` (`UniverseProvider` + `CachedUniverseProvider`) → Tasks 4-5.

#41's seven Acceptance bullets all mapped:
- Deterministic output → `test_deterministic_same_inputs_same_output` (Task 3).
- ScannerPick frozen / slots / picklable → `test_pick_is_frozen`, `test_pick_has_slots`, `test_pick_picklable_roundtrip` (Task 1).
- No DB / network in scanner layer → orchestrator is pure sync, takes pre-assembled snapshot map; `data/universe.py` is async-by-interface but called by A3, not by Scanner.
- `UniverseProvider` runtime-checkable Protocol mockable via `tests/fakes/` → Task 4 (`test_fake_satisfies_protocol` + `tests/fakes/universe.py`).
- `CachedUniverseProvider` exposes same protocol surface → both implement `async def list_symbols(self, as_of: date) -> frozenset[str]`; verified implicitly by the same test class shape.
- `mypy --strict` passes → Task 6.
- Phase-1 + A1 regression → Task 6 (full pytest).

#41's Test bullet (`tests/unit/test_scanner.py` cases: empty, all rejected, exactly 5 pass, more than 5 tie-break, missing input) — all covered: empty (`test_empty_universe_yields_empty`, `test_universe_member_with_no_snapshot_is_skipped`), all rejected (each `_rejects` test), exactly 5 / more than 5 / tie-break (`test_top_n_truncates_and_orders_by_pct_change` + ranker tie-break tests in Task 2), missing input (`test_missing_baseline_rejects`, `test_missing_float_record_rejects`, `test_universe_member_with_no_snapshot_is_skipped`).

**2. Placeholder scan.** No `TBD`, no `implement later`, no "add appropriate error handling" — every step shows the actual code. Test code is concrete. Source implementations are concrete. Commit messages are concrete.

**3. Type consistency.** `ScannerPick` field names match across `types.py` (definition), `ranking.py` (`replace(pick, rank=...)`), `scanner.py` (`ScannerPick(ticker=..., ts=..., rel_volume=..., pct_change=..., price=..., float_shares=..., news_present=..., headline_count=..., rank=0)`), and the test files (positional args via `_pick(...)` factories). `ScannerSnapshot` fields (`bar`, `last`, `prev_close`, `baseline_30d`, `float_record`, `headlines`) match across `types.py` and the `_snap(...)` factories in `test_scanner_types.py` and `test_scanner.py`. `UniverseProvider.list_symbols(self, as_of: date) -> frozenset[str]` matches across `data/universe.py`, `tests/fakes/universe.py`, and `CachedUniverseProvider`. `Clock` injection on `CachedUniverseProvider` matches `CachedFloatReference`'s constructor signature (`upstream`, `clock`, `ttl`).

**4. Anchor alignment.** Scanner uses `snap.bar.ts` (bar-open time) as the news anchor — consistent with A1's anchor rule. Live and replay produce identical `news_present` / `headline_count` values for the same snapshot. The `Scanner` itself takes no `Clock` because it's pure-sync and observes no wall time; only `CachedUniverseProvider` does (matching `CachedFloatReference`).

**5. Filter contract narrowing.** `Scanner.scan` checks `snap.baseline_30d is None` and `snap.float_record is None` *before* calling A1's filters — this both (a) lets mypy strict narrow the types for the build step (`baseline: Decimal`, `float_rec: FloatRecord`), and (b) makes the orchestrator-level reject explicit. The duplication with A1's internal None-checks is intentional defense-in-depth, not a contract assumption — A1's filters can change their None semantics later without breaking A2.

**6. Spec-text divergence vs issue #41.** Five intentional planning decisions diverge from the literal text of #41:
- D-A2-1: `Scanner.scan(universe, snapshot)` → `(universe: frozenset[str], snapshot: Mapping[str, ScannerSnapshot])`.
- D-A2-2: `ScannerSnapshot.baseline_30d: Decimal | None` (provider concern lifted to A3).
- D-A2-3: `ScannerSnapshot.headlines: Sequence[Headline]` (provider concern lifted to A3).
- D-A2-4: `ScannerPick.price = snap.last` (live quote, not bar.close).
- D-A2-5: `ScannerPick.rank: int = 0` default (single type, ranker overwrites).

These will be bundled into one spec-fix issue against #41 after the PR ships, matching A1's pattern with #40.

**7. Sequencing soundness.** Task dependencies trace cleanly:
- Task 1 (types) — no scanner deps.
- Task 2 (ranking) — depends on Task 1's `ScannerPick`.
- Task 3 (scanner orchestrator) — depends on Tasks 1-2 + A1's `filters.py`.
- Task 4 (UniverseProvider) — independent of scanner; depends on `core/errors.FeedError` (Phase 1).
- Task 5 (CachedUniverseProvider) — depends on Task 4 + `core/clock.Clock` (Phase 1).
- Task 6 (gate) — depends on all prior.
- Task 7 (PR) — depends on Task 6.

No cycle, no lookahead, no skipped dependency.
