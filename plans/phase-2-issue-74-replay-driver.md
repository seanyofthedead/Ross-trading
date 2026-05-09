# Phase 2 -- A8: Replay Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a back-test driver that walks recorded ticks from `recordings/<date>/*.jsonl.gz` through `ScannerLoop`, populating the journal (`Pick`, `ScannerDecision` rows) for each curated trading day. This is the journal-population prerequisite for the Phase 2 recall-gate evaluation (#70).

**Architecture:** New `scanner/replay.py` module exposing a `replay_day(*, day, recordings_dir, universe_dir, journal_engine, ...)` async function and a `python -m ross_trading.scanner.replay` CLI entry point. The driver:

1. Builds a `ReplayProvider` (existing, `data/providers/replay.py`) in `AS_FAST_AS_POSSIBLE` mode against `recordings_dir`.
2. Pre-loads every event for the day's universe (M1/D1 bars, quotes, headlines, floats) into an in-memory `_RecordingSnapshotAssembler`.
3. Wires the existing `ScannerLoop` (no changes) — replay-backed assembler, journal-backed `DecisionSink` (#44), and a `VirtualClock` driven by recorded event timestamps.
4. Drives the loop over the day's recorded event span (plus a tail pad), propagating any task exception, then cancels.
5. Writes nothing to stdout besides one summary line per day (`day, picks, decisions, runtime`).

The driver is the only new module — `Scanner`, `ScannerLoop`, and `JournalWriter` are reused as-is. `ReplayProvider` and `data/recorder.py` were originally out of scope for this atom but were extended by PR #86 to close the FEED_GAP rung of the AC: the recorder gained `record_feed_gap` (so the live capture path can persist reconnect events behind a `ReconnectingProvider(..., on_gap=recorder.record_feed_gap)` wiring), and `ReplayProvider` gained `subscribe_feed_gaps` (so the driver can replay those events). Both extensions are additive and the rest of the original scope (no `Scanner`/`ScannerLoop`/`JournalWriter` changes) still holds.

**Tech Stack:** Python 3.11, `asyncio`, `decimal.Decimal`, raw `sqlite3` for the analytic cache, SQLAlchemy 2.x for the journal, mypy `--strict`, ruff, pytest with `asyncio_mode = "auto"`.

**Issue:** [#74](https://github.com/seanyofthedead/Ross-trading/issues/74) — tracks under [#3](https://github.com/seanyofthedead/Ross-trading/issues/3).

**Decisions resolved:**

- **Replay-first over live-first** (drift-audit closure, PR #71 follow-up). Replay is reproducible, decouples Phase-2 closure from calendar time, and removes regime drift from the measurement.
- **Reuse `ReplayProvider` over a new driver-internal reader.** The existing provider already paces and decodes the recorded streams; reusing it keeps live and replay paths bit-identical.
- **`AS_FAST_AS_POSSIBLE` pacing.** Wall-clock pacing is for tests of timing-sensitive flows; the recall gate cares about *what got picked*, not *when*. Fast mode is also the only feasible mode for a 10-day backfill.
- **Idempotency by pre-flight DELETE on `(day)`.** Re-running the driver for the same day is a no-op on row counts. Originally the plan called for a unique index on `picks(ticker, ts)`; that choice would have broken the existing live-loop contract that emits one `Pick` row per qualifying tick (multiple ticks within a single M1 bar share the same `Pick.ts`, exercised by `test_scanner_loop_with_real_journal_writer_persists_picks`). Pre-flight DELETE is the plan's documented fallback ("`DELETE WHERE day = ?`") and the only one of the two options compatible with how the loop actually writes. Both `picks` and `scanner_decisions` for the day are deleted in one transaction before the loop drives.
- **Universe sourced from per-day JSON.** `CachedUniverseProvider` calls a live universe API; for replay the driver reads `<universe-dir>/<YYYY-MM-DD>.json` (a JSON list of tickers). Missing file means "empty universe" -- the driver short-circuits to a zero-count summary.
- **No synthetic-tick fallback in this atom.** If `recordings/` lacks a curated day, the driver returns a zero-count summary (the assembler reports empty bounds). A synthetic-tick generator is reserved for a follow-up atom; tracked in spike issue [#78](https://github.com/seanyofthedead/Ross-trading/issues/78). Until that lands, the recall gate has no signal for days without real recordings.

---

## Acceptance Criteria

- [x] `python -m ross_trading.scanner.replay --date YYYY-MM-DD --source recordings/ --universe-dir universe/` runs end-to-end against existing recordings and writes journal rows.
- [x] Multi-day form: `--from YYYY-MM-DD --to YYYY-MM-DD` iterates the date range. Days with no recordings are skipped with a single warning line, not a crash.
- [x] Re-running for the same day is idempotent — assert journal row counts match before vs after a re-run.
- [x] Decision stream matches the live-loop emission: `PICKED` (always), `REJECTED` (after #51), `STALE_FEED`, `FEED_GAP` as the loop already emits them.
- [x] CLI default DB URL matches `journal/__init__.py`'s canonical `sqlite:///journal.sqlite`. Override path: `--db-url`.
- [x] mypy --strict, ruff, pytest, CI all green.
- [x] Integration test: replay a fixture day → assert pick rows land in the journal; re-run is a no-op on counts; loop crashes propagate (no busy-yield hang).
- [x] No new runtime dependency in `pyproject.toml`.

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/scanner/replay.py` | `replay_day` async function, in-memory assembler, CLI entry point. |
| Create | `tests/integration/test_scanner_replay.py` | End-to-end smoke + idempotency + crash-propagation assertions. |
| Modify | `src/ross_trading/data/_codec.py` | `EventType.FEED_GAP` + `encode_feed_gap` / `decode_feed_gap` (added by PR #86 for the FEED_GAP AC rung). |
| Modify | `src/ross_trading/data/recorder.py` | `record_feed_gap` (added by PR #86; lets the live capture path persist reconnect events). |
| Modify | `src/ross_trading/data/providers/replay.py` | `subscribe_feed_gaps` (added by PR #86; lets the driver replay recorded gaps). |

No changes to `Scanner`, `ScannerLoop`, or `JournalWriter` — that's the atom's discipline. `ReplayProvider` and `data/recorder.py` were originally listed as out-of-scope and were extended by PR #86 to close the FEED_GAP AC rung; both extensions are additive (recordings without `feed_gap.jsonl.gz` produce an empty stream and behave as before). No journal migration: idempotency is enforced at the driver level via pre-flight DELETE rather than a schema constraint (see decisions above).

## Key Interfaces

```python
# src/ross_trading/scanner/replay.py — public surface

async def replay_day(
    *,
    day: date,
    recordings_dir: Path,
    universe_dir: Path,
    journal_engine: Engine,
    scanner: Scanner | None = None,        # default Scanner() with arch §3.1 thresholds
    tick_interval_s: float = 2.0,
    staleness_threshold_s: float = 5.0,
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
# Single day
python -m ross_trading.scanner.replay \
  --date 2026-04-15 \
  --source recordings/ \
  --universe-dir universe/ \
  --db-url sqlite:///journal.sqlite

# Inclusive date range (skips days with no recordings, single WARN per skip)
python -m ross_trading.scanner.replay \
  --from 2026-04-13 --to 2026-04-17 \
  --source recordings/ \
  --universe-dir universe/
```

## Test Strategy

Integration only — this atom is wiring, and unit-testing the wiring is lower-value than asserting the integrated journal output. Three scenarios in `tests/integration/test_scanner_replay.py`:

1. **Smoke happy path.** Synthetic single-ticker recording with one tick that passes every filter; assert `summary.picks_emitted >= 1` and that the journal contains the corresponding `Pick` row.
2. **Idempotency.** Run `replay_day` twice for the same fixture; assert `picks_emitted` and `decisions_emitted` are identical across the two runs (pre-flight DELETE keeps row counts stable).
3. **Loop-exception propagation.** Inject an `_ExplodingScanner` that raises in `scan_with_decisions`; assert `replay_day` re-raises `RuntimeError` rather than spinning forever in its busy-yield (Codex P2 fix on PR #79).

No unit tests for the CLI argument parser unless a follow-up bug demands one.

## Defects / Open Questions

- **Recording shape mismatch.** If existing `recordings/<date>/` files were produced before A4 (#43) landed, their schema may not include the fields `ScannerSnapshot` needs. Verify against a real recording before committing to fixture format. If mismatch found: scope a recording-format-bump migration as a defect issue, do not patch the driver.
- **Universe reconstruction for historical days.** Resolved: load from `<universe-dir>/<YYYY-MM-DD>.json`. If the file is missing, the driver returns an empty universe (and therefore an empty summary). Curators are expected to commit a per-day universe JSON alongside the ground-truth file.
- **Clock semantics under fast replay.** `is_market_hours` gates the loop. The driver builds a `VirtualClock` anchored at the recording's first intraday event ts; `is_market_hours` consumes that UTC value and translates to ET internally, so DST handling stays in `core/clock.py`.

## Out-of-scope follow-ups

These were considered for this atom and explicitly deferred:

- **Synthetic-tick fallback.** Spike-tracked in [#78](https://github.com/seanyofthedead/Ross-trading/issues/78). Until real recordings exist for curated days, the recall gate (#70) has no driver-side signal.
- **`docs/ground_truth.md` "Running the recall report" section.** A docs-only PR after the recall gate (#70) is wired; saves repeated rewrites as the surface evolves.

## Conventions

- Driver is async (matches `ScannerLoop`) but the CLI wrapper is sync — `asyncio.run` at the entry point.
- All times UTC internally; ET-translation is `core/clock.py`'s job.
- `Decimal` for all price math.
- `Optional` is `T | None`.
- Tests use `pytest` only.

## Tasks

- [x] 1. Implement `ReplaySummary` value object (`day`, `picks_emitted`, `decisions_emitted`, `runtime_seconds`).
- [x] 2. Implement in-memory `_RecordingSnapshotAssembler` and `_StaticUniverseProvider` for replay.
- [x] 3. Implement `replay_day` orchestrator: pre-flight DELETE for idempotency, build provider, wire `ScannerLoop`, drive over the recording's event span, propagate task exceptions, return summary.
- [x] 4. Add CLI: `argparse` with `--date` xor `--from`/`--to`, `--source`, `--universe-dir`, `--db-url`.
- [x] 5. Add `tests/integration/test_scanner_replay.py`: smoke + idempotency + loop-crash propagation.
- [x] 6. Verify `ruff check src tests` passes.
- [x] 7. Verify `mypy src tests` passes (strict).
- [x] 8. Verify `pytest` passes (the new tests carry the `integration` marker).
- [x] 9. Verify CI is green on the feature branch.
- [ ] 10. (Deferred) Document the curator → replay → recall flow in `docs/ground_truth.md` once the recall gate (#70) ships.
