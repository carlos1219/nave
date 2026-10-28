"""Acceso al motor de contenedores mediante su CLI (sin SDK, sin dependencias).

`subprocess` siempre con `shell=False` y lista de argumentos construida por código
(`templates.py`). El entorno del proceso hijo es mínimo. La salida de error del
contenedor se descarta: es texto no confiable y podría reflejar el token.
"""

import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

from nave_runner.templates import (
    HOLD_ROLE,
    MANAGED_LABEL,
    ContainerSpec,
    container_name,
    render_run_arguments,
    render_volume_arguments,
    workspace_volume,
)

_LOG = logging.getLogger("nave_runner.engine")
_ENGINE_TIMEOUT: Final = 60
_READ_CHUNK: Final = 65536
_ENGINE_ENV_ALLOWED: Final = ("PATH", "DOCKER_HOST", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
_NAME: Final = re.compile(r"nave-(?:fetch|scan|hold|ws)-[0-9a-f]{32}", re.ASCII)
_FRACTION: Final = re.compile(r"(\.\d{6})\d+")
_KILL_GRACE: Final = 10


class EngineError(Exception):
    """El motor no respondió o rechazó la operación. Es siempre un fallo transitorio."""


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int | None
    stdout: bytes
    timed_out: bool
    output_exceeded: bool


@dataclass(frozen=True, slots=True)
class Orphan:
    kind: str
    name: str
    created_at: datetime


class Engine(Protocol):
    def create_workspace(self, run_id: str, *, size_bytes: int) -> None: ...

    def start_holder(self, spec: ContainerSpec) -> None: ...

    def workspace_exists(self, run_id: str) -> bool: ...

    def workspace_ready(self, run_id: str) -> bool: ...

    def remove_workspace(self, run_id: str) -> None: ...

    def run(
        self, spec: ContainerSpec, *, stdin: bytes | None, timeout_seconds: int, max_output: int
    ) -> RunResult: ...

    def kill_run(self, run_id: str) -> None: ...

    def list_orphans(self) -> list[Orphan]: ...

    def remove_orphan(self, orphan: Orphan) -> None: ...

    def ping(self) -> bool: ...


class CliEngine:
    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._env = {k: os.environ[k] for k in _ENGINE_ENV_ALLOWED if k in os.environ}

    def _call(
        self, arguments: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(  # noqa: S603
                [self._binary, *arguments],
                capture_output=True,
                timeout=_ENGINE_TIMEOUT,
                check=False,
                env=self._env,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EngineError(type(error).__name__) from error
        if check and completed.returncode != 0:
            raise EngineError(f"exit {completed.returncode}")
        return completed

    def create_workspace(self, run_id: str, *, size_bytes: int) -> None:
        self._call(render_volume_arguments(run_id, size_bytes))

    def start_holder(self, spec: ContainerSpec) -> None:
        self._call(render_run_arguments(spec))

    def workspace_exists(self, run_id: str) -> bool:
        completed = self._call(["volume", "inspect", workspace_volume(run_id)], check=False)
        return completed.returncode == 0

    def workspace_ready(self, run_id: str) -> bool:
        """El volumen existe **y** su sostenedor sigue vivo: sin él el contenido ya se perdió
        (ADR 0025) y un escaneo vería un workspace vacío."""
        if not self.workspace_exists(run_id):
            return False
        holder = self._call(
            ["inspect", "--format", "{{.State.Running}}", container_name(HOLD_ROLE, run_id)],
            check=False,
        )
        return holder.returncode == 0 and holder.stdout.decode("ascii", "replace").strip() == "true"

    def remove_workspace(self, run_id: str) -> None:
        self.kill_run(run_id)
        completed = self._call(["volume", "rm", "--force", workspace_volume(run_id)], check=False)
        if completed.returncode != 0 and self.workspace_exists(run_id):
            raise EngineError("volume not removed")

    def kill_run(self, run_id: str) -> None:
        # El sostenedor va el último: al retirarlo el volumen deja de estar montado.
        for role in ("fetch", "scan", HOLD_ROLE):
            self._call(["rm", "--force", container_name(role, run_id)], check=False)

    def ping(self) -> bool:
        try:
            return (
                self._call(["version", "--format", "{{.Server.Version}}"], check=False).returncode
                == 0
            )
        except EngineError:
            return False

    def run(
        self, spec: ContainerSpec, *, stdin: bytes | None, timeout_seconds: int, max_output: int
    ) -> RunResult:
        arguments = render_run_arguments(spec)
        try:
            process = subprocess.Popen(  # noqa: S603
                [self._binary, *arguments],
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env,
            )
        except OSError as error:
            raise EngineError(type(error).__name__) from error
        # El gestor de contexto cierra las tuberías y espera al proceso en cualquier salida.
        with process:
            state = _Capture(max_output)
            reader = threading.Thread(target=state.drain, args=(process, spec.name), daemon=True)
            reader.start()
            try:
                self._feed(process, stdin)
                timed_out = False
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill(spec.name, process)
                reader.join(timeout=_KILL_GRACE)
            finally:
                # `--rm` no se ejecuta si matamos el cliente; el borrado forzado es idempotente.
                self._call(["rm", "--force", spec.name], check=False)
                if process.poll() is None:
                    process.kill()
        return RunResult(
            exit_code=None if timed_out or state.exceeded else process.returncode,
            stdout=bytes(state.buffer),
            timed_out=timed_out,
            output_exceeded=state.exceeded,
        )

    @staticmethod
    def _feed(process: "subprocess.Popen[bytes]", data: bytes | None) -> None:
        if data is None or process.stdin is None:
            return
        try:
            process.stdin.write(data)
            process.stdin.close()
        except BrokenPipeError:
            # El contenedor terminó antes de leer el token: su código de salida lo explica.
            _LOG.debug("container closed stdin early")

    def _kill(self, name: str, process: "subprocess.Popen[bytes]") -> None:
        self._call(["kill", name], check=False)
        process.kill()

    def list_orphans(self) -> list[Orphan]:
        orphans: list[Orphan] = []
        for kind, listing, inspect in (
            ("container", ["ps", "--all"], ["inspect", "--format", "{{.Created}}"]),
            ("volume", ["volume", "ls"], ["volume", "inspect", "--format", "{{.CreatedAt}}"]),
        ):
            names = (
                self._call(
                    [
                        *listing,
                        "--filter",
                        f"label={MANAGED_LABEL}=true",
                        "--format",
                        "{{.Names}}" if kind == "container" else "{{.Name}}",
                    ]
                )
                .stdout.decode("utf-8", "replace")
                .split()
            )
            for name in names:
                if not _NAME.fullmatch(name):
                    continue
                created = self._call([*inspect, name], check=False)
                parsed = _parse_time(created.stdout.decode("utf-8", "replace").strip())
                if created.returncode == 0 and parsed is not None:
                    orphans.append(Orphan(kind, name, parsed))
        return orphans

    def remove_orphan(self, orphan: Orphan) -> None:
        if not _NAME.fullmatch(orphan.name):
            raise EngineError("unexpected resource name")
        if orphan.kind == "container":
            self._call(["rm", "--force", orphan.name], check=False)
        else:
            self._call(["volume", "rm", "--force", orphan.name], check=False)


class _Capture:
    """Lee la salida estándar con tope. Si lo supera, mata el contenedor: un escáner
    comprometido no puede agotar la memoria del runner (AM-505)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = bytearray()
        self.exceeded = False

    def drain(self, process: "subprocess.Popen[bytes]", name: str) -> None:
        assert process.stdout is not None  # noqa: S101 - construido con PIPE
        while chunk := process.stdout.read(_READ_CHUNK):
            if self.exceeded:
                continue
            room = self.limit - len(self.buffer)
            if len(chunk) > room:
                self.exceeded = True
                self.buffer.clear()
                process.kill()
                continue
            self.buffer += chunk


def _parse_time(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_FRACTION.sub(r"\1", raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
