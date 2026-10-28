"""platform administration: platform admins, health reports and webhook counters (ADR 0030)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_platform_admins"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_platform_admins_user_id_users"
        ),
    )
    op.create_table(
        "health_reports",
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("detail", sa.String(256), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("component", name="pk_health_reports"),
        sa.CheckConstraint(
            "status IN ('OK', 'DEGRADED', 'DOWN')", name="ck_health_reports_status"
        ),
    )
    op.create_table(
        "webhook_counters",
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("minute", "reason", name="pk_webhook_counters"),
        sa.CheckConstraint(
            "reason IN ('SIGNATURE', 'MEDIA_TYPE', 'MALFORMED', 'DUPLICATE')",
            name="ck_webhook_counters_reason",
        ),
        sa.CheckConstraint("count > 0", name="ck_webhook_counters_count"),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_app') THEN
                GRANT SELECT, INSERT, DELETE ON platform_admins TO nave_app;
                GRANT SELECT, INSERT, UPDATE ON health_reports, webhook_counters TO nave_app;
                GRANT UPDATE (active) ON quality_gate_policies TO nave_app;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_maintenance') THEN
                GRANT SELECT, DELETE ON webhook_counters TO nave_maintenance;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_app') THEN
                REVOKE UPDATE (active) ON quality_gate_policies FROM nave_app;
            END IF;
        END
        $$
        """
    )
    op.drop_table("webhook_counters")
    op.drop_table("health_reports")
    op.drop_table("platform_admins")
