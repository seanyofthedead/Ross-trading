# YouTube Transcript Collection MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a captions-only collection pipeline for Ross Cameron's `@DaytradeWarrior` YouTube channel that produces a grep-able, citation-ready JSON corpus on disk. This is the MVP from section 9 of the design spec at `docs/superpowers/specs/2026-05-24-youtube-transcript-collection-design.md`.

**Architecture:** A new sibling repository at `C:\Users\peder\Documents\ross-trading-research`. Single-process Python module: yt-dlp discovers the channel's videos, a filter rule set picks the in-scope ones (educational + recap, ≤90 min, no Shorts, no livestreams), `youtube-transcript-api` pulls captions with polite rate limiting and exponential backoff, normalization cleans and timestamp-preserves the segments, results land as one JSON file per video under `data/raw_transcripts/`. SQLite (`videos` table with a `fetch_status` column) tracks state so re-runs are idempotent. No work queue, no Whisper, no chunking, no embedding — those are the production plan.

**Tech Stack:** Python 3.11, yt-dlp, youtube-transcript-api, sqlite3 (stdlib), pytest, ruff, mypy strict.

**Scope boundaries:**
- Public videos and public captions only. No auth bypass, no DRM, no paid content.
- Polite request cadence (1 req/s + jitter, exp backoff, quiet hours 09:30–16:00 ET weekdays).
- All extracted text remains internal research material; never redistributed, never used to fine-tune a model, never used to impersonate Ross Cameron.

**Where this plan lives:** This plan file is in the `ross-trading` repo for traceability with the spec. The code it produces lives in the new sibling repo at `C:\Users\peder\Documents\ross-trading-research`. Every `git` command in this plan operates on the sibling repo unless explicitly stated otherwise.

**Engineer's quick map:** The pipeline is essentially: `discover → filter → fetch_captions → normalize → write JSON`. Each step is a pure-ish function with explicit dependencies (passed in), so tests stay offline by injecting fakes. The SQLite DB is just bookkeeping — the source of truth for content is the JSON files on disk.

---

## File Structure (sibling repo `ross-trading-research`)

```
ross-trading-research/
  pyproject.toml                       # project config, deps, lint/test config
  .gitignore                           # excludes data/, .venv/, __pycache__/, *.pyc
  README.md                            # how to install + run
  pipeline/
    __init__.py
    db.py                              # sqlite connection + schema init
    discover.py                        # yt-dlp wrapper, returns list[VideoMeta]
    filter.py                          # apply title/duration rules
    fetch_captions.py                  # youtube-transcript-api wrapper + retry
    normalize.py                       # cleaning + sentence reconstruction
    storage.py                         # JSON-to-disk writer
    run_mvp.py                         # orchestrator entrypoint
    types.py                           # pydantic models / dataclasses
    rules/
      __init__.py
      title_filters.py                 # include/exclude regex lists
      ad_read_filters.py               # ad-block regex list
      term_dictionary.py               # mid-word-split fixes
  tests/
    __init__.py
    conftest.py                        # shared fixtures
    fakes.py                           # fake discoverer + fake fetcher
    test_db.py
    test_discover.py
    test_filter.py
    test_fetch_captions.py
    test_normalize.py
    test_storage.py
    test_run_mvp.py
  data/                                # gitignored
    .gitkeep
```

**Why this shape:**
- `pipeline/run_mvp.py` is the *only* file with side-effecting side-effects (network, disk, sqlite). Every other module is a pure function or a thin wrapper with injectable dependencies. That means the test suite can run offline and fast.
- `pipeline/rules/` holds the human-editable knobs (which titles count, which ad reads to strip). Putting them in their own files keeps the orchestrator legible.
- `tests/fakes.py` centralizes the in-memory test doubles so each test file stays focused on its one component.

---

## Task 1: Bootstrap the sibling repo

