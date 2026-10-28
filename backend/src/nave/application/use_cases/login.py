"""Casos de uso de autenticación (ADR 0016): iniciar login, completarlo, autenticar una
petición y cerrar sesión. Los fallos de autenticación no distinguen la causa."""

import hmac
from datetime import timedelta
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.dto.auth import (
    LoginFlow,
    LoginRedirect,
    NewSession,
    Principal,
    SessionPolicy,
    SessionRecord,
)
from nave.application.errors import AuthenticationError, TransientError
from nave.application.ports.auth import OidcProviderPort, SecretSource
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock, IdGenerator
from nave.application.services.session_tokens import hash_token, looks_like_token, pkce_challenge

FLOW_TTL: Final = timedelta(minutes=10)
MAX_LIVE_FLOWS: Final = 10_000
CLEANUP_BATCH: Final = 100
MAX_CODE_LENGTH: Final = 2048


class BeginLogin:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        oidc: OidcProviderPort,
        clock: Clock,
        secrets: SecretSource,
    ) -> None:
        self._uow_factory = uow_factory
        self._oidc = oidc
        self._clock = clock
        self._secrets = secrets

    def execute(self) -> LoginRedirect:
        state = self._secrets.new_token()
        nonce = self._secrets.new_token()
        verifier = self._secrets.new_token()
        binding = self._secrets.new_token()
        url = self._oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=pkce_challenge(verifier)
        )
        now = self._clock.now()
        with self._uow_factory() as uow:
            # Limpieza acotada de lo caducado: sin tarea aparte y con coste constante.
            uow.login_flows.delete_expired(now, limit=CLEANUP_BATCH)
            uow.sessions.delete_expired(now, limit=CLEANUP_BATCH)
            if uow.login_flows.count_live(now) >= MAX_LIVE_FLOWS:
                # Un anónimo no puede llenar la tabla: se rechaza sin guardar nada.
                raise TransientError("too many login attempts in progress")
            uow.login_flows.add(
                LoginFlow(
                    state_hash=hash_token(state),
                    binding_hash=hash_token(binding),
                    nonce=nonce,
                    code_verifier=verifier,
                    created_at=now,
                    expires_at=now + FLOW_TTL,
                )
            )
            uow.commit()
        return LoginRedirect(url=url, binding=binding, max_age=FLOW_TTL)


class CompleteLogin:
    def __init__(  # noqa: PLR0913 - composition root del caso de uso
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        oidc: OidcProviderPort,
        clock: Clock,
        ids: IdGenerator,
        secrets: SecretSource,
        policy: SessionPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._oidc = oidc
        self._clock = clock
        self._ids = ids
        self._secrets = secrets
        self._policy = policy

    def execute(self, *, state: object, code: object, binding: object) -> NewSession:
        if not (looks_like_token(state) and looks_like_token(binding)):
            raise AuthenticationError
        if (
            not isinstance(code, str)
            or not 1 <= len(code) <= MAX_CODE_LENGTH
            or not code.isprintable()
        ):
            raise AuthenticationError
        assert isinstance(state, str)  # noqa: S101 - estrechamiento para mypy tras looks_like_token
        assert isinstance(binding, str)  # noqa: S101
        flow = self._consume_flow(state)
        # El enlace se compara después de consumir: un `state` robado queda inutilizado.
        if not hmac.compare_digest(flow.binding_hash, hash_token(binding)):
            raise AuthenticationError
        identity = self._oidc.complete(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce
        )
        now = self._clock.now()
        token = self._secrets.new_token()
        expires = now + self._policy.absolute
        with self._uow_factory() as uow:
            user_id = uow.users.record_login(identity, new_id=self._ids.new_uuid(), at=now)
            uow.sessions.add(
                SessionRecord(
                    token_hash=hash_token(token),
                    user_id=user_id,
                    csrf_token=self._secrets.new_token(),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=expires,
                )
            )
            uow.audit.append(
                AuditEntry(
                    occurred_at=now,
                    action="session.created",
                    actor=user_id,
                    organization_id=None,
                    target_type="user",
                    target_id=str(user_id),
                )
            )
            uow.commit()
        return NewSession(token=token, expires_at=expires, max_age=self._policy.absolute)

    def _consume_flow(self, state: str) -> LoginFlow:
        now = self._clock.now()
        with self._uow_factory() as uow:
            flow = uow.login_flows.consume(hash_token(state), now)
            # Se confirma siempre: consumir es la operación que invalida el `state`.
            uow.commit()
        if flow is None:
            raise AuthenticationError
        return flow


class Authenticate:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock, policy: SessionPolicy):
        self._uow_factory = uow_factory
        self._clock = clock
        self._policy = policy

    def execute(self, token: object) -> Principal:
        if not looks_like_token(token):
            raise AuthenticationError
        assert isinstance(token, str)  # noqa: S101
        digest = hash_token(token)
        now = self._clock.now()
        with self._uow_factory() as uow:
            record = uow.sessions.get(digest)
            if record is None:
                raise AuthenticationError
            if record.expires_at <= now or record.last_seen_at + self._policy.idle <= now:
                uow.sessions.delete(digest)
                uow.commit()
                raise AuthenticationError
            if now - record.last_seen_at >= self._policy.touch_interval:
                uow.sessions.touch(digest, now)
                uow.commit()
            user = uow.users.get(record.user_id)
        return Principal(
            user_id=record.user_id,
            csrf_token=record.csrf_token,
            display_name=user.display_name,
            expires_at=min(record.expires_at, now + self._policy.idle),
        )


class Logout:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, principal: Principal, token: str) -> None:
        with self._uow_factory() as uow:
            if uow.sessions.delete(hash_token(token)):
                uow.audit.append(
                    AuditEntry(
                        occurred_at=self._clock.now(),
                        action="session.ended",
                        actor=principal.user_id,
                        organization_id=None,
                        target_type="user",
                        target_id=str(principal.user_id),
                    )
                )
            uow.commit()
