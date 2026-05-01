# Phase 2 — A1: Scanner Filter Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement six pure-function primitives for the Section 3.1 scanner hard filters (`rel_volume_ge`, `pct_change_ge`, `price_in_band`, `float_le`, `news_present`, `headline_count`) as the foundational layer for the Phase 2 scanner.

**Architecture:** A new `scanner/` package containing a single module of typed pure functions. No I/O, no logging, no provider calls. Inputs are existing data-layer types (`Quote`, `Bar`, `Headline`, `FloatRecord`) plus a precomputed 30-day baseline volume. The two news functions consume an already-collected sequence of `Headline` objects and rely on `HeadlineDeduper` (from `data/news_feed.py`) anchored at the bar-open time so live and replay produce identical counts.

**Tech Stack:** Python 3.11, `decimal.Decimal` arithmetic, `datetime` UTC timestamps, mypy `--strict`, ruff, pytest (no asyncio for this atom).

**Issue:** [#40](https://github.com/seanyofthedead/Ross-trading/issues/40) — tracked under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Decisions resolved:**
- [#39](https://github.com/seanyofthedead/Ross-trading/issues/39) (D5: catalyst treatment) — soft signal: `news_present` returns `bool`, `headline_count` returns `int`. Scanner does not gate on news.

---

## Acceptance Criteria (from issue #40)

- [ ] Each filter is a typed pure function. No I/O, no logging, no module-level mutable state.
- [ ] Boundary tests cover just-above, exact, just-below, and missing-data for each numeric filter.
- [ ] `news_present` 24-hour window matches `HeadlineDeduper`'s `DEFAULT_DEDUP_WINDOW`.
- [ ] `headline_count` returns post-deduplication counts using a fresh `HeadlineDeduper`, anchored at `anchor_ts` (bar-open time, not `datetime.now()`).
- [ ] `mypy --strict` passes on `src` and `tests`.
- [ ] `ruff check` passes on `src` and `tests`.
- [ ] All Phase-1 tests still pass (no regression).

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/scanner/__init__.py` | New package marker (empty + module docstring). |
| Create | `src/ross_trading/scanner/filters.py` | The six pure-function primitives. |
| Create | `tests/unit/test_scanner_filters.py` | Table-driven boundary tests for every filter. |

No modifications to existing modules. No dependency changes in `pyproject.toml`.

## Key Interfaces

All input types live in `src/ross_trading/data/types.py` (`Quote`, `Bar`, `Headline`, `FloatRecord`). `HeadlineDeduper` and `DEFAULT_DEDUP_WINDOW` live in `src/ross_trading/data/news_feed.py`.

```python
# src/ross_trading/scanner/filters.py — public surface

def rel_volume_ge(
    symbol: str,
    snapshot: Bar,
    baseline_30d: Decimal | None,
    threshold: float = 5.0,
) -> bool: ...

def pct_change_ge(
    current: Decimal,
    reference: Decimal,
    threshold_pct: Decimal,
) -> bool: ...

def price_in_band(
    symbol: str,
    snapshot: Bar,
    low: Decimal = Decimal("1"),
    high: Decimal = Decimal("20"),
) -> bool: ...

def float_le(
    record: FloatRecord | None,
    threshold: int = 20_000_000,
) -> bool: ...

def news_present(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> bool: ...

def headline_count(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> int: ...
```

**Snapshot semantics:** `Bar` carries `ts` (open time), `open`, `close`, `volume`. The scanner uses the **last completed bar** as the snapshot, so:
- `rel_volume_ge` compares `snapshot.volume` against the precomputed 30-day baseline (loaded once at start-of-day per #38; A1 receives it as a value, not a callable). The session-cumulative-vs-30-day-average ratio shaping is owned by the *caller* — A1 receives the volume number to compare and the baseline to compare against; it does not assume one is daily and the other a single bar.
- `pct_change_ge` is a **pure two-price primitive** — A1 stays ignorant of session boundaries. It takes `current` and `reference` as `Decimal`s and a `threshold_pct` in *percent units* (e.g. `Decimal("10")` ⇒ 10%). True iff `(current - reference) / reference >= threshold_pct / 100`. A2 (#41) composes the gainer-% check by passing `current=quote.last, reference=prev_close`; an intraday move check is `current=snapshot.close, reference=snapshot.open`.

**News semantics:**
- `news_present` is `headline_count(...) >= 1`. We still implement it as a separate function (cheaper short-circuit + clearer call sites).
- Both functions filter `headlines` to entries where `h.ticker.upper() == ticker.upper()` and `anchor_ts - timedelta(hours=lookback_hours) <= h.ts <= anchor_ts`.
- Then they pass the filtered list through a fresh `HeadlineDeduper(window=timedelta(hours=lookback_hours))`. Each call constructs a **fresh** deduper (no shared state). Order matters for `OrderedDict`-based eviction → sort filtered headlines by `h.ts` ascending before feeding the deduper.
- Window match: `lookback_hours=24` ⇒ deduper window = `timedelta(hours=24)` = `DEFAULT_DEDUP_WINDOW`.

**Missing-data conventions:**
- `rel_volume_ge` with `baseline_30d is None` → `False` (cannot evaluate, do not promote).
- `rel_volume_ge` with `baseline_30d == 0` → `False` (avoid divide-by-zero; symbol has no recent volume → not a momentum candidate).
- `float_le` with `record is None` → `False` (cannot evaluate, do not promote).
- `pct_change_ge` with `reference == 0` → `False` (avoid divide-by-zero).
- `price_in_band` always uses `snapshot.close`.

## Test Strategy

`tests/unit/test_scanner_filters.py` — table-driven cases per filter. Use `pytest.mark.parametrize` for boundary tables. No fakes from `tests/fakes/` are required for A1 — construct `Bar`/`Headline`/`FloatRecord` directly in the tests.

Per filter, cover:
- Just-above threshold → `True`/expected.
- Exact threshold (the `_ge`/`_le` functions are inclusive: `ge` ⇒ `≥`, `le` ⇒ `≤`).
- Just-below threshold → `False`.
- Missing-data path (None inputs / zero baselines).

For `news_present` and `headline_count`, additionally cover:
- Empty `headlines` sequence → `False` / `0`.
- Headline outside the 24-hour window (older) → excluded.
- Headline at exactly `anchor_ts` and at exactly `anchor_ts - 24h` → included (inclusive boundary).
- Headline newer than `anchor_ts` → excluded (we only look backward).
- Wrong ticker → excluded.
- Lower-case ticker in input matches upper-case query → included (consistent with `Headline.dedup_key` upper-casing).
- **Deduplication case:** same `(source, normalized_title, ticker)` reported twice → counts as 1. (This is the case the issue calls out: "same headline from two sources" — but `dedup_key` includes `source`, so two **different sources** ⇒ two distinct entries. Two **same-source** repeats ⇒ one. Test both interpretations to lock the behavior.)
- Whitespace/case-normalized title duplicate → counts as 1 (delegated to `HeadlineDeduper`).

## Risks / Unknowns

1. **News dedup window exactly equals lookback window.** Issue requires `news_present` 24h to match `DEFAULT_DEDUP_WINDOW`. Implementation: pass `timedelta(hours=lookback_hours)` to the deduper. If `lookback_hours != 24`, the deduper window tracks the lookback. Verified in tests.
2. **`HeadlineDeduper` is stateful across `is_duplicate` calls.** A1 must construct a fresh deduper per `headline_count` call to avoid leaking state across scanner ticks. Tested explicitly.
3. **Ticker case.** `Headline.dedup_key` upper-cases the ticker. A1 must do the same when filtering by ticker so callers can pass either case. Tested explicitly.
4. **Spec-text divergence vs issue #40.** Two signatures here intentionally differ from the literal text of #40:
   - `news_present` adds `anchor_ts` (the issue body's omission was a typo — replay determinism is a hard project rule).
   - `pct_change_ge` is a pure two-price primitive `(current, reference, threshold_pct)` — the issue body's `(symbol, snapshot, threshold)` shape leaked session semantics into A1.
   A follow-up "spec-fix" issue will be filed against #40 to update the issue body so future readers aren't misled. Not blocking for the PR.

## Effort Estimate

**S** (small). One source file, one test file, no dependencies. ~150 LoC source, ~250 LoC tests. Roughly 60–90 minutes for an engineer who has read this plan, including running ruff/mypy/pytest.

---

## Tasks

### Task 1: Create the `scanner` package skeleton

**Files:**
- Create: `src/ross_trading/scanner/__init__.py`

- [ ] **Step 1: Create the package init**

```python
"""Ross-trading Scanner package.

Phase 2 — Section 3.1 hard-filter pipeline. This package will grow to
contain (roughly in this order):

* ``filters`` — pure-function primitives for the five Section 3.1 hard
  filters plus the soft news signals (Atom A1, this module).
* ``ranking`` — top-N selector by % gain (A2).
* ``scanner`` — orchestrator that composes filters + ranking (A2).
* ``loop`` — async tick driver (A3).

Atoms are introduced one PR at a time so each is independently reviewable.
"""
```

- [ ] **Step 2: Verify the package imports**

Run: `python -c "import ross_trading.scanner"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add src/ross_trading/scanner/__init__.py
git commit -m "feat(scanner): create scanner package skeleton (#40)"
```

---

## Import Evolution Pattern (applies to Tasks 2–6)

Each task introduces exactly one function (or, for Task 6, the news pair) and the test file's `from ross_trading.scanner.filters import …` line evolves alphabetically as functions land. Concretely, the import line at the end of each task is:

| After Task | Import line |
|---|---|
| 2 | `from ross_trading.scanner.filters import rel_volume_ge` |
| 3 | `from ross_trading.scanner.filters import pct_change_ge, rel_volume_ge` |
| 4 | `from ross_trading.scanner.filters import pct_change_ge, price_in_band, rel_volume_ge` |
| 5 | `from ross_trading.scanner.filters import float_le, pct_change_ge, price_in_band, rel_volume_ge` |
| 6 | `from ross_trading.scanner.filters import (float_le, headline_count, news_present, pct_change_ge, price_in_band, rel_volume_ge)` (parenthesized once it grows past one line) |

This keeps every intermediate state lint-clean — no scattered mid-file imports that would trip ruff `E402`/`I001`. Task 7 then verifies the final block is a single alphabetized line.

---

### Task 2: `rel_volume_ge` — failing test first

**Files:**
- Test: `tests/unit/test_scanner_filters.py`

- [ ] **Step 1: Write the test file scaffold + first failing test**

The import line is exactly `from ross_trading.scanner.filters import rel_volume_ge` — only this one name. Other functions arrive in later tasks.


```python
"""Atom A1 — scanner filter primitives (issue #40)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.filters import rel_volume_ge

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar(
    *,
    symbol: str = "AVTX",
    ts: datetime | None = None,
    open_: str = "5.00",
    high: str = "5.50",
    low: str = "4.95",
    close: str = "5.50",
    volume: int = 1_000_000,
) -> Bar:
    return Bar(
        symbol=symbol,
        ts=ts or T0,
        timeframe="D1",
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


# ---------------------------------------------------------------- rel_volume_ge


@pytest.mark.parametrize(
    ("today_volume", "baseline", "threshold", "expected"),
    [
        (5_000_000, Decimal("1_000_000"), 5.0, True),   # exact 5.0×
        (5_000_001, Decimal("1_000_000"), 5.0, True),   # just above
        (4_999_999, Decimal("1_000_000"), 5.0, False),  # just below
        (10_000_000, Decimal("1_000_000"), 5.0, True),  # well above
    ],
)
def test_rel_volume_ge_boundaries(
    today_volume: int,
    baseline: Decimal,
    threshold: float,
    expected: bool,
) -> None:
    snapshot = _bar(volume=today_volume)
    assert rel_volume_ge("AVTX", snapshot, baseline, threshold) is expected


def test_rel_volume_ge_missing_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), None) is False


def test_rel_volume_ge_zero_baseline_is_false() -> None:
    assert rel_volume_ge("AVTX", _bar(volume=10_000_000), Decimal("0")) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scanner_filters.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `ross_trading.scanner.filters` (the source file does not exist yet).

- [ ] **Step 3: Create `src/ross_trading/scanner/filters.py` with the minimal `rel_volume_ge`**

```python
"""Pure-function primitives for the Section 3.1 hard filters.

Atom A1 of Phase 2 (#40, tracked under #3). No I/O, no logging, no
module-level mutable state. Thresholds are passed as parameters so
the scanner can A/B test them later without surgery here.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ross_trading.data.news_feed import HeadlineDeduper

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ross_trading.data.types import Bar, FloatRecord, Headline


def rel_volume_ge(
    symbol: str,  # noqa: ARG001 — kept for signature symmetry across filters
    snapshot: Bar,
    baseline_30d: Decimal | None,
    threshold: float = 5.0,
) -> bool:
    """True iff ``snapshot.volume / baseline_30d >= threshold``.

    Returns ``False`` when ``baseline_30d`` is ``None`` or zero — both
    mean "we don't have enough history to evaluate", and absence of
    evidence is not promotion.
    """
    if baseline_30d is None or baseline_30d == 0:
        return False
    ratio = Decimal(snapshot.volume) / baseline_30d
    return ratio >= Decimal(str(threshold))
```

- [ ] **Step 4: Run the tests to verify `rel_volume_ge` cases pass**

Run: `pytest tests/unit/test_scanner_filters.py::test_rel_volume_ge_boundaries tests/unit/test_scanner_filters.py::test_rel_volume_ge_missing_baseline_is_false tests/unit/test_scanner_filters.py::test_rel_volume_ge_zero_baseline_is_false -v`
Expected: 6 passed (4 parametrize cases + 2 single tests).

- [ ] **Step 5: Commit**

```bash
git add src/ross_trading/scanner/filters.py tests/unit/test_scanner_filters.py
git commit -m "feat(scanner): rel_volume_ge primitive (#40)"
```

---

### Task 3: `pct_change_ge`

**Files:**
- Modify: `tests/unit/test_scanner_filters.py` (edit import line + append tests)
- Modify: `src/ross_trading/scanner/filters.py` (append)

- [ ] **Step 1: Update the import line, then append the failing tests**

Edit the existing import line at the top of the test file from:

```python
from ross_trading.scanner.filters import rel_volume_ge
```

to:

```python
from ross_trading.scanner.filters import pct_change_ge, rel_volume_ge
```

Then append the new tests at the bottom of the file:

```python
# ----------------------------------------------------------------- pct_change_ge


@pytest.mark.parametrize(
    ("current", "reference", "threshold_pct", "expected"),
    [
        ("5.50", "5.00", "10",  True),    # exact +10%
        ("5.501", "5.00", "10", True),    # just above
        ("5.499", "5.00", "10", False),   # just below
        ("10.00", "5.00", "10", True),    # well above
        ("4.50", "5.00", "10",  False),   # negative move
        ("5.50", "5.00", "5",   True),    # lower threshold passes
        ("5.50", "5.00", "20",  False),   # higher threshold fails
    ],
)
def test_pct_change_ge_boundaries(
    current: str, reference: str, threshold_pct: str, expected: bool,
) -> None:
    assert pct_change_ge(
        Decimal(current), Decimal(reference), Decimal(threshold_pct)
    ) is expected


def test_pct_change_ge_zero_reference_is_false() -> None:
    """Avoid divide-by-zero — return False rather than raising."""
    assert pct_change_ge(Decimal("1.00"), Decimal("0"), Decimal("10")) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_scanner_filters.py -v -k pct_change_ge`
Expected: 8 failures with `ImportError: cannot import name 'pct_change_ge'`.

- [ ] **Step 3: Append `pct_change_ge` to `filters.py`**

```python
def pct_change_ge(
    current: Decimal,
    reference: Decimal,
    threshold_pct: Decimal,
) -> bool:
    """True iff ``(current - reference) / reference >= threshold_pct / 100``.

    A pure two-price primitive — A1 stays ignorant of session
    boundaries. The caller (A2) supplies the right pair: pass
    ``current=quote.last, reference=prior_session_close`` for
    gainer-% on the day; pass ``current=bar.close,
    reference=bar.open`` for an intraday move check.

    ``threshold_pct`` is in **percent units** (``Decimal("10")``
    means 10%, not 0.10) so call sites read like the spec language
    of "≥ +10%".

    Returns ``False`` when ``reference == 0`` (avoid divide-by-zero).
    """
    if reference == 0:
        return False
    change = (current - reference) / reference
    return change >= threshold_pct / Decimal(100)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/unit/test_scanner_filters.py -v -k pct_change_ge`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ross_trading/scanner/filters.py tests/unit/test_scanner_filters.py
git commit -m "feat(scanner): pct_change_ge primitive (#40)"
```

---

### Task 4: `price_in_band`

**Files:**
- Modify: `tests/unit/test_scanner_filters.py` (edit import line + append tests)
- Modify: `src/ross_trading/scanner/filters.py` (append)

- [ ] **Step 1: Update the import line, then append the failing tests**

Edit the existing import line at the top of the test file from:

```python
from ross_trading.scanner.filters import pct_change_ge, rel_volume_ge
```

to:

```python
from ross_trading.scanner.filters import pct_change_ge, price_in_band, rel_volume_ge
```

Then append the new tests at the bottom of the file:

```python
# ----------------------------------------------------------------- price_in_band


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        ("1.00", True),    # exact low
        ("0.99", False),   # just below low
        ("1.01", True),    # just above low
        ("19.99", True),   # just below high
        ("20.00", True),   # exact high
        ("20.01", False),  # just above high
        ("5.50", True),    # mid-band
    ],
)
def test_price_in_band_default_bounds(close: str, expected: bool) -> None:
    snapshot = _bar(close=close)
    assert price_in_band("AVTX", snapshot) is expected


def test_price_in_band_custom_bounds() -> None:
    snapshot = _bar(close="50.00")
    assert price_in_band("AVTX", snapshot, low=Decimal("10"), high=Decimal("100")) is True
    assert price_in_band("AVTX", snapshot, low=Decimal("60"), high=Decimal("100")) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_scanner_filters.py -v -k price_in_band`
Expected: 9 failures with `ImportError: cannot import name 'price_in_band'`.

- [ ] **Step 3: Append `price_in_band` to `filters.py`**

```python
def price_in_band(
    symbol: str,  # noqa: ARG001
    snapshot: Bar,
    low: Decimal = Decimal("1"),
    high: Decimal = Decimal("20"),
) -> bool:
    """True iff ``low <= snapshot.close <= high`` (inclusive both ends)."""
    return low <= snapshot.close <= high
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/unit/test_scanner_filters.py -v -k price_in_band`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ross_trading/scanner/filters.py tests/unit/test_scanner_filters.py
git commit -m "feat(scanner): price_in_band primitive (#40)"
```

---

### Task 5: `float_le`

**Files:**
- Modify: `tests/unit/test_scanner_filters.py` (edit import line + append tests)
- Modify: `src/ross_trading/scanner/filters.py` (append)

- [ ] **Step 1: Update the import line, then append the failing tests**

Edit the existing import line at the top of the test file from:

```python
from ross_trading.scanner.filters import pct_change_ge, price_in_band, rel_volume_ge
```

to:

```python
from ross_trading.scanner.filters import (
    float_le,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
```

The line is parenthesized + multi-line once it grows past one screen-width-friendly fit, matching the existing `data/news_feed.py` import-style convention. Then append the new tests at the bottom of the file:

```python
# --------------------------------------------------------------------- float_le


def _float(shares: int, ticker: str = "AVTX") -> FloatRecord:
    return FloatRecord(
        ticker=ticker,
        as_of=date(2026, 4, 26),
        float_shares=shares,
        shares_outstanding=shares * 2,
        source="test",
    )


@pytest.mark.parametrize(
    ("shares", "threshold", "expected"),
    [
        (20_000_000, 20_000_000, True),   # exact
        (19_999_999, 20_000_000, True),   # just below
        (20_000_001, 20_000_000, False),  # just above
        (5_000_000, 20_000_000, True),    # well below
    ],
)
def test_float_le_boundaries(shares: int, threshold: int, expected: bool) -> None:
    assert float_le(_float(shares), threshold) is expected


def test_float_le_missing_record_is_false() -> None:
    assert float_le(None) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_scanner_filters.py -v -k float_le`
Expected: 5 failures with `ImportError: cannot import name 'float_le'`.

- [ ] **Step 3: Append `float_le` to `filters.py`**

```python
def float_le(
    record: FloatRecord | None,
    threshold: int = 20_000_000,
) -> bool:
    """True iff ``record.float_shares <= threshold``.

    Returns ``False`` when ``record`` is ``None`` — absence of
    evidence is not promotion. The default threshold is 20M shares
    (Cameron's hard cap; ``<10M`` is the preferred soft target which
    the ranker (A2) will weigh separately).
    """
    if record is None:
        return False
    return record.float_shares <= threshold
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/unit/test_scanner_filters.py -v -k float_le`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ross_trading/scanner/filters.py tests/unit/test_scanner_filters.py
git commit -m "feat(scanner): float_le primitive (#40)"
```

---

### Task 6: `news_present` + `headline_count`

These two share the same windowing + deduper logic, so we implement them together with a shared private helper to avoid duplication.

**Files:**
- Modify: `tests/unit/test_scanner_filters.py` (edit import line + append tests)
- Modify: `src/ross_trading/scanner/filters.py` (append)

- [ ] **Step 1: Update the import line, then append the failing tests**

Edit the existing import line at the top of the test file from:

```python
from ross_trading.scanner.filters import (
    float_le,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
```

to:

```python
from ross_trading.scanner.filters import (
    float_le,
    headline_count,
    news_present,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
```

(Both `headline_count` and `news_present` arrive in this same task.) Then append the new tests at the bottom of the file:

```python
# -------------------------------------------------------- news_present / count


def _h(
    *,
    title: str = "AVTX up on FDA approval",
    source: str = "Benzinga",
    ticker: str = "AVTX",
    ts: datetime | None = None,
) -> Headline:
    return Headline(ticker=ticker, ts=ts or T0, source=source, title=title)


def test_news_present_empty_is_false() -> None:
    assert news_present("AVTX", [], anchor_ts=T0) is False
    assert headline_count("AVTX", [], anchor_ts=T0) == 0


def test_news_present_within_window_is_true() -> None:
    headlines = [_h(ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_at_exact_window_edge_is_inclusive() -> None:
    """24h ago exactly is still in the window."""
    headlines = [_h(ts=T0 - timedelta(hours=24))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_at_exact_anchor_is_inclusive() -> None:
    """A headline timestamped at anchor_ts itself is included."""
    headlines = [_h(ts=T0)]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_news_present_outside_window_is_false() -> None:
    headlines = [_h(ts=T0 - timedelta(hours=24, seconds=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_future_headlines_excluded() -> None:
    """Strictly look backward from anchor_ts. A headline with ts > anchor_ts is ignored."""
    headlines = [_h(ts=T0 + timedelta(seconds=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_wrong_ticker_excluded() -> None:
    headlines = [_h(ticker="OTHER", ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is False
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 0


def test_news_present_lowercase_ticker_query_matches() -> None:
    """Casing must not matter — Headline.dedup_key already upper-cases."""
    headlines = [_h(ticker="avtx", ts=T0 - timedelta(hours=1))]
    assert news_present("AVTX", headlines, anchor_ts=T0) is True
    assert news_present("avtx", headlines, anchor_ts=T0) is True


def test_headline_count_dedup_same_source_same_title() -> None:
    """Same (source, normalized_title, ticker) twice ⇒ one count."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_distinct_sources_not_deduped() -> None:
    """dedup_key includes source — Benzinga + Polygon ⇒ two distinct entries."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Polygon",  title="AVTX up on FDA approval", ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 2


def test_headline_count_normalized_title_dedupes() -> None:
    """HeadlineDeduper normalizes case + whitespace within the title."""
    headlines = [
        _h(source="Benzinga", title="AVTX up on FDA approval", ts=T0 - timedelta(hours=2)),
        _h(source="Benzinga", title="  avtx UP  ON  fda APPROVAL ",
           ts=T0 - timedelta(hours=1)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_uses_fresh_deduper_per_call() -> None:
    """Two consecutive calls with the same headlines must each return 1, not 0 then 0."""
    headlines = [_h(ts=T0 - timedelta(hours=1))]
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1
    assert headline_count("AVTX", headlines, anchor_ts=T0) == 1


def test_headline_count_custom_lookback_uses_matching_dedup_window() -> None:
    """If lookback_hours=2, the deduper window is also 2h."""
    headlines = [
        _h(source="Benzinga", title="story A", ts=T0 - timedelta(hours=3)),
        _h(source="Benzinga", title="story B", ts=T0 - timedelta(minutes=30)),
    ]
    assert headline_count("AVTX", headlines, anchor_ts=T0, lookback_hours=2) == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_scanner_filters.py -v -k "news_present or headline_count"`
Expected: failures with `ImportError: cannot import name 'news_present'` (and `headline_count`).

- [ ] **Step 3: Append the news functions + shared helper to `filters.py`**

```python
def _within_lookback(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int,
) -> list[Headline]:
    """Return headlines for ``ticker`` with ``ts`` in
    ``[anchor_ts - lookback, anchor_ts]`` (inclusive both ends),
    sorted by ``ts`` ascending.

    Sorted ascending so ``HeadlineDeduper``'s OrderedDict eviction
    sees timestamps in monotonic order — eviction keys off
    ``headline.ts`` and assumes incoming events progress forward.
    """
    cutoff = anchor_ts - timedelta(hours=lookback_hours)
    upper = ticker.upper()
    matched = [
        h for h in headlines
        if h.ticker.upper() == upper and cutoff <= h.ts <= anchor_ts
    ]
    matched.sort(key=lambda h: h.ts)
    return matched


def news_present(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> bool:
    """True iff at least one ticker-matching headline falls in
    ``[anchor_ts - lookback_hours, anchor_ts]``.

    Anchor is the bar-open time, never ``datetime.now()`` — so live
    and replay produce identical answers.
    """
    return headline_count(ticker, headlines, anchor_ts, lookback_hours) >= 1


def headline_count(
    ticker: str,
    headlines: Sequence[Headline],
    anchor_ts: datetime,
    lookback_hours: int = 24,
) -> int:
    """Count distinct ticker-matching headlines after running them
    through a fresh ``HeadlineDeduper`` with a window matching
    ``lookback_hours``.

    A fresh deduper is constructed each call so scanner ticks do not
    leak state into each other.
    """
    window = timedelta(hours=lookback_hours)
    deduper = HeadlineDeduper(window=window)
    count = 0
    for headline in _within_lookback(ticker, headlines, anchor_ts, lookback_hours):
        if not deduper.is_duplicate(headline):
            count += 1
    return count
```

- [ ] **Step 4: Run the full test file, verify all pass**

Run: `pytest tests/unit/test_scanner_filters.py -v`
Expected: every test passes (rough total: ~33 cases including parametrize expansions).

- [ ] **Step 5: Commit**

```bash
git add src/ross_trading/scanner/filters.py tests/unit/test_scanner_filters.py
git commit -m "feat(scanner): news_present and headline_count primitives (#40)"
```

---

### Task 7: Consolidate test-file imports (verification)

The Import Evolution Pattern means the final state of the import block should already be a single alphabetized `from ross_trading.scanner.filters import (…)` line. This task is a defensive verification — catching any drift before the static-checks pass in Task 8.

- [ ] **Step 1: Inspect the import block at the top of `tests/unit/test_scanner_filters.py`**

Expected final block:

```python
from ross_trading.scanner.filters import (
    float_le,
    headline_count,
    news_present,
    pct_change_ge,
    price_in_band,
    rel_volume_ge,
)
```

If the block matches: this task is a no-op, skip to Task 8.

- [ ] **Step 2: If the block drifted (multiple `from ross_trading.scanner.filters import …` lines, or names out of order):** consolidate to a single alphabetized parenthesized block as shown above.

- [ ] **Step 3: Run `ruff check tests/unit/test_scanner_filters.py`**

Expected: clean — no `E402`, no `I001`, no `F401`.

- [ ] **Step 4: Commit only if Step 2 changed anything:**

```bash
git add tests/unit/test_scanner_filters.py
git commit -m "chore(scanner): consolidate test-file imports (#40)"
```

---

### Task 8: Static checks + full suite

- [ ] **Step 1: Run ruff**

Run: `ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 2: Run mypy strict**

Run: `mypy --strict src tests`
Expected: `Success: no issues found in <N> source files` where `<N>` equals Phase 1's count plus the three new files (`scanner/__init__.py`, `scanner/filters.py`, `tests/unit/test_scanner_filters.py`). Verify the count is sensible rather than asserting an exact number — the Phase-1 baseline can drift between branches.

- [ ] **Step 3: Run the full pytest suite**

Run: `pytest`
Expected: every Phase-1 test still passes, plus the new `test_scanner_filters.py` cases. No regressions.

- [ ] **Step 4: If anything fails, fix and re-run; only proceed once all three are green.**

- [ ] **Step 5: No new commit needed if Tasks 2–7 commits are clean.** If lint or mypy turned up something, the fix lands as a `chore` commit:

```bash
git add -p
git commit -m "chore(scanner): satisfy ruff/mypy on filter primitives (#40)"
```

---

### Task 9: Open the PR

- [ ] **Step 1: Push the branch**

Run: `git push -u origin <branch-name>`

- [ ] **Step 2: Open the PR closing #40**

Use `gh pr create` with body referencing #40 and the parent #3. Title: `Phase 2 — A1: scanner filter primitives`. Body must include:
- "Closes #40."
- One-paragraph summary linking back to the parent #3.
- "Decisions resolved: #39 (catalyst is soft signal)."
- The exit-criterion checklist from #40 (all items checked).
- Verification block (`ruff`, `mypy --strict`, `pytest` all green).

- [ ] **Step 3: Confirm CI is green**, then hand off to reviewer.

---

## Self-Review

**1. Spec coverage.** Walking through issue #40's six functions: `rel_volume_ge` (Task 2), `pct_change_ge` (Task 3), `price_in_band` (Task 4), `float_le` (Task 5), `news_present` + `headline_count` (Task 6). Acceptance bullets all mapped: typed pure functions ✓, boundary tests ✓, 24h matches `DEFAULT_DEDUP_WINDOW` ✓, `headline_count` post-dedup anchored at `anchor_ts` ✓ (via `_within_lookback` + fresh deduper), mypy strict + ruff in Task 7 ✓. Test file path matches the issue (`tests/unit/test_scanner_filters.py`).

**2. Placeholder scan.** No `TBD`, no `implement later`, no "add appropriate error handling" — every step shows the actual code. Test code is concrete. Filter implementations are concrete. Commit messages are concrete.

**3. Type consistency.** `rel_volume_ge` takes `Decimal | None` for the baseline in both signature, plan prose, and tests. `float_le` takes `FloatRecord | None` consistently. `news_present` returns `bool`, `headline_count` returns `int` — both match issue #40. `_within_lookback` is the shared private helper; both `news_present` and `headline_count` go through it (well — `news_present` delegates to `headline_count` so it only needs the helper transitively, which is the simpler call graph).

**4. Anchor alignment.** The issue calls out anchoring at `bar.ts` (bar-open time). The plan documents this and the tests use `T0` as the anchor explicitly. `news_present`'s signature in the issue body listed `(ticker, headlines, lookback_hours=24)` without `anchor_ts` — confirmed as a typo. The plan uses the same `(ticker, headlines, anchor_ts, lookback_hours=24)` shape as `headline_count` so the two functions share semantics and the live/replay determinism invariant holds for both. To be tracked back to #40 via a small spec-fix issue.

**5. `pct_change_ge` shape.** The issue body's `(symbol, snapshot, threshold=0.10)` was confirmed superseded — A1 stays ignorant of session boundaries with `(current: Decimal, reference: Decimal, threshold_pct: Decimal) -> bool`. Skimmed #41 (A2 — scanner core): A2 records a `pct_change` value on `ScannerPick` and gates on a "%change" filter, but does not prescribe A1's signature. A2 is free to compose `current=quote.last, reference=prev_close` for the gainer-% filter, so no conflict to reconcile. The same spec-fix follow-up issue will update #40's signature text.
