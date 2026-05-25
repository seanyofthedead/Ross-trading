# Historical Intraday Data

Phase 2 replay uses the local recorder format consumed by
`ross_trading.data.providers.replay.ReplayProvider`:

```text
recordings/
  YYYY-MM-DD/
    bar.jsonl.gz
```

Each line is a schema-versioned JSON envelope written by `FeedRecorder`. The
backfill path below writes only `bar` events: `M1` intraday bars for the target
trading days and `D1` bars for the trailing baseline window. That is enough for
the #74 replay driver to run without its synthetic fallback and for the scanner
to compute price change and 30-day relative volume from recorded data.

## Chosen OS / Free-Tier Path

Use Alpha Vantage's CSV HTTP endpoints:

* `TIME_SERIES_INTRADAY` with `interval=1min`, `adjusted=false`,
  `extended_hours=true`, `month=YYYY-MM`, `outputsize=full`, `datatype=csv`.
* `TIME_SERIES_DAILY` with `outputsize=full`, `datatype=csv` for the daily
  volume baseline.

Alpha Vantage documents 1-minute intraday history with a `month` selector and
CSV output, including extended hours. A free API key is available, but usage is
subject to Alpha Vantage's terms of service and rate limits. Do not commit API
keys or downloaded vendor data unless the applicable license permits repository
redistribution.

Primary docs:

* https://www.alphavantage.co/documentation/
* https://www.alphavantage.co/support/#api-key
* https://www.alphavantage.co/terms_of_service/

## Fetch Steps

Install the project first:

```bash
pip install -e ".[dev]"
```

Set an API key:

```bash
export ALPHA_VANTAGE_API_KEY=...
```

On Windows PowerShell:

```powershell
$env:ALPHA_VANTAGE_API_KEY = "..."
```

Backfill every currently curated ground-truth ticker/date:

```bash
python scripts/backfill_historical.py `
  --ground-truth-dir ground_truth `
  --recordings-dir recordings
```

Backfill an explicit day/ticker set:

```bash
python scripts/backfill_historical.py `
  --date 2026-05-01 `
  --symbol AVTX `
  --symbol MSTR `
  --recordings-dir recordings
```

The script throttles requests for the free tier by default. Use `--dry-run` to
validate API access and parsing without writing files. Use `--overwrite` only
when intentionally replacing existing `bar.jsonl.gz` files for the fetched
dates and daily-baseline window.

## Replay Check

After backfill, create a replay universe directory whose files are plain JSON
ticker lists, for example `replay_universe/2026-05-01.json`:

```json
["AVTX", "MSTR"]
```

Then run:

```bash
python -m ross_trading.scanner.replay `
  --date 2026-05-01 `
  --source recordings `
  --universe-dir replay_universe `
  --db-url sqlite:///journal.sqlite
```

`ground_truth/YYYY-MM-DD.json` is an array of objects, not a plain ticker list,
so pass a separate replay universe directory until the recall runner owns that
conversion.

## Gaps

* The repository currently has only one curated day in `ground_truth/`; the
  Phase 2 target remains at least 10 human-curated trading days.
* This backfill path does not fetch quotes, tape prints, headlines, float, or
  halt/gap events. The replay assembler falls back to bar close when quotes are
  absent, but missing float/news data can still suppress picks or reduce mimic
  fidelity.
* Alpha Vantage free-tier rate limits make large multi-symbol backfills slow.
* The script is capable of creating the required recording files when a valid
  API key and source coverage are available, but this PR does not commit vendor
  recordings or fabricate historical data.
