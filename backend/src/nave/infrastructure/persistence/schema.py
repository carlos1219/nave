"""Esquema relacional (SQLAlchemy Core). Fuente para comparar con las migraciones.

Las migraciones de Alembic son la verdad desplegada; un test verifica que este
`metadata` y la base migrada no divergen. Se usa Core y no el ORM: las entidades del
dominio son dataclasses inmutables con invariantes, y el mapeo explícito fila → entidad
hace que toda fila cargada vuelva a pasar por esas validaciones.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING)


def _ts(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


organizations = Table(
    "organizations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("github_installation_id", BigInteger, nullable=False, unique=True),
    Column("account_login", String(39), nullable=False),
    Column("status", String(16), nullable=False),
    # Id inmutable de la cuenta de GitHub; el login puede cambiar o reasignarse (ADR 0014).
    Column("github_account_id", BigInteger, nullable=True, unique=True),
    # Inicio del plazo de purga de una organización desinstalada (RR-11).
    _ts("uninstalled_at", nullable=True),
    CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'UNINSTALLED')", name="status"),
    CheckConstraint(
        "(status = 'UNINSTALLED') = (uninstalled_at IS NOT NULL)", name="uninstalled_at"
    ),
)

repositories = Table(
    "repositories",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("organization_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("github_repo_id", BigInteger, nullable=False, unique=True),
    Column("owner", String(39), nullable=False),
    Column("name", String(100), nullable=False),
    Column("default_branch", String(255), nullable=False),
    Column("enabled", Boolean, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("oidc_issuer", String(255), nullable=False),
    Column("oidc_subject", String(255), nullable=False),
    Column("display_name", String(255), nullable=True),
    # Solo si el IdP lo marca como verificado. Informativo: nunca autentica ni vincula (ADR 0016).
    Column("email", String(320), nullable=True),
    _ts("last_login_at", nullable=True),
    UniqueConstraint("oidc_issuer", "oidc_subject"),
)

# Sesiones web (ADR 0016). Solo se guarda el SHA-256 del valor de la cookie.
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", LargeBinary, primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    _ts("created_at"),
    _ts("last_seen_at"),
    _ts("expires_at"),
    CheckConstraint("octet_length(token_hash) = 32", name="token_hash"),
    Index("ix_sessions_expires_at", "expires_at"),
    Index("ix_sessions_user_id", "user_id"),
)

# Flujos de login en curso: state, nonce y verificador PKCE de un solo uso.
login_flows = Table(
    "login_flows",
    metadata,
    Column("state_hash", LargeBinary, primary_key=True),
    Column("binding_hash", LargeBinary, nullable=False),
    Column("nonce", String(64), nullable=False),
    Column("code_verifier", String(128), nullable=False),
    _ts("created_at"),
    _ts("expires_at"),
    CheckConstraint("octet_length(state_hash) = 32", name="state_hash"),
    CheckConstraint("octet_length(binding_hash) = 32", name="binding_hash"),
    Index("ix_login_flows_expires_at", "expires_at"),
)

memberships = Table(
    "memberships",
    metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("organization_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("role", String(16), nullable=False),
    # NULL solo para el primer administrador, creado por CLI (ADR 0004).
    Column("granted_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=True),
    _ts("granted_at"),
    PrimaryKeyConstraint("user_id", "organization_id"),
    CheckConstraint("role IN ('VIEWER', 'TRIAGER', 'ADMIN')", name="role"),
)

scans = Table(
    "scans",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("repository_id", UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False),
    Column("commit_sha", String(40), nullable=False),
    Column("ref", String(255), nullable=False),
    Column("trigger", String(16), nullable=False),
    Column("trigger_key", String(128), nullable=False, unique=True),
    Column("status", String(16), nullable=False),
    _ts("queued_at"),
    Column("pull_request_number", Integer, nullable=True),
    Column("requested_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=True),
    _ts("started_at", nullable=True),
    _ts("finished_at", nullable=True),
    # Marca del barrido de mantenimiento: un escaneo atascado en QUEUED se reencola una sola vez.
    _ts("requeued_at", nullable=True),
    Column("status_reason_code", String(32), nullable=True),
    Column("status_reason_detail", String(256), nullable=True),
    Column("superseded_by", UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True),
    Column("summary", JSONB, nullable=True),
    Column("version", BigInteger, nullable=False),
    CheckConstraint("commit_sha ~ '^[0-9a-f]{40}$'", name="commit_sha"),
    CheckConstraint("trigger IN ('WEBHOOK', 'MANUAL')", name="trigger"),
    CheckConstraint(
        "status IN ('QUEUED', 'FETCHING', 'SCANNING', 'PROCESSING', "
        "'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT')",
        name="status",
    ),
    CheckConstraint(
        "(status IN ('FAILED', 'CANCELLED', 'TIMED_OUT')) = (status_reason_code IS NOT NULL)",
        name="reason_matches_status",
    ),
    CheckConstraint("version >= 0", name="version"),
    Index(
        "ix_scans_active_by_ref",
        "repository_id",
        "ref",
        postgresql_where=text("status IN ('QUEUED', 'FETCHING', 'SCANNING', 'PROCESSING')"),
    ),
    Index("ix_scans_repository_id_status_finished_at", "repository_id", "status", "finished_at"),
    Index("ix_scans_repository_id_queued_at", "repository_id", "queued_at"),
    Index("ix_scans_queued_at", "queued_at", postgresql_where=text("status = 'QUEUED'")),
    Index(
        "ix_scans_started_at",
        "started_at",
        postgresql_where=text("status IN ('FETCHING', 'SCANNING', 'PROCESSING')"),
    ),
    Index("ix_scans_finished_at", "finished_at", postgresql_where=text("finished_at IS NOT NULL")),
)

findings = Table(
    "findings",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("repository_id", UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("scanner_kind", String(32), nullable=False),
    Column("category", String(16), nullable=False),
    Column("rule_id", String(256), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("title", String(500), nullable=False),
    Column("first_seen_scan_id", UUID(as_uuid=True), ForeignKey("scans.id"), nullable=False),
    Column("last_seen_scan_id", UUID(as_uuid=True), ForeignKey("scans.id"), nullable=False),
    _ts("first_seen_at"),
    _ts("last_seen_at"),
    Column("cwe", ARRAY(String(16)), nullable=False),
    Column("cve", ARRAY(String(32)), nullable=False),
    Column("triage_state", String(16), nullable=False),
    _ts("triage_expires_at", nullable=True),
    Column("version", BigInteger, nullable=False),
    UniqueConstraint("repository_id", "fingerprint"),
    CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint"),
    CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')", name="severity"),
    CheckConstraint(
        "triage_state IN ('OPEN', 'FALSE_POSITIVE', 'ACCEPTED_RISK', 'FIXED')", name="triage_state"
    ),
    CheckConstraint(
        "(triage_state = 'ACCEPTED_RISK') = (triage_expires_at IS NOT NULL)",
        name="expiry_matches_state",
    ),
    CheckConstraint("version >= 0", name="version"),
    Index(
        "ix_findings_repository_id_triage_state_severity",
        "repository_id",
        "triage_state",
        "severity",
    ),
)

finding_occurrences = Table(
    "finding_occurrences",
    metadata,
    Column("scan_id", UUID(as_uuid=True), ForeignKey("scans.id"), nullable=False),
    Column("finding_id", UUID(as_uuid=True), ForeignKey("findings.id"), nullable=False),
    Column("path", String(1024), nullable=True),
    Column("start_line", Integer, nullable=True),
    Column("end_line", Integer, nullable=True),
    Column("message", String(4096), nullable=False),
    Column("snippet", String(2048), nullable=True),
    Column("is_new", Boolean, nullable=False),
    PrimaryKeyConstraint("scan_id", "finding_id"),
    CheckConstraint("(start_line IS NULL) = (end_line IS NULL)", name="lines_together"),
    CheckConstraint("path IS NOT NULL OR start_line IS NULL", name="lines_need_path"),
)

triage_decisions = Table(
    "triage_decisions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("finding_id", UUID(as_uuid=True), ForeignKey("findings.id"), nullable=False),
    Column("state", String(16), nullable=False),
    _ts("decided_at"),
    Column("decided_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=True),
    Column("justification", String(2000), nullable=True),
    _ts("expires_at", nullable=True),
    CheckConstraint("state IN ('OPEN', 'FALSE_POSITIVE', 'ACCEPTED_RISK', 'FIXED')", name="state"),
    CheckConstraint("(state = 'ACCEPTED_RISK') = (expires_at IS NOT NULL)", name="expiry"),
    Index("ix_triage_decisions_finding_id_decided_at", "finding_id", "decided_at"),
)

quality_gate_policies = Table(
    "quality_gate_policies",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("organization_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("document", JSONB, nullable=False),
    Column("active", Boolean, nullable=False),
    _ts("created_at"),
    UniqueConstraint("organization_id", "version"),
    Index(
        "uq_quality_gate_policies_one_active",
        "organization_id",
        unique=True,
        postgresql_where=text("active"),
    ),
)

policy_evaluations = Table(
    "policy_evaluations",
    metadata,
    Column("scan_id", UUID(as_uuid=True), ForeignKey("scans.id"), primary_key=True),
    Column("policy_id", UUID(as_uuid=True), ForeignKey("quality_gate_policies.id"), nullable=True),
    Column("verdict", String(16), nullable=False),
    Column("reasons", JSONB, nullable=False),
    CheckConstraint("verdict IN ('PASS', 'FAIL', 'NEUTRAL')", name="verdict"),
)

scan_logs = Table(
    "scan_logs",
    metadata,
    Column("scan_id", UUID(as_uuid=True), ForeignKey("scans.id"), nullable=False),
    Column("seq", BigInteger, nullable=False),
    _ts("at"),
    Column("level", String(16), nullable=False),
    Column("message", String(4096), nullable=False),
    PrimaryKeyConstraint("scan_id", "seq"),
    CheckConstraint("level IN ('INFO', 'WARNING', 'ERROR')", name="level"),
    Index("ix_scan_logs_at", "at"),
)

webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("delivery_id", UUID(as_uuid=True), primary_key=True),
    Column("event", String(32), nullable=False),
    _ts("received_at"),
    Column("outcome", String(16), nullable=False),
    Index("ix_webhook_deliveries_received_at", "received_at"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    _ts("occurred_at"),
    Column("action", String(64), nullable=False),
    Column("actor", UUID(as_uuid=True), nullable=True),
    Column("organization_id", UUID(as_uuid=True), nullable=True),
    Column("target_type", String(32), nullable=False),
    Column("target_id", String(128), nullable=False),
    Column("details", JSONB, nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False, unique=True),
)

# Administración de plataforma (ADR 0030). Solo la CLI escribe `platform_admins`.
platform_admins = Table(
    "platform_admins",
    metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    _ts("granted_at"),
)

# Último resultado de cada comprobación de salud; lo escribe el worker cada minuto.
health_reports = Table(
    "health_reports",
    metadata,
    Column("component", String(32), primary_key=True),
    Column("status", String(8), nullable=False),
    Column("detail", String(256), nullable=False),
    Column("latency_ms", Integer, nullable=True),
    _ts("checked_at"),
    CheckConstraint("status IN ('OK', 'DEGRADED', 'DOWN')", name="status"),
)

# Rechazos y duplicados de webhooks por minuto: sin cuerpo, cabeceras ni IP.
webhook_counters = Table(
    "webhook_counters",
    metadata,
    Column("minute", DateTime(timezone=True), nullable=False),
    Column("reason", String(16), nullable=False),
    Column("count", Integer, nullable=False),
    PrimaryKeyConstraint("minute", "reason"),
    CheckConstraint(
        "reason IN ('SIGNATURE', 'MEDIA_TYPE', 'MALFORMED', 'DUPLICATE')", name="reason"
    ),
    CheckConstraint("count > 0", name="count"),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("type", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    _ts("occurred_at"),
    _ts("published_at", nullable=True),
    Index("ix_outbox_events_unpublished", "id", postgresql_where=text("published_at IS NULL")),
    Index(
        "ix_outbox_events_published_at",
        "published_at",
        postgresql_where=text("published_at IS NOT NULL"),
    ),
)
