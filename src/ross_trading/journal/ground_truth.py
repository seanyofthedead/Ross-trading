"""Hand-curated ground-truth loader for the Phase 2 recall metric.

Phase 2 -- Atom A6 (#45). Per Decision D3 (#37), the source of truth for
"what would Cameron have traded today" is a hand-curated JSON file per
trading day, committed to the repo at ``ground_truth/YYYY-MM-DD.json``.
A7 (#46) joins the records returned here against the scanner journal
to compute the 70% recall gate that closes Phase 2.

Convention -- include only **actively-called** tickers. Tickers Cameron
mentioned, watched, or rejected during the recap are *excluded*. The file
is the oracle of what would have been traded, not a transcript.

Per-file shape -- a JSON array of records. Schema:

* ``ticker`` (required) -- non-empty string. Stripped + upper-cased on
  load (curators sometimes write lowercase or with stray whitespace), so
  matching against ``ScannerPick.ticker`` is consistent with the
  convention used by ``Headline.dedup_key`` upstream.
* ``direction`` (required) -- the literal string ``"long"``. Single-valued
  today but kept explicit so a future short-bias variant is a schema
  bump, not a silent reinterpretation. Case-strict.
* ``time_called_out`` (optional) -- ``"HH:MM"`` ET, validated by format
  only (no zone math is applied here -- A7 does any window logic). Omit
  when the time is unknown.
* ``notes`` (optional) -- free text.

The schema is closed: any unknown key in a record is a loud error, so a
curator typo (``"note"`` for ``"notes"``) surfaces immediately rather
than silently dropping data. Adding a real new field requires bumping
this loader and the curated files together.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_GROUND_TRUTH_DIR = _REPO_ROOT / "ground_truth"

_REQUIRED_FIELDS: frozenset[str] = frozenset({"ticker", "direction"})
_OPTIONAL_FIELDS: frozenset[str] = frozenset({"time_called_out", "notes"})
_ALLOWED_FIELDS: frozenset[str] = _REQUIRED_FIELDS | _OPTIONAL_FIELDS

# Strict ``HH:MM`` 24-hour clock. ``time.fromisoformat`` would accept
# ``07:30:15`` and other ISO variants -- the curated oracle is meant to be
# uniform, so we reject anything that is not exactly two digits, colon,
# two digits.
_HHMM_RE = re.compile(r"\A([01]\d|2[0-3]):[0-5]\d\Z")


class GroundTruthError(ValueError):
    """Raised when a ground-truth file is malformed or violates the schema."""


@dataclass(frozen=True, slots=True)
class GroundTruthEntry:
    """A single hand-curated "would have traded" record for one ticker."""

    ticker: str
    direction: Literal["long"]
    time_called_out: time | None
    notes: str | None


def load_ground_truth(
    day: date,
    *,
    root: Path | None = None,
) -> list[GroundTruthEntry]:
    """Load curated ground-truth entries for *day*.

    *root* defaults to the repo's ``ground_truth/`` directory; tests
    inject a ``tmp_path``. Raises :class:`FileNotFoundError` when the
    day's file does not exist and :class:`GroundTruthError` (a
    ``ValueError`` subclass) on every other malformed-input mode.
    """
    base = root if root is not None else _DEFAULT_GROUND_TRUTH_DIR
    path = base / f"{day.isoformat()}.json"
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{path.name}: malformed JSON ({exc.msg} at line {exc.lineno})"
        raise GroundTruthError(msg) from exc
    if not isinstance(payload, list):
        msg = (
            f"{path.name}: top-level value must be a JSON array, "
            f"got {type(payload).__name__}"
        )
        raise GroundTruthError(msg)

    entries: list[GroundTruthEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        entry = _parse_entry(item, index=index)
        if entry.ticker in seen:
            msg = f"{path.name}: duplicate ticker {entry.ticker!r} at index {index}"
            raise GroundTruthError(msg)
        seen.add(entry.ticker)
        entries.append(entry)
    return entries


def _parse_entry(item: object, *, index: int) -> GroundTruthEntry:
    if not isinstance(item, dict):
        msg = f"index {index}: each record must be a JSON object, got {type(item).__name__}"
        raise GroundTruthError(msg)

    keys = set(item.keys())
    missing = _REQUIRED_FIELDS - keys
    if missing:
        msg = f"index {index}: missing required field(s) {sorted(missing)}"
        raise GroundTruthError(msg)
    unknown = keys - _ALLOWED_FIELDS
    if unknown:
        msg = f"index {index}: unknown field(s) {sorted(unknown)}"
        raise GroundTruthError(msg)

    ticker = _parse_ticker(item["ticker"], index=index)
    direction = _parse_direction(item["direction"], index=index)
    time_called_out = (
        _parse_time(item["time_called_out"], index=index)
        if "time_called_out" in item
        else None
    )
    notes = _parse_notes(item["notes"], index=index) if "notes" in item else None
    return GroundTruthEntry(
        ticker=ticker,
        direction=direction,
        time_called_out=time_called_out,
        notes=notes,
    )


def _parse_ticker(value: object, *, index: int) -> str:
    if not isinstance(value, str):
        msg = f"index {index}: ticker must be a string, got {type(value).__name__}"
        raise GroundTruthError(msg)
    cleaned = value.strip().upper()
    if not cleaned:
        msg = f"index {index}: ticker must be non-empty"
        raise GroundTruthError(msg)
    return cleaned


def _parse_direction(value: object, *, index: int) -> Literal["long"]:
    if value != "long":
        msg = (
            f"index {index}: direction must be the literal string 'long' "
            f"(case-strict), got {value!r}"
        )
        raise GroundTruthError(msg)
    return "long"


def _parse_time(value: object, *, index: int) -> time:
    if not isinstance(value, str) or not _HHMM_RE.fullmatch(value):
        msg = (
            f"index {index}: time_called_out must be a 'HH:MM' 24-hour string, "
            f"got {value!r}"
        )
        raise GroundTruthError(msg)
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _parse_notes(value: object, *, index: int) -> str:
    if not isinstance(value, str):
        msg = f"index {index}: notes must be a string, got {type(value).__name__}"
        raise GroundTruthError(msg)
    return value
