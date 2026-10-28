"""Cliente HTTP de la API de GitHub (CLAUDE.md §9, ADR 0014).

- Biblioteca estándar, como el cliente del runner: sin redirecciones, respuesta acotada,
  TLS verificado con el almacén del sistema y, si se configura, salida por `egress-proxy`
  con `CONNECT` (ADR 0007).
- Las rutas las construye siempre el código a partir de valores ya validados; aun así se
  comprueban aquí (defensa en profundidad) para que ningún dato remoto pueda dirigir una
  petición a otro host o a otra ruta.
- Los errores nunca incluyen texto de la respuesta remota ni credenciales.
"""

import http.client
import json
import re
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final, Protocol
from urllib.parse import urlsplit

from nave.application.errors import VcsRejectedError, VcsUnavailableError
from nave.application.ports.system import Clock
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str

# 2022-11-28 sigue soportada hasta el 10-03-2028. Revisada el 2026-10-12 la lista de cambios
# incompatibles de 2026-03-10 (la última): ninguno afecta a los campos ni endpoints que usa NAVE,
# así que migrar sería cambiar esta constante y los dobles de prueba, pero no hay motivo para
# hacerlo antes de 2027; hacerlo exige volver a revisar esa lista.
GITHUB_API_VERSION: Final = "2022-11-28"
USER_AGENT: Final = "nave-backend"
DEFAULT_BASE_URL: Final = "https://api.github.com"
DEFAULT_TIMEOUT: Final = timedelta(seconds=30)
DEFAULT_MAX_RESPONSE: Final = 4 << 20
MAX_RETRY_AFTER: Final = timedelta(minutes=15)
DEFAULT_RATE_LIMIT_WAIT: Final = timedelta(seconds=60)

_METHODS: Final = frozenset({"GET", "POST", "PATCH", "DELETE"})
_PATH: Final = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,1999}", re.ASCII)
_BEARER: Final = re.compile(r"[A-Za-z0-9._~+/=-]{10,2048}", re.ASCII)
_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9:-]{1,64}", re.ASCII)
_DIGITS: Final = re.compile(r"[0-9]{1,10}", re.ASCII)
_KEPT_HEADERS: Final = frozenset(
    {
        "link",
        "etag",
        "retry-after",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-github-request-id",
    }
)
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_FORBIDDEN: Final = 403
_HTTP_TOO_MANY: Final = 429
_HTTP_SERVER_ERROR: Final = 500
_HTTPS_PORT: Final = 443


class TransportError(Exception):
    """Sin respuesta HTTP utilizable (conexión, TLS, timeout, respuesta demasiado grande)."""


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    """Solo las cabeceras que el cliente usa, con el nombre en minúsculas."""


class GithubTransport(Protocol):
    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> RawResponse: ...


type ConnectionFactory = Callable[[str, int, float, ssl.SSLContext], http.client.HTTPSConnection]


def _default_connection(
    host: str, port: int, timeout: float, context: ssl.SSLContext
) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=context)


