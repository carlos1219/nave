"""Contenedor de fetch: clona exactamente un commit con git endurecido (AM-504, AM-506).

Contrato con el runner (ADR 0012): argumentos `--clave=valor` validados, token por la
entrada estándar y código de salida cerrado: 0 ok, 10 demasiado grande, 11 fallo
permanente, 12 fallo transitorio. Nada se imprime: la salida de git es texto no confiable
y podría reflejar el token.
"""

import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

from pathguard import TreeTooLargeError, sanitize_tree

EXIT_OK: Final = 0
EXIT_TOO_LARGE: Final = 10
EXIT_FAILED: Final = 11
EXIT_UNAVAILABLE: Final = 12

WORKSPACE: Final = Path("/workspace")
TARGET: Final = "repo"
_OWNER: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", re.ASCII)
_NAME: Final = re.compile(r"[A-Za-z0-9._-]{1,100}", re.ASCII)
_SHA: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)
_TOKEN: Final = re.compile(r"[A-Za-z0-9._-]{20,1024}", re.ASCII)
_TRANSIENT_MARKERS: Final = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection reset",
    "rpc failed",
    "the remote end hung up",
    "returned error: 5",
    "returned error: 429",
    "tls",
)
_NO_SPACE: Final = "no space left on device"

HARDENING: Final = (
    "core.hooksPath=/dev/null",
    "core.symlinks=false",
    "core.protectNTFS=true",
    "core.protectHFS=true",
    "core.fsmonitor=false",
    "core.autocrlf=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "http.followRedirects=false",
    "transfer.fsckObjects=true",
    "submodule.recurse=false",
    "fetch.recurseSubmodules=false",
    "credential.helper=",
    "gc.auto=0",
    "advice.detachedHead=false",
)


def parse_arguments(argv: list[str]) -> dict[str, str] | None:
    patterns = {
        "owner": _OWNER,
        "name": _NAME,
        "sha": _SHA,
        "max-bytes": re.compile(r"[0-9]{1,12}", re.ASCII),
        "max-files": re.compile(r"[0-9]{1,9}", re.ASCII),
        "timeout": re.compile(r"[0-9]{1,5}", re.ASCII),
    }
    values: dict[str, str] = {}
    for argument in argv:
        key, separator, value = argument.removeprefix("--").partition("=")
        if not argument.startswith("--") or not separator or key not in patterns or key in values:
            return None
        if not patterns[key].fullmatch(value):
            return None
        values[key] = value
    return values if set(values) == set(patterns) else None


def read_token(stream: "object") -> str | None:
    line = stream.readline(1100)  # type: ignore[attr-defined]
    token = line.strip()
    return token if _TOKEN.fullmatch(token) else None


def git_environment(token: str) -> dict[str, str]:
    """El token viaja en una variable del proceso git, no en su línea de órdenes."""
    credentials = base64.b64encode(f"x-access-token:{token}".encode("ascii")).decode("ascii")
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",  # noqa: S108
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        # Ligada a la URL de GitHub: no se reenvía a otro host si hubiera una redirección.
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}",
        **{k: os.environ[k] for k in ("HTTPS_PROXY", "https_proxy", "NO_PROXY") if k in os.environ},
    }


def git(arguments: list[str], env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
    command = ["git"]
    for setting in HARDENING:
        command += ["-c", setting]
    completed = subprocess.run(  # noqa: S603
        [*command, *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode, completed.stderr.decode("utf-8", "replace").lower()


def free_bytes(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return usage.free


def classify_failure(stderr: str, workspace: Path) -> int:
    if _NO_SPACE in stderr or free_bytes(workspace) < (1 << 20):
        return EXIT_TOO_LARGE
    if any(marker in stderr for marker in _TRANSIENT_MARKERS):
        return EXIT_UNAVAILABLE
    return EXIT_FAILED


def clone(args: dict[str, str], token: str, workspace: Path = WORKSPACE) -> int:
    env = git_environment(token)
    target = workspace / TARGET
    timeout = int(args["timeout"])
    url = f"https://github.com/{args['owner']}/{args['name']}.git"
    try:
        target.mkdir(mode=0o700)
        steps = (
            ["init", "--quiet", "--initial-branch=main"],
            ["remote", "add", "origin", url],
            [
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "--no-recurse-submodules",
                "origin",
                args["sha"],
            ],
            ["checkout", "--quiet", "--detach", "--force", "FETCH_HEAD"],
        )
        for step in steps:
            code, stderr = git(step, env, target, timeout)
            if code != 0:
                return classify_failure(stderr, workspace)
        shutil.rmtree(target / ".git")
        sanitize_tree(target, max_files=int(args["max-files"]), max_bytes=int(args["max-bytes"]))
    except subprocess.TimeoutExpired:
        return EXIT_UNAVAILABLE
    except TreeTooLargeError:
        return EXIT_TOO_LARGE
    except OSError as error:
        return EXIT_TOO_LARGE if "no space" in str(error).lower() else EXIT_FAILED
    return EXIT_OK


def main(argv: list[str]) -> int:
    args = parse_arguments(argv)
    token = read_token(sys.stdin)
    if args is None or token is None:
        return EXIT_FAILED
    return clone(args, token)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
