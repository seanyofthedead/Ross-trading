"""Backfill replay recordings from free/open HTTP CSV sources.

Current source: Alpha Vantage ``TIME_SERIES_INTRADAY`` + ``TIME_SERIES_DAILY``
CSV endpoints. The script writes the same gzip JSONL envelopes consumed by
``ReplayProvider``:

    recordings/<YYYY-MM-DD>/bar.jsonl.gz

No third-party dependency is required; network access is via ``urllib`` and
CSV parsing uses the standard library.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from ross_trading.data.recorder import FeedRecorder
from ross_trading.data.types import Bar, Timeframe

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
ET = ZoneInfo("America/New_York")
DEFAULT_API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    symbols: frozenset[str]
    days: tuple[date, ...]


def _parse_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        msg = "CSV response has no header"
        raise ValueError(msg)
    if "timestamp" not in reader.fieldnames:
        # Alpha Vantage error/rate-limit responses may arrive as CSV-looking
        # one-column text. Surface the first line without printing secrets.
        first = text.splitlines()[0] if text.splitlines() else "<empty response>"
        msg = f"unexpected CSV header from source: {first}"
        raise ValueError(msg)
    return list(reader)


def parse_alpha_vantage_intraday_csv(
    text: str,
    *,
    symbol: str,
    wanted_days: Iterable[date],
    timeframe: Timeframe = Timeframe.M1,
) -> list[Bar]:
    """Parse Alpha Vantage intraday CSV rows into UTC ``Bar`` objects."""
    wanted = frozenset(wanted_days)
    bars: list[Bar] = []
    for row in _parse_csv(text):
        local_ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ET,
        )
        if local_ts.date() not in wanted:
            continue
        bars.append(
            Bar(
                symbol=symbol.upper(),
                ts=local_ts.astimezone(UTC),
                timeframe=timeframe.value,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars


def parse_alpha_vantage_daily_csv(
    text: str,
    *,
    symbol: str,
    start: date,
    end_exclusive: date,
) -> list[Bar]:
    """Parse Alpha Vantage daily CSV rows into UTC D1 ``Bar`` objects."""
    bars: list[Bar] = []
    for row in _parse_csv(text):
        day = date.fromisoformat(row["timestamp"])
        if not (start <= day < end_exclusive):
            continue
        close_et = datetime.combine(day, dt_time(16, 0), tzinfo=ET)
        bars.append(
            Bar(
                symbol=symbol.upper(),
                ts=close_et.astimezone(UTC),
                timeframe=Timeframe.D1.value,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars


async def write_bars(recordings_dir: Path, bars: Sequence[Bar]) -> None:
    """Write bars through ``FeedRecorder`` so replay encoding stays canonical."""
    async with FeedRecorder(recordings_dir) as recorder:
        for bar in sorted(bars, key=lambda b: (b.ts, b.symbol, b.timeframe)):
            recorder.record_bar(bar)


def _alpha_vantage_csv(params: dict[str, str]) -> str:
    url = f"{ALPHA_VANTAGE_URL}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed HTTPS source.
            data = cast("bytes", response.read())
            return data.decode("utf-8-sig")
    except HTTPError as exc:
        msg = f"Alpha Vantage HTTP {exc.code} for {params.get('function')}"
        raise RuntimeError(msg) from exc
    except URLError as exc:
        msg = f"Alpha Vantage request failed: {exc.reason}"
        raise RuntimeError(msg) from exc


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _load_ground_truth_request(path: Path) -> BackfillRequest:
    symbols: set[str] = set()
    days: list[date] = []
    for file in sorted(path.glob("*.json")):
        day = date.fromisoformat(file.stem)
        rows = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            msg = f"{file} must contain a JSON list"
            raise ValueError(msg)
        for row in rows:
            symbols.add(str(row["ticker"]).upper())
        days.append(day)
    return BackfillRequest(symbols=frozenset(symbols), days=tuple(days))


def _resolve_request(args: argparse.Namespace) -> BackfillRequest:
    symbols = {s.upper() for s in args.symbol}
    days = {date.fromisoformat(d) for d in args.date}
    if args.ground_truth_dir is not None:
        from_ground_truth = _load_ground_truth_request(args.ground_truth_dir)
        symbols.update(from_ground_truth.symbols)
        days.update(from_ground_truth.days)
    if not symbols:
        msg = "no symbols supplied; pass --symbol or --ground-truth-dir"
        raise SystemExit(msg)
    if not days:
        msg = "no dates supplied; pass --date or --ground-truth-dir"
        raise SystemExit(msg)
    return BackfillRequest(symbols=frozenset(symbols), days=tuple(sorted(days)))


def _overwrite_bar_files(recordings_dir: Path, days: Iterable[date]) -> None:
    for day in days:
        path = recordings_dir / day.isoformat() / "bar.jsonl.gz"
        if path.exists():
            path.unlink()


def _daily_window(days: Sequence[date], lookback_days: int) -> tuple[date, date]:
    first = min(days) - timedelta(days=lookback_days)
    last_exclusive = max(days) + timedelta(days=1)
    return first, last_exclusive


def fetch_alpha_vantage_bars(
    request: BackfillRequest,
    *,
    api_key: str,
    daily_lookback_days: int,
    throttle_seconds: float,
) -> list[Bar]:
    bars: list[Bar] = []
    days_by_month: dict[str, list[date]] = defaultdict(list)
    for day in request.days:
        days_by_month[_month_key(day)].append(day)

    for symbol in sorted(request.symbols):
        for month, month_days in sorted(days_by_month.items()):
            text = _alpha_vantage_csv(
                {
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": symbol,
                    "interval": "1min",
                    "adjusted": "false",
                    "extended_hours": "true",
                    "month": month,
                    "outputsize": "full",
                    "datatype": "csv",
                    "apikey": api_key,
                }
            )
            bars.extend(
                parse_alpha_vantage_intraday_csv(
                    text,
                    symbol=symbol,
                    wanted_days=month_days,
                )
            )
            if throttle_seconds > 0:
                time.sleep(throttle_seconds)

        start, end_exclusive = _daily_window(request.days, daily_lookback_days)
        text = _alpha_vantage_csv(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": "full",
                "datatype": "csv",
                "apikey": api_key,
            }
        )
        bars.extend(
            parse_alpha_vantage_daily_csv(
                text,
                symbol=symbol,
                start=start,
                end_exclusive=end_exclusive,
            )
        )
        if throttle_seconds > 0:
            time.sleep(throttle_seconds)
    return bars


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill replay bar recordings from Alpha Vantage CSV data.",
    )
    parser.add_argument(
        "--recordings-dir",
        type=Path,
        default=Path("recordings"),
        help="Replay recordings root (default: %(default)s).",
    )
    parser.add_argument(
        "--ground-truth-dir",
        type=Path,
        help="Read symbols and dates from ground_truth/*.json.",
    )
    parser.add_argument(
        "--date",
        action="append",
        default=[],
        help="Trading date YYYY-MM-DD. May be passed more than once.",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Ticker to fetch. May be passed more than once.",
    )
    parser.add_argument(
        "--api-key",
        help=f"Alpha Vantage API key. Defaults to ${DEFAULT_API_KEY_ENV}.",
    )
    parser.add_argument(
        "--api-key-env",
        default=DEFAULT_API_KEY_ENV,
        help="Environment variable containing the API key (default: %(default)s).",
    )
    parser.add_argument(
        "--daily-lookback-days",
        type=int,
        default=45,
        help="Calendar days of D1 bars to write before the first target date.",
    )
    parser.add_argument(
        "--throttle-seconds",
        type=float,
        default=12.5,
        help="Delay between API calls for free-tier rate limits.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing bar.jsonl.gz files for affected dates.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse, but do not write recordings.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    request = _resolve_request(args)
    api_key = args.api_key or os.environ.get(args.api_key_env)
    if not api_key:
        msg = f"missing Alpha Vantage key; pass --api-key or set {args.api_key_env}"
        raise SystemExit(msg)
    bars = fetch_alpha_vantage_bars(
        request,
        api_key=api_key,
        daily_lookback_days=args.daily_lookback_days,
        throttle_seconds=args.throttle_seconds,
    )
    target_days = set(request.days)
    daily_start, daily_end = _daily_window(request.days, args.daily_lookback_days)
    affected_days = {
        b.ts.astimezone(UTC).date()
        for b in bars
        if b.timeframe == Timeframe.M1.value or daily_start <= b.ts.date() < daily_end
    }
    intraday_count = sum(1 for b in bars if b.timeframe == Timeframe.M1.value)
    daily_count = sum(1 for b in bars if b.timeframe == Timeframe.D1.value)
    print(
        f"parsed bars: intraday={intraday_count} daily={daily_count} "
        f"symbols={len(request.symbols)} target_days={len(target_days)}",
        file=sys.stderr,
    )
    if args.dry_run:
        return 0
    if args.overwrite:
        _overwrite_bar_files(args.recordings_dir, affected_days)
    asyncio.run(write_bars(args.recordings_dir, bars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