class HttpsGithubTransport:
    """HTTPS directo o a través de un proxy HTTP con `CONNECT` (el TLS llega hasta GitHub:
    el proxy filtra por host de destino pero no ve el contenido)."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        proxy_url: str | None = None,
        connection_factory: ConnectionFactory = _default_connection,
    ) -> None:
        host, port = _https_endpoint(base_url)
        self._host, self._port = host, port
        self._proxy = _proxy_endpoint(proxy_url) if proxy_url else None
        self._connect = connection_factory
        self._context = ssl.create_default_context()

    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> RawResponse:
        seconds = timeout.total_seconds()
        if self._proxy is None:
            connection = self._connect(self._host, self._port, seconds, self._context)
        else:
            connection = self._connect(self._proxy[0], self._proxy[1], seconds, self._context)
            connection.set_tunnel(self._host, self._port)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(max_response_bytes + 1)
            if len(data) > max_response_bytes:
                raise TransportError("response too large")
            kept = {
                name.lower(): value
                for name, value in response.getheaders()
                if name.lower() in _KEPT_HEADERS
            }
            return RawResponse(response.status, data, kept)
        except (OSError, http.client.HTTPException) as error:
            raise TransportError(type(error).__name__) from error
        finally:
            connection.close()


def _https_endpoint(base_url: str) -> tuple[str, int]:
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidValueError("github_api_url", "must be an https URL with a host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InvalidValueError("github_api_url", "must not carry credentials, query or fragment")
    if parts.path not in {"", "/"}:
        raise InvalidValueError("github_api_url", "must not have a path")
    return parts.hostname, parts.port or _HTTPS_PORT


def _proxy_endpoint(proxy_url: str) -> tuple[str, int]:
    parts = urlsplit(proxy_url)
    if parts.scheme != "http" or not parts.hostname or parts.port is None:
        raise InvalidValueError("egress_proxy_url", "must be an http://host:port URL")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query:
        raise InvalidValueError("egress_proxy_url", "must not carry credentials or a path")
    return parts.hostname, parts.port


@dataclass(frozen=True, slots=True)
class GithubRequest:
    method: str
    path: str
    bearer: str = field(repr=False)
    json_body: Mapping[str, object] | None = None
    expected: frozenset[int] = frozenset({200})
    """Estados que el llamante sabe interpretar (p. ej. 404 = no existe). Cualquier otro
    estado se traduce a un error."""
    timeout: timedelta = DEFAULT_TIMEOUT
    max_response_bytes: int = DEFAULT_MAX_RESPONSE

    def __post_init__(self) -> None:
        if self.method not in _METHODS:
            raise InvalidValueError("method", "is not allowed")
        if (
            not fullmatch_str(_PATH, self.path)
            or ".." in self.path
            or "//" in self.path
            or "%2f" in self.path.lower()
        ):
            raise InvalidValueError("path", "is not a safe API path")
        if not fullmatch_str(_BEARER, self.bearer):
            raise InvalidValueError("bearer", "is not a valid credential")
        if not self.expected:
            raise InvalidValueError("expected", "must not be empty")


@dataclass(frozen=True, slots=True)
class GithubReply:
    status: int
    body: object
    """JSON ya decodificado (dict, list, ...) o `None` si la respuesta no tiene cuerpo."""
    headers: Mapping[str, str]


class GithubApiClient:
    def __init__(self, *, transport: GithubTransport, clock: Clock) -> None:
        self._transport = transport
        self._clock = clock

    def send(self, request: GithubRequest) -> GithubReply:
        payload = (
            json.dumps(request.json_body, separators=(",", ":")).encode("utf-8")
            if request.json_body is not None
            else None
        )
        headers = {
            "Authorization": f"Bearer {request.bearer}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            raw = self._transport.send(
                method=request.method,
                path=request.path,
                headers=headers,
                body=payload,
                timeout=request.timeout,
                max_response_bytes=request.max_response_bytes,
            )
        except TransportError as error:
            raise VcsUnavailableError(f"github unreachable: {error}") from error
        if raw.status not in request.expected:
            raise self._map_error(raw)
        return GithubReply(raw.status, _decode_body(raw), raw.headers)

    def _map_error(self, raw: RawResponse) -> Exception:
        request_id = raw.headers.get("x-github-request-id")
        safe_id = request_id if request_id and fullmatch_str(_REQUEST_ID, request_id) else None
        if raw.status == _HTTP_TOO_MANY or (
            raw.status == _HTTP_FORBIDDEN and self._is_rate_limited(raw)
        ):
            return VcsUnavailableError("github rate limit", retry_after=self._wait_for(raw))
        if raw.status >= _HTTP_SERVER_ERROR:
            return VcsUnavailableError(f"github status {raw.status}")
        return VcsRejectedError(
            f"github status {raw.status}", status=raw.status, request_id=safe_id
        )

    def _is_rate_limited(self, raw: RawResponse) -> bool:
        if "retry-after" in raw.headers or raw.headers.get("x-ratelimit-remaining") == "0":
            return True
        # El límite secundario no siempre trae cabeceras. El texto remoto solo sirve para
        # clasificar: no se devuelve ni se registra.
        document = _decode_body(raw, strict=False)
        message = document.get("message") if isinstance(document, dict) else None
        return isinstance(message, str) and "rate limit" in message.lower()

    def _wait_for(self, raw: RawResponse) -> timedelta:
        retry_after = raw.headers.get("retry-after")
        if retry_after is not None and fullmatch_str(_DIGITS, retry_after):
            return min(timedelta(seconds=int(retry_after)), MAX_RETRY_AFTER)
        reset = raw.headers.get("x-ratelimit-reset")
        if reset is not None and fullmatch_str(_DIGITS, reset):
            wait = timedelta(seconds=int(reset) - self._clock.now().timestamp())
            if wait > timedelta(0):
                return min(wait, MAX_RETRY_AFTER)
        return DEFAULT_RATE_LIMIT_WAIT


def _decode_body(raw: RawResponse, *, strict: bool = True) -> object:
    if not raw.body:
        return None
    try:
        return json.loads(raw.body)
    except (ValueError, RecursionError) as error:
        if strict:
            raise VcsRejectedError("invalid github response", status=raw.status) from error
        return None
