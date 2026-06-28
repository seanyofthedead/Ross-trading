"""JSON serialization shared by :mod:`recorder` and :mod:`providers.replay`.

Wire format:

::

    {"_schema": 2, "ts_recorded": "<iso>", "type": "<EventType>",
     "payload": {<event-specific fields>}}

Each on-disk file holds a single ``EventType`` so readers don't need
to dispatch line-by-line. Decimals are encoded as strings to preserve
exact precision; datetimes use ISO-8601 with explicit UTC offset.

Schema versioning. The market-data payloads gained a per-message
``seq`` and a three-way timestamp split (``exchange_ts``/``vendor_ts``/
``ingest_ts``) in v2 (Wave 0). **Every v1 decoder is kept forever**:
:func:`decode_envelope` surfaces the schema version and the envelope's
``ts_recorded`` so the typed decoders can upgrade a v1 payload with
deterministic synthesized defaults --

- ``seq`` <- a caller-supplied ``fallback_seq`` (file order),
- ``exchange_ts = vendor_ts = ts`` (the single v1 timestamp),
- ``ingest_ts = ts_recorded`` (the envelope receipt time),

so old recordings replay bit-identically to how they replayed under v1.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ross_trading.data.types import (
    Bar,
    Correction,
    FeedGap,
    FloatRecord,
    Halt,
    Headline,
    Quote,
    Side,
    Tape,
)

SCHEMA_VERSION = 2

# Versions a current build can still decode. New schemas append to this set
# as they're introduced; the corresponding decoders must dispatch on the
# version field and accept old payloads forever.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1, 2})


class EventType(StrEnum):
    QUOTE = "quote"
    BAR = "bar"
    TAPE = "tape"
    HEADLINE = "headline"
    FLOAT = "float"
    FEED_GAP = "feed_gap"
    HALT = "halt"
    CORRECTION = "correction"


class DecodedEnvelope:
    """Lightweight carrier for a decoded line's type, payload, and metadata.

    Exposes the schema ``version`` and the envelope ``ts_recorded`` so
    the typed decoders can synthesize v1 defaults (``ingest_ts`` derives
    from ``ts_recorded``) without re-parsing the line.
    """

    __slots__ = ("event_type", "payload", "ts_recorded", "version")

    def __init__(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        version: int,
        ts_recorded: datetime,
    ) -> None:
        self.event_type = event_type
        self.payload = payload
        self.version = version
        self.ts_recorded = ts_recorded


def encode_event(event_type: EventType, payload: dict[str, Any], ts_recorded: datetime) -> str:
    envelope = {
        "_schema": SCHEMA_VERSION,
        "ts_recorded": ts_recorded.isoformat(),
        "type": event_type.value,
        "payload": payload,
    }
    return json.dumps(envelope, separators=(",", ":"))


def decode_envelope(line: str) -> DecodedEnvelope:
    obj = json.loads(line)
    version = obj.get("_schema")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = sorted(SUPPORTED_SCHEMA_VERSIONS)
        msg = f"unsupported schema version: {version!r} (supported: {supported})"
        raise ValueError(msg)
    return DecodedEnvelope(
        event_type=EventType(obj["type"]),
        payload=obj["payload"],
        version=int(version),
        ts_recorded=datetime.fromisoformat(obj["ts_recorded"]),
    )


def encode_quote(q: Quote) -> dict[str, Any]:
    return {
        "symbol": q.symbol,
        "seq": q.seq,
        "exchange_ts": q.exchange_ts.isoformat(),
        "vendor_ts": q.vendor_ts.isoformat(),
        "ingest_ts": q.ingest_ts.isoformat(),
        "bid": str(q.bid),
        "ask": str(q.ask),
        "bid_size": q.bid_size,
        "ask_size": q.ask_size,
    }


def decode_quote(
    p: dict[str, Any],
    *,
    version: int = SCHEMA_VERSION,
    ts_recorded: datetime | None = None,
    fallback_seq: int = 0,
) -> Quote:
    if version >= 2:
        return Quote(
            symbol=p["symbol"],
            exchange_ts=datetime.fromisoformat(p["exchange_ts"]),
            vendor_ts=datetime.fromisoformat(p["vendor_ts"]),
            ingest_ts=datetime.fromisoformat(p["ingest_ts"]),
            seq=int(p["seq"]),
            bid=Decimal(p["bid"]),
            ask=Decimal(p["ask"]),
            bid_size=int(p["bid_size"]),
            ask_size=int(p["ask_size"]),
        )
    ts = datetime.fromisoformat(p["ts"])
    return Quote(
        symbol=p["symbol"],
        exchange_ts=ts,
        vendor_ts=ts,
        ingest_ts=_v1_ingest(ts, ts_recorded),
        seq=fallback_seq,
        bid=Decimal(p["bid"]),
        ask=Decimal(p["ask"]),
        bid_size=int(p["bid_size"]),
        ask_size=int(p["ask_size"]),
    )


def encode_bar(b: Bar) -> dict[str, Any]:
    return {
        "symbol": b.symbol,
        "seq": b.seq,
        "exchange_ts": b.exchange_ts.isoformat(),
        "vendor_ts": b.vendor_ts.isoformat(),
        "ingest_ts": b.ingest_ts.isoformat(),
        "timeframe": b.timeframe,
        "open": str(b.open),
        "high": str(b.high),
        "low": str(b.low),
        "close": str(b.close),
        "volume": b.volume,
    }


def decode_bar(
    p: dict[str, Any],
    *,
    version: int = SCHEMA_VERSION,
    ts_recorded: datetime | None = None,
    fallback_seq: int = 0,
) -> Bar:
    if version >= 2:
        return Bar(
            symbol=p["symbol"],
            exchange_ts=datetime.fromisoformat(p["exchange_ts"]),
            vendor_ts=datetime.fromisoformat(p["vendor_ts"]),
            ingest_ts=datetime.fromisoformat(p["ingest_ts"]),
            seq=int(p["seq"]),
            timeframe=p["timeframe"],
            open=Decimal(p["open"]),
            high=Decimal(p["high"]),
            low=Decimal(p["low"]),
            close=Decimal(p["close"]),
            volume=int(p["volume"]),
        )
    ts = datetime.fromisoformat(p["ts"])
    return Bar(
        symbol=p["symbol"],
        exchange_ts=ts,
        vendor_ts=ts,
        ingest_ts=_v1_ingest(ts, ts_recorded),
        seq=fallback_seq,
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
        "seq": t.seq,
        "exchange_ts": t.exchange_ts.isoformat(),
        "vendor_ts": t.vendor_ts.isoformat(),
        "ingest_ts": t.ingest_ts.isoformat(),
        "price": str(t.price),
        "size": t.size,
        "side": t.side.value,
    }


def decode_tape(
    p: dict[str, Any],
    *,
    version: int = SCHEMA_VERSION,
    ts_recorded: datetime | None = None,
    fallback_seq: int = 0,
) -> Tape:
    if version >= 2:
        return Tape(
            symbol=p["symbol"],
            exchange_ts=datetime.fromisoformat(p["exchange_ts"]),
            vendor_ts=datetime.fromisoformat(p["vendor_ts"]),
            ingest_ts=datetime.fromisoformat(p["ingest_ts"]),
            seq=int(p["seq"]),
            price=Decimal(p["price"]),
            size=int(p["size"]),
            side=Side(p.get("side", Side.UNKNOWN.value)),
        )
    ts = datetime.fromisoformat(p["ts"])
    return Tape(
        symbol=p["symbol"],
        exchange_ts=ts,
        vendor_ts=ts,
        ingest_ts=_v1_ingest(ts, ts_recorded),
        seq=fallback_seq,
        price=Decimal(p["price"]),
        size=int(p["size"]),
        side=Side(p.get("side", Side.UNKNOWN.value)),
    )


def encode_halt(h: Halt) -> dict[str, Any]:
    return {
        "symbol": h.symbol,
        "state": h.state,
        "seq": h.seq,
        "exchange_ts": h.exchange_ts.isoformat(),
        "ingest_ts": h.ingest_ts.isoformat(),
        "reason_code": h.reason_code,
    }


def decode_halt(p: dict[str, Any]) -> Halt:
    state = p["state"]
    if state not in ("halted", "resumed"):
        msg = f"Halt.state must be 'halted' or 'resumed', got {state!r}"
        raise ValueError(msg)
    return Halt(
        symbol=p["symbol"],
        state=state,
        seq=int(p["seq"]),
        exchange_ts=datetime.fromisoformat(p["exchange_ts"]),
        ingest_ts=datetime.fromisoformat(p["ingest_ts"]),
        reason_code=p.get("reason_code"),
    )


def encode_correction(c: Correction) -> dict[str, Any]:
    return {
        "symbol": c.symbol,
        "corrects_seq": c.corrects_seq,
        "new_size": c.new_size,
        "new_price": None if c.new_price is None else str(c.new_price),
        "seq": c.seq,
        "exchange_ts": c.exchange_ts.isoformat(),
        "ingest_ts": c.ingest_ts.isoformat(),
    }


def decode_correction(p: dict[str, Any]) -> Correction:
    raw_price = p.get("new_price")
    raw_size = p.get("new_size")
    return Correction(
        symbol=p["symbol"],
        corrects_seq=int(p["corrects_seq"]),
        new_size=None if raw_size is None else int(raw_size),
        new_price=None if raw_price is None else Decimal(raw_price),
        seq=int(p["seq"]),
        exchange_ts=datetime.fromisoformat(p["exchange_ts"]),
        ingest_ts=datetime.fromisoformat(p["ingest_ts"]),
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


def encode_feed_gap(g: FeedGap) -> dict[str, Any]:
    return {
        "symbol": g.symbol,
        "start": g.start.isoformat(),
        "end": g.end.isoformat(),
        "reason": g.reason,
    }


def decode_feed_gap(p: dict[str, Any]) -> FeedGap:
    return FeedGap(
        symbol=p.get("symbol"),
        start=datetime.fromisoformat(p["start"]),
        end=datetime.fromisoformat(p["end"]),
        reason=p["reason"],
    )


def _v1_ingest(ts: datetime, ts_recorded: datetime | None) -> datetime:
    """Synthesize ``ingest_ts`` for a v1 payload.

    The contract is ``ingest_ts = ts_recorded`` (the envelope's local
    receipt time). When a caller decodes a bare payload without the
    envelope (e.g. a direct ``decode_quote`` unit test), fall back to the
    single v1 timestamp so the value object still constructs.
    """
    return ts_recorded if ts_recorded is not None else ts
