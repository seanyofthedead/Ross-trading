# Ground-Truth Curator Workflow

The Phase 2 closure metric is recall against a hand-curated oracle of "what would Cameron have traded today". This document is the curator's procedure. The loader is `src/ross_trading/journal/ground_truth.py::load_ground_truth`; the daily comparison report is `src/ross_trading/journal/report.py`.

## Where to source recaps

Cameron publishes a daily YouTube recap on the Warrior Trading channel ("Today's morning recap" / "Today's stock watchlist", typical posting time mid-afternoon ET). The recap walks through the morning's calls, names each ticker, and replays the entry/exit. **The recap is the source of truth — not the live stream, not Twitter, not the chatroom.** Recaps are revised after the fact (Cameron sometimes notes "I shouldn't have taken that one"); the recorded recap is the durable record.

If a recap is unavailable for a date (vacation, holiday, premium-only video), skip the date — do not infer from a different source. A missing day is acceptable; an inferred day pollutes the gate.

## What counts as "actively-called"

Per the convention encoded in `ground_truth.py`:

* **Include** — tickers Cameron *actively traded* during the recap. Entry was taken (or, for paper-recap days, the recap explicitly says "I would have entered here").
* **Exclude** — tickers Cameron *mentioned*, *watched*, or *rejected*. The oracle is "what would have been traded", not a transcript of the recap audio. Watchlist names that he discusses but does not enter are not in scope.

Edge cases:
* If Cameron took a partial-share probe and immediately exited, *include* the ticker. The probe is a trade.
* If Cameron held overnight from a prior day and only managed the existing position, *exclude* the ticker — the gate is per-day entries.
* If Cameron names two tickers in the same setup ("AVTX or PROK, whichever pops first") and enters one, include only the entered ticker.

## File schema

Per-file path: `ground_truth/YYYY-MM-DD.json` (date in local-ET trading-day form; weekends and US-market holidays should not have files). Top-level value is a JSON array of records. Each record has the following keys:

| Key | Required | Type | Notes |
|---|---|---|---|
| `ticker` | yes | string | Stripped + upper-cased on load. |
| `direction` | yes | string | Must be the literal `"long"` (case-strict). The single-valued constraint is intentional — a future short-bias variant is a schema bump, not a silent reinterpretation. |
| `time_called_out` | no | string | `"HH:MM"` 24-hour ET. Strict format (no seconds, no zone suffix). Omit when the recap doesn't surface the exact time. |
| `notes` | no | string | Free text. |

Unknown keys raise `GroundTruthError` (e.g., `"note"` for `"notes"` is rejected). Adding a new field requires bumping `ground_truth.py::_ALLOWED_FIELDS` and the curated files together — this is a deliberate barrier, not friction to route around.

## Per-record example

```json
[
  {
    "ticker": "AVTX",
    "direction": "long",
    "time_called_out": "07:32",
    "notes": "small float biotech, called as a clean long off the FDA catalyst on the morning recap"
  },
  {
    "ticker": "MSTR",
    "direction": "long",
    "time_called_out": "09:35",
    "notes": "premarket gapper traded long off the opening range; sized down for the higher-priced name"
  }
]
```

`time_called_out` is currently unused by the report (matching is ticker-only per Decision D3 / #37) but is recorded so a future time-windowed match can use it without re-curation.

## Procedure

1. Identify the trading day. Confirm the recap exists and is from Cameron (not a Warrior staff trader's recap).
2. Watch the recap end-to-end. Take linear notes — ticker, time, what he did, why.
3. For each entered ticker, write one record per the schema. Keep `notes` brief — one sentence — but unambiguous about why this counts as "actively-called".
4. Save as `ground_truth/YYYY-MM-DD.json`. Validate locally:
   ```bash
   python -c "from datetime import date; from ross_trading.journal.ground_truth import load_ground_truth; print(load_ground_truth(date.fromisoformat('YYYY-MM-DD')))"
   ```
5. Run the validation test:
   ```bash
   pytest tests/integration/test_ground_truth_files.py
   ```
6. Commit with message `journal(ground-truth): curate YYYY-MM-DD (N tickers)`.

## Coverage target

Phase 2 closure (≥70% recall) is statistically meaningless on small N. Target ≥10 trading days before running the recall report (`src/ross_trading/journal/report.py`). Aim for variety: at least one cold-market day, at least one halt-resume day, at least one biotech-FDA day, at least one Gap-and-Go style day. The variety surfaces blind spots in the scanner's hard filters that a single regime would hide.

## Curation is blocking work

This file documents the procedure. The actual curation cannot be performed by an agent without source-recap access — agents do not have the ground-truth audio/video and must not invent tickers. Each curated file requires a human watching a real Cameron recap. Until ≥10 days are curated, the Phase 2 recall gate (`reports/phase-2-recall-summary.md` per ISSUE-015 / #70) cannot be evaluated.
