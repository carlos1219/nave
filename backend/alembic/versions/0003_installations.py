"""installation sync: immutable account id and uninstall timestamp

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("github_account_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "organizations", sa.Column("uninstalled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_organizations_github_account_id", "organizations", ["github_account_id"]
    )
    # Las organizaciones ya desinstaladas antes de esta migración empiezan su plazo ahora.
    op.execute("UPDATE organizations SET uninstalled_at = now() WHERE status = 'UNINSTALLED'")
    op.create_check_constraint(
        "ck_organizations_uninstalled_at",
        "organizations",
        "(status = 'UNINSTALLED') = (uninstalled_at IS NOT NULL)",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_app') THEN
                GRANT UPDATE ON organizations, repositories TO nave_app;
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
                REVOKE UPDATE ON organizations, repositories FROM nave_app;
            END IF;
        END
        $$
        """
    )
    op.drop_constraint("ck_organizations_uninstalled_at", "organizations", type_="check")
    op.drop_constraint("uq_organizations_github_account_id", "organizations", type_="unique")
    op.drop_column("organizations", "uninstalled_at")
    op.drop_column("organizations", "github_account_id")
