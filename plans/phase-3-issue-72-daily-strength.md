# Phase 3 — A1: Daily Chart Strength Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the daily-chart strength filter described in `docs/architecture.md` §3.3 (TA Series p.4) as a pure-function module that scores each ticker against its EMA20 / EMA50 / EMA200 plus optional breakout / turnaround flags, returning *keep* for score ≥ 2 and *demote* for 0–1. The scanner can then promote, demote, or filter picks against the score; this atom only produces the score.

**Architecture:** New `scanner/strength.py` module with a single public function `score_daily_strength(symbol, as_of, daily_close, cache, ...)` that reads `EMA20`, `EMA50`, `EMA200` from the existing `daily_emas` SQLite cache (`src/ross_trading/data/cache.py`) and returns a `DailyStrengthScore` value object. EMA values are produced ahead of time by `src/ross_trading/data/historical.py::precompute_daily_emas`; this atom is a *consumer* of that cache, not a producer. Breakout and turnaround flags are deferred to follow-up atoms (their inputs require additional historical context that is not yet wired); this atom emits them as `None` and documents the contract.

**Tech Stack:** Python 3.11, `decimal.Decimal` arithmetic, `datetime` UTC timestamps, raw `sqlite3` reads via the existing `data/cache.py` interface (no new SQLAlchemy surface — daily EMAs live in the analytic cache, per the SQLAlchemy / sqlite3 split documented in `journal/__init__.py`), mypy `--strict`, ruff, pytest.

