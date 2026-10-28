"""Derivaciones criptográficas del login y las sesiones (ADR 0016). Funciones puras."""

import base64
import hashlib
import re
from typing import Final

from nave.domain.validation import fullmatch_str

# `secrets.token_urlsafe(32)` produce 43 caracteres; se aceptan formas algo mayores para
# poder subir la entropía sin romper sesiones, y nada más.
_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{43,128}", re.ASCII)


def looks_like_token(value: object) -> bool:
    """Descarta antes de tocar la base de datos lo que no puede ser un token nuestro."""
    return isinstance(value, str) and fullmatch_str(_TOKEN, value)


def hash_token(token: str) -> bytes:
    """SHA-256 del valor de la cookie. Con 256 bits de entropía no hace falta sal ni KDF
    lenta; lo que se guarda no permite reconstruir la cookie."""
    return hashlib.sha256(token.encode("ascii")).digest()


_SESSION_ID_DOMAIN: Final = b"nave-session-id"
SESSION_ID_LENGTH: Final = 32


def public_session_id(token_hash: bytes) -> str:
    """Identificador de una sesión para su dueño (ADR 0029). Es un hash con separación de
    dominio del hash guardado: no permite reconstruir ni el token ni la clave de la fila."""
    return hashlib.sha256(_SESSION_ID_DOMAIN + token_hash).hexdigest()[:SESSION_ID_LENGTH]


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
