"""Phase 2 daily comparison report -- recall metric for the 70% gate.

Atom A7 (#46). Joins the scanner journal (A4/A5) with the hand-curated
ground-truth oracle (A6) for one trading day and emits a deterministic
markdown report under ``reports/YYYY-MM-DD.md``. Per Decision D3 (#37),
matching is ticker-only -- time-window matching is deferred.

The "scanner set" is sourced from ``ScannerDecision`` rows whose ``kind``
is ``PICKED``, joined to the linked ``Pick`` row -- *not* from
``WatchlistEntry``. ``WatchlistEntry`` is open-ended membership
(``added_at`` / ``removed_at``) and belongs to the Phase 3 graduation
surface; the recall metric here is "did the scanner pick what Cameron
called out", which is the per-day picked-set.

The day boundary is America/New_York (matches the scanner's
``is_market_hours`` window in :mod:`ross_trading.core.clock`). Picks
stored in tz-aware UTC are filtered against ``[YYYY-MM-DD 00:00 ET,
+1d 00:00 ET)`` translated to UTC. DST is handled by zoneinfo.

Pick.ticker is not normalized at the model layer today (a follow-up
issue tracks enforcing ``.upper()`` at ``Pick.__init__``); this module
defensively upper-cases the journal side of the join so a stray
lowercase row doesn't silently miss against the ground truth, which A6
already upper-cases on load.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.ground_truth import load_ground_truth
from ross_trading.journal.models import DecisionKind, Pick
from ross_trading.journal.models import ScannerDecision as ScannerDecisionRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session, sessionmaker


_NY_TZ = ZoneInfo("America/New_York")
_DEFAULT_DB_URL = "sqlite:///journal.sqlite"
_DEFAULT_REPORTS_DIR = Path("reports")


@dataclass(frozen=True, slots=True)
class DailyReport:
    """Single-day comparison of scanner picks vs Cameron's ground-truth."""

    day: date
    cameron: frozenset[str]
    scanner: frozenset[str]
    matched: frozenset[str]
    missed: frozenset[str]
    extra: frozenset[str]

    @classmethod
    def from_sets(
        cls,
        *,
        day: date,
        cameron: frozenset[str],
        scanner: frozenset[str],
    ) -> DailyReport:
        """Derive matched / missed / extra from the two ticker sets."""
        return cls(
            day=day,
            cameron=cameron,
            scanner=scanner,
            matched=cameron & scanner,
            missed=cameron - scanner,
            extra=scanner - cameron,
        )

    @property
    def recall(self) -> Decimal:
        # 0/0 -> 0 by convention so the report still renders on the
        # "ground truth missing entries" corner without raising.
        if not self.cameron:
            return Decimal(0)
        return Decimal(len(self.matched)) / Decimal(len(self.cameron))

    @property
    def precision(self) -> Decimal:
        # 0/0 -> 0 by convention so the report still renders on a
        # quiet scanner day without raising.
        if not self.scanner:
            return Decimal(0)
        return Decimal(len(self.matched)) / Decimal(len(self.scanner))


