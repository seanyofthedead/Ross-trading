"""Validate every committed ground-truth file is loader-clean.

Phase 2 -- ISSUE-011 (#68). The Phase 2 recall gate (ISSUE-015 / #70)
joins ``ground_truth/YYYY-MM-DD.json`` against the scanner journal.
A malformed file silently truncates the gate's denominator, so this
test enumerates every file in the repo's ``ground_truth/`` directory
and asserts the loader accepts it. Curators run this before opening
the PR; CI re-runs it on every push.

The test is marked ``integration`` so it runs in the integration job
(matches the marker convention in ``pyproject.toml``) and is excluded
from the fast unit pass.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ross_trading.journal.ground_truth import load_ground_truth

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GROUND_TRUTH_DIR = _REPO_ROOT / "ground_truth"


def _curated_days() -> list[date]:
    if not _GROUND_TRUTH_DIR.is_dir():
        return []
    days: list[date] = []
    for path in sorted(_GROUND_TRUTH_DIR.glob("*.json")):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError as exc:
            msg = (
                f"{path.name}: filename stem must be ISO date YYYY-MM-DD; "
                f"got {path.stem!r} ({exc})"
            )
            raise AssertionError(msg) from exc
    return days


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("day", _curated_days(), ids=lambda d: d.isoformat())
def test_ground_truth_file_is_loader_clean(day: date) -> None:
    """Every committed ground_truth/*.json file must load without raising."""
    entries = load_ground_truth(day, root=_GROUND_TRUTH_DIR)
    # At least one record per file -- an empty array is curator error
    # (delete the file rather than commit an empty oracle).
    assert entries, f"{day.isoformat()}.json: ground-truth file must not be empty"
