"""maintenance support: requeue mark and retention indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("requeued_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_scans_queued_at", "scans", ["queued_at"], postgresql_where=sa.text("status = 'QUEUED'")
    )
    op.create_index(
        "ix_scans_started_at",
        "scans",
        ["started_at"],
        postgresql_where=sa.text("status IN ('FETCHING', 'SCANNING', 'PROCESSING')"),
    )
    op.create_index(
        "ix_scans_finished_at",
        "scans",
        ["finished_at"],
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )
    op.create_index("ix_scan_logs_at", "scan_logs", ["at"])
    op.create_index("ix_webhook_deliveries_received_at", "webhook_deliveries", ["received_at"])
    op.create_index(
        "ix_outbox_events_published_at",
        "outbox_events",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_published_at", table_name="outbox_events")
    op.drop_index("ix_webhook_deliveries_received_at", table_name="webhook_deliveries")
    op.drop_index("ix_scan_logs_at", table_name="scan_logs")
    op.drop_index("ix_scans_finished_at", table_name="scans")
    op.drop_index("ix_scans_started_at", table_name="scans")
    op.drop_index("ix_scans_queued_at", table_name="scans")
    op.drop_column("scans", "requeued_at")