def _et_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Return ``[start, end)`` in UTC for *day* interpreted as ET wall-clock.

    DST handled by zoneinfo: a 23-hour or 25-hour day surfaces as a UTC
    range whose width matches the underlying ET span, not a fixed 24h.
    """
    start_et = datetime.combine(day, datetime.min.time(), tzinfo=_NY_TZ)
    end_et = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=_NY_TZ)
    return start_et.astimezone(ZoneInfo("UTC")), end_et.astimezone(ZoneInfo("UTC"))


def build_daily_report(
    day: date,
    *,
    session_factory: sessionmaker[Session],
    ground_truth_root: Path | None = None,
) -> DailyReport:
    """Compute the Phase 2 daily comparison for *day*.

    Reads the scanner picked-set from the journal (``ScannerDecision``
    rows with ``kind=PICKED`` joined to ``Pick``, filtered to *day* in
    ET wall-clock) and the ground-truth set from
    :func:`load_ground_truth`. Returns a typed result; rendering and
    persistence are separate concerns.

    Raises :class:`FileNotFoundError` when the ground-truth file for
    *day* is missing (surfaced from the loader unmodified, so a curator
    typo'd date debugs cleanly).
    """
    start_utc, end_utc = _et_day_bounds_utc(day)

    with session_factory() as session:
        rows = session.execute(
            select(Pick.ticker)
            .join(ScannerDecisionRow, ScannerDecisionRow.pick_id == Pick.id)
            .where(
                ScannerDecisionRow.kind == DecisionKind.PICKED,
                ScannerDecisionRow.decision_ts >= start_utc,
                ScannerDecisionRow.decision_ts < end_utc,
            )
        ).scalars().all()
    scanner = frozenset(t.upper() for t in rows)

    cameron = frozenset(
        e.ticker for e in load_ground_truth(day, root=ground_truth_root)
    )

    return DailyReport.from_sets(day=day, cameron=cameron, scanner=scanner)


def _format_pct(value: Decimal) -> str:
    """Format *value* (in 0..1) as a one-decimal percentage, e.g. ``72.7%``."""
    pct = (value * Decimal(100)).quantize(Decimal("0.1"))
    return f"{pct}%"


def render_report(report: DailyReport) -> str:
    """Render *report* as the deterministic markdown audit artifact.

    Section order is fixed (Definitions -> Summary -> Matched -> Missed
    -> Extra). Tickers within each ticker-list section are sorted A-Z.
    Empty sections render their heading plus ``_(none)_`` so the report
    shape is uniform across days. The body contains no datetime stamps;
    the only date is ``report.day`` in the title.
    """
    cameron_n = len(report.cameron)
    scanner_n = len(report.scanner)
    matched_n = len(report.matched)
    recall = _format_pct(report.recall)
    precision = _format_pct(report.precision)

    lines: list[str] = [
        f"# Phase 2 daily comparison -- {report.day.isoformat()}",
        "",
        "## Definitions",
        "- Recall = |Cameron ∩ scanner| / |Cameron|  (gating, target >= 70%)",
        "- Precision = |Cameron ∩ scanner| / |scanner|  (reported, not gating)",
        "",
        "## Summary",
        f"- Cameron called: {cameron_n} tickers",
        f"- Scanner picked: {scanner_n} tickers",
        f"- Matched: {matched_n}",
        f"- Recall: {matched_n}/{cameron_n} = {recall}",
        f"- Precision: {matched_n}/{scanner_n} = {precision}",
        "",
    ]
    for heading, tickers in (
        ("Matched", report.matched),
        ("Missed", report.missed),
        ("Extra", report.extra),
    ):
        lines.append(f"## {heading}")
        if not tickers:
            lines.append("_(none)_")
        else:
            for ticker in sorted(tickers):
                lines.append(f"- {ticker}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: write ``reports/YYYY-MM-DD.md`` for the requested day.

    Re-runs are idempotent -- the file is overwritten in place so the
    gate stays cheap to re-evaluate as the journal and ground truth
    grow. Stdout surfaces the written path plus a one-line summary
    suitable for shells and CI.
    """
    parser = argparse.ArgumentParser(
        prog="python -m ross_trading.journal.report",
        description=(
            "Phase 2 daily comparison report -- joins the scanner journal "
            "with hand-curated ground truth and writes a deterministic "
            "markdown summary."
        ),
    )
    parser.add_argument(
        "--date",
        required=True,
        type=date.fromisoformat,
        help="Trading day (ET) to report on, in YYYY-MM-DD form.",
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB_URL,
        help=(
            "SQLAlchemy URL for the scanner journal "
            f"(default: {_DEFAULT_DB_URL})."
        ),
    )
    parser.add_argument(
        "--ground-truth-root",
        type=Path,
        default=None,
        help=(
            "Override the ground-truth directory root. "
            "Defaults to the repo's ground_truth/ directory."
        ),
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=_DEFAULT_REPORTS_DIR,
        help=f"Directory to write the report into (default: {_DEFAULT_REPORTS_DIR}).",
    )
    args = parser.parse_args(argv)

    engine = create_journal_engine(args.db)
    try:
        session_factory = create_session_factory(engine)
        report = build_daily_report(
            args.date,
            session_factory=session_factory,
            ground_truth_root=args.ground_truth_root,
        )
    finally:
        engine.dispose()

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.reports_dir / f"{args.date.isoformat()}.md"
    out_path.write_text(render_report(report), encoding="utf-8")

    cameron_n = len(report.cameron)
    scanner_n = len(report.scanner)
    matched_n = len(report.matched)
    print(f"Wrote {out_path}")
    print(
        f"Recall: {matched_n}/{cameron_n} = {_format_pct(report.recall)} | "
        f"Precision: {matched_n}/{scanner_n} = {_format_pct(report.precision)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - thin shim
    raise SystemExit(main())
