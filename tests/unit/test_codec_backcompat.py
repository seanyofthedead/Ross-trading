"""Wave 0 -- schema v1 -> v2 backward compatibility.

The v2 build must decode v1 recordings forever, synthesizing deterministic
defaults (``seq`` from file order, ``exchange_ts = vendor_ts = ts``,
``ingest_ts = ts_recorded``) so old recordings replay bit-identically to
their v1 behavior. The offline upgrade script re-stamps them as native v2,
non-destructively and idempotently.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.data._codec import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    EventType,
    decode_bar,
    decode_envelope,
    decode_quote,
    decode_tape,
    encode_event,
    encode_quote,
)
from ross_trading.data.types import Quote
from scripts.upgrade_recordings_v1_to_v2 import upgrade_recordings

if TYPE_CHECKING:
    from pathlib import Path

# A captured v1 quote/bar/tape line (the pre-Wave-0 wire format: single
# ``ts``, no ``seq``/three-timestamp split). ts_recorded is the envelope's
# local-receipt time, five seconds after the event's exchange time.
_V1_TS = "2026-04-26T13:30:00+00:00"
_V1_RECORDED = "2026-04-26T13:30:05+00:00"
_V1_QUOTE = (
    '{"_schema":1,"ts_recorded":"' + _V1_RECORDED + '","type":"quote","payload":'
    '{"symbol":"AVTX","ts":"' + _V1_TS + '","bid":"4.21","ask":"4.22",'
    '"bid_size":100,"ask_size":200}}'
)
_V1_BAR = (
    '{"_schema":1,"ts_recorded":"' + _V1_RECORDED + '","type":"bar","payload":'
    '{"symbol":"AVTX","ts":"' + _V1_TS + '","timeframe":"M1","open":"4.20",'
    '"high":"4.30","low":"4.18","close":"4.25","volume":12345}}'
)
_V1_TAPE = (
    '{"_schema":1,"ts_recorded":"' + _V1_RECORDED + '","type":"tape","payload":'
    '{"symbol":"AVTX","ts":"' + _V1_TS + '","price":"4.22","size":500,"side":"BUY"}}'
)

_EXCHANGE = datetime(2026, 4, 26, 13, 30, tzinfo=UTC)
_RECORDED = datetime(2026, 4, 26, 13, 30, 5, tzinfo=UTC)


def test_supported_versions_include_one_and_two() -> None:
    assert SCHEMA_VERSION == 2
    assert {1, 2} <= SUPPORTED_SCHEMA_VERSIONS


def test_v1_quote_decodes_with_synthesized_defaults() -> None:
    env = decode_envelope(_V1_QUOTE)
    assert env.version == 1
    assert env.event_type is EventType.QUOTE
    quote = decode_quote(
        env.payload,
        version=env.version,
        ts_recorded=env.ts_recorded,
        fallback_seq=7,
    )
    # seq <- file order; exchange_ts = vendor_ts = ts; ingest_ts = ts_recorded.
    assert quote.seq == 7
    assert quote.exchange_ts == _EXCHANGE
    assert quote.vendor_ts == _EXCHANGE
    assert quote.ingest_ts == _RECORDED
    # The legacy as-of timestamp (``ts``) is unchanged from v1 behavior.
    assert quote.ts == _EXCHANGE
    assert quote.bid == Decimal("4.21")


def test_v1_bar_and_tape_decode_with_synthesized_defaults() -> None:
    bar_env = decode_envelope(_V1_BAR)
    bar = decode_bar(
        bar_env.payload,
        version=bar_env.version,
        ts_recorded=bar_env.ts_recorded,
        fallback_seq=3,
    )
    assert (bar.seq, bar.exchange_ts, bar.ingest_ts) == (3, _EXCHANGE, _RECORDED)
    assert bar.volume == 12345

    tape_env = decode_envelope(_V1_TAPE)
    tape = decode_tape(
        tape_env.payload,
        version=tape_env.version,
        ts_recorded=tape_env.ts_recorded,
        fallback_seq=0,
    )
    assert tape.exchange_ts == _EXCHANGE
    assert tape.vendor_ts == _EXCHANGE
    assert tape.ingest_ts == _RECORDED
    assert tape.price == Decimal("4.22")


def test_v2_quote_round_trips() -> None:
    quote = Quote(
        symbol="AVTX",
        exchange_ts=_EXCHANGE,
        vendor_ts=datetime(2026, 4, 26, 13, 30, 1, tzinfo=UTC),
        ingest_ts=datetime(2026, 4, 26, 13, 30, 2, tzinfo=UTC),
        seq=42,
        bid=Decimal("4.21"),
        ask=Decimal("4.22"),
        bid_size=100,
        ask_size=200,
    )
    line = encode_event(EventType.QUOTE, encode_quote(quote), _RECORDED)
    env = decode_envelope(line)
    assert env.version == 2
    decoded = decode_quote(
        env.payload, version=env.version, ts_recorded=env.ts_recorded,
    )
    assert decoded == quote


def test_unsupported_future_version_raises() -> None:
    future = json.dumps(
        {"_schema": 99, "ts_recorded": _V1_RECORDED, "type": "quote", "payload": {}},
        separators=(",", ":"),
    )
    with pytest.raises(ValueError, match="unsupported schema version"):
        decode_envelope(future)


def _write_v1_recording(root: Path) -> None:
    day_dir = root / "2026-04-26"
    day_dir.mkdir(parents=True)
    for event, line in (
        ("quote", _V1_QUOTE),
        ("bar", _V1_BAR),
        ("tape", _V1_TAPE),
    ):
        with gzip.open(day_dir / f"{event}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _read_gz(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def test_upgrade_script_produces_native_v2(tmp_path: Path) -> None:
    source = tmp_path / "recordings"
    dest = tmp_path / "recordings-v2"
    _write_v1_recording(source)

    written = upgrade_recordings(source, dest)
    assert len(written) == 3

    # Every upgraded line is native v2 and carries the synthesized defaults.
    quote_lines = _read_gz(dest / "2026-04-26" / "quote.jsonl.gz")
    env = decode_envelope(quote_lines[0])
    assert env.version == 2
    quote = decode_quote(env.payload)
    assert quote.seq == 0  # single line -> file order 0
    assert quote.exchange_ts == _EXCHANGE
    assert quote.ingest_ts == _RECORDED

    # Source is untouched -- originals remain recoverable as v1.
    assert decode_envelope(_read_gz(source / "2026-04-26" / "quote.jsonl.gz")[0]).version == 1


def test_upgrade_script_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "recordings"
    dest = tmp_path / "out"
    _write_v1_recording(source)

    upgrade_recordings(source, dest)
    first = (dest / "2026-04-26" / "quote.jsonl.gz").read_bytes()
    # Re-running over the same source writes byte-identical output (no-op).
    upgrade_recordings(source, dest)
    second = (dest / "2026-04-26" / "quote.jsonl.gz").read_bytes()
    assert first == second

    # Upgrading an already-v2 tree is also a no-op at the content level.
    dest2 = tmp_path / "out2"
    upgrade_recordings(dest, dest2)
    assert _read_gz(dest2 / "2026-04-26" / "quote.jsonl.gz") == _read_gz(
        dest / "2026-04-26" / "quote.jsonl.gz"
    )


def test_upgrade_script_refuses_in_place(tmp_path: Path) -> None:
    source = tmp_path / "recordings"
    _write_v1_recording(source)
    with pytest.raises(ValueError, match="outside source"):
        upgrade_recordings(source, source)


async def test_v1_recording_replays_through_provider(tmp_path: Path) -> None:
    """A v1 recording on disk replays under the v2 build with synthesized fields."""
    from ross_trading.data.providers.replay import ReplayMode, ReplayProvider

    source = tmp_path / "recordings"
    _write_v1_recording(source)

    provider = ReplayProvider(source, mode=ReplayMode.AS_FAST_AS_POSSIBLE)
    await provider.connect()
    quotes = [q async for q in provider.subscribe_quotes(["AVTX"])]

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.seq == 0  # file order
    assert quote.exchange_ts == _EXCHANGE
    assert quote.vendor_ts == _EXCHANGE
    assert quote.ingest_ts == _RECORDED
    # As-of value is unchanged from v1 behavior.
    assert quote.ts == _EXCHANGE
