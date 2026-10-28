"""login: sessions, login flows and user profile columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        _ts("created_at"),
        _ts("last_seen_at"),
        _ts("expires_at"),
        sa.PrimaryKeyConstraint("token_hash", name="pk_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "login_flows",
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("binding_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        sa.PrimaryKeyConstraint("state_hash", name="pk_login_flows"),
        sa.CheckConstraint("octet_length(state_hash) = 32", name="ck_login_flows_state_hash"),
        sa.CheckConstraint("octet_length(binding_hash) = 32", name="ck_login_flows_binding_hash"),
    )
    op.create_index("ix_login_flows_expires_at", "login_flows", ["expires_at"])
    # Listado de escaneos de un repositorio, más recientes primero (REST, fase 4).
    op.create_index("ix_scans_repository_id_queued_at", "scans", ["repository_id", "queued_at"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_app') THEN
                GRANT UPDATE ON users TO nave_app;
                GRANT SELECT, INSERT, UPDATE, DELETE ON sessions, login_flows TO nave_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index("ix_scans_repository_id_queued_at", table_name="scans")
    op.drop_table("login_flows")
    op.drop_table("sessions")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email")
    op.drop_column("users", "display_name")
