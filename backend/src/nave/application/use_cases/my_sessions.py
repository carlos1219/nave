"""Sesiones abiertas del propio usuario: verlas y cerrarlas a distancia (ADR 0029).

Solo se opera sobre las sesiones de `principal.user_id`: el identificador que llega del
cliente se busca entre ellas, nunca en la tabla entera. La sesión actual no se cierra por
aquí (para eso está el logout, que además borra la cookie)."""

import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.dto.auth import Principal, SessionPolicy, SessionRecord, SessionView
from nave.application.errors import NotFoundError
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.session_tokens import hash_token, public_session_id
from nave.domain.errors import InvalidValueError

MAX_SESSIONS: Final = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionCommand:
    principal: Principal
    token: str
    """Cookie de la petición: identifica cuál es la sesión actual."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RevokeSessionCommand(SessionCommand):
    session_id: str


class _MySessions:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock, policy: SessionPolicy):
        self._uow_factory = uow_factory
        self._clock = clock
        self._policy = policy

    def _live(self, uow: UnitOfWork, command: SessionCommand) -> list[SessionRecord]:
        now = self._clock.now()
        records = uow.sessions.list_for_user(command.principal.user_id, limit=MAX_SESSIONS)
        return [
            r for r in records if r.expires_at > now and r.last_seen_at + self._policy.idle > now
        ]

    def _revoke(self, uow: UnitOfWork, actor: Principal, record: SessionRecord) -> None:
        if uow.sessions.delete(record.token_hash):
            uow.audit.append(
                AuditEntry(
                    occurred_at=self._clock.now(),
                    action="session.revoked",
                    actor=actor.user_id,
                    organization_id=None,
                    target_type="session",
                    target_id=public_session_id(record.token_hash),
                )
            )


class ListMySessions(_MySessions):
    def execute(self, command: SessionCommand) -> Sequence[SessionView]:
        current = hash_token(command.token)
        with self._uow_factory() as uow:
            live = self._live(uow, command)
        return [
            SessionView(
                id=public_session_id(r.token_hash),
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                expires_at=min(r.expires_at, r.last_seen_at + self._policy.idle),
                current=hmac.compare_digest(r.token_hash, current),
            )
            for r in live
        ]


class RevokeSession(_MySessions):
    def execute(self, command: RevokeSessionCommand) -> None:
        current = hash_token(command.token)
        with self._uow_factory() as uow:
            target = next(
                (
                    r
                    for r in self._live(uow, command)
                    if hmac.compare_digest(public_session_id(r.token_hash), command.session_id)
                ),
                None,
            )
            if target is None:
                raise NotFoundError("session")
            if hmac.compare_digest(target.token_hash, current):
                raise InvalidValueError("session_id", "the current session ends with logout")
            self._revoke(uow, command.principal, target)
            uow.commit()


class RevokeOtherSessions(_MySessions):
    def execute(self, command: SessionCommand) -> int:
        current = hash_token(command.token)
        with self._uow_factory() as uow:
            others = [
                r
                for r in self._live(uow, command)
                if not hmac.compare_digest(r.token_hash, current)
            ]
            for record in others:
                self._revoke(uow, command.principal, record)
            uow.commit()
        return len(others)
