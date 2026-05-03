"""Atom A7 (#46) -- daily comparison report unit tests.

Joins the scanner journal (A4/A5) with the hand-curated ground truth
(A6) for one trading day and computes the recall metric that gates
Phase 2 closure (>= 70%). Per Decision D3 (#37), matching is
ticker-only -- no time-window joins.

Test ordering follows superpowers:test-driven-development: one
acceptance criterion = one red-green cycle. The metric math is
asserted against a synthetic ``DailyReport`` first; the database
integration tests come after.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import Base
from ross_trading.journal.report import (
    DailyReport,
    build_daily_report,
    main,
    render_report,
)
from ross_trading.journal.writer import JournalWriter
from ross_trading.scanner.decisions import ScannerDecision
from ross_trading.scanner.types import ScannerPick

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


# --------------------------------------------------------------------- helpers


def _report(
    *,
    cameron: frozenset[str],
    scanner: frozenset[str],
) -> DailyReport:
    """Build a DailyReport from raw cameron/scanner sets.

    Lets each metric test focus on the property under test rather
    than re-deriving matched / missed / extra in every case.
    """
    return DailyReport.from_sets(
        day=date(2026, 5, 1),
        cameron=cameron,
        scanner=scanner,
    )


def _pick(
    *,
    ticker: str,
    ts: datetime,
    rank: int = 1,
) -> ScannerPick:
    return ScannerPick(
        ticker=ticker,
        ts=ts,
        rel_volume=Decimal("12.5"),
        pct_change=Decimal("18.75"),
        price=Decimal("3.42"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=4,
        rank=rank,
    )


def _emit_picked(writer: JournalWriter, *, ticker: str, ts: datetime) -> None:
    writer.emit(
        ScannerDecision(
            kind="picked",
            decision_ts=ts,
            ticker=ticker,
            pick=_pick(ticker=ticker, ts=ts),
            reason=None,
            gap_start=None,
            gap_end=None,
        )
    )


def _write_ground_truth(root: Path, day: date, tickers: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = [{"ticker": t, "direction": "long"} for t in tickers]
    (root / f"{day.isoformat()}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_journal_engine("sqlite://")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def writer(session_factory: sessionmaker[Session]) -> JournalWriter:
    return JournalWriter(session_factory)


def test_recall_is_matched_over_cameron_when_cameron_non_empty() -> None:
    """recall = |Cameron ∩ scanner| / |Cameron| -- the gating metric."""
    report = _report(
        cameron=frozenset({"AAAA", "BBBB", "CCCC", "DDDD"}),
        scanner=frozenset({"AAAA", "BBBB", "XXXX"}),
    )
    assert report.recall == Decimal("0.5"), (
        f"expected 2 of 4 = 0.5, got {report.recall}"
    )


def test_precision_is_matched_over_scanner_when_scanner_non_empty() -> None:
    """precision = |Cameron ∩ scanner| / |scanner| -- reported, not gating."""
    report = _report(
        cameron=frozenset({"AAAA", "BBBB", "CCCC", "DDDD"}),
        scanner=frozenset({"AAAA", "BBBB", "XXXX"}),
    )
    # 2 matched of 3 picked
    expected = Decimal(2) / Decimal(3)
    assert report.precision == expected, (
        f"expected 2/3, got {report.precision}"
    )


def test_recall_is_zero_when_cameron_is_empty() -> None:
    """0/0 -> 0 by convention. The report still renders cleanly on empty days."""
    report = _report(
        cameron=frozenset(),
        scanner=frozenset({"AAAA", "BBBB"}),
    )
    assert report.recall == Decimal(0)


def test_precision_is_zero_when_scanner_is_empty() -> None:
    """0/0 -> 0 by convention. The report still renders on a quiet scanner day."""
    report = _report(
        cameron=frozenset({"AAAA", "BBBB"}),
        scanner=frozenset(),
    )
    assert report.precision == Decimal(0)


def test_matched_missed_extra_set_arithmetic() -> None:
    """matched = ∩ ; missed = cameron - scanner ; extra = scanner - cameron."""
    report = _report(
        cameron=frozenset({"AAAA", "BBBB", "CCCC"}),
        scanner=frozenset({"BBBB", "CCCC", "DDDD", "EEEE"}),
    )
    assert report.matched == frozenset({"BBBB", "CCCC"})
    assert report.missed == frozenset({"AAAA"})
    assert report.extra == frozenset({"DDDD", "EEEE"})


# ------------------------------------------------ build_daily_report (DB join)


def test_build_daily_report_joins_journal_picks_with_ground_truth(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Picks for the day land in scanner; ground truth lands in cameron."""
    day = date(2026, 5, 1)
    # 14:30 UTC on 2026-05-01 = 10:30 ET (DST), inside the morning window.
    inside_day = datetime(2026, 5, 1, 14, 30, tzinfo=UTC)

    _emit_picked(writer, ticker="AAAA", ts=inside_day)
    _emit_picked(writer, ticker="BBBB", ts=inside_day)
    _emit_picked(writer, ticker="EEEE", ts=inside_day)
    # Same ticker emitted twice in the day -> still one entry in scanner-set.
    _emit_picked(writer, ticker="AAAA", ts=inside_day)

    _write_ground_truth(tmp_path, day, ["AAAA", "BBBB", "CCCC", "DDDD"])

    report = build_daily_report(
        day,
        session_factory=session_factory,
        ground_truth_root=tmp_path,
    )

    assert report.day == day
    assert report.cameron == frozenset({"AAAA", "BBBB", "CCCC", "DDDD"})
    assert report.scanner == frozenset({"AAAA", "BBBB", "EEEE"})
    assert report.matched == frozenset({"AAAA", "BBBB"})
    assert report.missed == frozenset({"CCCC", "DDDD"})
    assert report.extra == frozenset({"EEEE"})