**Issue:** [#72](https://github.com/seanyofthedead/Ross-trading/issues/72) — tracks under [#4](https://github.com/seanyofthedead/Ross-trading/issues/4) (Phase 3 phase tracker). Follow-up atom (breakout / turnaround flags) tracked separately as [#73](https://github.com/seanyofthedead/Ross-trading/issues/73).

**Decisions resolved:**

- **Score range and tiering** — Per §3.3, score = (close > EMA20) + (close > EMA50) + (close > EMA200) + breakout_flag + turnaround_flag. Range 0–5. ≥2 → keep, 0–1 → demote. This atom emits the score; the scanner consumer (a separate atom) decides keep/demote semantics.
- **Cache as source of truth** — EMA values come from `daily_emas`. If the cache is missing the row for a symbol/period/date, the scorer returns `DailyStrengthScore(score=None, ...)` rather than recomputing on-the-fly; recomputation is the job of `precompute_daily_emas`. Absence of evidence is not promotion (matches `float_le` semantics in `scanner/filters.py`).
- **Breakout / turnaround flags** — Inputs (multi-month high series, 52-week low + reversal-volume signal) are not yet wired into the cache. This atom returns `None` for both flags, documents the contract, and reduces the score to the 0–3 EMA component until follow-up atoms land. The keep/demote threshold remains ≥2.
- **No I/O concurrency** — `score_daily_strength` is sync. The scanner loop that consumes it is async (existing `ScannerLoop`), but the scoring step itself is a pure read against the local SQLite cache.

---

## Acceptance Criteria

- [ ] `score_daily_strength(...)` is a typed pure function. No I/O beyond a single read from the existing cache layer; no module-level mutable state.
- [ ] `DailyStrengthScore` value object is `@dataclass(frozen=True, slots=True)` with fields `score: int | None`, `above_ema20: bool | None`, `above_ema50: bool | None`, `above_ema200: bool | None`, `breakout: bool | None`, `turnaround: bool | None`. `None` means "missing evidence" per the cache-as-source-of-truth decision above.
- [ ] When all three EMA cache rows exist: `score = sum(above_emaXX)`; flags emitted as observed booleans; `breakout` and `turnaround` are emitted as `None`.
- [ ] When any EMA cache row is missing: `score = None` and the corresponding `above_emaXX = None`. The scanner consumer treats `None` as "demote" (lower priority than score-0), matching `float_le`-style absence-of-evidence semantics.
- [ ] Boundary tests: `close == EMA` (strict greater-than only — `>` not `>=`), `close > EMA` by 1 cent, `close < EMA` by 1 cent, missing cache row.
- [ ] `mypy --strict` passes on `src` and `tests`.
- [ ] `ruff check` passes on `src` and `tests`.
- [ ] CI (`.github/workflows/ci.yml`) is green on the feature branch.

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/scanner/strength.py` | `DailyStrengthScore` + `score_daily_strength`. |
| Create | `tests/unit/test_scanner_strength.py` | Table-driven boundary tests + missing-cache cases. |

No modifications to `data/cache.py`, `data/historical.py`, `scanner/scanner.py`, or `scanner/ranking.py` in this atom — those are downstream wiring covered by follow-up plans.

## Key Interfaces

```python
# src/ross_trading/scanner/strength.py — public surface

@dataclass(frozen=True, slots=True)
class DailyStrengthScore:
    score: int | None              # 0..3 today, 0..5 once flags ship; None = missing cache
    above_ema20: bool | None
    above_ema50: bool | None
    above_ema200: bool | None
    breakout: bool | None          # always None until follow-up atom lands
    turnaround: bool | None        # always None until follow-up atom lands


def score_daily_strength(
    symbol: str,
    as_of: date,
    daily_close: Decimal,
    cache: Cache,                   # existing cache surface from data/cache.py
) -> DailyStrengthScore: ...
```

## Test Strategy

Table-driven unit tests against an in-memory `Cache` populated via the existing `cache.upsert_daily_ema` writer. Each case lists `(close, ema20, ema50, ema200)` and expected `(score, above_ema20, above_ema50, above_ema200)`.

Boundary cases (per acceptance criteria):
- All three EMAs present, close above all → score 3.
- All three EMAs present, close below all → score 0.
- Mixed: close > EMA20, < EMA50, > EMA200 → score 2.
- `close == EMA` → strict `>` returns `False` for that EMA.
- One EMA missing (e.g., EMA200 absent in cache) → `score=None`, `above_ema200=None`, the other two booleans observable.
- All missing → `score=None`, all flags `None`.

No integration test in this atom — the scanner-side wiring atom owns end-to-end coverage.

## Defects / Open Questions

- **Breakout and turnaround sourcing.** Multi-month resistance and 52-week-low + reversal-volume detection need a daily-bar history wider than the 30-day volume baseline. A follow-up atom must extend the cache (or derive on the fly) before `breakout` / `turnaround` move from `None` to observable. Note in the ADR appendix once it lands.
- **Decimal vs float in EMA values.** Existing `daily_emas` stores `value REAL` (sqlite3) but `precompute_daily_emas` writes `Decimal` strings via cache layer adapters. Confirm `Cache.get_daily_ema(...)` returns `Decimal` (or coerce here); add a focused test covering the round-trip if not.

## Conventions

- All filter primitives are pure functions in `scanner/`. Side-effecting code lives in `scanner/loop.py` only.
- `Decimal` for all price math. No `float` in scoring logic.
- `Optional` is `T | None`, not `Optional[T]` (matches the rest of `src/`).
- Tests use `pytest` only — no `unittest` or third-party assertion libraries.
- Cache reads accept the existing `Cache` protocol. Tests pass an in-memory adapter; no temp files.

## Tasks

- [ ] 1. Add `DailyStrengthScore` value object to `src/ross_trading/scanner/strength.py`.
- [ ] 2. Implement `score_daily_strength(symbol, as_of, daily_close, cache)` reading the three EMAs.
- [ ] 3. Decide cache-miss return shape (`score=None`, EMA-flag `None`); document in module docstring.
- [ ] 4. Wire strict `>` comparison for `above_emaXX` (close == EMA → False).
- [ ] 5. Add `tests/unit/test_scanner_strength.py` covering the boundary table above.
- [ ] 6. Verify `ruff check src tests` passes.
- [ ] 7. Verify `mypy src tests` passes (strict).
- [ ] 8. Verify `pytest -m "not integration"` passes.
- [ ] 9. Verify CI is green on the feature branch.
- [ ] 10. Open the breakout / turnaround follow-up atom (#73) once A1 lands.
