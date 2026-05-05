"""Atom A5 (#44) -- JournalWriter unit tests.

Round-trips every ``ScannerDecision`` kind through ``emit()``, asserts
``record_scan`` commits exactly once per call (SA event listener),
verifies partial-failure rollback, and pins the ``RejectionReason``
enum's seven values as the #51 contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from ross_trading.journal.engine import (
    create_journal_engine,
    create_session_factory,
)
from ross_trading.journal.models import (
    Base,
    DecisionKind,
    Pick,
    RejectionReason,
)
from ross_trading.journal.models import (
    ScannerDecision as ScannerDecisionRow,
)
from ross_trading.journal.writer import JournalWriter
from ross_trading.scanner.decisions import DecisionSink, ScannerDecision
from ross_trading.scanner.types import ScannerPick

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

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


# ------------------------------------------------------------------- helpers


def _pick(
    *,
    ticker: str = "ABCD",
    ts: datetime | None = None,
    rank: int = 1,
) -> ScannerPick:
    return ScannerPick(
        ticker=ticker,
        ts=ts or datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        rel_volume=Decimal("12.5"),
        pct_change=Decimal("18.75"),
        price=Decimal("3.42"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=4,
        rank=rank,
    )


def _commit_counter(engine: Engine) -> list[int]:
    """Return a single-element list whose value tracks engine-level commits."""
    counter = [0]

    @event.listens_for(engine, "commit")
    def _on_commit(_conn: object) -> None:
        counter[0] += 1

    return counter


# ----------------------------------------------------------- emit: per-kind


def test_emit_picked_round_trips_pick_and_decision(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    """``emit('picked')`` writes both the Pick row and the linked decision."""
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    pick = _pick(ts=decision_ts)
    writer.emit(
        ScannerDecision(
            kind="picked",
            decision_ts=decision_ts,
            ticker=pick.ticker,
            pick=pick,
            reason=None,
            gap_start=None,
            gap_end=None,
        )
    )

    with session_factory() as session:
        rows = session.execute(
            select(ScannerDecisionRow).order_by(ScannerDecisionRow.id)
        ).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.kind is DecisionKind.PICKED
        assert row.decision_ts == decision_ts
        assert row.ticker == "ABCD"
        assert row.pick_id is not None

        pick_row = session.get(Pick, row.pick_id)
        assert pick_row is not None
        assert pick_row.ticker == "ABCD"
        assert pick_row.ts == decision_ts
        assert pick_row.rel_volume == Decimal("12.5")
        assert pick_row.pct_change == Decimal("18.75")
        assert pick_row.price == Decimal("3.42")
        assert pick_row.float_shares == 8_500_000
        assert pick_row.news_present is True
        assert pick_row.headline_count == 4
        assert pick_row.rank == 1


def test_emit_stale_feed_round_trips(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    writer.emit(
        ScannerDecision(
            kind="stale_feed",
            decision_ts=decision_ts,
            ticker=None,
            pick=None,
            reason="feed stale by 7.0s",
            gap_start=None,
            gap_end=None,
        )
    )

    with session_factory() as session:
        rows = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind is DecisionKind.STALE_FEED
    assert row.decision_ts == decision_ts
    assert row.ticker is None
    assert row.pick_id is None
    assert row.reason == "feed stale by 7.0s"
    assert row.gap_start is None
    assert row.gap_end is None


def test_emit_feed_gap_round_trips(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    gap_start = datetime(2026, 5, 2, 14, 29, tzinfo=UTC)
    gap_end = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    writer.emit(
        ScannerDecision(
            kind="feed_gap",
            decision_ts=decision_ts,
            ticker=None,
            pick=None,
            reason="upstream socket reset",
            gap_start=gap_start,
            gap_end=gap_end,
        )
    )

    with session_factory() as session:
        rows = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind is DecisionKind.FEED_GAP
    assert row.decision_ts == decision_ts
    assert row.ticker is None
    assert row.pick_id is None
    assert row.reason == "upstream socket reset"
    assert row.gap_start == gap_start
    assert row.gap_end == gap_end


def test_emit_picked_creates_independent_pick_per_call(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    """Same (ticker, ts) across emits => two Pick rows by design (no dedup)."""
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    for _ in range(2):
        writer.emit(
            ScannerDecision(
                kind="picked",
                decision_ts=decision_ts,
                ticker="ABCD",
                pick=_pick(ts=decision_ts),
                reason=None,
                gap_start=None,
                gap_end=None,
            )
        )

    with session_factory() as session:
        pick_rows = session.execute(select(Pick)).scalars().all()
        decision_rows = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert len(pick_rows) == 2
    assert len(decision_rows) == 2
    assert {r.pick_id for r in decision_rows} == {p.id for p in pick_rows}


# -------------------------------------------------------------- record_scan


def test_record_scan_single_commit_for_mixed_picks_and_rejections(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    """One scan tick = one transaction: ``record_scan`` commits exactly once."""
    counter = _commit_counter(engine)
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    picks = [_pick(ticker="ABCD", ts=decision_ts, rank=1),
             _pick(ticker="WXYZ", ts=decision_ts, rank=2)]
    rejected = {
        "AAAA": RejectionReason.REL_VOLUME,
        "BBBB": RejectionReason.PCT_CHANGE,
        "CCCC": RejectionReason.FLOAT_SIZE,
    }

    writer.record_scan(decision_ts=decision_ts, picks=picks, rejected=rejected)

    assert counter[0] == 1, f"expected 1 commit, got {counter[0]}"

    with session_factory() as session:
        decisions = session.execute(
            select(ScannerDecisionRow).order_by(ScannerDecisionRow.id)
        ).scalars().all()
        kinds = [(d.kind, d.ticker) for d in decisions]
        assert (DecisionKind.PICKED, "ABCD") in kinds
        assert (DecisionKind.PICKED, "WXYZ") in kinds
        assert (DecisionKind.REJECTED, "AAAA") in kinds
        assert (DecisionKind.REJECTED, "BBBB") in kinds
        assert (DecisionKind.REJECTED, "CCCC") in kinds

        rejected_rows = [d for d in decisions if d.kind is DecisionKind.REJECTED]
        for r in rejected_rows:
            assert r.ticker is not None
            assert r.rejection_reason is rejected[r.ticker]
            assert r.pick_id is None

        pick_rows = session.execute(select(Pick)).scalars().all()
        assert len(pick_rows) == 2


def test_record_scan_empty_inputs_is_a_no_op(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    """Zero picks + zero rejected: doesn't crash, persists nothing."""
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)

    writer.record_scan(decision_ts=decision_ts, picks=[], rejected={})

    with session_factory() as session:
        rows = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert rows == []


