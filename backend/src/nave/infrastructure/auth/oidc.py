"""Cliente OIDC genérico: Authorization Code + PKCE, cliente confidencial (ADR 0004, 0016).

- Biblioteca estándar para HTTP: sin redirecciones, respuesta acotada, TLS verificado con el
  almacén del sistema. Todas las URL deben ser del **mismo origen** que el emisor configurado,
  así que un documento de descubrimiento comprometido no puede dirigir peticiones (con el
  secreto del cliente) a otro host.
- `joserfc` valida firma y claims del ID token; solo algoritmos asimétricos. El `none` y los
  HMAC quedan fuera por construcción (un atacante que conozca la clave pública no puede
  firmar con ella como si fuera un secreto).
- Los errores no incluyen texto de la respuesta del IdP ni el código recibido.
"""

import base64
import http.client
import json
import logging
import ssl
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Protocol
from urllib.parse import quote, urlencode, urlsplit

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from nave.application.dto.auth import OidcIdentity
from nave.application.errors import AuthenticationError, IdentityProviderUnavailableError
from nave.application.ports.system import Clock
from nave.domain.errors import DomainError
from nave.domain.value_objects.text import sanitize_untrusted

_LOG: Final = logging.getLogger("nave.oidc")
ALLOWED_ALGORITHMS: Final = ("RS256", "PS256", "ES256", "EdDSA")
SCOPE: Final = "openid profile email"
METADATA_TTL: Final = timedelta(hours=1)
METADATA_STALE_LIMIT: Final = timedelta(hours=24)
JWKS_TTL: Final = timedelta(minutes=10)
JWKS_MIN_REFRESH: Final = timedelta(seconds=60)
CLOCK_LEEWAY_SECONDS: Final = 60
TIMEOUT: Final = timedelta(seconds=10)
MAX_METADATA_BYTES: Final = 256 << 10
MAX_JWKS_BYTES: Final = 1 << 20
MAX_TOKEN_RESPONSE_BYTES: Final = 128 << 10
_ASYMMETRIC_TYPES: Final = frozenset({"RSA", "EC", "OKP"})
_LOOPBACK: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_HTTP_OK: Final = 200
_HTTP_BAD_REQUEST: Final = 400
MAX_ENDPOINT_URL: Final = 2048
MAX_ID_TOKEN: Final = 16384
MAX_KEYS: Final = 64


class OidcTransportError(Exception):
    """Sin respuesta utilizable (conexión, TLS, tiempo, tamaño)."""


@dataclass(frozen=True, slots=True)
class OidcResponse:
    status: int
    body: bytes


class OidcTransport(Protocol):
    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_bytes: int,
    ) -> OidcResponse: ...


@dataclass(frozen=True, slots=True)
class OidcClientConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    allow_insecure_loopback: bool = False
    proxy_url: str | None = None
    """Proxy HTTP con `CONNECT` por el que sale el tráfico hacia el emisor (http://host:puerto)."""

    def __post_init__(self) -> None:
        scheme, _host, _port = origin_of(
            self.issuer, allow_insecure_loopback=self.allow_insecure_loopback
        )
        if self.proxy_url is not None:
            proxy_endpoint(self.proxy_url)
            if scheme != "https":
                raise ValueError("a proxy needs an https issuer")


def proxy_endpoint(proxy_url: str) -> tuple[str, int]:
    """(host, puerto) de un proxy `http://host:puerto` sin credenciales, ruta ni consulta."""
    try:
        parts = urlsplit(proxy_url)
        host, port = parts.hostname, parts.port
    except ValueError as error:
        raise ValueError("proxy URL is not valid") from error
    if parts.scheme != "http" or not host or port is None:
        raise ValueError("proxy URL must be http://host:port")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query:
        raise ValueError("proxy URL must not carry credentials, path or query")
    if parts.fragment:
        raise ValueError("proxy URL must not carry a fragment")
    return host, port


