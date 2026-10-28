"""Certificado autofirmado SOLO para desarrollo: deploy/secrets/tls_cert y tls_key.

    python deploy/scripts/dev_certs.py

Válido para `localhost` y `keycloak.localhost`. Es su propia autoridad: la API lo usa como
raíz de confianza (SSL_CERT_FILE) para hablar con Keycloak. Nunca para producción.
Necesita el paquete `cryptography` (el entorno del backend lo trae).
"""

import datetime
import ipaddress
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

DIRECTORY = Path(__file__).resolve().parents[1] / "secrets"


def main() -> int:
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    cert_path, key_path = DIRECTORY / "tls_cert", DIRECTORY / "tls_key"
    if cert_path.exists() or key_path.exists():
        print("ya existen tls_cert/tls_key; no se tocan", file=sys.stderr)
        return 1
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nave-dev")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("keycloak.localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    print("creados tls_cert y tls_key (90 días, solo desarrollo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
