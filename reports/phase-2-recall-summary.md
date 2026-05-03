# Phase 2 Recall Summary

**Status: Phase 2 closure criterion NOT met — gate cannot yet be statistically evaluated.**

The closing gate is mean recall ≥70% across the curated ground-truth days. Today the oracle covers a single day (2026-05-01) and the scanner journal is empty, which makes the gate vacuously fail at 0% / N=1. The two unblockers are tracked separately: ISSUE-011 (#68 — curated-day backfill, blocking on a human watching real Cameron recaps) and the scanner-run prerequisite (run the live loop or a back-test driver against the same days, no driver wired in this phase).

## Per-day table

| Day        | Cameron | Scanner | Matched | Recall | Precision | Notes |
|------------|---------|---------|---------|--------|-----------|-------|
| 2026-05-01 | 2       | 0       | 0       | 0.0%   | 0.0%      | Empty journal — no scanner run recorded for this day. |

## Aggregate

* Mean recall: 0.0% (N=1)
* Mean precision: 0.0% (N=1)
* Days below 70%: 1 of 1

A 70% gate over N=1 is statistically meaningless. Do not interpret this number as a scanner verdict — the cause is missing inputs, not a scanner deficiency.

## Highest-leverage findings

1. **Curated days = 1, target = ≥10.** ISSUE-011 (#68) is the prerequisite. `docs/ground_truth.md` documents the curation procedure; an agent cannot fabricate calls. Until ≥10 days are curated against real recaps, this summary cannot be re-run meaningfully.
2. **Scanner journal is empty.** The recall calculation joins curated tickers against `Pick` rows produced by `Scanner.scan` (atom A2/A3) running against historical-equivalent data. Either run the live loop while curating in real time, or build a small replay-driver atom that walks recorded ticks through `ScannerLoop` and writes the journal. The replay driver is out of scope for ISSUE-015 itself; track separately.
3. **Once both unblockers land**, re-run `python -m ross_trading.journal.report --date YYYY-MM-DD` for each curated day, regenerate this summary's per-day table, and recompute the aggregate. The pass/fail call replaces the line at the top of this file.

## Re-run procedure

```bash
# 1. Confirm ground_truth/ has ≥10 days and tests pass.
pytest tests/integration/test_ground_truth_files.py

# 2. Confirm the scanner journal has Pick rows for each curated day.
#    (alembic upgrade head; populate via ScannerLoop or a back-test driver.)

# 3. Re-generate per-day reports.
for f in ground_truth/*.json; do
  day="${f##*/}"; day="${day%.json}"
  python -m ross_trading.journal.report --date "$day"
done

# 4. Replace the table above by aggregating the per-day reports.
```

## Resolution

This file will be rewritten with real per-day numbers and an explicit pass/fail decision once #68 and the scanner-run prerequisite are unblocked. Until then it stands as the durable record of *why* the gate is not yet evaluable — not as evidence the scanner has failed.
