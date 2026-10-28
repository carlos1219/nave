"""Errores de la capa de aplicación. Los adaptadores de infraestructura los lanzan
para que los casos de uso puedan distinguir fallos transitorios de permanentes sin
conocer la tecnología concreta."""

from datetime import timedelta
from typing import ClassVar

from nave.domain.value_objects.status_reason import ReasonCode


class ApplicationError(Exception):
    code: ClassVar[str] = "application_error"


class NotFoundError(ApplicationError):
    """El recurso no existe o el actor no pertenece a su organización.

    Se usa la misma respuesta en ambos casos para no revelar la existencia de
    recursos de otras organizaciones (AM-106).
    """

    code = "not_found"

    def __init__(self, resource: str) -> None:
        super().__init__(f"{resource} not found")
        self.resource = resource


class AuthenticationError(ApplicationError):
    """Sin sesión válida o login fallido. Nunca lleva el motivo: distinguirlo ayudaría a un
    atacante (sesión caducada, token inexistente, `state` robado, ID token inválido)."""

    code = "unauthenticated"


class AccessDeniedError(ApplicationError):
    code = "access_denied"


class ConcurrentModificationError(ApplicationError):
    code = "concurrent_modification"


class RepositoryNotScannableError(ApplicationError):
    code = "repository_not_scannable"


class IdempotencyConflictError(ApplicationError):
    """La clave de idempotencia ya designa otra petición distinta (otro repositorio o commit)."""

    code = "idempotency_conflict"


class LastAdminError(ApplicationError):
    """Quitar o degradar al último administrador dejaría la organización sin gestión."""

    code = "last_admin"


class TransientError(ApplicationError):
    """Fallo temporal: la tarea puede reintentarse con backoff (CLAUDE.md §10).

    `retry_after` es la espera que exige el servicio remoto (`Retry-After` de GitHub,
    §9); el reintento nunca ocurre antes.
    """

    code = "transient"

    def __init__(self, *args: object, retry_after: timedelta | None = None) -> None:
        super().__init__(*args)
        self.retry_after = retry_after if retry_after and retry_after > timedelta(0) else None


class FetchUnavailableError(TransientError):
    code = "fetch_unavailable"


class ScannerUnavailableError(TransientError):
    code = "scanner_unavailable"


class PersistenceUnavailableError(TransientError):
    """La base de datos no responde, canceló la consulta o abortó por conflicto de
    serialización. Reintentar la operación completa es seguro."""

    code = "persistence_unavailable"


class QueueUnavailableError(TransientError):
    """El broker no acepta el trabajo (caído, saturado o con la cola llena). El estado
    ya está confirmado en PostgreSQL: el barrido de mantenimiento reencola (AM-308)."""

    code = "queue_unavailable"


class IdentityProviderUnavailableError(TransientError):
    """El IdP OIDC no responde o devuelve algo inutilizable. Los usuarios con sesión no
    se ven afectados; solo fallan los logins nuevos."""

    code = "identity_provider_unavailable"


class VcsUnavailableError(TransientError):
    """GitHub no responde, limita las peticiones o falla (5xx). Reintentar con backoff,
    respetando `retry_after` si GitHub lo exige (§9)."""

    code = "vcs_unavailable"


class VcsRejectedError(ApplicationError):
    """GitHub rechazó la petición de forma definitiva (permisos, recurso inexistente,
    petición inválida). Reintentarla igual no lo arregla. Nunca lleva texto de la respuesta
    remota: solo el estado HTTP y el identificador de petición de GitHub para soporte."""

    code = "vcs_rejected"

    def __init__(self, reason: str, *, status: int | None = None, request_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.request_id = request_id


class PurgeRefusedError(ApplicationError):
    """La base de datos se negó a purgar la organización (no está desinstalada o no ha
    pasado el plazo). Defensa en profundidad: la aplicación ya filtra por plazo."""

    code = "purge_refused"


class PermanentScanError(ApplicationError):
    """Fallo definitivo del escaneo. `detail` puede contener texto no confiable
    (stderr del escáner): solo se usa a través de `StatusReason.from_untrusted`."""

    code = "scan_failed"
    reason_code: ClassVar[ReasonCode] = ReasonCode.INTERNAL_ERROR

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.reason_code.value)
        self.detail = detail


class RepositoryFetchError(PermanentScanError):
    reason_code = ReasonCode.FETCH_FAILED


class RepositoryTooLargeError(PermanentScanError):
    reason_code = ReasonCode.REPOSITORY_TOO_LARGE


class ScannerExecutionError(PermanentScanError):
    reason_code = ReasonCode.SCANNER_FAILED


class ScannerOutputInvalidError(PermanentScanError):
    reason_code = ReasonCode.SCANNER_OUTPUT_INVALID


class ScanTimeoutError(ApplicationError):
    code = "scan_timeout"
