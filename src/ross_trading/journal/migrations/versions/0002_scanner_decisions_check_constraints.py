"""scanner_decisions: kind -> field-population CHECK constraints.

Revision ID: 0002_check_constraints
Revises: 0001_initial
Create Date: 2026-05-02 00:00:01

Carries the deferred P2 review item from #43 / PR #54: enforce the
``kind`` -> field-population invariants at the schema level so a buggy
writer cannot silently insert a malformed row.

Five CHECKs on ``scanner_decisions`` (names + conditions live in
``_CONSTRAINTS`` below; documented inline rather than in this docstring
to keep lines under the project's line-length cap).

SQLite cannot ``ALTER TABLE ... ADD CHECK`` directly, so this revision
uses Alembic batch mode (``with op.batch_alter_table``) which rewrites
as ``CREATE TABLE new + COPY + DROP old + RENAME``. Round-trip (upgrade
+ downgrade) on a populated DB is verified by
``tests/integration/test_journal_check_constraints.py::test_upgrade_then_downgrade_with_seeded_rows``.
"""

from __future__ import annotations

from alembic import op

revision = "0002_check_constraints"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


_CONSTRAINTS = (
    (
        "ck_scanner_decisions_picked_pick_id",
        "(kind = 'picked') = (pick_id IS NOT NULL)",
    ),
    (
        "ck_scanner_decisions_rejected_reason",
        "(kind = 'rejected') = (rejection_reason IS NOT NULL)",
    ),
    (
        "ck_scanner_decisions_feed_gap_start",
        "(kind = 'feed_gap') = (gap_start IS NOT NULL)",
    ),
    (
        "ck_scanner_decisions_feed_gap_end",
        "(kind = 'feed_gap') = (gap_end IS NOT NULL)",
    ),
    (
        "ck_scanner_decisions_ticker_required",
        "kind IN ('stale_feed', 'feed_gap') OR ticker IS NOT NULL",
    ),
)


def upgrade() -> None:
    with op.batch_alter_table("scanner_decisions") as batch_op:
        for name, condition in _CONSTRAINTS:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    with op.batch_alter_table("scanner_decisions") as batch_op:
        for name, _ in _CONSTRAINTS:
            batch_op.drop_constraint(name, type_="check")
