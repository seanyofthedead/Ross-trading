# Phase 2 -- A8: Replay Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a back-test driver that walks recorded ticks from `recordings/<date>/*.jsonl.gz` through `ScannerLoop`, populating the journal (`Pick`, `ScannerDecision` rows) for each curated trading day. This is the journal-population prerequisite for the Phase 2 recall-gate evaluation (#70).

**Architecture:** New `scanner/replay.py` module exposing a `replay_day(*, day, recordings_dir, journal_engine, ...)` async function and a `python -m ross_trading.scanner.replay` CLI entry point. The driver:

1. Builds a `ReplayProvider` (existing, `data/providers/replay.py`) in `AS_FAST_AS_POSSIBLE` mode against `recordings_dir`.
2. Wires the existing `ScannerLoop` (no changes) — replay provider, snapshot assembler, journal-backed `DecisionSink` (#44), and a deterministic `Clock` driven by recorded event timestamps.
3. Drives the loop until the recording is exhausted, then flushes.
4. Writes nothing to stdout besides one summary line per day (`day, picks, decisions, runtime`).

The driver is the only new module — `Scanner`, `ScannerLoop`, `JournalWriter`, and `ReplayProvider` are reused as-is. `data/recorder.py` is not touched (this atom only consumes recordings; writing them is out of scope).

**Tech Stack:** Python 3.11, `asyncio`, `decimal.Decimal`, raw `sqlite3` for the analytic cache, SQLAlchemy 2.x for the journal, mypy `--strict`, ruff, pytest with `asyncio_mode = "auto"`.

**Issue:** [#74](https://github.com/seanyofthedead/Ross-trading/issues/74) — tracks under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Decisions resolved:**

- **Replay-first over live-first** (drift-audit closure, PR #71 follow-up). Replay is reproducible, decouples Phase-2 closure from calendar time, and removes regime drift from the measurement.
- **Reuse `ReplayProvider` over a new driver-internal reader.** The existing provider already paces and decodes the recorded streams; reusing it keeps live and replay paths bit-identical.
- **`AS_FAST_AS_POSSIBLE` pacing.** Wall-clock pacing is for tests of timing-sensitive flows; the recall gate cares about *what got picked*, not *when*. Fast mode is also the only feasible mode for a 10-day backfill.
- **Idempotency by `(scan_ts, ticker)`.** Re-running the driver for the same day must not duplicate journal rows. Either a unique index on `picks(ticker, ts)` (preferred) or a pre-flight `DELETE WHERE day = ?`. Pick the index; preserves audit history if the user wants to compare two driver runs.
- **No synthetic-tick fallback in this atom.** If `recordings/` lacks a curated day, the driver fails loudly with a clear message. A synthetic-tick generator is a separate atom, only filed if real recordings cover <10 curated days.

---

## Acceptance Criteria

- [ ] `python -m ross_trading.scanner.replay --date YYYY-MM-DD --recordings-dir recordings/` runs end-to-end against existing recordings and writes journal rows.
- [ ] Multi-day form: `--from YYYY-MM-DD --to YYYY-MM-DD` iterates the date range. Days with no recordings are skipped with a single warning line, not a crash.
- [ ] Re-running for the same day is idempotent — assert journal row counts match before vs after a re-run.
- [ ] Decision stream matches the live-loop emission: `PICKED` (always), `REJECTED` (after #51), `STALE_FEED`, `FEED_GAP` as the loop already emits them.
- [ ] CLI default DB URL matches `journal/__init__.py`'s canonical `sqlite:///journal.sqlite`. Override path: `--db-url`.
- [ ] mypy --strict, ruff, pytest, CI all green.
- [ ] Integration test: replay a fixture day → assert pick rows match a recorded golden output.
- [ ] No new runtime dependency in `pyproject.toml`.

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/scanner/replay.py` | `replay_day` async function + CLI entry point. |
| Create | `tests/integration/test_scanner_replay.py` | End-to-end fixture-day replay; assertions over journal rows. |
| Modify | `src/ross_trading/journal/migrations/versions/0003_picks_unique_ticker_ts.py` (new revision) | Add unique index on `picks(ticker, ts)` for idempotency. |

No changes to `Scanner`, `ScannerLoop`, `JournalWriter`, or `ReplayProvider` — that's the atom's discipline.

## Key Interfaces

```python
# src/ross_trading/scanner/replay.py — public surface

async def replay_day(
    *,
    day: date,
    recordings_dir: Path,
    journal_engine: Engine,
    scanner: Scanner | None = None,        # default Scanner() with arch §3.1 thresholds
    clock_factory: Callable[[], Clock] | None = None,
) -> ReplaySummary: ...


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    day: date
    picks_emitted: int
    decisions_emitted: int
    runtime_seconds: float
```

CLI:
```bash
python -m ross_trading.scanner.replay \
  --date 2026-04-15 \
  --recordings-dir recordings/ \
  --db-url sqlite:///journal.sqlite
```

## Test Strategy

Integration only — this atom is wiring, and unit-testing the wiring is lower-value than asserting the integrated journal output. Two test scenarios:

1. **Golden-day replay.** Fixture under `tests/fixtures/replay/2026-04-15/` containing a small recording (3–5 tickers, ~30 minutes of bars). Assert: journal contains exactly N pick rows; tickers and ranks match a checked-in golden JSON; decision count matches.
2. **Idempotency.** Run `replay_day` twice for the same fixture; assert journal row counts are identical and the unique index prevents duplicates.

No unit tests for the CLI argument parser unless a follow-up bug demands one.

## Defects / Open Questions

- **Recording shape mismatch.** If existing `recordings/<date>/` files were produced before A4 (#43) landed, their schema may not include the fields `ScannerSnapshot` needs. Verify against a real recording before committing to fixture format. If mismatch found: scope a recording-format-bump migration as a defect issue, do not patch the driver.
- **Universe reconstruction for historical days.** `CachedUniverseProvider` calls a live universe API. For replay, the driver needs to either snapshot the universe at recording time or load it from a flat file. Decision: load from `ground_truth/<date>.json`'s ticker set ∪ a per-day `universe/<date>.json` file if present; fail loudly if neither is available.
- **Clock semantics under fast replay.** `is_market_hours` gates the loop. Under `AS_FAST_AS_POSSIBLE` the deterministic clock must report ET-market-hours timestamps from the recording, not wall-clock. Verify in the integration test.

## Conventions

- Driver is async (matches `ScannerLoop`) but the CLI wrapper is sync — `asyncio.run` at the entry point.
- All times UTC internally; ET-translation is `core/clock.py`'s job.
- `Decimal` for all price math.
- `Optional` is `T | None`.
- Tests use `pytest` only.

## Tasks

- [ ] 1. Add unique index migration `0003_picks_unique_ticker_ts.py`. Verify against existing test fixtures (no row collisions).
- [ ] 2. Implement `ReplaySummary` value object in `src/ross_trading/scanner/replay.py`.
- [ ] 3. Implement `replay_day` orchestrator: build `ReplayProvider`, wire `ScannerLoop`, await completion, return summary.
- [ ] 4. Decide and implement universe-source policy (per-day JSON or ground-truth-derived); document in module docstring.
- [ ] 5. Add CLI: `argparse` with `--date`, `--from/--to`, `--recordings-dir`, `--db-url`. Single-day path uses `--date`; range path is a thin loop over `replay_day`.
- [ ] 6. Add `tests/integration/test_scanner_replay.py`: golden-day replay + idempotency assertions.
- [ ] 7. Verify `ruff check src tests` passes.
- [ ] 8. Verify `mypy src tests` passes (strict).
- [ ] 9. Verify `pytest -m integration` passes (the new tests are in this group).
- [ ] 10. Verify CI is green on the feature branch.
- [ ] 11. Document the curator → replay → recall flow in `docs/ground_truth.md` (a single new "Running the recall report" section linking to this CLI).
