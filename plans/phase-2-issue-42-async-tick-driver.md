# Phase 2 -- A3: Async Tick Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The harness owner has asked to be paused after each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive A2's `Scanner` on a deterministic clock so it produces a stream of `ScannerDecision` rows during the 07:00-11:00 ET trading window, self-suppresses on stale feeds, and records retrospective feed gaps -- in both live and replay modes.

**Architecture:** One new package surface in `scanner/` (loop + decisions + assembler protocol) plus a single free function in `core/clock.py`. The loop is a long-running coroutine that ticks every 2 wall-seconds (or virtual seconds, under `VirtualClock`), gates work with `is_market_hours`, asks an injected `SnapshotAssembler` for an as-of snapshot, runs A2's `Scanner.scan`, and emits per-pick `ScannerDecision` rows to an injected `DecisionSink`. The assembler is the replay-determinism boundary -- the loop itself owns no provider I/O. A3 does not reimplement A2's filter chain and does not write to the journal directly; A5 (#44) implements the `DecisionSink` Protocol when it ships.

**Tech Stack:** Python 3.11, `asyncio` cooperative cancellation, `decimal.Decimal` arithmetic, `datetime` UTC + `zoneinfo.ZoneInfo("America/New_York")` for the market-hours window, mypy `--strict`, ruff (`["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`), pytest with `asyncio_mode = "auto"`.

**Issue:** [#42](https://github.com/seanyofthedead/Ross-trading/issues/42) -- tracked under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Depends on:** A2 (#41) -- scanner core. Merged in PR #50 at `41e7bfe`.

**Decisions resolved:**
- [#38](https://github.com/seanyofthedead/Ross-trading/issues/38) (D4: refresh cadence) -- 2-second tick interval, staleness self-check at 5s (2.5x tick), `is_market_hours` is a free function in `core/clock.py`.

**Decoupled by deferral:**
- [#44](https://github.com/seanyofthedead/Ross-trading/issues/44) (A5: journal writer) -- A3 ships with a `DecisionSink` Protocol so it does not block on A5. A5 implements the protocol later. Same pattern A2 used for `UniverseProvider`.
- [#51](https://github.com/seanyofthedead/Ross-trading/issues/51) (Extend Scanner with `scan_with_decisions`) -- rejected-candidate enumeration deferred. A3 emits three decision kinds only: `picked`, `stale_feed`, `feed_gap`. Filed as a follow-up against #42 before this plan was written so the deferral is explicit, not hidden.

---

## Acceptance Criteria (from issue #42)

- [ ] 4-hour replay holds steady (no unbounded queue/memory growth).
- [ ] No scans executed outside the 07:00-11:00 ET window.
- [ ] No staleness suppression before the first quote arrives.
- [ ] `stale_feed` decisions emitted in real time when last-quote staleness exceeds threshold.
- [ ] `feed_gap` rows emitted retrospectively with quote-time duration when `on_gap` fires.
- [ ] Under `VirtualClock`, staleness comparisons measure virtual time elapsed (test harness must drive `clock.sleep(2)` faithfully between ticks).
- [ ] `mypy --strict` passes.
- [ ] All A1 + A2 + Phase-1 tests still pass (no regression).

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Modify | `src/ross_trading/core/clock.py` | Append `is_market_hours(utc_dt)` free function. |
| Create | `src/ross_trading/scanner/decisions.py` | `ScannerDecision` frozen dataclass + `DecisionSink` Protocol. |
| Create | `src/ross_trading/scanner/assembler.py` | `SnapshotAssembler` Protocol -- the replay-determinism boundary A3 consumes. |
| Create | `src/ross_trading/scanner/loop.py` | `ScannerLoop` orchestrator with `run()` + `on_feed_gap()`. |
| Create | `tests/fakes/decision_sink.py` | `FakeDecisionSink` recording emit calls in order. |
| Create | `tests/fakes/snapshot_assembler.py` | `FakeSnapshotAssembler` scripted by `anchor_ts`. |
| Modify | `tests/unit/test_clock.py` | Append `is_market_hours` tests. |
| Create | `tests/unit/test_scanner_decisions.py` | Frozen / slots / Protocol-conformance invariants for decisions + sink. |
| Create | `tests/unit/test_scanner_assembler.py` | Protocol conformance for `SnapshotAssembler` via the fake. |
| Create | `tests/unit/test_scanner_loop.py` | Loop unit tests: market-hours gate, picked emission, staleness, feed_gap, cancellation, determinism. |
| Create | `tests/integration/test_scanner_loop.py` | End-to-end replay test: full window, mid-window disconnect, boundary, pre-first-quote, byte-identical determinism. |

No modifications to existing scanner modules. No dependency changes in `pyproject.toml`. The `scanner/__init__.py` package marker already exists from A1/A2.

## Key Interfaces

All input types live in `src/ross_trading/data/types.py` (`Bar`, `FloatRecord`, `Headline`, `FeedGap`). A2's `Scanner` is consumed read-only.

```python
# src/ross_trading/core/clock.py -- append (no Clock-protocol change)

def is_market_hours(utc_dt: datetime) -> bool:
    """True iff utc_dt falls in [07:00, 11:00) America/New_York on a weekday.

    DST is handled by zoneinfo -- the window is wall-clock ET, so the
    corresponding UTC range shifts twice a year. Holidays are out of
    scope; the universe provider returns empty on those days anyway.
    """


# src/ross_trading/scanner/decisions.py -- new

@dataclass(frozen=True, slots=True)
class ScannerDecision:
    """One row emitted to the journal per tick outcome.

    Three kinds A3 emits:
    - ``picked``: ticker passed all hard filters; ``pick`` carries the
      ranked ScannerPick. ``ticker`` mirrors ``pick.ticker`` for
      query convenience.
    - ``stale_feed``: emitted in real time, once per suppressed tick,
      while ``clock.now() - most_recent_quote_ts > threshold``.
      ``ticker`` is None (loop-wide event). ``reason`` carries a
      human-readable description (e.g. "feed stale by 12.3s").
    - ``feed_gap``: emitted retrospectively when ``ReconnectingProvider``
      fires its ``on_gap`` callback. ``gap_start`` / ``gap_end`` are
      quote-time, not wall-time -- ``gap_end - gap_start`` may be zero
      for pre-first-event gaps; this is intentional.

    A fourth kind (``rejected``) is deferred to #51.
    """

    kind: Literal["picked", "stale_feed", "feed_gap"]
    decision_ts: datetime           # clock.now() at emit
    ticker: str | None              # mirrors pick.ticker for "picked"; None otherwise
    pick: ScannerPick | None        # set when kind == "picked"
    reason: str | None              # human description for stale_feed / feed_gap
    gap_start: datetime | None      # quote-time start, feed_gap only
    gap_end: datetime | None        # quote-time end, feed_gap only


@runtime_checkable
class DecisionSink(Protocol):
    """Where ScannerLoop sends decisions. A5 (#44) implements this."""

    def emit(self, decision: ScannerDecision) -> None: ...


# src/ross_trading/scanner/assembler.py -- new

@runtime_checkable
class SnapshotAssembler(Protocol):
    """Per-tick assembler of A2's ScannerSnapshot map.

    The assembler is the replay-determinism boundary: it reads data
    *as of* ``anchor_ts``. Live: ``anchor_ts == real_clock.now()``.
    Replay: ``anchor_ts == virtual_clock.now()``. Returning the same
    inputs at the same anchor_ts must yield byte-identical output.

    Returns a tuple of ``(snapshot_map, most_recent_quote_ts)``:
    - ``snapshot_map`` -- per-symbol ScannerSnapshot for every symbol
      in ``universe`` for which the assembler has data; symbols not
      yet observed are omitted (Scanner.scan tolerates universe drift).
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


# src/ross_trading/scanner/loop.py -- new

class ScannerLoop:
    """Drive Scanner.scan on a Clock-paced tick.

    Cooperative cancellation: ``run()`` re-raises CancelledError. No
    drain on shutdown -- decisions emitted before cancel land in the
    sink; in-flight assembly is dropped. No subscription cleanup --
    upstream feed lifecycle is the provider owner's concern.
    """

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
    ) -> None: ...

    async def run(self) -> None:
        """Tick forever until cancelled. All waits via injected Clock."""

    def on_feed_gap(self, gap: FeedGap) -> None:
        """Wire as ``on_gap`` of a ReconnectingProvider."""
```

**Tick contract (per `_tick` invocation):**
1. `anchor_ts = clock.now()`. If `not is_market_hours(anchor_ts)`: return early (no-op tick).
2. `universe = await universe_provider.list_symbols(anchor_ts.date())`.
3. `snapshot, most_recent_quote_ts = await snapshot_assembler.assemble(universe, anchor_ts)`.
4. Staleness self-check: if `most_recent_quote_ts is None`, skip the check (pre-first-quote arming). Otherwise compute `staleness = (anchor_ts - most_recent_quote_ts).total_seconds()`. If `staleness > staleness_threshold_s`: emit one `stale_feed` decision and return.
5. `picks = scanner.scan(universe, snapshot)` (A2's pure-sync call). Returns ranked picks 1..N.
6. For each pick in returned order, emit one `picked` decision.

**Run loop:**
```python
async def run(self) -> None:
    while True:
        await self._tick()
        await self._clock.sleep(self._tick_interval_s)
```
The order matters: tick first, then sleep. A loop that sleeps first would skip the first wakeup's tick, which under `VirtualClock` would offset every subsequent assertion by one interval.

**Feed-gap callback:**
```python
def on_feed_gap(self, gap: FeedGap) -> None:
    self._sink.emit(ScannerDecision(
        kind="feed_gap",
        decision_ts=self._clock.now(),
        ticker=None,
        pick=None,
        reason=gap.reason,
        gap_start=gap.start,
        gap_end=gap.end,
    ))
```
Wired by the caller as `ReconnectingProvider(upstream, on_gap=loop.on_feed_gap)`. The loop does not change `data/reconnect.py` -- it just consumes the existing callback hook. `on_feed_gap` runs synchronously inside the reconnect provider's exception handler and must not block; emit-and-return is correct.

## Replay Determinism

A3's deterministic-output contract has two scopes -- one inherited from A2, one new for A3 -- both pinned here so the executor cannot quietly broaden them.

**Inherited (from A2):** `Scanner.scan(universe, snapshot)` is pure-sync. Same `(universe, snapshot)` -> identical `list[ScannerPick]`.

**New (this issue):** Given the same `(snapshot_assembler, universe_provider, decision_sink, clock_start, tick_interval_s, staleness_threshold_s)`, two `ScannerLoop.run()` invocations produce **byte-identical decision streams**. This holds because:

1. **Every wait goes through the injected `Clock`** -- `await self._clock.sleep(...)`, never `await asyncio.sleep(...)` directly. Under `VirtualClock`, `sleep` advances virtual time deterministically (`core/clock.py:63-68`). Direct `asyncio.sleep` calls would couple the loop to wall time and break replay parity. **No `asyncio.sleep` in `loop.py` -- enforced by code review and (transitively) by the integration test that asserts byte-identical streams.**
2. **All decision timestamps come from `clock.now()`** at well-defined emit points. `picked.decision_ts` and `stale_feed.decision_ts` are both the tick's `anchor_ts`. `feed_gap.decision_ts` is `clock.now()` at the moment the `on_feed_gap` callback fires (still deterministic under `VirtualClock` because the test harness controls when the callback runs).
3. **`SnapshotAssembler` honors as-of semantics.** Same `anchor_ts` -> same `(snapshot_map, most_recent_quote_ts)`. The assembler is the boundary; the loop is not. A non-deterministic assembler is a test-fake bug, not a loop bug.
4. **Decision emit order is fixed:** within a tick, accepted picks are emitted in `Scanner.scan` return order (already rank-ascending 1..N from `rank_picks`). Across ticks, `stale_feed` and `feed_gap` are emitted in tick order. The loop never reorders, batches, or coalesces emissions.
5. **`Scanner.scan` is invoked at most once per tick.** Universe lookups are cached in A2's `CachedUniverseProvider` (24-hour TTL); same-day re-fetches return the same `frozenset` (frozensets compare equal regardless of internal hash order).

The integration test `test_replay_full_window_deterministic` asserts byte-equality directly: two `ScannerLoop.run()` invocations against two `FakeDecisionSink` instances produce equal `decisions` lists.

## Cancellation / Shutdown Semantics

Pinned explicitly because the issue is silent:

- `ScannerLoop.run()` is a long-running coroutine. The expected lifecycle is `task = asyncio.create_task(loop.run())` and `task.cancel()` to stop.
- On `asyncio.CancelledError`, `run()` does NOT swallow the exception. No `try/except CancelledError`. The exception propagates out so the supervising task observes cancellation.
- **No drain on shutdown.** Decisions emitted before the cancel point are in the sink; an in-flight `_tick()` whose `await assembler.assemble(...)` is interrupted contributes nothing. The journal layer (A5/A4) owns durability, not the loop.
- **No upstream subscription cleanup.** The loop does not own `MarketDataProvider` connections. The caller that constructed the providers also owns disconnecting them (typically in an `async with` or finally block). A loop that tried to clean up subscriptions would conflict with that ownership.
- **Outside-market-hours = no-op tick, NOT exit.** The loop sleeps `tick_interval_s` and re-checks. A 24/7 process that starts at 4 AM ET will tick silently for three hours until the window opens, then begin emitting. This is the same shape as a cron daemon -- the loop runs, the work skips.

## Defects / Open Questions

These three planning decisions diverge from the literal text of #42 because the spec leaves them under-specified. Each is named here so the executor doesn't paper them over; each gets a one-line proposed answer and a follow-up commitment to file a single bundled spec-fix issue against #42 once A3 ships (matching A1's pattern with #40 and A2's with #41).

**D-A3-1 -- Decision-sink decoupling from A5.** #42 lists A5 (#44) as a hard dependency, but A5 is unmerged and would block A3 indefinitely. **Proposed answer:** ship a `DecisionSink` Protocol in `scanner/decisions.py`. A3 takes a `DecisionSink` constructor arg. Tests use `FakeDecisionSink`. A5's concrete writer just implements the protocol later. Same pattern as A2's `UniverseProvider`. Approved by harness owner before plan was written.

**D-A3-2 -- Snapshot assembly source.** #42 describes "pull latest cached quote" (singular) but `Scanner.scan` consumes `Mapping[str, ScannerSnapshot]` (per-symbol bag of bar + quote + prev_close + baseline + float + headlines). #42 is silent on how the loop builds that map per tick. **Proposed answer:** introduce a `SnapshotAssembler` Protocol with `assemble(universe, anchor_ts) -> (map, most_recent_quote_ts)`. The assembler is the *replay-determinism boundary* -- it reads data as-of `anchor_ts`. Concrete vendor wiring (which provider feeds bars, which feeds quotes, which feeds news, etc.) is deferred to a later atom. Approved.

**D-A3-3 -- Rejected-candidate enumeration deferred to #51.** #42 says "feed both picks and rejected candidates plus their reasons to the journal writer" but A2's `Scanner.scan` returns accepted picks only -- rejection reasons are unrecoverable without re-running A1's filter chain. A3 will NOT reimplement that chain. **Proposed answer:** A3 ships three decision kinds: `picked`, `stale_feed`, `feed_gap`. Issue [#51](https://github.com/seanyofthedead/Ross-trading/issues/51) tracks extending `Scanner` with `scan_with_decisions(...)` so a future PR can add a fourth `rejected` kind. Approved; issue filed before this plan was written.

**D-A3-4 -- `is_market_hours` placement.** #42 specifies `core/clock.py`, but the 07:00-11:00 ET window is *trading-policy*, not a clock primitive. Acceptable per #42's literal text; flagged here so a future refactor can move it to a policy module without surprise. **Proposed answer:** ship in `core/clock.py` as the issue requests. Recorded as a soft ergonomic concern only.

**D-A3-5 -- `stale_feed` re-emission cadence.** #42 says "once per suppressed tick" -- a 30-minute outage produces ~900 `stale_feed` decisions. Intentional auditability, not a bug, but worth pinning so an executor doesn't add deduplication. **Proposed answer:** A3 does NOT dedupe. Downstream (A7's daily report) summarizes if needed.

These five will be bundled into one spec-fix issue against #42 after the PR ships, matching A1's and A2's pattern.

## Conventions (applies to all tasks)

The same three patterns A1 and A2 codified, restated for A3:

- **Imports arrive when needed (not pre-emptively).** Each task's test/source files start with only what that task references; later tasks add to the same files. For multi-task files (`loop.py`, `test_scanner_loop.py`, `test_clock.py`), the Import Evolution Pattern below tracks the growth alphabetically. Rationale: every intermediate state stays lint-clean (no `F401` from premature imports, no `E402` from scattered mid-file imports), so each task's red->green cycle is self-contained.
- **Do not add `# noqa` for ruff rules outside the project's `select` list.** `pyproject.toml` selects `["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]`. Notably absent: `ARG` (unused arguments are fine), `C901` (complexity is fine), `D` (no docstring style enforcement). A suppression like `# noqa: ARG002` will trip `RUF100` (unused noqa).
- **Use ASCII in comments and strings where it reads identically.** Ruff `RUF001`/`RUF002`/`RUF003` flag visually-ambiguous Unicode (e.g., `*` MULTIPLICATION SIGN vs `x`). Prefer `2.5x` over `2.5*`, `>=` over `>=`.

## Import Evolution Pattern (loop.py, test_scanner_loop.py)

The loop module and its primary test file are touched in Tasks 4, 5, and 6. Their growth-bearing import lines evolve alphabetically as follows:

| File | After Task 4 | After Task 5 | After Task 6 |
|---|---|---|---|
| `scanner/loop.py` | `from typing import TYPE_CHECKING`<br>`from ross_trading.core.clock import is_market_hours`<br>`if TYPE_CHECKING:`<br>`  from ross_trading.core.clock import Clock`<br>`  from ross_trading.data.universe import UniverseProvider`<br>`  from ross_trading.scanner.assembler import SnapshotAssembler`<br>`  from ross_trading.scanner.decisions import DecisionSink`<br>`  from ross_trading.scanner.scanner import Scanner` | (unchanged -- staleness reuses already-imported names) | `from typing import TYPE_CHECKING`<br>`from ross_trading.core.clock import is_market_hours`<br>`from ross_trading.scanner.decisions import ScannerDecision`<br>`if TYPE_CHECKING:`<br>`  from ross_trading.core.clock import Clock`<br>`  from ross_trading.data.types import FeedGap`<br>`  from ross_trading.data.universe import UniverseProvider`<br>`  from ross_trading.scanner.assembler import SnapshotAssembler`<br>`  from ross_trading.scanner.decisions import DecisionSink`<br>`  from ross_trading.scanner.scanner import Scanner` |
| `test_scanner_loop.py` | `import asyncio`<br>`from datetime import UTC, date, datetime, timedelta`<br>`from decimal import Decimal`<br>`import pytest`<br>`from ross_trading.core.clock import VirtualClock`<br>`from ross_trading.data.types import Bar, FloatRecord`<br>`from ross_trading.data.universe import CachedUniverseProvider`<br>`from ross_trading.scanner.loop import ScannerLoop`<br>`from ross_trading.scanner.scanner import Scanner`<br>`from ross_trading.scanner.types import ScannerSnapshot`<br>`from tests.fakes.decision_sink import FakeDecisionSink`<br>`from tests.fakes.snapshot_assembler import FakeSnapshotAssembler`<br>`from tests.fakes.universe import FakeUniverseProvider` | (add nothing; reuse imports) | `from ross_trading.data.types import Bar, FeedGap, FloatRecord` (FeedGap added) |

The integration test file `tests/integration/test_scanner_loop.py` is born complete in Task 7 -- no in-task evolution there.

## Effort Estimate

**M-L** (medium-to-large). Three new source files + one modified, six new test files. ~350 LoC source, ~700 LoC tests. Roughly 3-4 hours for an engineer who has read this plan, including running ruff/mypy/pytest after each task and the integration test in Task 7.

---

## Tasks

### Task 1: `is_market_hours` in `core/clock.py`

**Files:**
- Modify: `src/ross_trading/core/clock.py` (append free function)
- Modify: `tests/unit/test_clock.py` (append tests)

- [ ] **Step 1: Append the failing tests to `tests/unit/test_clock.py`**

Append at the bottom of the existing file:

```python
# ----------------------------------------------------------- is_market_hours

# 2025-01-02 (Thu) is winter (EST, UTC-5): 07:00 ET = 12:00 UTC.
# 2025-07-02 (Wed) is summer (EDT, UTC-4): 07:00 ET = 11:00 UTC.
# 2025-01-04 (Sat) is a non-trading weekend day.

WINTER_OPEN_UTC = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)   # 07:00 EST
WINTER_CLOSE_UTC = datetime(2025, 1, 2, 16, 0, tzinfo=UTC)  # 11:00 EST
SUMMER_OPEN_UTC = datetime(2025, 7, 2, 11, 0, tzinfo=UTC)   # 07:00 EDT
SUMMER_CLOSE_UTC = datetime(2025, 7, 2, 15, 0, tzinfo=UTC)  # 11:00 EDT
SATURDAY_NOON_UTC = datetime(2025, 1, 4, 14, 30, tzinfo=UTC)


def test_market_hours_winter_inside_window() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC + timedelta(hours=2)) is True


def test_market_hours_winter_open_inclusive() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC) is True


def test_market_hours_winter_close_exclusive() -> None:
    """The window is [07:00, 11:00) ET -- 11:00:00 itself is outside."""
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_CLOSE_UTC) is False


def test_market_hours_winter_just_before_open() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_OPEN_UTC - timedelta(seconds=1)) is False


def test_market_hours_winter_just_after_close() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(WINTER_CLOSE_UTC + timedelta(seconds=1)) is False


def test_market_hours_summer_inside_window() -> None:
    """DST: window is wall-clock ET, so the corresponding UTC range shifts."""
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(SUMMER_OPEN_UTC + timedelta(hours=2)) is True


def test_market_hours_summer_pre_window_utc_matches_winter_window() -> None:
    """11:30 UTC is 06:30 EDT in summer (outside) but 06:30 EST in winter (outside).

    Sanity check that both DST regimes correctly reject 06:30 ET.
    """
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(datetime(2025, 7, 2, 10, 30, tzinfo=UTC)) is False  # 06:30 EDT
    assert is_market_hours(datetime(2025, 1, 2, 11, 30, tzinfo=UTC)) is False  # 06:30 EST


def test_market_hours_weekend_always_false() -> None:
    from ross_trading.core.clock import is_market_hours
    assert is_market_hours(SATURDAY_NOON_UTC) is False


def test_market_hours_naive_datetime_raises() -> None:
    """Tz-naive input is a programming error; refuse rather than guess."""
    from ross_trading.core.clock import is_market_hours
    with pytest.raises(ValueError, match="tz-aware"):
        is_market_hours(datetime(2025, 1, 2, 14, 0))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_clock.py -v -k market_hours`
Expected: `ImportError` (the function does not exist yet) on every new test.

- [ ] **Step 3: Append the function to `src/ross_trading/core/clock.py`**

First, update the import block at the top of `clock.py`. The file currently imports `from datetime import UTC, datetime, timedelta` and `from typing import Protocol, runtime_checkable`. Add `time` from `datetime` and `ZoneInfo`:

```python
from datetime import UTC, datetime, time, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo
```

Then append the function at the bottom of the file:

```python
_NY_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = time(7, 0)   # inclusive
_MARKET_CLOSE = time(11, 0)  # exclusive


def is_market_hours(utc_dt: datetime) -> bool:
    """True iff ``utc_dt`` falls in [07:00, 11:00) America/New_York on a weekday.

    The window is wall-clock ET (matches Cameron's pre-market + first-hour
    momentum window per #38). DST is handled by zoneinfo. Holidays are out
    of scope -- the universe provider returns empty on those days, so
    out-of-band gating here is unnecessary.
    """
    if utc_dt.tzinfo is None:
        msg = "is_market_hours requires a tz-aware datetime"
        raise ValueError(msg)
    local = utc_dt.astimezone(_NY_TZ)
    if local.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    return _MARKET_OPEN <= local.time() < _MARKET_CLOSE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_clock.py -v -k market_hours`
Expected: 9 passed.

- [ ] **Step 5: Local gate (full project)**

Run, confirm each is clean, then proceed:
```bash
ruff check .
mypy src tests
pytest
```

If anything outside the new test cases breaks, **stop and surface** before fixing -- likely indicates a plan defect.

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/core/clock.py tests/unit/test_clock.py
git commit -m "feat(core): is_market_hours free function for ET trading window (#42)"
```

---

### Task 2: `ScannerDecision` + `DecisionSink` Protocol + `FakeDecisionSink`

**Files:**
- Create: `src/ross_trading/scanner/decisions.py`
- Create: `tests/fakes/decision_sink.py`
- Create: `tests/unit/test_scanner_decisions.py`

- [ ] **Step 1: Write the failing test file `tests/unit/test_scanner_decisions.py`**

```python
"""Atom A3 -- ScannerDecision + DecisionSink (issue #42)."""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.scanner.decisions import DecisionSink, ScannerDecision
from ross_trading.scanner.types import ScannerPick
from tests.fakes.decision_sink import FakeDecisionSink

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _pick() -> ScannerPick:
    return ScannerPick(
        ticker="AVTX",
        ts=T0,
        rel_volume=Decimal("8.5"),
        pct_change=Decimal("18.4"),
        price=Decimal("5.50"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=2,
        rank=1,
    )


def _picked() -> ScannerDecision:
    p = _pick()
    return ScannerDecision(
        kind="picked",
        decision_ts=T0,
        ticker=p.ticker,
        pick=p,
        reason=None,
        gap_start=None,
        gap_end=None,
    )


def _stale() -> ScannerDecision:
    return ScannerDecision(
        kind="stale_feed",
        decision_ts=T0,
        ticker=None,
        pick=None,
        reason="feed stale by 12.3s",
        gap_start=None,
        gap_end=None,
    )


def _gap() -> ScannerDecision:
    return ScannerDecision(
        kind="feed_gap",
        decision_ts=T0,
        ticker=None,
        pick=None,
        reason="upstream socket reset",
        gap_start=T0 - timedelta(seconds=30),
        gap_end=T0,
    )


# --------------------------------------------------------------- ScannerDecision


def test_decision_is_frozen() -> None:
    d = _picked()
    with pytest.raises(FrozenInstanceError):
        d.kind = "stale_feed"  # type: ignore[misc]


def test_decision_has_slots() -> None:
    assert "__slots__" in ScannerDecision.__dict__


def test_decision_picklable_roundtrip() -> None:
    for d in (_picked(), _stale(), _gap()):
        revived = pickle.loads(pickle.dumps(d))  # noqa: S301
        assert revived == d


def test_picked_carries_pick_and_mirrors_ticker() -> None:
    d = _picked()
    assert d.pick is not None
    assert d.ticker == d.pick.ticker


def test_stale_feed_has_no_ticker_no_pick_and_a_reason() -> None:
    d = _stale()
    assert d.ticker is None
    assert d.pick is None
    assert d.reason is not None
    assert d.gap_start is None
    assert d.gap_end is None


def test_feed_gap_carries_quote_time_window() -> None:
    d = _gap()
    assert d.kind == "feed_gap"
    assert d.gap_start is not None
    assert d.gap_end is not None
    assert d.gap_end > d.gap_start


# ------------------------------------------------------------ DecisionSink Protocol


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeDecisionSink(), DecisionSink)


def test_fake_records_emit_calls_in_order() -> None:
    sink = FakeDecisionSink()
    a, b = _picked(), _stale()
    sink.emit(a)
    sink.emit(b)
    assert sink.decisions == [a, b]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_decisions.py -v`
Expected: `ImportError` for `ross_trading.scanner.decisions` and `tests.fakes.decision_sink`.

- [ ] **Step 3: Create `src/ross_trading/scanner/decisions.py`**

```python
"""Decision rows emitted by the scanner loop.

Phase 2 -- Atom A3 (#42). ``ScannerDecision`` is the unit the loop
writes to its sink per tick outcome. Three kinds for now -- ``picked``,
``stale_feed``, ``feed_gap`` -- with a fourth (``rejected``) deferred
to #51. ``DecisionSink`` is the Protocol A5 (#44) implements; A3 ships
with a fake sink so it does not block on A5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from ross_trading.scanner.types import ScannerPick


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    """One row emitted to the journal per tick outcome.

    Three kinds:
    - ``picked``: ticker passed all hard filters; ``pick`` carries
      the ranked ScannerPick; ``ticker`` mirrors ``pick.ticker``.
    - ``stale_feed``: emitted in real time, once per suppressed tick;
      ``ticker`` is None (loop-wide); ``reason`` is human-readable.
    - ``feed_gap``: emitted retrospectively when the reconnect provider
      fires its on_gap callback; ``gap_start`` / ``gap_end`` are
      quote-time, not wall-time.
    """

    kind: Literal["picked", "stale_feed", "feed_gap"]
    decision_ts: datetime
    ticker: str | None
    pick: ScannerPick | None
    reason: str | None
    gap_start: datetime | None
    gap_end: datetime | None


@runtime_checkable
class DecisionSink(Protocol):
    """Where ScannerLoop writes decisions. A5 (#44) implements this."""

    def emit(self, decision: ScannerDecision) -> None: ...
```

- [ ] **Step 4: Create `tests/fakes/decision_sink.py`**

```python
"""In-memory DecisionSink for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ross_trading.scanner.decisions import ScannerDecision


class FakeDecisionSink:
    """Records every ``emit`` call in order on ``self.decisions``."""

    def __init__(self) -> None:
        self.decisions: list[ScannerDecision] = []

    def emit(self, decision: ScannerDecision) -> None:
        self.decisions.append(decision)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_decisions.py -v`
Expected: 8 passed.

- [ ] **Step 6: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 7: Commit**

```bash
git add src/ross_trading/scanner/decisions.py tests/fakes/decision_sink.py tests/unit/test_scanner_decisions.py
git commit -m "feat(scanner): ScannerDecision and DecisionSink protocol (#42)"
```

---

### Task 3: `SnapshotAssembler` Protocol + `FakeSnapshotAssembler`

**Files:**
- Create: `src/ross_trading/scanner/assembler.py`
- Create: `tests/fakes/snapshot_assembler.py`
- Create: `tests/unit/test_scanner_assembler.py`

- [ ] **Step 1: Write the failing test file `tests/unit/test_scanner_assembler.py`**

```python
"""Atom A3 -- SnapshotAssembler protocol (issue #42)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord
from ross_trading.scanner.assembler import SnapshotAssembler
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _snap(symbol: str = "AVTX") -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol,
        ts=T0,
        timeframe="M1",
        open=Decimal("5.30"),
        high=Decimal("5.55"),
        low=Decimal("5.25"),
        close=Decimal("5.50"),
        volume=900_000,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal("5.52"),
        prev_close=Decimal("4.80"),
        baseline_30d=Decimal("100000"),
        float_record=FloatRecord(
            ticker=symbol,
            as_of=date(2026, 4, 26),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        headlines=(),
    )


def test_fake_satisfies_protocol() -> None:
    fake = FakeSnapshotAssembler({})
    assert isinstance(fake, SnapshotAssembler)


async def test_fake_returns_scripted_map_at_anchor_ts() -> None:
    snap = _snap()
    fake = FakeSnapshotAssembler({T0: ({"AVTX": snap}, T0)})
    universe = frozenset(["AVTX", "BBAI"])
    snapshot, most_recent = await fake.assemble(universe, T0)
    assert snapshot == {"AVTX": snap}
    assert most_recent == T0


async def test_fake_supports_pre_first_quote() -> None:
    """most_recent_quote_ts is None until the first quote arrives."""
    fake = FakeSnapshotAssembler({T0: ({}, None)})
    snapshot, most_recent = await fake.assemble(frozenset(["AVTX"]), T0)
    assert snapshot == {}
    assert most_recent is None


async def test_fake_raises_on_unscripted_anchor() -> None:
    """Any anchor_ts the test forgot to script is a programming error."""
    fake = FakeSnapshotAssembler({})
    with pytest.raises(KeyError):
        await fake.assemble(frozenset(["AVTX"]), T0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_assembler.py -v`
Expected: `ImportError` for `ross_trading.scanner.assembler` and `tests.fakes.snapshot_assembler`.

- [ ] **Step 3: Create `src/ross_trading/scanner/assembler.py`**

```python
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
```

- [ ] **Step 4: Create `tests/fakes/snapshot_assembler.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_assembler.py -v`
Expected: 4 passed.

- [ ] **Step 6: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 7: Commit**

```bash
git add src/ross_trading/scanner/assembler.py tests/fakes/snapshot_assembler.py tests/unit/test_scanner_assembler.py
git commit -m "feat(scanner): SnapshotAssembler protocol and fake (#42)"
```

---

### Task 4: `ScannerLoop` -- market-hours gate, scan dispatch, picked emission, cancellation

**Files:**
- Create: `src/ross_trading/scanner/loop.py`
- Create: `tests/unit/test_scanner_loop.py`

This task lays the loop's structural skeleton. Staleness lands in Task 5; feed_gap lands in Task 6.

- [ ] **Step 1: Write the failing test file `tests/unit/test_scanner_loop.py`**

```python
"""Atom A3 -- ScannerLoop unit tests (issue #42)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.types import Bar, FloatRecord
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.decision_sink import FakeDecisionSink
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler
from tests.fakes.universe import FakeUniverseProvider

# 2025-01-02 (Thursday, EST). 14:30 UTC = 09:30 ET (inside window).
INSIDE_TS = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
OUTSIDE_TS = datetime(2025, 1, 2, 6, 0, tzinfo=UTC)  # 01:00 ET


def _snap(symbol: str, last: str = "5.50", prev_close: str = "5.00") -> ScannerSnapshot:
    bar = Bar(
        symbol=symbol,
        ts=INSIDE_TS,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal(last),
        low=Decimal("4.95"),
        close=Decimal(last),
        volume=5_000_000,
    )
    return ScannerSnapshot(
        bar=bar,
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol,
            as_of=date(2025, 1, 2),
            float_shares=8_500_000,
            shares_outstanding=12_000_000,
            source="test",
        ),
        headlines=(),
    )


def _build_loop(
    *,
    start: datetime,
    by_anchor: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]],
    universe: frozenset[str] = frozenset(["AVTX"]),
    tick_interval_s: float = 2.0,
) -> tuple[ScannerLoop, VirtualClock, FakeDecisionSink, FakeSnapshotAssembler]:
    clock = VirtualClock(start)
    sink = FakeDecisionSink()
    assembler = FakeSnapshotAssembler(by_anchor)
    upstream = FakeUniverseProvider({start.date(): universe})
    cached = CachedUniverseProvider(upstream, clock=clock)
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=cached,
        snapshot_assembler=assembler,
        decision_sink=sink,
        clock=clock,
        tick_interval_s=tick_interval_s,
    )
    return loop, clock, sink, assembler


async def _run_for_n_ticks(loop: ScannerLoop, n: int) -> None:
    """Spin the loop for exactly n ticks then cancel cleanly."""
    task = asyncio.create_task(loop.run())
    for _ in range(n):
        await asyncio.sleep(0)  # let _tick start
        await asyncio.sleep(0)  # let clock.sleep yield
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ----------------------------------------------------------- market-hours gate


async def test_outside_market_hours_does_not_call_assembler() -> None:
    loop, _, sink, assembler = _build_loop(start=OUTSIDE_TS, by_anchor={})
    await _run_for_n_ticks(loop, n=3)
    assert assembler.calls == []
    assert sink.decisions == []


async def test_outside_market_hours_loop_keeps_running_not_exits() -> None:
    """Out-of-window ticks are no-ops, not termination."""
    loop, clock, _, assembler = _build_loop(start=OUTSIDE_TS, by_anchor={})
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not task.done()  # still running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    del clock, assembler


# ------------------------------------------------------ inside-window happy path


async def test_inside_market_hours_calls_assembler_and_emits_picked() -> None:
    snap = _snap("AVTX")
    loop, _, sink, assembler = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert assembler.calls == [INSIDE_TS]
    assert len(sink.decisions) == 1
    d = sink.decisions[0]
    assert d.kind == "picked"
    assert d.ticker == "AVTX"
    assert d.pick is not None
    assert d.pick.rank == 1
    assert d.decision_ts == INSIDE_TS


async def test_no_picks_emits_no_decisions() -> None:
    """Empty Scanner result -> empty decision stream for that tick."""
    # Use a snap that fails the rel-volume filter (volume too low).
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


async def test_multiple_picks_emitted_in_rank_order() -> None:
    a, b, c = _snap("AAA", last="5.50"), _snap("BBB", last="6.50"), _snap("CCC", last="6.00")
    snapshot_map = {"AAA": a, "BBB": b, "CCC": c}
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: (snapshot_map, INSIDE_TS)},
        universe=frozenset(["AAA", "BBB", "CCC"]),
    )
    await _run_for_n_ticks(loop, n=1)
    # Sorted by pct_change desc: BBB (+30%), CCC (+20%), AAA (+10%).
    assert [d.ticker for d in sink.decisions] == ["BBB", "CCC", "AAA"]
    assert [d.pick.rank for d in sink.decisions if d.pick is not None] == [1, 2, 3]


# ----------------------------------------------------------------- cancellation


async def test_cancellation_reraises_cancelled_error() -> None:
    loop, _, _, _ = _build_loop(start=OUTSIDE_TS, by_anchor={})
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancellation_does_not_swallow() -> None:
    """Even mid-tick cancellation propagates without try/except suppression."""
    snap = _snap("AVTX")
    loop, _, _, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, INSIDE_TS)},
    )
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ------------------------------------------------------------------ injection


async def test_loop_uses_injected_clock_sleep_not_asyncio_sleep() -> None:
    """VirtualClock.sleep advances virtual time. If the loop used asyncio.sleep
    directly, virtual time would not advance and the second tick would re-fire
    at the same anchor_ts -- this would surface as a duplicate calls[0] entry
    or a KeyError on the un-scripted second anchor.
    """
    snap = _snap("AVTX")
    snap_t2 = _snap("AVTX")
    loop, _, sink, assembler = _build_loop(
        start=INSIDE_TS,
        by_anchor={
            INSIDE_TS: ({"AVTX": snap}, INSIDE_TS),
            INSIDE_TS.replace(second=2): ({"AVTX": snap_t2}, INSIDE_TS.replace(second=2)),
        },
    )
    await _run_for_n_ticks(loop, n=2)
    assert assembler.calls == [INSIDE_TS, INSIDE_TS.replace(second=2)]
    assert len(sink.decisions) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: `ImportError` for `ross_trading.scanner.loop`.

- [ ] **Step 3: Create `src/ross_trading/scanner/loop.py`**

```python
"""Async tick driver for the scanner.

Phase 2 -- Atom A3 (#42). Long-running coroutine that paces
:meth:`Scanner.scan` on a Clock and emits per-pick decisions to an
injected :class:`DecisionSink`. The loop owns no provider I/O --
the injected :class:`SnapshotAssembler` is the replay-determinism
boundary.

Cancellation: ``run()`` re-raises CancelledError. No drain on
shutdown, no upstream subscription cleanup. Outside-market-hours
ticks are no-ops, not exits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ross_trading.core.clock import is_market_hours
from ross_trading.scanner.decisions import ScannerDecision

if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner


class ScannerLoop:
    """Drive Scanner.scan on a Clock-paced tick."""

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
        snapshot, _most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
        # Staleness self-check lands in Task 5; for now scan unconditionally.
        picks = self._scanner.scan(universe, snapshot)
        for pick in picks:
            self._sink.emit(
                ScannerDecision(
                    kind="picked",
                    decision_ts=anchor_ts,
                    ticker=pick.ticker,
                    pick=pick,
                    reason=None,
                    gap_start=None,
                    gap_end=None,
                )
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: 8 passed.

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/loop.py tests/unit/test_scanner_loop.py
git commit -m "feat(scanner): ScannerLoop with market-hours gate and picked emission (#42)"
```

---

### Task 5: Staleness self-check + `stale_feed` emission

**Files:**
- Modify: `src/ross_trading/scanner/loop.py` (extend `_tick`)
- Modify: `tests/unit/test_scanner_loop.py` (append staleness tests)

- [ ] **Step 1: Append the failing tests to `tests/unit/test_scanner_loop.py`**

Append at the bottom of the file:

```python
# ------------------------------------------------------------------ staleness


async def test_pre_first_quote_does_not_suppress_scan() -> None:
    """most_recent_quote_ts=None -> staleness check is skipped."""
    snap = _snap("AVTX")
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, None)},  # None = pre-first-quote
    )
    await _run_for_n_ticks(loop, n=1)
    assert len(sink.decisions) == 1
    assert sink.decisions[0].kind == "picked"


async def test_stale_feed_suppresses_scan_and_emits_stale_decision() -> None:
    """anchor_ts - most_recent_quote_ts > threshold -> emit stale_feed, skip scan."""
    snap = _snap("AVTX")
    stale_quote_ts = INSIDE_TS.replace(second=0) - timedelta(seconds=30)
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, stale_quote_ts)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert len(sink.decisions) == 1
    d = sink.decisions[0]
    assert d.kind == "stale_feed"
    assert d.ticker is None
    assert d.pick is None
    assert d.decision_ts == INSIDE_TS
    assert d.reason is not None and "30." in d.reason  # human-readable seconds


async def test_fresh_feed_within_threshold_runs_scan() -> None:
    """anchor_ts - most_recent_quote_ts <= threshold -> normal scan."""
    snap = _snap("AVTX")
    fresh_quote_ts = INSIDE_TS - timedelta(seconds=2)  # <5s threshold
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={INSIDE_TS: ({"AVTX": snap}, fresh_quote_ts)},
    )
    await _run_for_n_ticks(loop, n=1)
    assert len(sink.decisions) == 1
    assert sink.decisions[0].kind == "picked"


async def test_stale_feed_emitted_each_tick_no_dedup() -> None:
    """A persistent stale feed yields one stale_feed per tick (no dedup)."""
    snap = _snap("AVTX")
    stale_quote_ts = INSIDE_TS - timedelta(minutes=5)
    second_anchor = INSIDE_TS.replace(second=2)
    loop, _, sink, _ = _build_loop(
        start=INSIDE_TS,
        by_anchor={
            INSIDE_TS: ({"AVTX": snap}, stale_quote_ts),
            second_anchor: ({"AVTX": snap}, stale_quote_ts),
        },
    )
    await _run_for_n_ticks(loop, n=2)
    assert [d.kind for d in sink.decisions] == ["stale_feed", "stale_feed"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_loop.py -v -k "staleness or stale or pre_first or fresh"`
Expected: failures (`stale_feed` decisions never emitted; `picked` emitted instead).

- [ ] **Step 3: Update `src/ross_trading/scanner/loop.py` `_tick` method**

Replace the body of `_tick` from:

```python
    async def _tick(self) -> None:
        anchor_ts = self._clock.now()
        if not is_market_hours(anchor_ts):
            return
        universe = await self._universe_provider.list_symbols(anchor_ts.date())
        snapshot, _most_recent_quote_ts = await self._assembler.assemble(universe, anchor_ts)
        # Staleness self-check lands in Task 5; for now scan unconditionally.
        picks = self._scanner.scan(universe, snapshot)
        for pick in picks:
            self._sink.emit(
                ScannerDecision(
                    kind="picked",
                    decision_ts=anchor_ts,
                    ticker=pick.ticker,
                    pick=pick,
                    reason=None,
                    gap_start=None,
                    gap_end=None,
                )
            )
```

to:

```python
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
        picks = self._scanner.scan(universe, snapshot)
        for pick in picks:
            self._sink.emit(
                ScannerDecision(
                    kind="picked",
                    decision_ts=anchor_ts,
                    ticker=pick.ticker,
                    pick=pick,
                    reason=None,
                    gap_start=None,
                    gap_end=None,
                )
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: 12 passed (8 from Task 4 + 4 staleness tests).

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/loop.py tests/unit/test_scanner_loop.py
git commit -m "feat(scanner): staleness self-check emits stale_feed decisions (#42)"
```

---

### Task 6: `feed_gap` retrospective emission via `on_feed_gap`

**Files:**
- Modify: `src/ross_trading/scanner/loop.py` (add `on_feed_gap`, import `FeedGap` + `ScannerDecision`)
- Modify: `tests/unit/test_scanner_loop.py` (append feed_gap tests + import FeedGap)

- [ ] **Step 1: Append the failing tests to `tests/unit/test_scanner_loop.py`**

First, add `FeedGap` to the existing import-from-`ross_trading.data.types` line so it reads:

```python
from ross_trading.data.types import Bar, FeedGap, FloatRecord
```

Then append at the bottom of the file:

```python
# ------------------------------------------------------------------- feed_gap


async def test_on_feed_gap_emits_feed_gap_decision() -> None:
    loop, clock, sink, _ = _build_loop(start=INSIDE_TS, by_anchor={})
    gap = FeedGap(
        symbol=None,
        start=INSIDE_TS - timedelta(seconds=30),
        end=INSIDE_TS,
        reason="upstream socket reset",
    )
    loop.on_feed_gap(gap)
    assert len(sink.decisions) == 1
    d = sink.decisions[0]
    assert d.kind == "feed_gap"
    assert d.ticker is None
    assert d.pick is None
    assert d.gap_start == gap.start
    assert d.gap_end == gap.end
    assert d.reason == "upstream socket reset"
    assert d.decision_ts == clock.now()


async def test_on_feed_gap_quote_time_duration_reflects_inputs() -> None:
    """gap_end - gap_start is exactly the input window (no clock-time mixing)."""
    loop, _, sink, _ = _build_loop(start=INSIDE_TS, by_anchor={})
    gap_start = INSIDE_TS - timedelta(minutes=2)
    gap_end = INSIDE_TS - timedelta(minutes=1)
    loop.on_feed_gap(FeedGap(symbol=None, start=gap_start, end=gap_end, reason="x"))
    d = sink.decisions[0]
    assert d.gap_end - d.gap_start == timedelta(minutes=1)


async def test_on_feed_gap_does_not_block_or_call_async_path() -> None:
    """Sync entry point -- callable from inside ReconnectingProvider's
    sync exception handler. The ts comes from clock.now(); no new tick
    is forced.
    """
    loop, _, sink, assembler = _build_loop(start=INSIDE_TS, by_anchor={})
    loop.on_feed_gap(FeedGap(symbol=None, start=INSIDE_TS, end=INSIDE_TS, reason="x"))
    assert len(sink.decisions) == 1
    assert assembler.calls == []  # no scan triggered
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_loop.py -v -k feed_gap`
Expected: failures with `AttributeError: 'ScannerLoop' object has no attribute 'on_feed_gap'`.

- [ ] **Step 3: Update `src/ross_trading/scanner/loop.py`**

First grow the imports. The current import block is:

```python
from typing import TYPE_CHECKING

from ross_trading.core.clock import is_market_hours
from ross_trading.scanner.decisions import ScannerDecision

if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner
```

Add `FeedGap` to the `TYPE_CHECKING` block:

```python
if TYPE_CHECKING:
    from ross_trading.core.clock import Clock
    from ross_trading.data.types import FeedGap
    from ross_trading.data.universe import UniverseProvider
    from ross_trading.scanner.assembler import SnapshotAssembler
    from ross_trading.scanner.decisions import DecisionSink
    from ross_trading.scanner.scanner import Scanner
```

Then append the method to the `ScannerLoop` class (after `_tick`):

```python
    def on_feed_gap(self, gap: FeedGap) -> None:
        """Receive a retrospective FeedGap and emit a feed_gap decision.

        Wired by callers as ``ReconnectingProvider(upstream, on_gap=loop.on_feed_gap)``.
        Sync because ReconnectingProvider's callback runs synchronously
        inside its FeedDisconnected handler -- emit-and-return is correct.
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_scanner_loop.py -v`
Expected: 15 passed (12 from Tasks 4-5 + 3 feed_gap tests).

- [ ] **Step 5: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/ross_trading/scanner/loop.py tests/unit/test_scanner_loop.py
git commit -m "feat(scanner): on_feed_gap callback emits retrospective feed_gap decisions (#42)"
```

---

### Task 7: Integration test (full replay window + byte-identical determinism)

**Files:**
- Create: `tests/integration/test_scanner_loop.py`

This is the load-bearing replay test. It exercises the loop end-to-end against `VirtualClock`, `CachedUniverseProvider`, and the fakes -- mirroring `tests/integration/test_replay_day.py`'s shape.

- [ ] **Step 1: Write the failing integration test file**

```python
"""Atom A3 -- ScannerLoop integration / replay test (issue #42).

Mirrors tests/integration/test_replay_day.py: drives the loop on a
VirtualClock through a synthetic 4-hour-window day, asserts no scans
outside the window, asserts byte-identical decision streams across
two runs (replay-determinism contract), and exercises the
pre-first-quote / mid-window-disconnect boundary cases from #42's
acceptance.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.core.clock import VirtualClock
from ross_trading.data.types import Bar, FeedGap, FloatRecord
from ross_trading.data.universe import CachedUniverseProvider
from ross_trading.scanner.loop import ScannerLoop
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot
from tests.fakes.decision_sink import FakeDecisionSink
from tests.fakes.snapshot_assembler import FakeSnapshotAssembler
from tests.fakes.universe import FakeUniverseProvider

pytestmark = pytest.mark.integration

# 2025-01-02 (Thursday, EST, no DST). Window: 12:00 UTC (07:00 ET) to 16:00 UTC (11:00 ET).
DAY = date(2025, 1, 2)
WINDOW_OPEN = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)   # 07:00 ET
WINDOW_CLOSE = datetime(2025, 1, 2, 16, 0, tzinfo=UTC)  # 11:00 ET (exclusive)


def _bar(symbol: str, ts: datetime, close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol=symbol, ts=ts, timeframe="M1",
        open=Decimal("5.00"), high=Decimal(close),
        low=Decimal("4.95"), close=Decimal(close), volume=volume,
    )


def _snap(symbol: str, ts: datetime, last: str = "5.50") -> ScannerSnapshot:
    return ScannerSnapshot(
        bar=_bar(symbol, ts, close=last),
        last=Decimal(last),
        prev_close=Decimal("5.00"),
        baseline_30d=Decimal("1000000"),
        float_record=FloatRecord(
            ticker=symbol, as_of=DAY, float_shares=8_500_000,
            shares_outstanding=12_000_000, source="test",
        ),
        headlines=(),
    )


def _script_window(
    *,
    start: datetime,
    end: datetime,
    tick_s: float,
    snap_for: dict[datetime, dict[str, ScannerSnapshot]],
    quote_ts_for: dict[datetime, datetime | None],
) -> dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]]:
    """Build an anchor->(snapshot_map, most_recent_quote_ts) script for every tick in [start, end).

    Tests pass per-anchor overrides via ``snap_for`` / ``quote_ts_for``;
    unspecified anchors get an empty snapshot and None quote_ts.
    """
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    cur = start
    while cur < end:
        script[cur] = (snap_for.get(cur, {}), quote_ts_for.get(cur))
        cur = cur + timedelta(seconds=tick_s)
    return script


async def _drive_until(loop: ScannerLoop, clock: VirtualClock, until: datetime) -> None:
    """Run the loop until clock.now() >= until, then cancel cleanly."""
    task = asyncio.create_task(loop.run())
    while clock.now() < until:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _build(
    *,
    start: datetime,
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]],
    universe: frozenset[str] = frozenset(["AVTX"]),
    tick_s: float = 2.0,
    staleness_threshold_s: float = 5.0,
) -> tuple[ScannerLoop, VirtualClock, FakeDecisionSink]:
    clock = VirtualClock(start)
    sink = FakeDecisionSink()
    upstream = FakeUniverseProvider({DAY: universe})
    loop = ScannerLoop(
        scanner=Scanner(),
        universe_provider=CachedUniverseProvider(upstream, clock=clock),
        snapshot_assembler=FakeSnapshotAssembler(script),
        decision_sink=sink,
        clock=clock,
        tick_interval_s=tick_s,
        staleness_threshold_s=staleness_threshold_s,
    )
    return loop, clock, sink


# ------------------------------------------------------------- market boundary


async def test_no_scans_outside_07_to_11_et() -> None:
    """Start one hour before window open, drive past close. No scans outside."""
    pre_open = WINDOW_OPEN - timedelta(hours=1)
    snap = _snap("AVTX", WINDOW_OPEN)
    # Inside the window every tick, return a passing snap with fresh quote.
    script = _script_window(
        start=pre_open, end=WINDOW_CLOSE + timedelta(hours=1), tick_s=2.0,
        snap_for={
            t: {"AVTX": _snap("AVTX", t)}
            for t in (pre_open + timedelta(seconds=2 * i) for i in range(7200))
            if WINDOW_OPEN <= t < WINDOW_CLOSE
        },
        quote_ts_for={
            t: t
            for t in (pre_open + timedelta(seconds=2 * i) for i in range(7200))
            if WINDOW_OPEN <= t < WINDOW_CLOSE
        },
    )
    del snap
    loop, clock, sink = _build(start=pre_open, script=script)
    await _drive_until(loop, clock, WINDOW_CLOSE + timedelta(minutes=5))
    # Every decision must fall in [WINDOW_OPEN, WINDOW_CLOSE).
    for d in sink.decisions:
        assert WINDOW_OPEN <= d.decision_ts < WINDOW_CLOSE
    # And we should have *some* picks (window contains 7200 ticks).
    assert any(d.kind == "picked" for d in sink.decisions)


# ------------------------------------------------------------- pre-first-quote


async def test_pre_first_quote_no_stale_feed() -> None:
    """Before the first quote, no staleness suppression should fire."""
    # Drive 5 ticks at the open with most_recent_quote_ts=None on each.
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    for i in range(5):
        t = WINDOW_OPEN + timedelta(seconds=2 * i)
        script[t] = ({}, None)  # empty snapshot, pre-first-quote
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    await _drive_until(loop, clock, WINDOW_OPEN + timedelta(seconds=10))
    assert all(d.kind != "stale_feed" for d in sink.decisions)


# ------------------------------------------------------------- mid-window disconnect


async def test_mid_window_disconnect_emits_feed_gap() -> None:
    """A FeedGap delivered via on_feed_gap during the window emits one feed_gap row."""
    snap = _snap("AVTX", WINDOW_OPEN)
    script = {WINDOW_OPEN: ({"AVTX": snap}, WINDOW_OPEN)}
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    # Tick once, then synchronously deliver the gap.
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    loop.on_feed_gap(FeedGap(
        symbol=None,
        start=WINDOW_OPEN - timedelta(seconds=30),
        end=WINDOW_OPEN,
        reason="upstream socket reset",
    ))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    del clock
    feed_gaps = [d for d in sink.decisions if d.kind == "feed_gap"]
    assert len(feed_gaps) == 1
    assert feed_gaps[0].gap_end - feed_gaps[0].gap_start == timedelta(seconds=30)


# ------------------------------------------------------------- byte-identical replay


async def test_two_runs_produce_byte_identical_decision_streams() -> None:
    """The replay-determinism contract: same inputs -> equal decision lists."""
    snap_pass = _snap("AVTX", WINDOW_OPEN, last="5.50")
    snap_fail = _snap("AVTX", WINDOW_OPEN.replace(second=4), last="5.05")  # +1% only -> reject
    script = {
        WINDOW_OPEN: ({"AVTX": snap_pass}, WINDOW_OPEN),
        WINDOW_OPEN.replace(second=2): ({"AVTX": snap_pass}, WINDOW_OPEN.replace(second=2)),
        WINDOW_OPEN.replace(second=4): ({"AVTX": snap_fail}, WINDOW_OPEN.replace(second=4)),
    }
    loop_a, clock_a, sink_a = _build(start=WINDOW_OPEN, script=dict(script))
    loop_b, clock_b, sink_b = _build(start=WINDOW_OPEN, script=dict(script))
    await _drive_until(loop_a, clock_a, WINDOW_OPEN + timedelta(seconds=6))
    await _drive_until(loop_b, clock_b, WINDOW_OPEN + timedelta(seconds=6))
    assert sink_a.decisions == sink_b.decisions
    # Plus a positive sanity: at least one pick fired in the run.
    assert any(d.kind == "picked" for d in sink_a.decisions)


# ----------------------------------------------------- steady-state memory shape


async def test_steady_state_no_unbounded_growth() -> None:
    """Sanity: 100-tick run produces exactly the expected count of decisions.

    The loop must not buffer or coalesce. One pick per qualifying tick;
    one stale_feed per stale tick; nothing else is queued.
    """
    snap = _snap("AVTX", WINDOW_OPEN)
    script: dict[datetime, tuple[dict[str, ScannerSnapshot], datetime | None]] = {}
    for i in range(100):
        t = WINDOW_OPEN + timedelta(seconds=2 * i)
        script[t] = ({"AVTX": snap}, t)
    loop, clock, sink = _build(start=WINDOW_OPEN, script=script)
    await _drive_until(loop, clock, WINDOW_OPEN + timedelta(seconds=200))
    assert len(sink.decisions) == 100  # one picked per tick
    assert all(d.kind == "picked" for d in sink.decisions)
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/integration/test_scanner_loop.py -v`
Expected: 5 passed.

If the boundary test (`test_no_scans_outside_07_to_11_et`) is too slow on CI (it scripts 7200 anchors), tighten the window to `WINDOW_OPEN + timedelta(minutes=2)` and verify the same property still holds at the boundary -- but only after confirming the long form passes locally.

- [ ] **Step 3: Local gate (full project)**

```bash
ruff check .
mypy src tests
pytest
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_scanner_loop.py
git commit -m "test(scanner): integration replay test for ScannerLoop (#42)"
```

---

### Task 8: Static checks + full suite (verification gate)

This is a defensive sweep -- if the per-task gate in Tasks 1-7 was honored, this is a no-op. If anything drifted, fix here before opening the PR.

- [ ] **Step 1: Run ruff**

Run: `ruff check .`
Expected: `All checks passed!`

- [ ] **Step 2: Run mypy strict**

Run: `mypy src tests`
Expected: `Success: no issues found in <N> source files` where `<N>` is the A2-baseline count plus the new files. Verify the count is sensible rather than asserting an exact number.

- [ ] **Step 3: Run the full pytest suite**

Run: `pytest`
Expected: every Phase-1 + A1 + A2 test still passes, plus the new A3 cases. No regressions, no skips, no warnings.

- [ ] **Step 4: If anything fails, fix and re-run.** A failure here likely indicates a plan defect -- surface to the harness owner before patching.

- [ ] **Step 5: No new commit needed if Tasks 1-7 commits are clean.** If lint or mypy turned up something, the fix lands as a `chore` commit:

```bash
git add -p
git commit -m "chore(scanner): satisfy ruff/mypy on async tick driver (#42)"
```

---

### Task 9: Open the PR

- [ ] **Step 1: Push the branch**

Run: `git push -u origin phase-2-a3-async-tick-driver`

- [ ] **Step 2: Open the PR closing #42**

Use `gh pr create --base main`. Title: `Phase 2 -- A3: async tick driver`. Body must include:

- `Closes #42.` on its own line so the issue auto-closes on merge.
- A one-paragraph summary linking back to the parent #3.
- The four files added under `src/` (one is a modify on `core/clock.py`) and six under `tests/`.
- "Decisions resolved: #38 (D4, refresh cadence + market-hours window)."
- "Decoupled by deferral: A5 (#44) via DecisionSink Protocol; rejected-decision enumeration deferred to #51."
- The full Acceptance Criteria checklist from #42 with each item checked.
- Spec-fix notice: "Five planning decisions diverged from #42's literal text -- see plan Defects/Open Questions; will be bundled into a follow-up spec-fix issue."
- Verification block: `ruff check .`, `mypy src tests`, `pytest` -- all green, with counts.
- Tag `@claude` and `@codex` per project review convention.

- [ ] **Step 3: Confirm CI is green**, then hand off to reviewer. Do NOT merge.

---

## Self-Review

**1. Spec coverage.** Walking through #42's "Files (new)" list:
- `scanner/loop.py` (`ScannerLoop`) -> Tasks 4-6.
- `scanner/decisions.py` (`ScannerDecision` with kinds picked/stale_feed/feed_gap) -> Task 2. Fourth kind `rejected` deferred to #51.
- `core/clock.py` `is_market_hours` free function -> Task 1.

#42's eight Acceptance bullets all mapped:
- 4-hour replay no unbounded growth -> `test_steady_state_no_unbounded_growth` (Task 7).
- No scans outside 07:00-11:00 ET -> `test_no_scans_outside_07_to_11_et` (Task 7) + `test_outside_market_hours_does_not_call_assembler` (Task 4).
- No staleness before first quote -> `test_pre_first_quote_no_stale_feed` (Task 7) + `test_pre_first_quote_does_not_suppress_scan` (Task 5).
- `stale_feed` real-time -> `test_stale_feed_suppresses_scan_and_emits_stale_decision` + `test_stale_feed_emitted_each_tick_no_dedup` (Task 5).
- `feed_gap` retrospective with quote-time duration -> `test_on_feed_gap_quote_time_duration_reflects_inputs` (Task 6) + `test_mid_window_disconnect_emits_feed_gap` (Task 7).
- VirtualClock staleness measures virtual time -> `test_loop_uses_injected_clock_sleep_not_asyncio_sleep` (Task 4) + the byte-identical replay test (Task 7).
- mypy --strict -> Task 8.
- Phase-1 + A1 + A2 regression -> Task 8 (full pytest).

#42's Tests bullet (`tests/integration/test_scanner_loop.py` cases: full window, mid-window disconnect, market-hours boundary, pre-first-quote tick, same-source duplicate quotes) -- four of five covered explicitly in Task 7. Same-source duplicate quotes is implicitly covered: `FakeSnapshotAssembler` script is keyed by anchor_ts only, so two consecutive ticks with the same `most_recent_quote_ts` are exactly the "same-source duplicate quotes" shape -- verified by `test_steady_state_no_unbounded_growth` showing one picked per tick (no buffering, no dedup, no skip).

**2. Placeholder scan.** No `TBD`, no `implement later`, no "add appropriate error handling" -- every step shows the actual code. Test code is concrete. Source implementations are concrete. Commit messages are concrete.

**3. Type consistency.** `ScannerDecision` field names match across `decisions.py` (definition), `loop.py` (`picked` / `stale_feed` / `feed_gap` constructions), and the test files (`_picked()` / `_stale()` / `_gap()` factories). `SnapshotAssembler.assemble(universe, anchor_ts) -> tuple[Mapping[str, ScannerSnapshot], datetime | None]` matches across `assembler.py`, `tests/fakes/snapshot_assembler.py`, and the loop's call site. `DecisionSink.emit(decision: ScannerDecision) -> None` matches across `decisions.py` and `tests/fakes/decision_sink.py`. `is_market_hours(utc_dt: datetime) -> bool` matches across `clock.py` definition, the loop's `_tick` call, and every test's import.

**4. Anchor alignment.** The loop's `decision_ts` is always `clock.now()` -- consistent across all three decision kinds. Under `VirtualClock`, this is deterministic; under `RealClock`, this is the wall-clock at emit. `pick.ts` (set by A2's Scanner from `snap.bar.ts`) is independent of `decision_ts` -- both timestamps appear on a `picked` decision and that's intentional (one is when the bar was sampled, the other is when we decided to pick it).

**5. Replay-determinism boundary.** The loop never reads wall time directly (`time.time()`, `datetime.now()`, `asyncio.sleep`). All time comes from `self._clock`. The assembler's "as of anchor_ts" semantics are codified in `SnapshotAssembler`'s docstring and re-asserted in the replay-determinism section. The byte-identical contract is verified by `test_two_runs_produce_byte_identical_decision_streams` (Task 7).

**6. Cancellation surface.** No `try/except CancelledError` anywhere in `loop.py`. `run()`'s `while True` body raises out cleanly when either `_tick()` or `clock.sleep()` is interrupted. Verified by `test_cancellation_reraises_cancelled_error` and `test_cancellation_does_not_swallow` (Task 4).

**7. Spec-text divergence vs issue #42.** Five intentional planning decisions diverge from the literal text of #42:
- D-A3-1: A3 ships with a `DecisionSink` Protocol so it does not block on A5 (#44).
- D-A3-2: A3 introduces a `SnapshotAssembler` Protocol; concrete vendor wiring deferred.
- D-A3-3: A3 emits three decision kinds only; rejected-candidate enumeration deferred to #51 (filed before this plan).
- D-A3-4: `is_market_hours` ships in `core/clock.py` per #42 text; flagged as a soft ergonomic concern.
- D-A3-5: `stale_feed` re-emits per tick with no dedup.

These will be bundled into one spec-fix issue against #42 after the PR ships, matching A1's pattern with #40 and A2's with #41.

**8. Sequencing soundness.** Task dependencies trace cleanly:
- Task 1 (`is_market_hours`) -- no scanner deps.
- Task 2 (`ScannerDecision` + `DecisionSink`) -- depends on A2's `ScannerPick`.
- Task 3 (`SnapshotAssembler`) -- depends on A2's `ScannerSnapshot`.
- Task 4 (`ScannerLoop` skeleton) -- depends on Tasks 1-3 + A2's `Scanner`.
- Task 5 (staleness) -- modifies Task 4's `_tick` only.
- Task 6 (`feed_gap`) -- adds an orthogonal method; doesn't touch `_tick`.
- Task 7 (integration test) -- depends on Tasks 1-6.
- Task 8 (gate) -- depends on all prior.
- Task 9 (PR) -- depends on Task 8.

No cycle, no lookahead, no skipped dependency.
