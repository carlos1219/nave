"""initial schema

Revision ID: 0001
Revises: -
Create Date: 2026-10-11 16:41:21.106991
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_log_hash")),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=39), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'UNINSTALLED')", name=op.f("ck_organizations_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint(
            "github_installation_id", name=op.f("uq_organizations_github_installation_id")
        ),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=255), nullable=False),
        sa.Column("oidc_subject", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint(
            "oidc_issuer", "oidc_subject", name=op.f("uq_users_oidc_issuer_oidc_subject")
        ),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.UUID(), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("delivery_id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.UUID(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('VIEWER', 'TRIAGER', 'ADMIN')", name=op.f("ck_memberships_role")
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], name=op.f("fk_memberships_granted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memberships_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", "organization_id", name=op.f("pk_memberships")),
    )
    op.create_table(
        "quality_gate_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_quality_gate_policies_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_gate_policies")),
        sa.UniqueConstraint(
            "organization_id",
            "version",
            name=op.f("uq_quality_gate_policies_organization_id_version"),
        ),
    )
    op.create_index(
        "uq_quality_gate_policies_one_active",
        "quality_gate_policies",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(length=39), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_repositories_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("github_repo_id", name=op.f("uq_repositories_github_repo_id")),
    )
    op.create_table(
        "scans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("trigger_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason_code", sa.String(length=32), nullable=True),
        sa.Column("status_reason_detail", sa.String(length=256), nullable=True),
        sa.Column("superseded_by", sa.UUID(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "(status IN ('FAILED', 'CANCELLED', 'TIMED_OUT')) = (status_reason_code IS NOT NULL)",
            name=op.f("ck_scans_reason_matches_status"),
        ),
        sa.CheckConstraint("commit_sha ~ '^[0-9a-f]{40}$'", name=op.f("ck_scans_commit_sha")),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'FETCHING', 'SCANNING', 'PROCESSING', "
            "'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT')",
            name=op.f("ck_scans_status"),
        ),
        sa.CheckConstraint("trigger IN ('WEBHOOK', 'MANUAL')", name=op.f("ck_scans_trigger")),
        sa.CheckConstraint("version >= 0", name=op.f("ck_scans_version")),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], name=op.f("fk_scans_repository_id_repositories")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_scans_requested_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["scans.id"], name=op.f("fk_scans_superseded_by_scans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
        sa.UniqueConstraint("trigger_key", name=op.f("uq_scans_trigger_key")),
    )
    op.create_index(
        "ix_scans_active_by_ref",
        "scans",
        ["repository_id", "ref"],
        unique=False,
        postgresql_where=sa.text("status IN ('QUEUED', 'FETCHING', 'SCANNING', 'PROCESSING')"),
    )
    op.create_index(
        "ix_scans_repository_id_status_finished_at",
        "scans",
        ["repository_id", "status", "finished_at"],
        unique=False,
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scanner_kind", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=256), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("first_seen_scan_id", sa.UUID(), nullable=False),
        sa.Column("last_seen_scan_id", sa.UUID(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cwe", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("cve", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("triage_state", sa.String(length=16), nullable=False),
        sa.Column("triage_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "(triage_state = 'ACCEPTED_RISK') = (triage_expires_at IS NOT NULL)",
            name=op.f("ck_findings_expiry_matches_state"),
        ),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_findings_fingerprint")),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')",
            name=op.f("ck_findings_severity"),
        ),
        sa.CheckConstraint(
            "triage_state IN ('OPEN', 'FALSE_POSITIVE', 'ACCEPTED_RISK', 'FIXED')",
            name=op.f("ck_findings_triage_state"),
        ),
        sa.CheckConstraint("version >= 0", name=op.f("ck_findings_version")),
        sa.ForeignKeyConstraint(
            ["first_seen_scan_id"], ["scans.id"], name=op.f("fk_findings_first_seen_scan_id_scans")
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_scan_id"], ["scans.id"], name=op.f("fk_findings_last_seen_scan_id_scans")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_findings_repository_id_repositories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint(
            "repository_id", "fingerprint", name=op.f("uq_findings_repository_id_fingerprint")
        ),
    )
    op.create_index(
        "ix_findings_repository_id_triage_state_severity",
        "findings",
        ["repository_id", "triage_state", "severity"],
        unique=False,
    )
    op.create_table(
        "policy_evaluations",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("policy_id", sa.UUID(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('PASS', 'FAIL', 'NEUTRAL')", name=op.f("ck_policy_evaluations_verdict")
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["quality_gate_policies.id"],
            name=op.f("fk_policy_evaluations_policy_id_quality_gate_policies"),
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f("fk_policy_evaluations_scan_id_scans")
        ),
        sa.PrimaryKeyConstraint("scan_id", name=op.f("pk_policy_evaluations")),
    )
    op.create_table(
        "scan_logs",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=4096), nullable=False),
        sa.CheckConstraint(
            "level IN ('INFO', 'WARNING', 'ERROR')", name=op.f("ck_scan_logs_level")
        ),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], name=op.f("fk_scan_logs_scan_id_scans")),
        sa.PrimaryKeyConstraint("scan_id", "seq", name=op.f("pk_scan_logs")),
    )
    op.create_table(
        "finding_occurrences",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=4096), nullable=False),
        sa.Column("snippet", sa.String(length=2048), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "(start_line IS NULL) = (end_line IS NULL)",
            name=op.f("ck_finding_occurrences_lines_together"),
        ),
        sa.CheckConstraint(
            "path IS NOT NULL OR start_line IS NULL",
            name=op.f("ck_finding_occurrences_lines_need_path"),
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name=op.f("fk_finding_occurrences_finding_id_findings")
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f("fk_finding_occurrences_scan_id_scans")
        ),
        sa.PrimaryKeyConstraint("scan_id", "finding_id", name=op.f("pk_finding_occurrences")),
    )
    op.create_table(
        "triage_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("justification", sa.String(length=2000), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state = 'ACCEPTED_RISK') = (expires_at IS NOT NULL)",
            name=op.f("ck_triage_decisions_expiry"),
        ),
        sa.CheckConstraint(
            "state IN ('OPEN', 'FALSE_POSITIVE', 'ACCEPTED_RISK', 'FIXED')",
            name=op.f("ck_triage_decisions_state"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_triage_decisions_decided_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name=op.f("fk_triage_decisions_finding_id_findings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triage_decisions")),
    )
    op.create_index(
        "ix_triage_decisions_finding_id_decided_at",
        "triage_decisions",
        ["finding_id", "decided_at"],
        unique=False,
    )
    _create_append_only_guards()
    _grant_runtime_roles()


def downgrade() -> None:
    op.drop_index("ix_triage_decisions_finding_id_decided_at", table_name="triage_decisions")
    op.drop_table("triage_decisions")
    op.drop_table("finding_occurrences")
    op.drop_table("scan_logs")
    op.drop_table("policy_evaluations")
    op.drop_index("ix_findings_repository_id_triage_state_severity", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_scans_repository_id_status_finished_at", table_name="scans")
    op.drop_index(
        "ix_scans_active_by_ref",
        table_name="scans",
        postgresql_where=sa.text("status IN ('QUEUED', 'FETCHING', 'SCANNING', 'PROCESSING')"),
    )
    op.drop_table("scans")
    op.drop_table("repositories")
    op.drop_index(
        "uq_quality_gate_policies_one_active",
        table_name="quality_gate_policies",
        postgresql_where=sa.text("active"),
    )
    op.drop_table("quality_gate_policies")
    op.drop_table("memberships")
    op.drop_table("webhook_deliveries")
    op.drop_table("users")
    op.drop_index(
        "ix_outbox_events_unpublished",
        table_name="outbox_events",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox_events")
    op.drop_table("organizations")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION IF EXISTS nave_forbid_mutation()")


_APP_TABLES = (
    "organizations",
    "repositories",
    "users",
    "memberships",
    "scans",
    "findings",
    "finding_occurrences",
    "quality_gate_policies",
    "policy_evaluations",
    "scan_logs",
    "webhook_deliveries",
    "outbox_events",
)
_APPEND_ONLY_TABLES = ("audit_log", "triage_decisions")
_APP_UPDATABLE = ("scans", "findings", "memberships", "policy_evaluations", "outbox_events")
_RETENTION_TABLES = (
    "scans",
    "finding_occurrences",
    "scan_logs",
    "webhook_deliveries",
    "outbox_events",
    "policy_evaluations",
)


def _create_append_only_guards() -> None:
    """Defensa en profundidad (AM-305): aunque la aplicación se conectara con un rol con
    más permisos de los debidos, el audit log no se puede alterar ni borrar y una
    decisión de triage no se puede reescribir."""
    op.execute(
        """
        CREATE FUNCTION nave_forbid_mutation() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION nave_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION nave_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER triage_decisions_immutable BEFORE UPDATE ON triage_decisions "
        "FOR EACH ROW EXECUTE FUNCTION nave_forbid_mutation()"
    )


def _grant_runtime_roles() -> None:
    """Mínimo privilegio para los roles de runtime, si existen (los crea el despliegue,
    fase 6). `nave_app`: sin DDL, sin UPDATE/DELETE en tablas append-only, DELETE solo en
    memberships. `nave_maintenance`: borrado por retención (ADR 0006), sin audit_log."""
    app_tables = ", ".join(_APP_TABLES)
    append_only = ", ".join(_APPEND_ONLY_TABLES)
    updatable = ", ".join(_APP_UPDATABLE)
    retention = ", ".join(_RETENTION_TABLES)
    # S608: solo se interpolan las constantes de este módulo, nunca entrada externa.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_app') THEN
                GRANT SELECT, INSERT ON {app_tables} TO nave_app;
                GRANT UPDATE ON {updatable} TO nave_app;
                GRANT DELETE ON memberships TO nave_app;
                GRANT SELECT, INSERT ON {append_only} TO nave_app;
                GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO nave_app;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_maintenance') THEN
                GRANT SELECT, DELETE ON {retention} TO nave_maintenance;
                GRANT SELECT ON repositories, organizations, findings TO nave_maintenance;
            END IF;
        END
        $$
        """  # noqa: S608
    )
