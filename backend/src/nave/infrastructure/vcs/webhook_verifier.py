"""Verificación de la firma de los webhooks de GitHub (AM-201).

HMAC-SHA256 sobre el cuerpo **crudo**, comparado en tiempo constante. Se admite un
secreto anterior para rotar sin perder entregas. Toda entrada mal formada devuelve
`False` sin distinguir el motivo."""

import hashlib
import hmac
import re
from collections.abc import Sequence
from typing import Final

_SIGNATURE: Final = re.compile(r"sha256=([0-9a-f]{64})", re.ASCII)
MAX_SECRETS: Final = 2


class WebhookVerifier:
    def __init__(self, secrets: Sequence[bytes]) -> None:
        if not 1 <= len(secrets) <= MAX_SECRETS or any(not s for s in secrets):
            raise ValueError("one or two non-empty webhook secrets are required")
        self._secrets = tuple(secrets)

    def __repr__(self) -> str:
        return "WebhookVerifier(...)"

    def verify(self, body: bytes, signature_header: str | None) -> bool:
        if signature_header is None:
            return False
        match = _SIGNATURE.fullmatch(signature_header)
        if match is None:
            return False
        received = bytes.fromhex(match.group(1))
        # Se comprueban todos los secretos aunque el primero acierte: el tiempo no
        # revela cuál de los dos era el válido.
        results = [
            hmac.compare_digest(hmac.new(secret, body, hashlib.sha256).digest(), received)
            for secret in self._secrets
        ]
        return any(results)