def test_build_daily_report_filters_picks_by_ET_calendar_day(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Day boundary is America/New_York, not UTC.

    A pick at 03:00 UTC on 2026-05-02 is 23:00 ET on 2026-05-01 -- still
    within the 2026-05-01 ET trading day. A pick at 04:30 UTC on
    2026-05-01 is 00:30 ET on the same date and should also be included.
    A pick at 04:30 UTC on 2026-05-02 is 00:30 ET on 2026-05-02 and
    must NOT appear in the 2026-05-01 report.
    """
    day = date(2026, 5, 1)

    # 23:00 ET 2026-05-01  (= 03:00 UTC 2026-05-02 during DST)
    late_et = datetime(2026, 5, 2, 3, 0, tzinfo=UTC)
    # 00:30 ET 2026-05-01  (= 04:30 UTC 2026-05-01 during DST)
    early_et = datetime(2026, 5, 1, 4, 30, tzinfo=UTC)
    # 00:30 ET 2026-05-02  (= 04:30 UTC 2026-05-02 during DST) -- NEXT day
    next_day = datetime(2026, 5, 2, 4, 30, tzinfo=UTC)

    _emit_picked(writer, ticker="LATEET", ts=late_et)
    _emit_picked(writer, ticker="EARLYET", ts=early_et)
    _emit_picked(writer, ticker="NEXTDAY", ts=next_day)

    _write_ground_truth(tmp_path, day, [])

    report = build_daily_report(
        day,
        session_factory=session_factory,
        ground_truth_root=tmp_path,
    )

    assert "LATEET" in report.scanner
    assert "EARLYET" in report.scanner
    assert "NEXTDAY" not in report.scanner


def test_build_daily_report_uppercases_journal_tickers_defensively(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Pick.ticker is not normalized on write; report normalizes on read.

    Follow-up issue tracks enforcing .upper() at the model layer; until
    then the report defensively upper-cases so a stray lowercase journal
    row doesn't silently miss against ground truth (which is upper-cased
    on load by A6).
    """
    day = date(2026, 5, 1)
    inside_day = datetime(2026, 5, 1, 14, 30, tzinfo=UTC)
    _emit_picked(writer, ticker="aaaa", ts=inside_day)

    _write_ground_truth(tmp_path, day, ["AAAA"])

    report = build_daily_report(
        day,
        session_factory=session_factory,
        ground_truth_root=tmp_path,
    )

    assert report.scanner == frozenset({"AAAA"})
    assert report.matched == frozenset({"AAAA"})


def test_build_daily_report_only_counts_picked_kind_not_other_decisions(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """stale_feed / feed_gap rows must not leak into the scanner set."""
    day = date(2026, 5, 1)
    inside_day = datetime(2026, 5, 1, 14, 30, tzinfo=UTC)

    _emit_picked(writer, ticker="REAL", ts=inside_day)
    writer.emit(
        ScannerDecision(
            kind="stale_feed",
            decision_ts=inside_day,
            ticker=None,
            pick=None,
            reason="stale",
            gap_start=None,
            gap_end=None,
        )
    )
    writer.emit(
        ScannerDecision(
            kind="feed_gap",
            decision_ts=inside_day,
            ticker=None,
            pick=None,
            reason="reset",
            gap_start=inside_day,
            gap_end=inside_day,
        )
    )

    _write_ground_truth(tmp_path, day, ["REAL"])

    report = build_daily_report(
        day,
        session_factory=session_factory,
        ground_truth_root=tmp_path,
    )

    assert report.scanner == frozenset({"REAL"})


# ---------------------------------------------------------- render_report


def _populated_report() -> DailyReport:
    """Hand-built non-trivial DailyReport for render-side tests."""
    return DailyReport.from_sets(
        day=date(2026, 5, 1),
        cameron=frozenset({"AAAA", "BBBB", "CCCC"}),
        scanner=frozenset({"BBBB", "CCCC", "DDDD", "EEEE"}),
    )


def test_render_report_is_byte_identical_across_calls() -> None:
    """Determinism: same input -> exact same bytes, every time."""
    report = _populated_report()
    assert render_report(report) == render_report(report)


def test_render_report_sorts_tickers_alphabetically_in_each_section() -> None:
    """Tickers in matched / missed / extra appear A-Z so the bytes are stable
    across the non-deterministic frozenset iteration order.
    """
    # Build a report whose three sets each contain >1 ticker so sort
    # order is observable.
    report = DailyReport.from_sets(
        day=date(2026, 5, 1),
        cameron=frozenset({"ZZZZ", "AAAA", "MMMM"}),  # missed will be all three
        scanner=frozenset({"YYYY", "BBBB"}),  # extra will be both
    )
    rendered = render_report(report)

    # Pull out tickers from each ## Missed (cameron - scanner) section.
    # All three cameron tickers are missed, all two scanner tickers are extra.
    missed_section = rendered.split("## Missed")[1].split("## ")[0]
    assert missed_section.index("AAAA") < missed_section.index("MMMM")
    assert missed_section.index("MMMM") < missed_section.index("ZZZZ")

    extra_section = rendered.split("## Extra")[1].split("## ")[0]
    assert extra_section.index("BBBB") < extra_section.index("YYYY")


def test_render_report_has_definitions_section_pinning_recall_vs_precision() -> None:
    """A reviewer should never have to re-derive the math from the body."""
    rendered = render_report(_populated_report())
    assert "## Definitions" in rendered
    assert "Recall" in rendered
    assert "Precision" in rendered
    assert "gating" in rendered.lower()


def test_render_report_emits_each_section_even_when_empty() -> None:
    """Uniform shape across days. Empty sections show `_(none)_` so a
    reviewer can't mistake a missing heading for a missing check.
    """
    perfect = DailyReport.from_sets(
        day=date(2026, 5, 1),
        cameron=frozenset({"AAAA"}),
        scanner=frozenset({"AAAA"}),  # matched=AAAA, missed=∅, extra=∅
    )
    rendered = render_report(perfect)

    assert "## Matched" in rendered
    assert "## Missed" in rendered
    assert "## Extra" in rendered

    missed_section = rendered.split("## Missed")[1].split("## ")[0]
    extra_section = rendered.split("## Extra")[1]
    assert "_(none)_" in missed_section
    assert "_(none)_" in extra_section


def test_render_report_summary_section_has_counts_and_percentages() -> None:
    """Summary must surface |cameron|, |scanner|, |matched|, recall, precision."""
    rendered = render_report(_populated_report())  # 3 cameron, 4 scanner, 2 matched
    assert "## Summary" in rendered
    assert "Cameron called: 3 tickers" in rendered
    assert "Scanner picked: 4 tickers" in rendered
    assert "Matched: 2" in rendered


def test_render_report_percentages_round_to_one_decimal() -> None:
    """Recall = 8/11 -> 72.7%; precision = 8/9 -> 88.9%. Fixed precision so
    deterministic bytes don't depend on Decimal default exponent.
    """
    cameron = frozenset(f"C{i:02d}" for i in range(11))
    matched_set = {f"C{i:02d}" for i in range(8)}
    extra_set = {f"E{i:02d}" for i in range(1)}
    scanner = frozenset(matched_set | extra_set)  # |scanner| = 9, |matched| = 8

    report = DailyReport.from_sets(
        day=date(2026, 5, 1),
        cameron=cameron,
        scanner=scanner,
    )
    rendered = render_report(report)

    assert "Recall: 8/11 = 72.7%" in rendered
    assert "Precision: 8/9 = 88.9%" in rendered


# ----------------------------------------------------------------------- CLI


def test_main_writes_deterministic_report_file(
    writer: JournalWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke: main() with --date + --db + roots writes reports/YYYY-MM-DD.md.

    Re-running on the same inputs produces byte-identical output (the
    file is overwritten in place; the gate is re-runnable as the journal
    and ground truth grow).
    """
    day = date(2026, 5, 1)
    inside_day = datetime(2026, 5, 1, 14, 30, tzinfo=UTC)
    _emit_picked(writer, ticker="AAAA", ts=inside_day)
    _emit_picked(writer, ticker="BBBB", ts=inside_day)
    _write_ground_truth(tmp_path, day, ["AAAA", "CCCC"])

    db_path = tmp_path / "journal.sqlite"
    reports_dir = tmp_path / "reports"

    # Use the same on-disk DB as the writer fixture: serialize the
    # in-memory engine to a file so the CLI can open it independently.
    # Easier: run main against the *same* file the writer wrote into.
    # The writer fixture is in-memory by default, so for this test we
    # write directly into a file-backed engine.
    eng = create_journal_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)
    file_writer = JournalWriter(create_session_factory(eng))
    _emit_picked(file_writer, ticker="AAAA", ts=inside_day)
    _emit_picked(file_writer, ticker="BBBB", ts=inside_day)
    eng.dispose()

    rc = main(
        [
            "--date", day.isoformat(),
            "--db", f"sqlite:///{db_path}",
            "--ground-truth-root", str(tmp_path),
            "--reports-dir", str(reports_dir),
        ]
    )
    assert rc == 0

    out_path = reports_dir / f"{day.isoformat()}.md"
    assert out_path.exists(), f"expected {out_path} to be written"
    first = out_path.read_text(encoding="utf-8")

    # Stdout should surface the path written and the recall/precision summary.
    captured = capsys.readouterr()
    assert str(out_path) in captured.out
    assert "Recall:" in captured.out

    # Re-run: file is overwritten, bytes are identical.
    rc2 = main(
        [
            "--date", day.isoformat(),
            "--db", f"sqlite:///{db_path}",
            "--ground-truth-root", str(tmp_path),
            "--reports-dir", str(reports_dir),
        ]
    )
    assert rc2 == 0
    second = out_path.read_text(encoding="utf-8")
    assert first == second