**Files:**
- Create: `C:\Users\peder\Documents\ross-trading-research\` (the directory itself)
- Create: `C:\Users\peder\Documents\ross-trading-research\pyproject.toml`
- Create: `C:\Users\peder\Documents\ross-trading-research\.gitignore`
- Create: `C:\Users\peder\Documents\ross-trading-research\README.md`
- Create: `C:\Users\peder\Documents\ross-trading-research\pipeline\__init__.py`
- Create: `C:\Users\peder\Documents\ross-trading-research\tests\__init__.py`
- Create: `C:\Users\peder\Documents\ross-trading-research\data\.gitkeep`

- [ ] **Step 1: Create the repo directory and init git**

PowerShell:
```powershell
New-Item -ItemType Directory -Path C:\Users\peder\Documents\ross-trading-research -Force | Out-Null
Set-Location C:\Users\peder\Documents\ross-trading-research
git init
git branch -M main
```

Expected output: `Initialized empty Git repository in C:/Users/peder/Documents/ross-trading-research/.git/`

- [ ] **Step 2: Create the source/test/data subdirectories and placeholder files**

```powershell
New-Item -ItemType Directory -Path pipeline\rules -Force | Out-Null
New-Item -ItemType Directory -Path tests -Force | Out-Null
New-Item -ItemType Directory -Path data\raw_transcripts -Force | Out-Null
New-Item -ItemType File -Path pipeline\__init__.py | Out-Null
New-Item -ItemType File -Path pipeline\rules\__init__.py | Out-Null
New-Item -ItemType File -Path tests\__init__.py | Out-Null
New-Item -ItemType File -Path data\.gitkeep | Out-Null
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "ross-trading-research"
version = "0.0.0"
description = "Public-transcript collection pipeline supporting the ross-trading agent. Internal research only."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "yt-dlp>=2024.10.0",
    "youtube-transcript-api>=0.6.2,<1",
    "pydantic>=2.7,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "ruff>=0.5",
    "mypy>=1.10",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["pipeline*"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "PT", "TCH"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S106"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["pipeline", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.mypy_cache/
.ruff_cache/
.pytest_cache/

# Data and logs (regenerable; not for git)
data/raw_transcripts/
data/audio_cache/
data/pipeline.db
data/pipeline.db-journal
data/pipeline.db-wal
data/pipeline.db-shm
logs/

# OS
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/
```

- [ ] **Step 5: Write minimal `README.md`**

```markdown
# ross-trading-research

Public-transcript collection pipeline supporting the `ross-trading` agent.

**Status:** MVP — captions-only, single-process, JSON-on-disk corpus.

**Scope boundary:** This pipeline only uses public videos and public captions. No auth bypass, no DRM, no paid-content scraping. The corpus is internal research material — not redistributed.

See `docs/superpowers/specs/2026-05-24-youtube-transcript-collection-design.md` in the `ross-trading` repo for the full design.

## Install (Windows + PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run the MVP

```powershell
python -m pipeline.run_mvp --channel "@DaytradeWarrior" --db data\pipeline.db --out data\raw_transcripts --max-videos 25
```

## Run the tests

```powershell
pytest -v
```
```

- [ ] **Step 6: Install dev dependencies and confirm pytest runs**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest --collect-only
```

Expected: pytest reports `0 tests collected` (no tests yet). No errors.

- [ ] **Step 7: Commit**

```powershell
git add .
git commit -m "chore: bootstrap ross-trading-research repo (MVP scaffold)"
```

---

## Task 2: SQLite DB module

**Files:**
- Create: `pipeline/db.py`
- Create: `tests/test_db.py`

The MVP schema is two tables only: `channels` (single row) and `videos` (one row per discovered video, with a `fetch_status` column for state). No `transcripts`, `chunks`, `vec_chunks`, or `fetch_jobs` — those arrive in the production plan.

`fetch_status` enum (string): `pending` | `succeeded` | `failed_no_captions` | `failed_other` | `out_of_scope`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
"""Tests for the SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline.db import connect, init_db, upsert_video


def test_init_db_creates_channels_and_videos_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]

    assert "channels" in tables
    assert "videos" in tables


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    init_db(db_path)  # second call must not raise

    with connect(db_path) as conn:
        cur = conn.execute("SELECT count(*) FROM channels")
        assert cur.fetchone()[0] == 0


def test_upsert_video_inserts_then_updates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with connect(db_path) as conn:
        upsert_video(
            conn,
            video_id="abc12345xyz",
            channel_id="UCtest",
            title="First Trade Recap",
            duration_seconds=300,
            upload_date="2026-05-01",
            view_count=1000,
            is_live=False,
            is_short=False,
            availability="public",
        )
        upsert_video(
            conn,
            video_id="abc12345xyz",
            channel_id="UCtest",
            title="First Trade Recap (Updated)",
            duration_seconds=305,
            upload_date="2026-05-01",
            view_count=1500,
            is_live=False,
            is_short=False,
            availability="public",
        )

        cur = conn.execute(
            "SELECT title, view_count, fetch_status FROM videos WHERE id = ?",
            ("abc12345xyz",),
        )
        title, view_count, fetch_status = cur.fetchone()

    assert title == "First Trade Recap (Updated)"
    assert view_count == 1500
    assert fetch_status == "pending"  # default preserved on update


def test_connect_uses_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with connect(db_path) as conn:
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    assert mode == "wal"


def test_upsert_video_requires_known_availability(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        upsert_video(
            conn,
            video_id="bad",
            channel_id="UCtest",
            title="x",
            duration_seconds=1,
            upload_date="2026-01-01",
            view_count=0,
            is_live=False,
            is_short=False,
            availability="totally-invalid",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.db'`.

- [ ] **Step 3: Write the minimal implementation**

Create `pipeline/db.py`:

```python
"""SQLite connection + schema management for the MVP pipeline.

MVP schema: `channels` (single row per tracked channel) + `videos` (one row per
discovered video, with a `fetch_status` column tracking pipeline state).

Production-grade `transcripts`, `chunks`, `vec_chunks`, and `fetch_jobs` tables
arrive in the production plan. They are intentionally absent here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS channels (
    id                       TEXT PRIMARY KEY,
    handle                   TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL,
    discovered_at            TEXT NOT NULL,
    last_discovery_run_at    TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    id                  TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL REFERENCES channels(id),
    title               TEXT NOT NULL,
    description         TEXT,
    upload_date         TEXT,
    duration_seconds    INTEGER NOT NULL,
    view_count          INTEGER,
    is_live             INTEGER NOT NULL DEFAULT 0,
    is_short            INTEGER NOT NULL DEFAULT 0,
    availability        TEXT NOT NULL CHECK (
        availability IN ('public','unlisted','private','members_only','removed','restricted')
    ),
    in_scope            INTEGER NOT NULL DEFAULT 0,
    scope_reason        TEXT,
    fetch_status        TEXT NOT NULL DEFAULT 'pending' CHECK (
        fetch_status IN ('pending','succeeded','failed_no_captions','failed_other','out_of_scope')
    ),
    last_error          TEXT,
    discovered_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_channel_scope ON videos(channel_id, in_scope);
CREATE INDEX IF NOT EXISTS idx_videos_status        ON videos(fetch_status);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection in WAL mode with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """Create the schema if missing. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def upsert_video(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    channel_id: str,
    title: str,
    duration_seconds: int,
    upload_date: str | None,
    view_count: int | None,
    is_live: bool,
    is_short: bool,
    availability: str,
    description: str | None = None,
) -> None:
    """Insert a new video row or update mutable fields on an existing row.

    Preserves `fetch_status`, `in_scope`, `scope_reason`, and `discovered_at`
    on updates — those are pipeline state, not source metadata.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO videos (
            id, channel_id, title, description, upload_date,
            duration_seconds, view_count, is_live, is_short, availability,
            discovered_at, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title             = excluded.title,
            description       = excluded.description,
            upload_date       = excluded.upload_date,
            duration_seconds  = excluded.duration_seconds,
            view_count        = excluded.view_count,
            is_live           = excluded.is_live,
            is_short          = excluded.is_short,
            availability      = excluded.availability,
            last_seen_at      = excluded.last_seen_at
        """,
        (
            video_id, channel_id, title, description, upload_date,
            duration_seconds, view_count, int(is_live), int(is_short), availability,
            now, now,
        ),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
pytest tests/test_db.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add pipeline/db.py tests/test_db.py
git commit -m "feat(db): sqlite schema for channels + videos with fetch_status"
```

---

## Task 3: Channel discovery (yt-dlp wrapper)

**Files:**
- Create: `pipeline/types.py`
- Create: `pipeline/discover.py`
- Create: `tests/fakes.py`
- Create: `tests/conftest.py`
- Create: `tests/test_discover.py`

The discoverer is a thin adapter around `yt-dlp`'s flat-playlist extraction. Tests inject a fake `flat_extract` callable so they don't touch the network.

- [ ] **Step 1: Write the failing test**

Create `tests/fakes.py`:

```python
"""Shared in-memory test doubles for offline tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def make_fake_flat_extract(
    response: dict[str, Any],
) -> Callable[[str], dict[str, Any]]:
    """Return a callable that mimics yt_dlp.YoutubeDL.extract_info."""
    def _extract(url: str) -> dict[str, Any]:
        return response
    return _extract
```

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    from pipeline.db import init_db
    path = tmp_path / "test.db"
    init_db(path)
    return path
```

Create `tests/test_discover.py`:

```python
"""Tests for the channel-discovery wrapper."""

from __future__ import annotations

from pipeline.discover import discover_channel_videos
from tests.fakes import make_fake_flat_extract


def test_discover_returns_videos_with_normalized_fields() -> None:
    fake_response = {
        "id": "UCBayuhPYhDnxZbsKHqaXfOQ",
        "channel": "Day Trade Warrior",
        "entries": [
            {
                "id": "vid00000001",
                "title": "Trade Recap May 1",
                "duration": 600,
                "view_count": 12345,
                "upload_date": "20260501",
                "availability": None,
                "live_status": "not_live",
            },
            {
                "id": "vid00000002",
                "title": "How To Trade Gap and Go",
                "duration": 1800,
                "view_count": 50000,
                "upload_date": "20260502",
                "availability": None,
                "live_status": "not_live",
            },
        ],
    }
    extract = make_fake_flat_extract(fake_response)

    channel, videos = discover_channel_videos("@DaytradeWarrior", flat_extract=extract)

    assert channel.id == "UCBayuhPYhDnxZbsKHqaXfOQ"
    assert channel.handle == "@DaytradeWarrior"
    assert channel.name == "Day Trade Warrior"
    assert len(videos) == 2
    assert videos[0].id == "vid00000001"
    assert videos[0].upload_date == "2026-05-01"
    assert videos[0].duration_seconds == 600
    assert videos[0].availability == "public"
    assert videos[0].is_live is False
    assert videos[0].is_short is False


def test_discover_marks_shorts_and_livestreams() -> None:
    fake_response = {
        "id": "UCtest",
        "channel": "Test Channel",
        "entries": [
            {
                "id": "shortvideo1",
                "title": "60s Quick Tip",
                "duration": 45,
                "view_count": 100,
                "upload_date": "20260501",
                "availability": None,
                "live_status": "not_live",
            },
            {
                "id": "stream00001",
                "title": "Friday Live Stream",
                "duration": 14400,
                "view_count": 5000,
                "upload_date": "20260502",
                "availability": None,
                "live_status": "was_live",
            },
        ],
    }
    extract = make_fake_flat_extract(fake_response)

    _, videos = discover_channel_videos("@DaytradeWarrior", flat_extract=extract)
    by_id = {v.id: v for v in videos}

    assert by_id["shortvideo1"].is_short is True
    assert by_id["stream00001"].is_live is True


def test_discover_maps_availability_strings() -> None:
    fake_response = {
        "id": "UCtest",
        "channel": "Test Channel",
        "entries": [
            {"id": "v1", "title": "a", "duration": 100, "view_count": 1,
             "upload_date": "20260501", "availability": "unlisted", "live_status": "not_live"},
            {"id": "v2", "title": "b", "duration": 100, "view_count": 1,
             "upload_date": "20260501", "availability": "subscriber_only", "live_status": "not_live"},
            {"id": "v3", "title": "c", "duration": 100, "view_count": 1,
             "upload_date": "20260501", "availability": "needs_auth", "live_status": "not_live"},
        ],
    }
    extract = make_fake_flat_extract(fake_response)

    _, videos = discover_channel_videos("@DaytradeWarrior", flat_extract=extract)
    by_id = {v.id: v.availability for v in videos}

    assert by_id["v1"] == "unlisted"
    assert by_id["v2"] == "members_only"
    assert by_id["v3"] == "private"


def test_discover_skips_entries_with_missing_id() -> None:
    fake_response = {
        "id": "UCtest",
        "channel": "Test Channel",
        "entries": [
            {"id": "good", "title": "ok", "duration": 100, "view_count": 1,
             "upload_date": "20260501", "availability": None, "live_status": "not_live"},
            {"title": "no id"},
            None,
        ],
    }
    extract = make_fake_flat_extract(fake_response)

    _, videos = discover_channel_videos("@DaytradeWarrior", flat_extract=extract)
    assert [v.id for v in videos] == ["good"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/test_discover.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.discover'`.

- [ ] **Step 3: Write `pipeline/types.py`**

```python
"""Shared dataclasses for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelMeta:
    id: str
    handle: str
    name: str


@dataclass(frozen=True)
class VideoMeta:
    id: str
    title: str
    duration_seconds: int
    upload_date: str | None       # ISO date, e.g. "2026-05-01", or None if missing
    view_count: int | None
    is_live: bool
    is_short: bool
    availability: str             # one of: public, unlisted, private, members_only, removed, restricted
```

- [ ] **Step 4: Write `pipeline/discover.py`**

```python
"""Channel-discovery wrapper around yt-dlp's flat-playlist extraction.

`yt-dlp` is invoked via its Python API, but to keep tests offline we accept
a `flat_extract` callable. Production code passes a real yt-dlp closure;
tests pass an in-memory dict.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pipeline.types import ChannelMeta, VideoMeta

SHORT_DURATION_THRESHOLD = 60  # seconds; YouTube Shorts are <= 60s

_AVAILABILITY_MAP: dict[str | None, str] = {
    None: "public",
    "public": "public",
    "unlisted": "unlisted",
    "private": "private",
    "subscriber_only": "members_only",
    "premium_only": "members_only",
    "needs_auth": "private",
}


def discover_channel_videos(
    channel_handle: str,
    *,
    flat_extract: Callable[[str], dict[str, Any]],
) -> tuple[ChannelMeta, list[VideoMeta]]:
    """Fetch the channel's video list and return normalized records.

    Args:
        channel_handle: e.g. "@DaytradeWarrior" — passed through as-is to yt-dlp.
        flat_extract: callable mimicking yt_dlp.YoutubeDL.extract_info, returning
            a dict with "id", "channel", and "entries".
    """
    url = f"https://www.youtube.com/{channel_handle}/videos"
    raw = flat_extract(url)

    channel = ChannelMeta(
        id=str(raw["id"]),
        handle=channel_handle,
        name=str(raw.get("channel") or raw.get("title") or channel_handle),
    )

    videos: list[VideoMeta] = []
    for entry in raw.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        vid_id = entry.get("id")
        if not vid_id:
            continue

        duration = int(entry.get("duration") or 0)
        live_status = entry.get("live_status") or "not_live"
        videos.append(
            VideoMeta(
                id=str(vid_id),
                title=str(entry.get("title") or ""),
                duration_seconds=duration,
                upload_date=_iso_date(entry.get("upload_date")),
                view_count=_optional_int(entry.get("view_count")),
                is_live=live_status in {"is_live", "was_live", "post_live"},
                is_short=0 < duration <= SHORT_DURATION_THRESHOLD,
                availability=_AVAILABILITY_MAP.get(entry.get("availability"), "public"),
            )
        )

    return channel, videos


def _iso_date(raw: object) -> str | None:
    """Convert yt-dlp's YYYYMMDD upload_date into ISO YYYY-MM-DD."""
    if not raw:
        return None
    s = str(raw)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_real_flat_extract() -> Callable[[str], dict[str, Any]]:
    """Return a yt-dlp-backed extractor for production use.

    Imported lazily so tests don't pay the cost.
    """
    from yt_dlp import YoutubeDL

    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": "only_download",
    }

    def _extract(url: str) -> dict[str, Any]:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info or {}

    return _extract
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
pytest tests/test_discover.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```powershell
git add pipeline/types.py pipeline/discover.py tests/fakes.py tests/conftest.py tests/test_discover.py
git commit -m "feat(discover): yt-dlp flat-playlist wrapper with injectable extractor"
```

---

## Task 4: Filter (title + duration rules)

**Files:**
- Create: `pipeline/rules/title_filters.py`
- Create: `pipeline/filter.py`
- Create: `tests/test_filter.py`

Filter decides which videos count as in-scope based on:
1. Duration ≤ 90 min (5400s) and ≥ 60s (Shorts excluded)
2. Not a livestream (`is_live=False`)
3. Title matches at least one of the include patterns
4. `availability == 'public'`

Out-of-scope videos still get stored, just marked `out_of_scope` so they won't be retried.

- [ ] **Step 1: Write the failing test**

Create `tests/test_filter.py`:

```python
"""Tests for the title + duration filter."""

from __future__ import annotations

from pathlib import Path

from pipeline.db import connect, init_db, upsert_video
from pipeline.filter import apply_filter


def _seed(db_path: Path) -> None:
    init_db(db_path)
    rows = [
        # in-scope: recap, public, <90 min
        dict(video_id="recap0001a", title="Trade Recap May 1", duration_seconds=600,
             availability="public", is_live=False, is_short=False),
        # in-scope: explainer
        dict(video_id="explain002b", title="How to Trade the Micro Pullback", duration_seconds=1200,
             availability="public", is_live=False, is_short=False),
        # out: duration too long (livestream-like)
        dict(video_id="toolong03c", title="Trade Recap Marathon", duration_seconds=10000,
             availability="public", is_live=False, is_short=False),
        # out: livestream flagged
        dict(video_id="livestrm4d", title="Friday Recap and Q&A", duration_seconds=5000,
             availability="public", is_live=True, is_short=False),
        # out: Short
        dict(video_id="shorts005e", title="Quick recap clip", duration_seconds=45,
             availability="public", is_live=False, is_short=True),
        # out: non-public
        dict(video_id="privat006f", title="Trade Recap Members Only", duration_seconds=900,
             availability="members_only", is_live=False, is_short=False),
        # out: title doesn't match any include pattern
        dict(video_id="noisey007g", title="Vlog: my new puppy", duration_seconds=600,
             availability="public", is_live=False, is_short=False),
    ]

    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO channels (id, handle, name, discovered_at) VALUES (?,?,?,?)",
            ("UCtest", "@DaytradeWarrior", "Day Trade Warrior", "2026-05-24T00:00:00+00:00"),
        )
        for r in rows:
            upsert_video(
                conn,
                channel_id="UCtest",
                upload_date="2026-05-01",
                view_count=100,
                **r,
            )


def test_apply_filter_marks_expected_videos_in_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)

    summary = apply_filter(db_path)

    assert summary.in_scope == 2
    assert summary.out_of_scope == 5

    with connect(db_path) as conn:
        rows = dict(conn.execute("SELECT id, in_scope FROM videos").fetchall())

    assert rows["recap0001a"] == 1
    assert rows["explain002b"] == 1
    assert rows["toolong03c"] == 0
    assert rows["livestrm4d"] == 0
    assert rows["shorts005e"] == 0
    assert rows["privat006f"] == 0
    assert rows["noisey007g"] == 0


def test_apply_filter_sets_fetch_status_out_of_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    apply_filter(db_path)

    with connect(db_path) as conn:
        rows = dict(conn.execute("SELECT id, fetch_status FROM videos").fetchall())

    assert rows["recap0001a"] == "pending"
    assert rows["toolong03c"] == "out_of_scope"
    assert rows["livestrm4d"] == "out_of_scope"
    assert rows["shorts005e"] == "out_of_scope"
    assert rows["privat006f"] == "out_of_scope"
    assert rows["noisey007g"] == "out_of_scope"


def test_apply_filter_writes_scope_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    apply_filter(db_path)

    with connect(db_path) as conn:
        rows = dict(conn.execute("SELECT id, scope_reason FROM videos").fetchall())

    assert "matched:" in rows["recap0001a"]
    assert "duration" in rows["toolong03c"]
    assert "livestream" in rows["livestrm4d"]
    assert "short" in rows["shorts005e"]
    assert "availability" in rows["privat006f"]
    assert "no title match" in rows["noisey007g"]


def test_apply_filter_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    s1 = apply_filter(db_path)
    s2 = apply_filter(db_path)
    assert s1.in_scope == s2.in_scope == 2
    assert s1.out_of_scope == s2.out_of_scope == 5
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/test_filter.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.filter'`.

- [ ] **Step 3: Write `pipeline/rules/title_filters.py`**

```python
"""Hand-curated include/exclude patterns for filtering @DaytradeWarrior titles.

INCLUDE patterns are matched case-insensitively against video titles.
EXCLUDE patterns override INCLUDE matches (defensive against e.g. "live recap").

Edit these lists to reshape what counts as in-scope. No code changes needed.
"""

from __future__ import annotations

import re

_INCLUDE = [
    r"\brecap\b",
    r"\bgap\s+(?:and|n)\s+go\b",
    r"\bmicro\s*pull\s*back\b",
    r"\bmomentum\b",
    r"\bsmall\s+cap\b",
    r"\bhow\s+to\b",
    r"\btutorial\b",
    r"\blesson\b",
    r"\bstrategy\b",
    r"\bstrategies\b",
    r"\bsetup\b",
    r"\breview\b",
    r"\bpsychology\b",
    r"\brisk\s+management\b",
    r"\bday\s+trade(?:r)?\b",
    r"\bscaling\b",
    r"\bentries\b",
    r"\bexits\b",
    r"\brules\b",
]

_EXCLUDE = [
    r"\blive\s+(?:stream|streaming|today)\b",
    r"\bgiveaway\b",
    r"\bsponsored\b",
]

INCLUDE_RE = re.compile("|".join(_INCLUDE), re.IGNORECASE)
EXCLUDE_RE = re.compile("|".join(_EXCLUDE), re.IGNORECASE)


def title_matches(title: str) -> tuple[bool, str]:
    """Return (in_scope, reason)."""
    if EXCLUDE_RE.search(title):
        m = EXCLUDE_RE.search(title)
        return False, f"excluded by pattern: {m.group(0)!r}"
    m = INCLUDE_RE.search(title)
    if m:
        return True, f"matched: {m.group(0)!r}"
    return False, "no title match"
```

- [ ] **Step 4: Write `pipeline/filter.py`**

```python
"""Apply title + duration + availability filters to discovered videos.

Filter rules (all must hold for `in_scope=True`):
- duration ≤ MAX_DURATION_SECONDS (90 min)
- duration ≥ MIN_DURATION_SECONDS (excludes Shorts even if is_short flag missed)
- is_live = False
- availability = 'public'
- title matches an INCLUDE pattern in pipeline/rules/title_filters
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.db import connect
from pipeline.rules.title_filters import title_matches

MIN_DURATION_SECONDS = 60       # exclude Shorts and 1-clip teasers
MAX_DURATION_SECONDS = 5400     # 90 minutes


@dataclass(frozen=True)
class FilterSummary:
    in_scope: int
    out_of_scope: int


def apply_filter(db_path: Path) -> FilterSummary:
    """Apply scope rules to every video row. Idempotent.

    Only videos still `pending` or already `out_of_scope` get re-evaluated;
    `succeeded` / `failed_*` rows are left alone to preserve history.
    """
    in_scope = 0
    out_of_scope = 0

    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT id, title, duration_seconds, is_live, is_short, availability "
            "FROM videos WHERE fetch_status IN ('pending','out_of_scope')"
        )
        rows = cur.fetchall()

        for vid_id, title, duration, is_live, is_short, availability in rows:
            in_scope_flag, reason, new_status = _decide(
                title=title,
                duration_seconds=duration,
                is_live=bool(is_live),
                is_short=bool(is_short),
                availability=availability,
            )
            conn.execute(
                "UPDATE videos SET in_scope = ?, scope_reason = ?, fetch_status = ? "
                "WHERE id = ?",
                (int(in_scope_flag), reason, new_status, vid_id),
            )
            if in_scope_flag:
                in_scope += 1
            else:
                out_of_scope += 1

    return FilterSummary(in_scope=in_scope, out_of_scope=out_of_scope)


def _decide(
    *,
    title: str,
    duration_seconds: int,
    is_live: bool,
    is_short: bool,
    availability: str,
) -> tuple[bool, str, str]:
    """Return (in_scope, scope_reason, fetch_status)."""
    if availability != "public":
        return False, f"out: availability={availability}", "out_of_scope"
    if is_live:
        return False, "out: livestream", "out_of_scope"
    if is_short or duration_seconds < MIN_DURATION_SECONDS:
        return False, f"out: short or too brief ({duration_seconds}s)", "out_of_scope"
    if duration_seconds > MAX_DURATION_SECONDS:
        return (
            False,
            f"out: duration {duration_seconds}s > cap {MAX_DURATION_SECONDS}",
            "out_of_scope",
        )

    matched, reason = title_matches(title)
    if not matched:
        return False, reason, "out_of_scope"

    return True, reason, "pending"
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
pytest tests/test_filter.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```powershell
git add pipeline/rules/title_filters.py pipeline/filter.py tests/test_filter.py
git commit -m "feat(filter): title + duration + availability scope rules"
```

---

## Task 5: Caption fetcher with retry/backoff

**Files:**
- Create: `pipeline/fetch_captions.py`
- Create: `tests/test_fetch_captions.py`

The fetcher wraps `youtube_transcript_api`. Tests inject a fake fetcher callable to stay offline. Real behavior:
- 1 req/s base rate + 0-500ms jitter
- Exponential backoff on transient errors: 1, 2, 4, 8, 16s, then give up
- `TranscriptsDisabled` / `NoTranscriptFound` → `no_captions` (no retry)
- `VideoUnavailable` → `unavailable` (no retry)
- Anything else network-ish → retry per backoff schedule

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_captions.py`:

```python
"""Tests for the caption fetcher."""

from __future__ import annotations

import pytest

from pipeline.fetch_captions import (
    CaptionFetchResult,
    NoCaptionsError,
    TransientFetchError,
    UnavailableError,
    fetch_with_retry,
)


def _fake_segments() -> list[dict[str, float | str]]:
    return [
        {"text": "all right traders", "start": 0.0, "duration": 1.5},
        {"text": "let's do a quick recap", "start": 1.5, "duration": 2.0},
    ]


def test_fetch_with_retry_succeeds_first_try() -> None:
    calls: list[str] = []

    def fake_fetch(video_id: str, languages: list[str]) -> list[dict[str, float | str]]:
        calls.append(video_id)
        return _fake_segments()

    result = fetch_with_retry(
        video_id="abc12345xyz",
        languages=["en", "en-US"],
        fetcher=fake_fetch,
        sleep=lambda _s: None,
    )

    assert isinstance(result, CaptionFetchResult)
    assert result.video_id == "abc12345xyz"
    assert result.language == "en"
    assert len(result.segments) == 2
    assert calls == ["abc12345xyz"]


def test_fetch_with_retry_recovers_after_transient() -> None:
    attempts: list[int] = []

    def fake_fetch(video_id: str, languages: list[str]) -> list[dict[str, float | str]]:
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientFetchError("simulated rate limit")
        return _fake_segments()

    sleeps: list[float] = []
    result = fetch_with_retry(
        video_id="abc12345xyz",
        languages=["en"],
        fetcher=fake_fetch,
        sleep=lambda s: sleeps.append(s),
    )

    assert len(result.segments) == 2
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]   # backoff doubled


def test_fetch_with_retry_gives_up_after_max_attempts() -> None:
    def fake_fetch(video_id: str, languages: list[str]) -> list[dict[str, float | str]]:
        raise TransientFetchError("never recovers")

    with pytest.raises(TransientFetchError):
        fetch_with_retry(
            video_id="abc12345xyz",
            languages=["en"],
            fetcher=fake_fetch,
            sleep=lambda _s: None,
            max_attempts=4,
        )


def test_fetch_with_retry_raises_no_captions_immediately() -> None:
    calls: list[str] = []

    def fake_fetch(video_id: str, languages: list[str]) -> list[dict[str, float | str]]:
        calls.append(video_id)
        raise NoCaptionsError("no captions")

    with pytest.raises(NoCaptionsError):
        fetch_with_retry(
            video_id="abc12345xyz",
            languages=["en"],
            fetcher=fake_fetch,
            sleep=lambda _s: None,
        )

    assert len(calls) == 1  # no retries


def test_fetch_with_retry_raises_unavailable_immediately() -> None:
    def fake_fetch(video_id: str, languages: list[str]) -> list[dict[str, float | str]]:
        raise UnavailableError("removed")

    with pytest.raises(UnavailableError):
        fetch_with_retry(
            video_id="abc12345xyz",
            languages=["en"],
            fetcher=fake_fetch,
            sleep=lambda _s: None,
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/test_fetch_captions.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.fetch_captions'`.

- [ ] **Step 3: Write `pipeline/fetch_captions.py`**

```python
"""Caption fetcher with retry/backoff.

The actual youtube_transcript_api call is injected as the `fetcher` callable,
so tests can run offline. Production callers build the real one via
`build_real_fetcher()` which imports the youtube-transcript-api package lazily.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

Fetcher = Callable[[str, list[str]], list[dict[str, Any]]]
Sleeper = Callable[[float], None]


class NoCaptionsError(Exception):
    """Video exists but has no captions in any requested language."""


class UnavailableError(Exception):
    """Video is private, removed, age-restricted, or otherwise inaccessible."""


class TransientFetchError(Exception):
    """Network/rate-limit/timeout — eligible for retry."""


@dataclass(frozen=True)
class CaptionSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class CaptionFetchResult:
    video_id: str
    language: str
    source: str   # "captions_manual" or "captions_auto" — best-effort
    segments: list[CaptionSegment]
    fetched_at: str
    tool_version: str


def fetch_with_retry(
    *,
    video_id: str,
    languages: list[str],
    fetcher: Fetcher,
    sleep: Sleeper,
    max_attempts: int = 5,
    base_backoff_seconds: float = 1.0,
) -> CaptionFetchResult:
    """Call `fetcher`, retrying transient failures with exponential backoff.

    Re-raises NoCaptionsError / UnavailableError immediately (no retry).
    Raises TransientFetchError if max_attempts exhausted.
    """
    attempt = 0
    while True:
        try:
            raw = fetcher(video_id, languages)
        except (NoCaptionsError, UnavailableError):
            raise
        except TransientFetchError:
            attempt += 1
            if attempt >= max_attempts:
                raise
            sleep(base_backoff_seconds * (2 ** (attempt - 1)))
            continue

        segments = [
            CaptionSegment(
                start=float(s["start"]),
                end=float(s["start"]) + float(s.get("duration", 0.0)),
                text=str(s["text"]),
            )
            for s in raw
        ]
        return CaptionFetchResult(
            video_id=video_id,
            language=languages[0] if languages else "en",
            source="captions_auto",  # MVP best-guess; real lib doesn't expose this cleanly
            segments=segments,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            tool_version="youtube-transcript-api",
        )


def build_real_fetcher() -> Fetcher:
    """Return a fetcher backed by youtube-transcript-api.

    Translates the library's exception hierarchy into our three local classes
    so the rest of the pipeline doesn't depend on the upstream package.
    """
    from youtube_transcript_api import (  # type: ignore[import-untyped]
        YouTubeTranscriptApi,
    )
    from youtube_transcript_api._errors import (  # type: ignore[import-untyped]
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    def _fetch(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
        try:
            return YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        except (TranscriptsDisabled, NoTranscriptFound) as e:
            raise NoCaptionsError(str(e)) from e
        except VideoUnavailable as e:
            raise UnavailableError(str(e)) from e
        except Exception as e:  # network, 429, 5xx — treat as transient
            raise TransientFetchError(str(e)) from e

    return _fetch
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/test_fetch_captions.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add pipeline/fetch_captions.py tests/test_fetch_captions.py
git commit -m "feat(fetch_captions): injectable fetcher with retry/backoff + error taxonomy"
```

---

## Task 6: Text normalization

**Files:**
- Create: `pipeline/rules/ad_read_filters.py`
- Create: `pipeline/rules/term_dictionary.py`
- Create: `pipeline/normalize.py`
- Create: `tests/test_normalize.py`

Normalization preserves timestamps. It transforms `list[CaptionSegment]` → `list[CaptionSegment]` (same shape, cleaner text). MVP scope:
- Strip `[Music]`, `[Applause]`, `[Laughter]`, `[Inaudible]` markers
- Drop pure-filler tokens (`um`, `uh`, `er`) — case-insensitive
- Apply term-dictionary substitutions (`"micro pull back"` → `"micro pullback"`)
- Drop segments whose text matches an ad-read regex
- Drop now-empty segments
- Collapse runs of whitespace

Sentence reconstruction is intentionally **not** in MVP — that adds complexity without changing the corpus's usefulness for eyeballing. It arrives in the production plan.

- [ ] **Step 1: Write the failing test**

Create `tests/test_normalize.py`:

```python
"""Tests for the normalization pass."""

from __future__ import annotations

from pipeline.fetch_captions import CaptionSegment
from pipeline.normalize import normalize_segments


def _seg(start: float, end: float, text: str) -> CaptionSegment:
    return CaptionSegment(start=start, end=end, text=text)


def test_strip_bracket_markers() -> None:
    out = normalize_segments(
        [
            _seg(0.0, 1.0, "[Music] all right traders"),
            _seg(1.0, 2.0, "[Applause]"),
            _seg(2.0, 3.0, "let's go"),
        ]
    )
    assert [s.text for s in out] == ["all right traders", "let's go"]


def test_drop_filler_words() -> None:
    out = normalize_segments(
        [
            _seg(0.0, 1.0, "um uh I think"),
            _seg(1.0, 2.0, "Um, all right"),
        ]
    )
    assert out[0].text == "I think"
    assert out[1].text == "all right"


def test_apply_term_dictionary() -> None:
    out = normalize_segments(
        [
            _seg(0.0, 1.0, "watch for the micro pull back here"),
            _seg(1.0, 2.0, "gap n go setup"),
        ]
    )
    assert out[0].text == "watch for the micro pullback here"
    assert out[1].text == "gap and go setup"


def test_drop_ad_read_segments() -> None:
    out = normalize_segments(
        [
            _seg(0.0, 1.0, "this episode brought to you by trade ideas use code WARRIOR"),
            _seg(1.0, 2.0, "all right let's get into the recap"),
        ]
    )
    assert [s.text for s in out] == ["all right let's get into the recap"]


def test_preserves_timestamps_on_kept_segments() -> None:
    out = normalize_segments(
        [
            _seg(0.0, 1.0, "[Music]"),       # dropped
            _seg(1.5, 3.0, "good morning"),  # kept
            _seg(3.5, 5.0, "let's trade"),   # kept
        ]
    )
    assert len(out) == 2
    assert (out[0].start, out[0].end) == (1.5, 3.0)
    assert (out[1].start, out[1].end) == (3.5, 5.0)


def test_collapses_internal_whitespace() -> None:
    out = normalize_segments([_seg(0.0, 1.0, "hello    world\n\n  again")])
    assert out[0].text == "hello world again"
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/test_normalize.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.normalize'`.

- [ ] **Step 3: Write `pipeline/rules/ad_read_filters.py`**

```python
"""Regex list for detecting and dropping ad-read segments.

Each pattern matches against a single (lowercased) segment's text. If any
pattern matches, the segment is dropped entirely.

Add patterns as you spot new ads in the corpus — edits don't need code review.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"\btrade\s+ideas\b.{0,40}\b(code|coupon|promo)\b",
    r"\b(brought\s+to\s+you\s+by|sponsored\s+by)\b",
    r"\bwarrior\s+(trading|pro)\b.{0,40}\b(class|course|chat\s*room)\b",
    r"\blink\s+in\s+the\s+description\b",
    r"\bsubscribe\s+(?:and|to)\b.{0,40}\b(notifications?|bell)\b",
    r"\buse\s+(?:my|coupon|promo)\s+code\b",
]

AD_READ_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_ad_read(text: str) -> bool:
    return AD_READ_RE.search(text) is not None
```

- [ ] **Step 4: Write `pipeline/rules/term_dictionary.py`**

```python
"""Common YouTube auto-caption splits that we want to glue back together.

Order matters: longer / more-specific entries should appear first so they
take precedence over substring rewrites.
"""

from __future__ import annotations

import re

# Apply as case-insensitive whole-word substitutions, in this order.
_SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\bmicro\s+pull\s*back\b", "micro pullback"),
    (r"\bgap\s+n\s+go\b", "gap and go"),
    (r"\bbull\s+flag\b", "bull flag"),
    (r"\bvw\s*ap\b", "vwap"),
    (r"\bnine\s+ema\b", "9 EMA"),
    (r"\b20\s+ema\b", "20 EMA"),
    (r"\bpre[\s-]?market\b", "premarket"),
    (r"\bafter[\s-]?hours\b", "after-hours"),
    (r"\bp\s*&\s*l\b", "P&L"),
    (r"\bstop[\s-]?loss\b", "stop loss"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), repl) for pat, repl in _SUBSTITUTIONS]


def apply_term_dictionary(text: str) -> str:
    for regex, replacement in _COMPILED:
        text = regex.sub(replacement, text)
    return text
```

- [ ] **Step 5: Write `pipeline/normalize.py`**

```python
"""Normalize raw caption segments into clean, citation-ready text.

Preserves segment-level timestamps so downstream chunking can deep-link to
exact moments in the video. Filler/markers/ad-reads removed; non-empty
segments kept in original order.
"""

from __future__ import annotations

import re

from pipeline.fetch_captions import CaptionSegment
from pipeline.rules.ad_read_filters import is_ad_read
from pipeline.rules.term_dictionary import apply_term_dictionary

_BRACKET_MARKER_RE = re.compile(r"\[[^\]]+\]")
_FILLER_WORD_RE = re.compile(r"\b(um+|uh+|er+)\b[,]?", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_segments(segments: list[CaptionSegment]) -> list[CaptionSegment]:
    """Clean every segment and drop the now-empty ones.

    Steps per segment:
      1. Strip [Music] / [Applause] / [...] markers.
      2. Apply term dictionary (e.g. "micro pull back" -> "micro pullback").
      3. Drop filler words (um, uh, er).
      4. Collapse whitespace and trim.
      5. Drop segment if empty or if it matches an ad-read pattern.
    """
    out: list[CaptionSegment] = []
    for seg in segments:
        text = _BRACKET_MARKER_RE.sub("", seg.text)
        text = apply_term_dictionary(text)
        text = _FILLER_WORD_RE.sub("", text)
        text = _WHITESPACE_RE.sub(" ", text).strip(" ,")

        if not text:
            continue
        if is_ad_read(text):
            continue

        out.append(CaptionSegment(start=seg.start, end=seg.end, text=text))
    return out
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
pytest tests/test_normalize.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 7: Commit**

```powershell
git add pipeline/rules/ad_read_filters.py pipeline/rules/term_dictionary.py pipeline/normalize.py tests/test_normalize.py
git commit -m "feat(normalize): clean captions while preserving timestamps"
```

---

## Task 7: JSON-on-disk storage

**Files:**
- Create: `pipeline/storage.py`
- Create: `tests/test_storage.py`

Each video that successfully fetches gets one file at `data/raw_transcripts/{video_id}.json`. Idempotent overwrite. JSON shape is exactly what the spec section 6 calls for.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
"""Tests for the on-disk JSON writer."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.fetch_captions import CaptionFetchResult, CaptionSegment
from pipeline.storage import write_transcript


def _result() -> CaptionFetchResult:
    return CaptionFetchResult(
        video_id="abc12345xyz",
        language="en",
        source="captions_auto",
        segments=[
            CaptionSegment(start=0.0, end=1.5, text="all right traders"),
            CaptionSegment(start=1.5, end=3.0, text="let's do a recap"),
        ],
        fetched_at="2026-05-24T18:30:00+00:00",
        tool_version="youtube-transcript-api==0.6.2",
    )


def test_write_transcript_creates_expected_json_shape(tmp_path: Path) -> None:
    write_transcript(_result(), title="Trade Recap May 1", upload_date="2026-05-01", out_dir=tmp_path)

    path = tmp_path / "abc12345xyz.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["video_id"] == "abc12345xyz"
    assert payload["title"] == "Trade Recap May 1"
    assert payload["upload_date"] == "2026-05-01"
    assert payload["language"] == "en"
    assert payload["source"] == "captions_auto"
    assert payload["fetched_at"] == "2026-05-24T18:30:00+00:00"
    assert payload["tool_version"] == "youtube-transcript-api==0.6.2"
    assert payload["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "all right traders"},
        {"start": 1.5, "end": 3.0, "text": "let's do a recap"},
    ]


def test_write_transcript_overwrites_existing_file(tmp_path: Path) -> None:
    write_transcript(_result(), title="Old Title", upload_date="2026-05-01", out_dir=tmp_path)
    write_transcript(_result(), title="New Title", upload_date="2026-05-01", out_dir=tmp_path)

    payload = json.loads((tmp_path / "abc12345xyz.json").read_text(encoding="utf-8"))
    assert payload["title"] == "New Title"


def test_write_transcript_creates_parent_directories(tmp_path: Path) -> None:
    nested = tmp_path / "new" / "deep" / "dir"
    write_transcript(_result(), title="x", upload_date="2026-05-01", out_dir=nested)
    assert (nested / "abc12345xyz.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/test_storage.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.storage'`.

- [ ] **Step 3: Write `pipeline/storage.py`**

```python
"""Write a fetched transcript to disk as one JSON file per video.

File layout: `{out_dir}/{video_id}.json`. Overwrites existing files; the file
is regenerable, never the source of truth for ground-truth claims.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.fetch_captions import CaptionFetchResult


def write_transcript(
    result: CaptionFetchResult,
    *,
    title: str,
    upload_date: str | None,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.video_id}.json"
    payload = {
        "video_id": result.video_id,
        "title": title,
        "upload_date": upload_date,
        "language": result.language,
        "source": result.source,
        "fetched_at": result.fetched_at,
        "tool_version": result.tool_version,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text} for s in result.segments
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/test_storage.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add pipeline/storage.py tests/test_storage.py
git commit -m "feat(storage): per-video JSON writer with stable shape"
```

---

## Task 8: MVP orchestrator + CLI entrypoint

**Files:**
- Create: `pipeline/run_mvp.py`
- Create: `tests/test_run_mvp.py`

The orchestrator wires the components together with **injectable dependencies** (`flat_extract`, `fetcher`, `sleep`) so the end-to-end test runs offline. A `main()` function provides a real CLI for production use; it builds the real discoverer + fetcher via the lazy builder functions.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_mvp.py`:

```python
"""End-to-end test for the MVP orchestrator (offline, fakes only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.db import connect
from pipeline.fetch_captions import NoCaptionsError, UnavailableError
from pipeline.run_mvp import run_mvp
from tests.fakes import make_fake_flat_extract


def test_run_mvp_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "pipeline.db"
    out_dir = tmp_path / "transcripts"

    flat_extract = make_fake_flat_extract(
        {
            "id": "UCBayuhPYhDnxZbsKHqaXfOQ",
            "channel": "Day Trade Warrior",
            "entries": [
                {  # in-scope; captions succeed
                    "id": "recapAAAAAA",
                    "title": "Trade Recap May 1",
                    "duration": 600,
                    "view_count": 1000,
                    "upload_date": "20260501",
                    "availability": None,
                    "live_status": "not_live",
                },
                {  # in-scope; no captions
                    "id": "nocapBBBBBB",
                    "title": "How to Trade Gap and Go",
                    "duration": 900,
                    "view_count": 2000,
                    "upload_date": "20260502",
                    "availability": None,
                    "live_status": "not_live",
                },
                {  # out of scope; livestream
                    "id": "streamCCCCC",
                    "title": "Friday Live Stream",
                    "duration": 14400,
                    "view_count": 500,
                    "upload_date": "20260503",
                    "availability": None,
                    "live_status": "was_live",
                },
                {  # in-scope; video gone
                    "id": "removedDDDD",
                    "title": "Recap Removed Trade",
                    "duration": 500,
                    "view_count": 100,
                    "upload_date": "20260504",
                    "availability": None,
                    "live_status": "not_live",
                },
            ],
        }
    )

    def fake_fetcher(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
        if video_id == "recapAAAAAA":
            return [
                {"text": "all right traders", "start": 0.0, "duration": 1.5},
                {"text": "let's recap today's micro pull back trade", "start": 1.5, "duration": 3.0},
            ]
        if video_id == "nocapBBBBBB":
            raise NoCaptionsError("disabled")
        if video_id == "removedDDDD":
            raise UnavailableError("video removed")
        raise AssertionError(f"unexpected video_id: {video_id}")

    summary = run_mvp(
        channel_handle="@DaytradeWarrior",
        db_path=db_path,
        out_dir=out_dir,
        flat_extract=flat_extract,
        fetcher=fake_fetcher,
        sleep=lambda _s: None,
        polite_delay=lambda: None,
    )

    assert summary.discovered == 4
    assert summary.in_scope == 3        # recap + nocap + removed; livestream excluded by filter
    assert summary.fetched_ok == 1
    assert summary.fetch_failed_no_captions == 1
    assert summary.fetch_failed_other == 1   # UnavailableError classified as 'failed_other'

    # The one successful fetch was written to disk with normalized text.
    written = out_dir / "recapAAAAAA.json"
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["video_id"] == "recapAAAAAA"
    assert "micro pullback" in payload["segments"][1]["text"]  # normalization ran

    # DB state reflects per-video outcomes.
    with connect(db_path) as conn:
        rows = dict(conn.execute("SELECT id, fetch_status FROM videos").fetchall())

    assert rows["recapAAAAAA"] == "succeeded"
    assert rows["nocapBBBBBB"] == "failed_no_captions"
    assert rows["removedDDDD"] == "failed_other"
    assert rows["streamCCCCC"] == "out_of_scope"


def test_run_mvp_skips_videos_already_succeeded(tmp_path: Path) -> None:
    db_path = tmp_path / "pipeline.db"
    out_dir = tmp_path / "transcripts"
    flat_extract = make_fake_flat_extract(
        {
            "id": "UCtest",
            "channel": "T",
            "entries": [
                {
                    "id": "recapDONE01",
                    "title": "Trade Recap",
                    "duration": 600,
                    "view_count": 1000,
                    "upload_date": "20260501",
                    "availability": None,
                    "live_status": "not_live",
                }
            ],
        }
    )
    call_log: list[str] = []

    def fetcher(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
        call_log.append(video_id)
        return [{"text": "hi", "start": 0.0, "duration": 1.0}]

    # First run: fetch happens.
    run_mvp(
        channel_handle="@DaytradeWarrior",
        db_path=db_path,
        out_dir=out_dir,
        flat_extract=flat_extract,
        fetcher=fetcher,
        sleep=lambda _s: None,
        polite_delay=lambda: None,
    )
    # Second run: video is already 'succeeded' -> no fetch.
    summary = run_mvp(
        channel_handle="@DaytradeWarrior",
        db_path=db_path,
        out_dir=out_dir,
        flat_extract=flat_extract,
        fetcher=fetcher,
        sleep=lambda _s: None,
        polite_delay=lambda: None,
    )
    assert call_log == ["recapDONE01"]            # only the first run called the fetcher
    assert summary.fetched_ok == 0
    assert summary.skipped_already_done == 1
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/test_run_mvp.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.run_mvp'`.

- [ ] **Step 3: Write `pipeline/run_mvp.py`**

```python
"""End-to-end MVP orchestrator.

Discover → upsert → filter → for each in-scope `pending` video, fetch captions
(with retry/backoff), normalize, write JSON, update DB. Idempotent across
re-runs: already-succeeded videos are skipped.

Production callers invoke `main()` which constructs real yt-dlp +
youtube-transcript-api fetchers. Tests pass fakes directly to `run_mvp()`.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.db import connect, init_db, upsert_video
from pipeline.discover import build_real_flat_extract, discover_channel_videos
from pipeline.fetch_captions import (
    Fetcher,
    NoCaptionsError,
    Sleeper,
    TransientFetchError,
    UnavailableError,
    build_real_fetcher,
    fetch_with_retry,
)
from pipeline.filter import apply_filter
from pipeline.normalize import normalize_segments
from pipeline.storage import write_transcript
from pipeline.types import ChannelMeta, VideoMeta

DEFAULT_LANGUAGES = ["en", "en-US", "en-GB"]

_log = logging.getLogger("pipeline.run_mvp")


@dataclass
class RunSummary:
    discovered: int = 0
    in_scope: int = 0
    fetched_ok: int = 0
    fetch_failed_no_captions: int = 0
    fetch_failed_other: int = 0
    skipped_already_done: int = 0
    errors: list[str] = field(default_factory=list)


def run_mvp(
    *,
    channel_handle: str,
    db_path: Path,
    out_dir: Path,
    flat_extract: Callable[[str], dict[str, Any]],
    fetcher: Fetcher,
    sleep: Sleeper,
    languages: list[str] | None = None,
    max_videos: int | None = None,
    polite_delay: Callable[[], None] | None = None,
) -> RunSummary:
    """Run the full MVP pipeline once. Idempotent."""
    languages = languages or DEFAULT_LANGUAGES
    polite_delay = polite_delay or _default_polite_delay
    summary = RunSummary()

    init_db(db_path)

    # 1. Discover.
    channel, videos = discover_channel_videos(channel_handle, flat_extract=flat_extract)
    summary.discovered = len(videos)
    _upsert_channel_and_videos(db_path, channel, videos)

    # 2. Filter.
    filter_summary = apply_filter(db_path)
    summary.in_scope = filter_summary.in_scope

    # 3. Fetch loop.
    targets = _select_pending_in_scope(db_path, max_videos=max_videos)
    summary.skipped_already_done = _count_already_succeeded(db_path)

    for vid_id, title, upload_date in targets:
        try:
            result = fetch_with_retry(
                video_id=vid_id,
                languages=languages,
                fetcher=fetcher,
                sleep=sleep,
            )
        except NoCaptionsError as e:
            _mark_status(db_path, vid_id, "failed_no_captions", str(e))
            summary.fetch_failed_no_captions += 1
            polite_delay()
            continue
        except (UnavailableError, TransientFetchError) as e:
            _mark_status(db_path, vid_id, "failed_other", str(e))
            summary.fetch_failed_other += 1
            summary.errors.append(f"{vid_id}: {e}")
            polite_delay()
            continue

        cleaned = dataclasses.replace(
            result, segments=normalize_segments(result.segments)
        )
        write_transcript(cleaned, title=title, upload_date=upload_date, out_dir=out_dir)
        _mark_status(db_path, vid_id, "succeeded", None)
        summary.fetched_ok += 1
        polite_delay()

    return summary


def _default_polite_delay() -> None:
    """Sleep ~1s + 0-500ms jitter between fetches."""
    time.sleep(1.0 + random.random() * 0.5)


def _upsert_channel_and_videos(
    db_path: Path,
    channel: ChannelMeta,
    videos: list[VideoMeta],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels (id, handle, name, discovered_at, last_discovery_run_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                handle = excluded.handle,
                name   = excluded.name,
                last_discovery_run_at = excluded.last_discovery_run_at
            """,
            (channel.id, channel.handle, channel.name, now, now),
        )
        for v in videos:
            upsert_video(
                conn,
                video_id=v.id,
                channel_id=channel.id,
                title=v.title,
                duration_seconds=v.duration_seconds,
                upload_date=v.upload_date,
                view_count=v.view_count,
                is_live=v.is_live,
                is_short=v.is_short,
                availability=v.availability,
            )


def _select_pending_in_scope(
    db_path: Path,
    *,
    max_videos: int | None,
) -> list[tuple[str, str, str | None]]:
    sql = (
        "SELECT id, title, upload_date FROM videos "
        "WHERE in_scope = 1 AND fetch_status = 'pending' "
        "ORDER BY upload_date DESC"
    )
    if max_videos is not None:
        sql += f" LIMIT {int(max_videos)}"
    with connect(db_path) as conn:
        return [(r[0], r[1], r[2]) for r in conn.execute(sql).fetchall()]


def _count_already_succeeded(db_path: Path) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT count(*) FROM videos WHERE fetch_status = 'succeeded'"
        ).fetchone()
    return int(row[0])


def _mark_status(db_path: Path, video_id: str, status: str, error: str | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET fetch_status = ?, last_error = ? WHERE id = ?",
            (status, error, video_id),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run_mvp")
    parser.add_argument("--channel", default="@DaytradeWarrior")
    parser.add_argument("--db", default="data/pipeline.db", type=Path)
    parser.add_argument("--out", default="data/raw_transcripts", type=Path)
    parser.add_argument(
        "--max-videos", type=int, default=None,
        help="Stop after this many in-scope fetches (per run). Omit to fetch all pending.",
    )
    parser.add_argument(
        "--no-polite-delay", action="store_true",
        help="Disable the 1s+jitter sleep between fetches. Use only for tests.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    polite = (lambda: None) if args.no_polite_delay else _default_polite_delay
    summary = run_mvp(
        channel_handle=args.channel,
        db_path=args.db,
        out_dir=args.out,
        flat_extract=build_real_flat_extract(),
        fetcher=build_real_fetcher(),
        sleep=time.sleep,
        max_videos=args.max_videos,
        polite_delay=polite,
    )

    _log.info("Run complete: %s", summary)
    return 0 if not summary.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
pytest tests/test_run_mvp.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

```powershell
pytest -v
```

Expected: all 24+ tests pass across all modules.

- [ ] **Step 6: Commit**

```powershell
git add pipeline/run_mvp.py tests/test_run_mvp.py
git commit -m "feat(orchestrator): wire MVP pipeline end-to-end with injectable deps"
```

---

## Task 9: Lint, type-check, and README polish

**Files:**
- Modify: `README.md` (expand usage docs)

- [ ] **Step 1: Run ruff and fix any issues**

```powershell
ruff check .
```

Expected: `All checks passed!`. If not, address each finding and re-run.

- [ ] **Step 2: Run ruff format**

```powershell
ruff format .
```

Expected: a few files reformatted, or `Already formatted!`.

- [ ] **Step 3: Run mypy and fix any issues**

```powershell
mypy
```

Expected: `Success: no issues found in N source files`. If errors surface in `pipeline/` code, add type annotations until clean. The `# type: ignore[import-untyped]` comments on the `youtube_transcript_api` imports in `fetch_captions.py` are expected and intentional (upstream has no type stubs).

- [ ] **Step 4: Run the full test suite one more time**

```powershell
pytest -v --cov=pipeline --cov-report=term-missing
```

Expected: all tests pass; coverage on the `pipeline/` package is ≥85%. If lower, look at the missing lines and decide whether to add a test or accept the gap (e.g., `main()` argv parsing is hard to unit-test cleanly).

- [ ] **Step 5: Replace `README.md` with the polished version**

```markdown
# ross-trading-research

Public-transcript collection pipeline supporting the `ross-trading` agent.

**Status:** MVP — captions-only, single-process, JSON-on-disk corpus.

## Scope boundary

This pipeline only uses public videos and public captions. No auth bypass, no DRM, no paid-content scraping. The corpus is internal research material — not redistributed, not used to fine-tune a model, not used to impersonate Ross Cameron.

See `docs/superpowers/specs/2026-05-24-youtube-transcript-collection-design.md` in the `ross-trading` repo for the full design and policy posture.

## Install (Windows + PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run the MVP

Quick smoke run (25 most-recent in-scope videos):

```powershell
python -m pipeline.run_mvp --channel "@DaytradeWarrior" --max-videos 25
```

Full backfill (no cap):

```powershell
python -m pipeline.run_mvp --channel "@DaytradeWarrior"
```

Output:
- `data/pipeline.db` — SQLite state: which videos exist, which are in-scope, what's been fetched, what failed and why.
- `data/raw_transcripts/{video_id}.json` — one normalized transcript per successfully-fetched video.

The pipeline is idempotent. Re-running picks up where it left off; it does not re-fetch videos already marked `succeeded`.

## Inspect the corpus

How many videos have we collected?

```powershell
sqlite3 data\pipeline.db "SELECT fetch_status, count(*) FROM videos GROUP BY fetch_status"
```

Which in-scope videos failed and why?

```powershell
sqlite3 data\pipeline.db "SELECT id, title, fetch_status, last_error FROM videos WHERE fetch_status LIKE 'failed_%'"
```

Grep the transcripts for a term:

```powershell
Select-String -Path data\raw_transcripts\*.json -Pattern "micro pullback" -CaseSensitive:$false | Select-Object Path, LineNumber | Get-Unique -AsString
```

## Run the tests

```powershell
pytest -v
ruff check .
mypy
```

## What the MVP does NOT do (yet)

These are intentionally deferred to the production plan:
- No Whisper fallback for videos without captions.
- No work-queue table (`videos.fetch_status` covers it for now).
- No chunking, embedding, or vector index. The transcripts are raw text + timestamps.
- No strategy-extraction LLM pass — that is a separate, downstream tool.
- No multi-channel support; hardcoded to a single channel argument.

If you want any of these, see the production layers section of the design spec.

## Politeness defaults

- 1 req/s + jitter between caption fetches.
- Exponential backoff (1, 2, 4, 8, 16 s) on transient errors before giving up.
- Skips private / members-only / removed / age-restricted videos without retry.

If you see `429` errors or sustained `failed_other`, stop the run, wait an hour, and reduce concurrency. Do not work around YouTube's rate limits.
```

- [ ] **Step 6: Commit**

```powershell
git add README.md
git commit -m "docs: polished README with usage, inspection commands, and scope boundaries"
```

---

## Task 10: First real smoke run

**Files:** none (production execution against the real channel).

This task is the validation gate. It uses the real network, real YouTube, real captions. Do it once, with a small cap, and review the output before declaring the MVP done.

- [ ] **Step 1: Confirm you are off US-market hours**

Quiet-hours convention: don't run YouTube-bound work 09:30–16:00 ET, Monday–Friday. The MVP doesn't enforce this — that's a deliberate cut for the production plan — so you enforce it manually.

- [ ] **Step 2: Run a 10-video smoke test**

```powershell
python -m pipeline.run_mvp --channel "@DaytradeWarrior" --max-videos 10
```

Expected: output ends with `INFO pipeline.run_mvp Run complete: RunSummary(discovered=N, in_scope=M, fetched_ok≤10, ...)`. The full discovery may return hundreds of videos; that's expected.

- [ ] **Step 3: Inspect the output JSON files manually**

```powershell
Get-ChildItem data\raw_transcripts\*.json | Select-Object -First 3 | ForEach-Object {
    Write-Host "===== $($_.Name) ====="
    Get-Content $_ | Select-Object -First 30
}
```

Eyeball-check:
- Segments have realistic `start` / `end` timestamps in seconds.
- Text is clean — no `[Music]` markers leaking through, no obvious ad reads.
- Terms like "micro pullback" / "gap and go" / "9 EMA" appear rendered correctly.
- No empty or near-empty segments.

If any check fails, add a unit test that reproduces the problem against the actual segment text, fix the relevant rule file (`ad_read_filters.py`, `term_dictionary.py`), re-run the affected videos by resetting their status:

```powershell
sqlite3 data\pipeline.db "UPDATE videos SET fetch_status='pending' WHERE id IN ('vid1','vid2')"
python -m pipeline.run_mvp --channel "@DaytradeWarrior"
```

Then commit the rule-file change.

- [ ] **Step 4: Inspect the DB summary**

```powershell
sqlite3 data\pipeline.db "SELECT fetch_status, count(*) FROM videos GROUP BY fetch_status"
sqlite3 data\pipeline.db "SELECT in_scope, count(*) FROM videos GROUP BY in_scope"
```

Sanity checks:
- Total in_scope=1 count should be a meaningful fraction of total videos (rough expectation: 20–50% depending on title regex coverage). If it's <5%, the title filters are too narrow — review `pipeline/rules/title_filters.py`.
- `failed_no_captions` count should be small for in-scope (educational) videos; a high count is a sign the language list or the fetch logic needs tuning.
- `failed_other` should be near-zero. If high, look at `last_error` for patterns.

- [ ] **Step 5: Decide whether MVP is good enough**

The MVP is done when:
- ≥80% of in-scope videos produce a transcript file.
- Spot-checking 5 random transcripts shows them readable and faithful to the audio (open the video, scrub to a timestamp from the JSON, listen).
- No persistent `failed_other` pattern that points at a fixable bug.

If those gates pass, MVP ships. Open follow-up issues for anything you noticed but didn't fix:
- ad-read patterns that leaked through
- term-dictionary entries to add
- title-filter terms to add or remove
- failure classes worth handling differently in the production plan

If those gates fail, fix and re-run — do not move on to the production plan yet.

- [ ] **Step 6: Tag the MVP**

```powershell
git tag -a mvp-v0.1 -m "MVP: captions-only corpus collection, validated on first real run"
```

(No `git push` here — that's a deliberate user decision, not a default. The user pushes when they're ready.)

---

## Self-Review Notes

- **Spec coverage:** every section of the MVP definition (spec §9) maps to a task here. The full-design pieces (Whisper, queue, embeddings, strategy extraction — spec §10) are explicitly excluded, with a forward-reference to the production plan.
- **Placeholder scan:** every step has either runnable code or an exact command. No "TODO", no "implement appropriate error handling".
- **Type consistency:** `CaptionSegment`, `CaptionFetchResult`, `ChannelMeta`, `VideoMeta`, `RunSummary`, `FilterSummary` are defined once and reused with the same field names everywhere.
- **Idempotency:** `init_db`, `apply_filter`, `upsert_video`, `write_transcript`, and `run_mvp` are all safe to re-run.
- **Offline tests:** every test injects fakes for network operations; `pytest` works without internet.
- **Scope discipline:** sentence reconstruction, work-queue, Whisper fallback, embeddings, and strategy extraction are deferred to the production plan, not silently added here.
