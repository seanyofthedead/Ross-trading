"""JSON serialization shared by :mod:`recorder` and :mod:`providers.replay`.

Wire format:

::

    {"_schema": 1, "ts_recorded": "<iso>", "type": "<EventType>",
     "payload": {<event-specific fields>}}

Each on-disk file holds a single ``EventType`` so readers don't need
to dispatch line-by-line. Decimals are encoded as strings to preserve
exact precision; datetimes use ISO-8601 with explicit UTC offset.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ross_trading.data.types import Bar, FloatRecord, Headline, Quote, Side, Tape

SCHEMA_VERSION = 1

# Versions a current build can still decode. New schemas append to this set
# as they're introduced; the corresponding decoders must dispatch on the
# version field and accept old payloads forever.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


class EventType(StrEnum):
    QUOTE = "quote"
    BAR = "bar"
    TAPE = "tape"
    HEADLINE = "headline"
    FLOAT = "float"


def encode_event(event_type: EventType, payload: dict[str, Any], ts_recorded: datetime) -> str:
    envelope = {
        "_schema": SCHEMA_VERSION,
        "ts_recorded": ts_recorded.isoformat(),
        "type": event_type.value,
        "payload": payload,
    }
    return json.dumps(envelope, separators=(",", ":"))


def decode_envelope(line: str) -> tuple[EventType, dict[str, Any]]:
    obj = json.loads(line)
    version = obj.get("_schema")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = sorted(SUPPORTED_SCHEMA_VERSIONS)
        msg = f"unsupported schema version: {version!r} (supported: {supported})"
        raise ValueError(msg)
    return EventType(obj["type"]), obj["payload"]


def encode_quote(q: Quote) -> dict[str, Any]:
    return {
        "symbol": q.symbol,
        "ts": q.ts.isoformat(),
        "bid": str(q.bid),
        "ask": str(q.ask),
        "bid_size": q.bid_size,
        "ask_size": q.ask_size,
    }


def decode_quote(p: dict[str, Any]) -> Quote:
    return Quote(
        symbol=p["symbol"],
        ts=datetime.fromisoformat(p["ts"]),
        bid=Decimal(p["bid"]),
        ask=Decimal(p["ask"]),
        bid_size=int(p["bid_size"]),
        ask_size=int(p["ask_size"]),
    )


def encode_bar(b: Bar) -> dict[str, Any]:
    return {
        "symbol": b.symbol,
        "ts": b.ts.isoformat(),
        "timeframe": b.timeframe,
        "open": str(b.open),
        "high": str(b.high),
        "low": str(b.low),
        "close": str(b.close),
        "volume": b.volume,
    }


def decode_bar(p: dict[str, Any]) -> Bar:
    return Bar(
        symbol=p["symbol"],
        ts=datetime.fromisoformat(p["ts"]),
        timeframe=p["timeframe"],
        open=Decimal(p["open"]),
        high=Decimal(p["high"]),
        low=Decimal(p["low"]),
        close=Decimal(p["close"]),
        volume=int(p["volume"]),
    )


def encode_tape(t: Tape) -> dict[str, Any]:
    return {
        "symbol": t.symbol,
        "ts": t.ts.isoformat(),
        "price": str(t.price),
        "size": t.size,
        "side": t.side.value,
    }


def decode_tape(p: dict[str, Any]) -> Tape:
    return Tape(
        symbol=p["symbol"],
        ts=datetime.fromisoformat(p["ts"]),
        price=Decimal(p["price"]),
        size=int(p["size"]),
        side=Side(p.get("side", Side.UNKNOWN.value)),
    )


def encode_headline(h: Headline) -> dict[str, Any]:
    return {
        "ticker": h.ticker,
        "ts": h.ts.isoformat(),
        "source": h.source,
        "title": h.title,
        "url": h.url,
        "body": h.body,
    }


def decode_headline(p: dict[str, Any]) -> Headline:
    return Headline(
        ticker=p["ticker"],
        ts=datetime.fromisoformat(p["ts"]),
        source=p["source"],
        title=p["title"],
        url=p.get("url"),
        body=p.get("body"),
    )


def encode_float(r: FloatRecord) -> dict[str, Any]:
    return {
        "ticker": r.ticker,
        "as_of": r.as_of.isoformat(),
        "float_shares": r.float_shares,
        "shares_outstanding": r.shares_outstanding,
        "source": r.source,
    }


def decode_float(p: dict[str, Any]) -> FloatRecord:
    return FloatRecord(
        ticker=p["ticker"],
        as_of=date.fromisoformat(p["as_of"]),
        float_shares=int(p["float_shares"]),
        shares_outstanding=int(p["shares_outstanding"]),
        source=p["source"],
    )
