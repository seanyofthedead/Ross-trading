from __future__ import annotations

import gzip
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from ross_trading.data._codec import decode_bar, decode_envelope
from ross_trading.data.types import Timeframe
from scripts.backfill_historical import (
    parse_alpha_vantage_daily_csv,
    parse_alpha_vantage_intraday_csv,
    write_bars,
)

if TYPE_CHECKING:
    from pathlib import Path


INTRADAY_CSV = """timestamp,open,high,low,close,volume
2026-05-01 07:30:00,4.00,4.20,3.90,4.10,1000
2026-05-01 07:31:00,4.10,4.30,4.05,4.25,2000
2026-05-02 07:30:00,5.00,5.20,4.90,5.10,3000
"""

DAILY_CSV = """timestamp,open,high,low,close,volume
2026-04-30,3.50,3.80,3.40,3.70,500000
2026-05-01,4.00,4.30,3.90,4.25,700000
2026-05-02,4.20,4.50,4.10,4.40,800000
"""


def test_parse_alpha_vantage_intraday_csv_filters_days_and_converts_et_to_utc() -> None:
    bars = parse_alpha_vantage_intraday_csv(
        INTRADAY_CSV,
        symbol="avtx",
        wanted_days={date(2026, 5, 1)},
    )

    assert [b.symbol for b in bars] == ["AVTX", "AVTX"]
    assert [b.timeframe for b in bars] == [Timeframe.M1.value, Timeframe.M1.value]
    assert bars[0].ts == datetime(2026, 5, 1, 11, 30, tzinfo=UTC)
    assert bars[0].close == Decimal("4.10")
    assert bars[1].volume == 2000


def test_parse_alpha_vantage_daily_csv_writes_market_close_utc() -> None:
    bars = parse_alpha_vantage_daily_csv(
        DAILY_CSV,
        symbol="MSTR",
        start=date(2026, 4, 30),
        end_exclusive=date(2026, 5, 2),
    )

    assert [b.ts for b in bars] == [
        datetime(2026, 4, 30, 20, 0, tzinfo=UTC),
        datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
    ]
    assert all(b.timeframe == Timeframe.D1.value for b in bars)


async def test_write_bars_uses_replay_recording_shape(tmp_path: Path) -> None:
    bars = parse_alpha_vantage_intraday_csv(
        INTRADAY_CSV,
        symbol="AVTX",
        wanted_days={date(2026, 5, 1)},
    )

    await write_bars(tmp_path, bars)

    path = tmp_path / "2026-05-01" / "bar.jsonl.gz"
    assert path.exists()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]
    assert len(lines) == 2
    event_type, payload = decode_envelope(lines[0])
    assert event_type.value == "bar"
    assert decode_bar(payload) == bars[0]
