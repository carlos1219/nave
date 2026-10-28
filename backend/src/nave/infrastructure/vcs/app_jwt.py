"""Identidad de la GitHub App: clave privada y JWT de aplicación (ADR 0014).

Solo se *firma*: la verificación de JWT la hace GitHub, de modo que NAVE no incluye
código de validación de tokens (la fuente clásica de fallos). La clave privada es el
activo A-01: vive únicamente en el `worker`, no se imprime, no se serializa y no sale
de este módulo salvo como firma.
"""

import base64
import json
import re
from typing import Final, NoReturn, Self

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nave.application.ports.system import Clock
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str

MIN_KEY_BITS: Final = 2048
# GitHub admite hasta 10 min; 9 deja margen ante desfases de reloj hacia el futuro.
JWT_LIFETIME_SECONDS: Final = 9 * 60
# `iat` en el pasado: GitHub recomienda 60 s para tolerar relojes adelantados.
JWT_BACKDATE_SECONDS: Final = 60
_ISSUER: Final = re.compile(r"[A-Za-z0-9._-]{1,64}", re.ASCII)
_MAX_PEM_BYTES: Final = 16_384
_HEADER: Final = b'{"alg":"RS256","typ":"JWT"}'


class AppPrivateKey:
    """Clave RSA de la App, ya validada. Sin `repr` útil ni serialización posible."""

    __slots__ = ("_key",)

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        if key.key_size < MIN_KEY_BITS:
            raise InvalidValueError(
                "github_app_private_key", f"must be at least {MIN_KEY_BITS} bits"
            )
        self._key = key

    @classmethod
    def from_pem(cls, pem: str) -> Self:
        """Los mensajes de error nunca incluyen el contenido recibido."""
        try:
            raw = pem.encode("ascii")
            if len(raw) > _MAX_PEM_BYTES:
                raise ValueError  # noqa: TRY301 - un único camino de rechazo
            loaded = serialization.load_pem_private_key(raw, password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm) as error:
            raise InvalidValueError(
                "github_app_private_key", "is not a valid unencrypted PEM private key"
            ) from error
        if not isinstance(loaded, rsa.RSAPrivateKey):
            raise InvalidValueError("github_app_private_key", "must be an RSA key")
        return cls(loaded)

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message, padding.PKCS1v15(), hashes.SHA256())

    def __repr__(self) -> str:
        return "AppPrivateKey(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("AppPrivateKey cannot be pickled")


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class AppJwtSigner:
    """JWT RS256 de vida corta con el que NAVE se autentica *como App* (no como instalación)."""

    def __init__(self, *, issuer: str, key: AppPrivateKey, clock: Clock) -> None:
        if not fullmatch_str(_ISSUER, issuer):
            raise InvalidValueError("github_app_id", "is not a valid App ID or client ID")
        self._issuer = issuer
        self._key = key
        self._clock = clock

    def token(self) -> str:
        now = int(self._clock.now().timestamp())
        claims = {
            "iat": now - JWT_BACKDATE_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            "iss": self._issuer,
        }
        signing_input = _b64url(_HEADER) + b"." + _b64url(json.dumps(claims).encode("ascii"))
        return (signing_input + b"." + _b64url(self._key.sign(signing_input))).decode("ascii")

    def __repr__(self) -> str:
        return f"AppJwtSigner(issuer={self._issuer!r})"