def origin_of(url: str, *, allow_insecure_loopback: bool) -> tuple[str, str, int]:
    """(esquema, host, puerto) de una URL admisible: HTTPS, o HTTP solo a bucle local si el
    perfil de desarrollo lo permite. Sin credenciales, sin fragmento."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as error:
        raise ValueError("not a valid URL") from error
    if not host or parts.username or parts.password or parts.fragment:
        raise ValueError("URL must have a host and no credentials or fragment")
    secure = parts.scheme == "https"
    if not secure and not (
        allow_insecure_loopback and parts.scheme == "http" and host in _LOOPBACK
    ):
        raise ValueError("URL must use https")
    return parts.scheme, host, port or (443 if secure else 80)


ConnectionFactory = Callable[[str, str, int, float], http.client.HTTPConnection]


def _connect(scheme: str, host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)


class HttpOidcTransport:
    def __init__(
        self,
        *,
        origin_url: str,
        allow_insecure_loopback: bool = False,
        proxy_url: str | None = None,
        connection_factory: ConnectionFactory = _connect,
    ) -> None:
        self._origin = origin_of(origin_url, allow_insecure_loopback=allow_insecure_loopback)
        self._proxy = proxy_endpoint(proxy_url) if proxy_url else None
        if self._proxy is not None and self._origin[0] != "https":
            raise ValueError("a proxy needs an https issuer")
        self._allow_insecure = allow_insecure_loopback
        self._connect = connection_factory

    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_bytes: int,
    ) -> OidcResponse:
        try:
            origin = origin_of(url, allow_insecure_loopback=self._allow_insecure)
        except ValueError as error:
            raise OidcTransportError("invalid URL") from error
        if origin != self._origin:
            raise OidcTransportError("URL is outside the issuer origin")
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        seconds = timeout.total_seconds()
        if self._proxy is None:
            connection = self._connect(origin[0], origin[1], origin[2], seconds)
        else:
            # TCP hasta el proxy y TLS de extremo a extremo con el emisor: el proxy filtra
            # por host de destino pero no ve ni puede alterar el contenido.
            connection = self._connect(origin[0], self._proxy[0], self._proxy[1], seconds)
            connection.set_tunnel(origin[1], origin[2])
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(max_bytes + 1)
            status = response.status
        except (OSError, http.client.HTTPException) as error:
            raise OidcTransportError(type(error).__name__) from error
        finally:
            connection.close()
        if len(data) > max_bytes:
            raise OidcTransportError("response too large")
        return OidcResponse(status=status, body=data)


@dataclass(frozen=True, slots=True)
class _Metadata:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    fetched_at: datetime


def _reject_constant(_: str) -> object:
    raise ValueError("non-finite number")


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError("not JSON") from error
    if not isinstance(value, dict):
        raise ValueError("not an object")  # noqa: TRY004
    return value


class OidcClient:
    def __init__(self, config: OidcClientConfig, transport: OidcTransport, clock: Clock) -> None:
        self._config = config
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._metadata_cache: _Metadata | None = None
        self._keys: KeySet | None = None
        self._keys_fetched: datetime | None = None

    # -- puerto -----------------------------------------------------------------------

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        meta = self._metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._config.client_id,
                "redirect_uri": self._config.redirect_uri,
                "scope": SCOPE,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{meta.authorization_endpoint}?{query}"

    def complete(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        meta = self._metadata()
        id_token = self._exchange(meta, code, code_verifier)
        claims = self._validated_claims(meta, id_token, nonce)
        return self._identity(claims)

    # -- descubrimiento ---------------------------------------------------------------

    def _metadata(self) -> _Metadata:
        now = self._clock.now()
        with self._lock:
            cached = self._metadata_cache
            if cached is not None and now - cached.fetched_at < METADATA_TTL:
                return cached
            try:
                fresh = self._fetch_metadata(now)
            except IdentityProviderUnavailableError:
                if cached is not None and now - cached.fetched_at < METADATA_STALE_LIMIT:
                    return cached
                raise
            self._metadata_cache = fresh
            return fresh

    def _fetch_metadata(self, now: datetime) -> _Metadata:
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        document = self._get_json(url, MAX_METADATA_BYTES)
        if document.get("issuer") != self._config.issuer:
            _LOG.error("oidc issuer mismatch in discovery document")
            raise IdentityProviderUnavailableError("discovery document does not match the issuer")
        methods = document.get("code_challenge_methods_supported")
        if methods is not None and (not isinstance(methods, list) or "S256" not in methods):
            raise IdentityProviderUnavailableError("the identity provider does not support PKCE")
        endpoints: dict[str, str] = {}
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            endpoints[name] = self._same_origin(document.get(name))
        if urlsplit(endpoints["authorization_endpoint"]).query:
            raise IdentityProviderUnavailableError("unexpected authorization endpoint")
        return _Metadata(fetched_at=now, **endpoints)

    def _same_origin(self, value: object) -> str:
        try:
            if not isinstance(value, str) or len(value) > MAX_ENDPOINT_URL:
                raise ValueError("endpoint")  # noqa: TRY301
            expected = origin_of(
                self._config.issuer, allow_insecure_loopback=self._config.allow_insecure_loopback
            )
            actual = origin_of(value, allow_insecure_loopback=self._config.allow_insecure_loopback)
            if actual != expected:
                raise ValueError("origin")  # noqa: TRY301
        except ValueError as error:
            _LOG.error("oidc endpoint outside the issuer origin")
            raise IdentityProviderUnavailableError("unusable discovery document") from error
        assert isinstance(value, str)  # noqa: S101
        return value

    # -- token ------------------------------------------------------------------------

    def _exchange(self, meta: _Metadata, code: str, verifier: str) -> str:
        credentials = (
            f"{quote(self._config.client_id, safe='')}:{quote(self._config.client_secret, safe='')}"
        )
        headers = {
            "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "code_verifier": verifier,
            }
        ).encode("ascii")
        response = self._send("POST", meta.token_endpoint, headers, body, MAX_TOKEN_RESPONSE_BYTES)
        if response.status == _HTTP_BAD_REQUEST:
            raise AuthenticationError  # código inválido, caducado o ya usado
        if response.status != _HTTP_OK:
            # 401: secreto de cliente mal configurado, problema nuestro y no del usuario.
            _LOG.error("oidc token endpoint returned %s", response.status)
            raise IdentityProviderUnavailableError("token endpoint failure")
        try:
            payload = _json_object(response.body)
        except ValueError as error:
            raise IdentityProviderUnavailableError("unusable token response") from error
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not 0 < len(id_token) <= MAX_ID_TOKEN:
            raise AuthenticationError
        return id_token

    def _validated_claims(self, meta: _Metadata, id_token: str, nonce: str) -> dict[str, Any]:
        for attempt in (1, 2):
            keys = self._key_set(meta, force=attempt == 2)  # noqa: PLR2004
            try:
                token = jwt.decode(id_token, keys, algorithms=list(ALLOWED_ALGORITHMS))
            except (JoseError, ValueError, TypeError):
                # Puede ser una clave rotada: un único refresco, con intervalo mínimo.
                continue
            return self._check_claims(token.claims, nonce)
        raise AuthenticationError

    def _check_claims(self, claims: dict[str, Any], nonce: str) -> dict[str, Any]:
        registry = jwt.JWTClaimsRegistry(
            now=int(self._clock.now().timestamp()),
            leeway=CLOCK_LEEWAY_SECONDS,
            iss={"essential": True, "value": self._config.issuer},
            aud={"essential": True, "value": self._config.client_id},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        try:
            registry.validate(claims)
        except (JoseError, ValueError, TypeError) as error:
            raise AuthenticationError from error
        audience = claims.get("aud")
        if (
            isinstance(audience, list)
            and len(audience) > 1
            and claims.get("azp") != self._config.client_id
        ):
            raise AuthenticationError
        if not isinstance(claims.get("sub"), str):
            raise AuthenticationError
        return claims

    def _key_set(self, meta: _Metadata, *, force: bool) -> KeySet:
        now = self._clock.now()
        with self._lock:
            fetched = self._keys_fetched
            fresh = fetched is not None and now - fetched < JWKS_TTL
            allowed = fetched is None or now - fetched >= JWKS_MIN_REFRESH
            if self._keys is not None and ((fresh and not force) or not allowed):
                return self._keys
            document = self._get_json(meta.jwks_uri, MAX_JWKS_BYTES)
            raw_keys = document.get("keys")
            if not isinstance(raw_keys, list) or not 0 < len(raw_keys) <= MAX_KEYS:
                raise IdentityProviderUnavailableError("unusable key set")
            usable = [
                key
                for key in raw_keys
                if isinstance(key, dict)
                and key.get("use", "sig") == "sig"
                and key.get("kty") in _ASYMMETRIC_TYPES
                and "d" not in key
            ]
            if not usable:
                raise IdentityProviderUnavailableError("no usable signing key")
            try:
                self._keys = KeySet.import_key_set({"keys": usable})
            except (JoseError, ValueError, TypeError, KeyError) as error:
                raise IdentityProviderUnavailableError("unusable key set") from error
            self._keys_fetched = now
            return self._keys

    # -- identidad --------------------------------------------------------------------

    @staticmethod
    def _identity(claims: dict[str, Any]) -> OidcIdentity:
        issuer, subject = claims["iss"], claims["sub"]
        name = claims.get("name") or claims.get("preferred_username")
        display = sanitize_untrusted(name, 255) if isinstance(name, str) and name.strip() else None
        email = claims.get("email")
        verified = isinstance(email, str) and claims.get("email_verified") is True
        try:
            return OidcIdentity(
                issuer=issuer,
                subject=subject,
                display_name=display,
                email=email if verified else None,
            )
        except DomainError:
            # Un perfil extraño no debe impedir el login; la identidad (iss, sub) sí es obligatoria.
            try:
                return OidcIdentity(issuer=issuer, subject=subject)
            except DomainError as error:
                raise AuthenticationError from error

    # -- HTTP -------------------------------------------------------------------------

    def _get_json(self, url: str, limit: int) -> dict[str, Any]:
        response = self._send("GET", url, {"Accept": "application/json"}, None, limit)
        if response.status != _HTTP_OK:
            raise IdentityProviderUnavailableError("identity provider error")
        try:
            return _json_object(response.body)
        except ValueError as error:
            raise IdentityProviderUnavailableError("unusable response") from error

    def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        limit: int,
    ) -> OidcResponse:
        try:
            return self._transport.send(
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=TIMEOUT,
                max_bytes=limit,
            )
        except OidcTransportError as error:
            raise IdentityProviderUnavailableError("identity provider unreachable") from error
