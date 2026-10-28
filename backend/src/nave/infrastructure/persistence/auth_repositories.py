"""Usuarios, sesiones y flujos de login en PostgreSQL (ADR 0016).

Nunca se guarda un token en claro: las claves primarias son SHA-256 de los valores de las
cookies. Los errores del driver los traduce la unidad de trabajo."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nave.application.dto.auth import (
    LoginFlow,
    OidcIdentity,
    SessionRecord,
    UserSummary,
)
from nave.application.errors import NotFoundError
from nave.domain.value_objects.identifiers import UserId
from nave.infrastructure.persistence import schema as t


def _summary(row: Row[Any]) -> UserSummary:
    return UserSummary(
        id=UserId(row.id),
        issuer=row.oidc_issuer,
        subject=row.oidc_subject,
        display_name=row.display_name,
        email=row.email,
        last_login_at=row.last_login_at,
    )


def _session(row: Row[Any]) -> SessionRecord:
    return SessionRecord(
        token_hash=bytes(row.token_hash),
        user_id=UserId(row.user_id),
        csrf_token=row.csrf_token,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
    )


class SqlUserRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record_login(self, identity: OidcIdentity, *, new_id: UUID, at: datetime) -> UserId:
        statement = pg_insert(t.users).values(
            id=new_id,
            oidc_issuer=identity.issuer,
            oidc_subject=identity.subject,
            display_name=identity.display_name,
            email=identity.email,
            last_login_at=at,
        )
        user_id: UUID = self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["oidc_issuer", "oidc_subject"],
                set_={
                    "display_name": statement.excluded.display_name,
                    "email": statement.excluded.email,
                    "last_login_at": statement.excluded.last_login_at,
                },
            ).returning(t.users.c.id)
        ).scalar_one()
        return UserId(user_id)

    def get(self, user: UserId) -> UserSummary:
        row = self._conn.execute(select(t.users).where(t.users.c.id == user.value)).first()
        if row is None:
            raise NotFoundError("user")
        return _summary(row)

    def list_recent(self, *, limit: int) -> Sequence[UserSummary]:
        rows = self._conn.execute(
            select(t.users)
            .order_by(t.users.c.last_login_at.desc().nulls_last(), t.users.c.id)
            .limit(limit)
        ).all()
        return [_summary(row) for row in rows]


class SqlSessionRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, record: SessionRecord) -> None:
        self._conn.execute(
            insert(t.sessions).values(
                token_hash=record.token_hash,
                user_id=record.user_id.value,
                csrf_token=record.csrf_token,
                created_at=record.created_at,
                last_seen_at=record.last_seen_at,
                expires_at=record.expires_at,
            )
        )

    def get(self, token_hash: bytes) -> SessionRecord | None:
        row = self._conn.execute(
            select(t.sessions).where(t.sessions.c.token_hash == token_hash)
        ).first()
        return None if row is None else _session(row)

    def list_for_user(self, user: UserId, *, limit: int) -> Sequence[SessionRecord]:
        rows = self._conn.execute(
            select(t.sessions)
            .where(t.sessions.c.user_id == user.value)
            .order_by(t.sessions.c.last_seen_at.desc(), t.sessions.c.token_hash)
            .limit(limit)
        )
        return [_session(row) for row in rows]

    def touch(self, token_hash: bytes, at: datetime) -> None:
        self._conn.execute(
            update(t.sessions).where(t.sessions.c.token_hash == token_hash).values(last_seen_at=at)
        )

    def delete(self, token_hash: bytes) -> bool:
        result = self._conn.execute(delete(t.sessions).where(t.sessions.c.token_hash == token_hash))
        return result.rowcount > 0

    def delete_for_user(self, user: UserId) -> int:
        result = self._conn.execute(delete(t.sessions).where(t.sessions.c.user_id == user.value))
        return int(result.rowcount)

    def delete_expired(self, now: datetime, *, limit: int) -> int:
        expired = select(t.sessions.c.token_hash).where(t.sessions.c.expires_at <= now).limit(limit)
        result = self._conn.execute(delete(t.sessions).where(t.sessions.c.token_hash.in_(expired)))
        return int(result.rowcount)


class SqlLoginFlowRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, flow: LoginFlow) -> None:
        self._conn.execute(
            insert(t.login_flows).values(
                state_hash=flow.state_hash,
                binding_hash=flow.binding_hash,
                nonce=flow.nonce,
                code_verifier=flow.code_verifier,
                created_at=flow.created_at,
                expires_at=flow.expires_at,
            )
        )

    def consume(self, state_hash: bytes, now: datetime) -> LoginFlow | None:
        # DELETE … RETURNING es atómico: dos callbacks con el mismo `state` no pueden ganar
        # los dos. El flujo caducado también se borra, pero no se devuelve.
        row = self._conn.execute(
            delete(t.login_flows)
            .where(t.login_flows.c.state_hash == state_hash)
            .returning(t.login_flows)
        ).first()
        if row is None or row.expires_at <= now:
            return None
        return LoginFlow(
            state_hash=bytes(row.state_hash),
            binding_hash=bytes(row.binding_hash),
            nonce=row.nonce,
            code_verifier=row.code_verifier,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    def count_live(self, now: datetime) -> int:
        count = self._conn.execute(
            select(func.count()).select_from(t.login_flows).where(t.login_flows.c.expires_at > now)
        ).scalar_one()
        return int(count)

    def delete_expired(self, now: datetime, *, limit: int) -> int:
        expired = (
            select(t.login_flows.c.state_hash).where(t.login_flows.c.expires_at <= now).limit(limit)
        )
        result = self._conn.execute(
            delete(t.login_flows).where(t.login_flows.c.state_hash.in_(expired))
        )
        return int(result.rowcount)
