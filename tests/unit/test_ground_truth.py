"""Atom A6 (#45) -- ground-truth loader unit tests.

Reads hand-curated daily JSON files from ``ground_truth/YYYY-MM-DD.json``
and returns typed records. Per Decision D3 (#37), this is the oracle of
"what would Cameron have traded today" -- A7 (#46) joins this output
against the journal to compute the 70% recall metric that gates Phase 2.
"""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pytest

from ross_trading.journal.ground_truth import (
    GroundTruthEntry,
    GroundTruthError,
    load_ground_truth,
)


def _write(root: Path, day: date, payload: object) -> None:
    """Write *payload* as JSON for *day* under *root*/."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day.isoformat()}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_happy_path_returns_typed_entries(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [
            {
                "ticker": "TSLA",
                "direction": "long",
                "time_called_out": "07:32",
                "notes": "called on the recap as a clean long off the news catalyst",
            },
            {
                "ticker": "AVTX",
                "direction": "long",
            },
        ],
    )

    entries = load_ground_truth(day, root=tmp_path)

    assert entries == [
        GroundTruthEntry(
            ticker="TSLA",
            direction="long",
            time_called_out=time(7, 32),
            notes="called on the recap as a clean long off the news catalyst",
        ),
        GroundTruthEntry(
            ticker="AVTX",
            direction="long",
            time_called_out=None,
            notes=None,
        ),
    ]


def test_missing_file_raises_file_not_found_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ground_truth(date(2026, 5, 1), root=tmp_path)


def test_malformed_json_raises_ground_truth_error(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{day.isoformat()}.json").write_text("not json {", encoding="utf-8")

    with pytest.raises(GroundTruthError):
        load_ground_truth(day, root=tmp_path)


def test_top_level_must_be_a_list(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, {"ticker": "TSLA", "direction": "long"})

    with pytest.raises(GroundTruthError, match="top-level"):
        load_ground_truth(day, root=tmp_path)


def test_empty_array_returns_empty_list(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [])

    assert load_ground_truth(day, root=tmp_path) == []


def test_duplicate_ticker_within_a_file_rejected(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [
            {"ticker": "TSLA", "direction": "long"},
            {"ticker": "tsla", "direction": "long"},
        ],
    )

    with pytest.raises(GroundTruthError, match="duplicate"):
        load_ground_truth(day, root=tmp_path)


def test_lowercase_ticker_normalized_to_uppercase(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": "tsla", "direction": "long"}])

    [entry] = load_ground_truth(day, root=tmp_path)

    assert entry.ticker == "TSLA"


def test_ticker_whitespace_stripped(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": " tsla  ", "direction": "long"}])

    [entry] = load_ground_truth(day, root=tmp_path)

    assert entry.ticker == "TSLA"


def test_empty_ticker_rejected(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": "", "direction": "long"}])

    with pytest.raises(GroundTruthError, match="ticker"):
        load_ground_truth(day, root=tmp_path)


def test_whitespace_only_ticker_rejected(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": "   ", "direction": "long"}])

    with pytest.raises(GroundTruthError, match="ticker"):
        load_ground_truth(day, root=tmp_path)


def test_missing_required_field_rejected(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": "TSLA"}])

    with pytest.raises(GroundTruthError, match="direction"):
        load_ground_truth(day, root=tmp_path)


@pytest.mark.parametrize("bad_direction", ["short", "Long", "LONG", "buy", ""])
def test_direction_other_than_long_rejected(
    tmp_path: Path,
    bad_direction: str,
) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, [{"ticker": "TSLA", "direction": bad_direction}])

    with pytest.raises(GroundTruthError, match="direction"):
        load_ground_truth(day, root=tmp_path)


@pytest.mark.parametrize("bad_time", ["7:30", "25:00", "07:30:15", "07:60", "noon"])
def test_invalid_time_called_out_rejected(
    tmp_path: Path,
    bad_time: str,
) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [{"ticker": "TSLA", "direction": "long", "time_called_out": bad_time}],
    )

    with pytest.raises(GroundTruthError, match="time_called_out"):
        load_ground_truth(day, root=tmp_path)


def test_time_called_out_zero_padded_parses(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [{"ticker": "TSLA", "direction": "long", "time_called_out": "07:30"}],
    )

    [entry] = load_ground_truth(day, root=tmp_path)

    assert entry.time_called_out == time(7, 30)


def test_unknown_field_rejected(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [{"ticker": "TSLA", "direction": "long", "confidence": 0.9}],
    )

    with pytest.raises(GroundTruthError, match="unknown field"):
        load_ground_truth(day, root=tmp_path)


def test_notes_field_must_be_string_when_present(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(
        tmp_path,
        day,
        [{"ticker": "TSLA", "direction": "long", "notes": 42}],
    )

    with pytest.raises(GroundTruthError, match="notes"):
        load_ground_truth(day, root=tmp_path)


def test_record_must_be_object(tmp_path: Path) -> None:
    day = date(2026, 5, 1)
    _write(tmp_path, day, ["TSLA"])

    with pytest.raises(GroundTruthError, match="JSON object"):
        load_ground_truth(day, root=tmp_path)


def test_default_root_is_repo_ground_truth_directory() -> None:
    """Sanity-check that omitting *root* targets the repo's ground_truth/ dir.

    Asserts via the exception's ``filename`` attribute, which is platform-
    independent (unlike ``str(exc)`` whose Errno prefix is OS-specific).
    """
    bogus_day = date(1990, 1, 2)
    with pytest.raises(FileNotFoundError) as exc:
        load_ground_truth(bogus_day)

    assert exc.value.filename is not None
    target = Path(exc.value.filename)
    assert target.parent.name == "ground_truth"
    assert target.name == f"{bogus_day.isoformat()}.json"
