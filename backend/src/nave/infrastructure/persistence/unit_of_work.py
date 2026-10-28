from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, RootTransaction
from sqlalchemy.exc import DBAPIError

from nave.infrastructure.persistence.audit import SqlAuditLog
from nave.infrastructure.persistence.auth_repositories import (
    SqlLoginFlowRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from nave.infrastructure.persistence.errors import translate_database_error
from nave.infrastructure.persistence.insights import SqlInsightsQueries
from nave.infrastructure.persistence.outbox import SqlOutbox
from nave.infrastructure.persistence.platform import (
    SqlHealthStore,
    SqlPlatformAdminRepository,
    SqlPlatformQueries,
)
from nave.infrastructure.persistence.repositories import (
    SqlFindingRepository,
    SqlMembershipRepository,
    SqlPolicyRepository,
    SqlScanLogRepository,
    SqlScanRepository,
    SqlTenantAdmin,
    SqlTenantCatalog,
    SqlWebhookDeliveryRepository,
)


class SqlUnitOfWork:
    """Una transacción de PostgreSQL. Sin `commit()`, al salir se deshace todo.

    Es también el punto único donde los errores del driver se traducen a errores de
    aplicación: un fallo dentro del `with` sale de aquí como `ConcurrentModificationError`,
    `NotFoundError` o `PersistenceUnavailableError`, nunca como excepción de SQLAlchemy.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._conn: Connection | None = None
        self._tx: RootTransaction | None = None

    def __enter__(self) -> Self:
        if self._conn is not None:
            raise RuntimeError("a unit of work cannot be entered twice")
        try:
            self._conn = self._engine.connect()
            self._tx = self._conn.begin()
        except DBAPIError as error:
            self._close()
            translated = translate_database_error(error)
            if translated is None:
                raise
            raise translated from error
        conn = self._conn
        self.scans = SqlScanRepository(conn)
        self.findings = SqlFindingRepository(conn)
        self.memberships = SqlMembershipRepository(conn)
        self.users = SqlUserRepository(conn)
        self.sessions = SqlSessionRepository(conn)
        self.login_flows = SqlLoginFlowRepository(conn)
        self.tenants = SqlTenantCatalog(conn)
        self.tenant_admin = SqlTenantAdmin(conn)
        self.scan_logs = SqlScanLogRepository(conn)
        self.webhook_deliveries = SqlWebhookDeliveryRepository(conn)
        self.policies = SqlPolicyRepository(conn)
        self.audit = SqlAuditLog(conn)
        self.insights = SqlInsightsQueries(conn)
        self.platform_admins = SqlPlatformAdminRepository(conn)
        self.platform = SqlPlatformQueries(conn)
        self.health = SqlHealthStore(conn)
        self.events = SqlOutbox(conn)
        return self

    def commit(self) -> None:
        if self._tx is None or not self._tx.is_active:
            raise RuntimeError("no active transaction to commit")
        try:
            self._tx.commit()
        except DBAPIError as error:
            translated = translate_database_error(error)
            if translated is None:
                raise
            raise translated from error

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._close()
        if isinstance(exc, DBAPIError):
            translated = translate_database_error(exc)
            if translated is not None:
                raise translated from exc

    def _close(self) -> None:
        try:
            if self._tx is not None and self._tx.is_active:
                self._tx.rollback()
        except DBAPIError:
            # La conexión está rota: al cerrarla se invalida y no vuelve al pool. Se
            # descarta este error para que se propague el original, que es el relevante.
            pass
        finally:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._tx = None


class SqlUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._engine)