def test_record_scan_rolls_back_on_partial_failure(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    """A constraint violation mid-tick rolls back ALL rows for that tick."""
    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)
    bad_pick = ScannerPick(
        ticker="BAD",
        ts=decision_ts,
        rel_volume=Decimal("12.5"),
        pct_change=Decimal("18.75"),
        price=Decimal("3.42"),
        float_shares=8_500_000,
        news_present=True,
        headline_count=4,
        rank=1,
    )
    # Force NOT NULL violation by zeroing out a non-nullable field
    # post-construction. ScannerPick is frozen, so we use object.__setattr__
    # to make it dirty in a way that bypasses dataclass validation.
    object.__setattr__(bad_pick, "ticker", None)

    picks = [_pick(ticker="GOOD", ts=decision_ts, rank=1), bad_pick]
    rejected = {"REJX": RejectionReason.REL_VOLUME}

    with pytest.raises((IntegrityError, Exception)):
        writer.record_scan(decision_ts=decision_ts, picks=picks, rejected=rejected)

    with session_factory() as session:
        pick_rows = session.execute(select(Pick)).scalars().all()
        decision_rows = session.execute(select(ScannerDecisionRow)).scalars().all()
    assert pick_rows == [], "GOOD Pick must be rolled back"
    assert decision_rows == [], "REJX rejection must be rolled back"


# -------------------------------------------------------- enum stability


def test_record_scan_commit_p99_under_2s_on_populated_db(
    writer: JournalWriter,
    session_factory: sessionmaker[Session],
) -> None:
    """Microbenchmark: record_scan p99 commit time < 2s with >=10k existing rows.

    Not a hard perf gate -- if this regresses the answer is to revisit
    (per #44 acceptance: "the answer is to revisit, not to add aiosqlite
    preemptively").
    """
    import time

    decision_ts = datetime(2026, 5, 2, 14, 30, tzinfo=UTC)

    # Seed >= 10k existing scanner_decisions rows via raw INSERT for speed.
    seed_ts = "2026-04-01T14:30:00+00:00"
    with session_factory() as session, session.begin():
        conn = session.connection()
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, "
            "gap_start, gap_end, rejection_reason) "
            "VALUES "
            + ",".join(
                f"('rejected', '{seed_ts}', 'T{i:05d}', NULL, NULL, NULL, NULL, "
                "'rel_volume')"
                for i in range(10_000)
            ),
        )

    picks = [_pick(ticker=f"P{i:03d}", ts=decision_ts, rank=i + 1) for i in range(5)]
    rejected = {f"R{i:03d}": RejectionReason.REL_VOLUME for i in range(20)}

    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        writer.record_scan(decision_ts=decision_ts, picks=picks, rejected=rejected)
        samples.append(time.perf_counter() - start)

    p99 = sorted(samples)[int(len(samples) * 0.99) - 1]
    assert p99 < 2.0, (
        f"record_scan p99 over 2s budget: {p99:.3f}s on 10k-row DB. "
        "If this regresses, revisit per #44 acceptance -- do not add aiosqlite preemptively."
    )


def test_rejection_reason_values_match_51_contract() -> None:
    """The seven RejectionReason values are the #51 contract; rename = migration.

    Locked-in literals (order matches the Scanner.scan AND-chain).
    """
    expected = [
        "no_snapshot",
        "missing_baseline",
        "missing_float",
        "rel_volume",
        "pct_change",
        "price_band",
        "float_size",
    ]
    actual = [r.value for r in RejectionReason]
    assert actual == expected, (
        f"RejectionReason vocabulary drifted: expected {expected}, got {actual}. "
        "This is the #51 contract; renaming requires a migration."
    )


# =====================================================================
# Issue #51 -- DecisionSink Protocol conformance
# =====================================================================


def test_journal_writer_satisfies_decision_sink_protocol(
    session_factory: sessionmaker[Session],
) -> None:
    """Post-#51, DecisionSink requires both emit and record_scan.
    JournalWriter must satisfy both."""
    writer = JournalWriter(session_factory)
    assert isinstance(writer, DecisionSink)
