# Phase 3 — A2 (follow-up): Daily-Strength Breakout + Turnaround Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the `breakout` and `turnaround` boolean flags of `DailyStrengthScore` (architecture §3.3) so the strength score uses the full 0..5 range. A1 (#72) emitted both flags as `None` because their inputs require a daily-bar history wider than the existing 30-day volume baseline. This atom extends the cache with a `daily_bars(symbol, as_of, high, low)` table, adds rolling-max-high / rolling-min-low aggregate readers, adds a `populate_daily_bars` companion to the existing populate functions, and extends `score_daily_strength` to compute the two flags.

**Architecture:** Extend `data/cache.py` with one new SQLite table (`daily_bars`) and aggregate query methods; extend `data/historical.py` with a `populate_daily_bars` async function that reads daily bars from any `MarketDataProvider` and persists `(high, low)` per (symbol, day); extend `scanner/strength.py` so `score_daily_strength` reads the new aggregates plus the existing volume cache to compute `breakout` (close > rolling resistance high) and `turnaround` (close near 52-week low + today's volume ≥ N× the 30-day average). All thresholds are parameterized with documented defaults.

**Tech Stack:** Python 3.11, `decimal.Decimal`, raw `sqlite3` reads/writes via the existing `data/cache.py` interface, mypy `--strict`, ruff, pytest.

**Issue:** [#73](https://github.com/seanyofthedead/Ross-trading/issues/73) — tracks under [#4](https://github.com/seanyofthedead/Ross-trading/issues/4). Predecessor: [#72](https://github.com/seanyofthedead/Ross-trading/issues/72).

**Decisions resolved:**

- **Score semantics with optional flags.** `score = number of explicitly-True flags among (above_ema20, above_ema50, above_ema200, breakout, turnaround)` whenever all three EMA cache rows exist. A `None` flag contributes 0 — it is "no evidence", not "False". When any EMA cache row is missing, `score` is `None` (matches existing A1 behaviour). This keeps backward-compatibility with the A1-only test suite (cases without `daily_bars` rows still report `score 0..3`) while letting the score climb to 5 once `daily_bars` and `daily_volumes` are populated.
- **Multi-month resistance lookback.** Default `breakout_lookback_days = 66` (~3 trading-month average; "multi-month resistance" in §3.3 is intentionally loose). Caller-overridable. `breakout = close > cache.max_high(symbol, prior_day, lookback)`. Strict `>` mirrors §3.3's pseudocode and the existing EMA comparison.
- **52-week-low band + reversal-volume thresholds.** Default `turnaround_lookback_days = 252` (~52 weeks), `near_52w_low_pct = Decimal("0.10")` (close within +10% of the 52-week low qualifies as "at or near"), `reversal_volume_ratio = Decimal("2.0")` (today's volume must be ≥ 2× the trailing-30-day average). All three are parameters with documented defaults; the architecture doc keeps the high-level rule, this atom owns the operational thresholds.
- **Cache as source of truth.** New `daily_bars` table mirrors the existing `daily_volumes` / `daily_emas` pattern. Aggregate methods (`max_high`, `min_low`) return `Decimal | None`; `None` means "no rows in window". Aggregate readers refuse non-positive lookbacks. Following A1, absence of evidence is not promotion — a missing aggregate yields `breakout=None` (or `turnaround=None`), not `False`.
- **Excluded today from rolling windows.** Both aggregates are computed against `prior_day = as_of - 1`, so today's bar (which the strength filter is judging) does not become its own resistance / 52-week-low. Mirrors `cache.relative_volume`.

---

## Acceptance Criteria

- [ ] `breakout` and `turnaround` move from `None` to observable booleans whenever the new cache aggregates plus the existing volume cache are populated.
- [ ] Strength score covers 0..5 when all five inputs are observable; 0..3 when only EMAs are observable; `None` when any EMA row is missing.
- [ ] Boundary tests cover: at-resistance (close == prior max high → not breakout), just-broke-out (close > prior max high by 1 cent → breakout), near-52w-low + heavy volume → turnaround, near-52w-low + no/low volume → not turnaround, missing daily_bars rows → flags `None` while EMA score still reports.
- [ ] `populate_daily_bars` writes one row per provider-returned bar; idempotent under repeat runs (INSERT OR REPLACE).
- [ ] mypy `--strict` passes on `src` and `tests`.
- [ ] `ruff check` passes on `src` and `tests`.
- [ ] CI is green on the feature branch.

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Edit   | `src/ross_trading/data/cache.py`             | Add `daily_bars` table; `record_daily_bar(s)`, `max_high`, `min_low` methods. |
| Edit   | `src/ross_trading/data/historical.py`        | Add `populate_daily_bars` async populator. |
| Edit   | `src/ross_trading/scanner/strength.py`       | Wire breakout / turnaround logic; new params with documented defaults. |
| Edit   | `tests/unit/test_scanner_strength.py`        | Boundary tests per acceptance criteria; update existing assertions. |
| Edit   | `tests/unit/test_historical.py`              | Coverage for `populate_daily_bars`. |
| Create | `plans/phase-3-issue-73-breakout-turnaround.md` (this file) | Plan record for the atom. |

No changes to `scanner/scanner.py`, `scanner/ranking.py`, `scanner/loop.py`, journal, or migrations — out of scope for this atom (consumer wiring is a separate atom).

## Key Interfaces

```python
# src/ross_trading/data/cache.py — additions

class HistoricalCache:
    def record_daily_bar(self, symbol: str, as_of: date, high: Decimal, low: Decimal) -> None: ...
    def record_daily_bars(self, rows: Iterable[tuple[str, date, Decimal, Decimal]]) -> None: ...
    def max_high(self, symbol: str, end_inclusive: date, lookback_days: int) -> Decimal | None: ...
    def min_low(self, symbol: str, end_inclusive: date, lookback_days: int) -> Decimal | None: ...


# src/ross_trading/data/historical.py — addition

async def populate_daily_bars(
    provider: MarketDataProvider,
    symbol: str,
    end_inclusive: date,
    cache: HistoricalCache,
    history_days: int = 252,
) -> int: ...


# src/ross_trading/scanner/strength.py — extended signature

def score_daily_strength(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: HistoricalCache,
    *,
    breakout_lookback_days: int = 66,
    turnaround_lookback_days: int = 252,
    near_52w_low_pct: Decimal = Decimal("0.10"),
    reversal_volume_ratio: Decimal = Decimal("2.0"),
    avg_volume_lookback_days: int = 30,
) -> DailyStrengthScore: ...
```

## Test Strategy

Unit tests against an in-memory `HistoricalCache` populated via the new `record_daily_bar(s)` writer plus the existing `record_ema` / `record_daily_volume(s)` writers. New cases:

- **Breakout — at resistance.** Close == prior max high → `breakout` False (strict `>`).
- **Breakout — one cent over.** Close = prior max high + 0.01 → `breakout` True.
- **Breakout — clearly above.** Close >> prior max high → `breakout` True.
- **Breakout — clearly below.** Close < prior max high → `breakout` False.
- **Breakout — no daily_bars rows.** Cache has EMAs but no `daily_bars` → `breakout` None, EMA score still reports.
- **Turnaround — near low + heavy volume.** Close within `near_52w_low_pct` of 52-week-min-low and today's volume ≥ `reversal_volume_ratio` × avg → True.
- **Turnaround — near low + flat volume.** Same low proximity, today's volume < threshold → False.
- **Turnaround — far above 52-week low.** Close well above the low band → False even if volume is heavy.
- **Turnaround — missing volume row.** No `daily_volumes` for `as_of` → None.
- **Turnaround — missing min_low.** No `daily_bars` rows in window → None.
- **Score climbs to 5.** All EMAs True, breakout True, turnaround True → `score == 5`.
- **Score 0..3 with no daily_bars.** Existing A1 behaviour preserved (legacy tests pass without modification beyond the additive flag assertions).
- **Score is None when any EMA is missing.** Existing behaviour preserved.

Plus one async test for `populate_daily_bars` in `tests/unit/test_historical.py`, paralleling the existing `populate_daily_volumes` test.

No integration test in this atom — the scanner-side wiring atom owns end-to-end coverage.

## Defects / Open Questions

- **Where do thresholds live long-term?** `near_52w_low_pct` and `reversal_volume_ratio` are operational thresholds without a clean home today. They live as `score_daily_strength` parameters with documented defaults; if the scanner consumer needs to A/B test them, we can route them through the consumer's config.
- **Trading-day vs calendar-day rolling windows.** `lookback_days` here is calendar days (the cache stores rows by `as_of` and the SQL `LIMIT N` returns the last N rows present, which approximates trading days when only weekday rows exist). This matches `avg_daily_volume`'s semantics. If the cache is ever populated with weekend rows, the windows widen — same caveat as existing avg-volume.

## Conventions

- Pure-function strength scorer; SQL writes go through the cache.
- `Decimal` for all price math; `int` for volume.
- `Optional` is `T | None`.
- Tests use `pytest` only.
- Cache reads accept the existing `HistoricalCache` directly; no new protocol.

## Tasks

- [ ] 1. Add the `daily_bars` table + `record_daily_bar(s)` + `max_high` + `min_low` methods to `data/cache.py`.
- [ ] 2. Add `populate_daily_bars` to `data/historical.py`, paralleling `populate_daily_volumes`.
- [ ] 3. Extend `score_daily_strength` with breakout / turnaround logic and new keyword-only parameters.
- [ ] 4. Update `tests/unit/test_scanner_strength.py` with the boundary cases above; preserve existing case semantics.
- [ ] 5. Add `populate_daily_bars` coverage to `tests/unit/test_historical.py`.
- [ ] 6. Verify `ruff check src tests` passes.
- [ ] 7. Verify `mypy src tests` passes (strict).
- [ ] 8. Verify `pytest -m "not integration"` and full pytest pass.
- [ ] 9. Verify CI is green on the feature branch.
