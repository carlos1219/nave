"""Especificaciones de contenedor construidas desde plantillas fijas (AM-402, AM-501).

Ningún valor de la petición llega a un flag, a un montaje, a la red ni a la imagen: solo
a nombres de recurso derivados de un `run_id` validado y a argumentos con la forma
`--clave=valor` (un valor que empezara por `-` no puede convertirse en otra opción).
"""

from dataclasses import dataclass
from typing import Final, Literal

from nave_runner.config import RunnerConfig, ScannerImage
from nave_runner.schema import FetchRequest, ScanRequest

NON_ROOT_USER: Final = "65532:65532"
WORKSPACE_MOUNT: Final = "/workspace"
MANAGED_LABEL: Final = "nave.managed"
RUN_LABEL: Final = "nave.run"
ROLE_LABEL: Final = "nave.role"
FETCH_BINARY: Final = "/usr/local/bin/nave-fetch"
# Cada imagen de escáner lleva su propio envoltorio, versionado junto al binario fijado
# por digest: los flags concretos del escáner no viajan en la petición ni en este código.
SCAN_BINARY: Final = "/usr/local/bin/nave-scan"

EXIT_FETCH_TOO_LARGE: Final = 10
EXIT_FETCH_FAILED: Final = 11
EXIT_FETCH_UNAVAILABLE: Final = 12
SCAN_OK_EXIT_CODES: Final = frozenset({0})

HOLD_ROLE: Final = "hold"
_HOLD_SECONDS: Final = 86400
_HOLD_MEMORY: Final = 16 << 20
_HOLD_CPUS: Final = "0.1"
_HOLD_PIDS: Final = 8
_PIDS: Final = 256
_FETCH_MEMORY: Final = 1 << 30
_SCAN_MEMORY: Final = 2 << 30
_FETCH_CPUS: Final = "1"
_SCAN_CPUS: Final = "2"
_TMP_FETCH: Final = 256 << 20
_TMP_SCAN: Final = 1 << 30
_FILE_SIZE_ULIMIT: Final = 1 << 30
_OPEN_FILES: Final = 4096


@dataclass(frozen=True, slots=True)
class Mount:
    kind: Literal["volume", "tmpfs"]
    target: str
    source: str | None = None
    readonly: bool = False
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    name: str
    image: str
    command: tuple[str, ...]
    network: str
    workdir: str
    memory_bytes: int
    cpus: str
    mounts: tuple[Mount, ...]
    env: tuple[tuple[str, str], ...]
    labels: tuple[tuple[str, str], ...]
    runtime: str | None
    seccomp_profile: str | None
    user: str = NON_ROOT_USER
    pids_limit: int = _PIDS
    read_only: bool = True
    cap_drop_all: bool = True
    no_new_privileges: bool = True
    stdin: bool = False
    detach: bool = False


def workspace_volume(run_id: str) -> str:
    return f"nave-ws-{run_id}"


def container_name(role: str, run_id: str) -> str:
    return f"nave-{role}-{run_id}"


def _labels(role: str, run_id: str) -> tuple[tuple[str, str], ...]:
    return ((MANAGED_LABEL, "true"), (RUN_LABEL, run_id), (ROLE_LABEL, role))


def hold_spec(config: RunnerConfig, run_id: str) -> ContainerSpec:
    """Contenedor inerte que mantiene montado el volumen del workspace (ADR 0025).

    Un volumen `tmpfs` del driver `local` se monta con el primer contenedor que lo usa y se
    desmonta, perdiendo su contenido, al terminar el último: sin alguien que lo sostenga, lo
    que clona el fetch no llega al escaneo. No tiene red, no ejecuta nada del repositorio y
    su vida la acota `_HOLD_SECONDS` (el barrido de huérfanos lo retira antes)."""
    return ContainerSpec(
        name=container_name(HOLD_ROLE, run_id),
        image=config.fetch_image,
        command=("sleep", str(_HOLD_SECONDS)),
        network="none",
        workdir="/",
        memory_bytes=_HOLD_MEMORY,
        cpus=_HOLD_CPUS,
        mounts=(Mount("volume", WORKSPACE_MOUNT, source=workspace_volume(run_id)),),
        env=(),
        labels=_labels(HOLD_ROLE, run_id),
        runtime=config.runtime,
        seccomp_profile=config.seccomp_profile,
        pids_limit=_HOLD_PIDS,
        detach=True,
    )


def fetch_spec(config: RunnerConfig, request: FetchRequest) -> ContainerSpec:
    """Contenedor de clonado: único con red (solo hacia el proxy de egress, ADR 0007).

    El token no va en argumentos ni en variables de entorno (visibles en `/proc` y en
    `docker inspect`): entra por la entrada estándar (AM-506)."""
    proxy = config.egress_proxy_url
    return ContainerSpec(
        name=container_name("fetch", request.run_id),
        image=config.fetch_image,
        command=(
            FETCH_BINARY,
            f"--owner={request.owner}",
            f"--name={request.name}",
            f"--sha={request.commit_sha}",
            f"--max-bytes={request.max_bytes}",
            f"--max-files={request.max_files}",
            f"--timeout={request.timeout_seconds}",
        ),
        network=config.egress_network,
        workdir="/tmp",  # noqa: S108 - tmpfs del contenedor
        memory_bytes=_FETCH_MEMORY,
        cpus=_FETCH_CPUS,
        mounts=(
            Mount("volume", WORKSPACE_MOUNT, source=workspace_volume(request.run_id)),
            Mount("tmpfs", "/tmp", size_bytes=_TMP_FETCH),  # noqa: S108
        ),
        env=(
            ("HOME", "/tmp"),  # noqa: S108
            ("HTTPS_PROXY", proxy),
            ("https_proxy", proxy),
            ("NO_PROXY", ""),
            ("GIT_TERMINAL_PROMPT", "0"),
        ),
        labels=_labels("fetch", request.run_id),
        runtime=config.runtime,
        seccomp_profile=config.seccomp_profile,
        stdin=True,
    )


def scan_spec(config: RunnerConfig, request: ScanRequest, scanner: ScannerImage) -> ContainerSpec:
    """Contenedor de escaneo: sin red, sin secretos, workspace de solo lectura (AM-502).

    El directorio de trabajo es la raíz del volumen y el objetivo su subdirectorio `repo`:
    el proceso nunca arranca dentro del árbol del repositorio, así que no encuentra su
    `trivy.yaml` ni su `.semgrepignore` (AM-503)."""
    return ContainerSpec(
        name=container_name("scan", request.run_id),
        image=scanner.reference,
        command=(SCAN_BINARY,),
        network="none",
        workdir=WORKSPACE_MOUNT,
        memory_bytes=_SCAN_MEMORY,
        cpus=_SCAN_CPUS,
        mounts=(
            Mount(
                "volume", WORKSPACE_MOUNT, source=workspace_volume(request.run_id), readonly=True
            ),
            Mount("tmpfs", "/tmp", size_bytes=_TMP_SCAN),  # noqa: S108
            *(
                (Mount("volume", "/trivy-db", source=config.trivy_db_volume, readonly=True),)
                if scanner.kind == "trivy"
                else ()
            ),
        ),
        env=(
            ("HOME", "/tmp"),  # noqa: S108
            ("NAVE_SCAN_TIMEOUT_SECONDS", str(request.timeout_seconds)),
        ),
        labels=_labels("scan", request.run_id),
        runtime=config.runtime,
        seccomp_profile=config.seccomp_profile,
    )


def render_run_arguments(spec: ContainerSpec) -> list[str]:
    """Argumentos de `docker run`. Es el único sitio donde se traduce una especificación
    a flags; los tests de `test_sandbox_spec` recorren el resultado."""
    args = [
        "run",
        *(["--detach"] if spec.detach else []),
        "--name",
        spec.name,
        "--rm",
        "--pull",
        "never",
        "--log-driver",
        "none",
        "--user",
        spec.user,
        "--network",
        spec.network,
        "--workdir",
        spec.workdir,
        "--pids-limit",
        str(spec.pids_limit),
        "--memory",
        str(spec.memory_bytes),
        "--memory-swap",
        str(spec.memory_bytes),
        "--cpus",
        spec.cpus,
        "--ulimit",
        f"nofile={_OPEN_FILES}:{_OPEN_FILES}",
        "--ulimit",
        f"fsize={_FILE_SIZE_ULIMIT}:{_FILE_SIZE_ULIMIT}",
    ]
    if spec.read_only:
        args.append("--read-only")
    if spec.cap_drop_all:
        args += ["--cap-drop", "ALL"]
    if spec.no_new_privileges:
        args += ["--security-opt", "no-new-privileges:true"]
    if spec.seccomp_profile:
        args += ["--security-opt", f"seccomp={spec.seccomp_profile}"]
    if spec.runtime:
        args += ["--runtime", spec.runtime]
    if spec.stdin:
        args.append("--interactive")
    for key, value in spec.env:
        args += ["--env", f"{key}={value}"]
    for key, value in spec.labels:
        args += ["--label", f"{key}={value}"]
    for mount in spec.mounts:
        args += _render_mount(mount)
    return [*args, spec.image, *spec.command]


def _render_mount(mount: Mount) -> list[str]:
    if mount.kind == "tmpfs":
        return ["--tmpfs", f"{mount.target}:rw,noexec,nosuid,nodev,size={mount.size_bytes}"]
    option = ",readonly" if mount.readonly else ""
    return ["--mount", f"type=volume,src={mount.source},dst={mount.target}{option}"]


def render_volume_arguments(run_id: str, size_bytes: int) -> list[str]:
    """Volumen tmpfs con tamaño acotado, `noexec` (nada del repositorio se ejecuta) y
    propiedad del usuario sin privilegios."""
    uid = NON_ROOT_USER.split(":", maxsplit=1)[0]
    options = f"size={size_bytes},uid={uid},gid={uid},mode=0700,noexec,nosuid,nodev"
    return [
        "volume",
        "create",
        "--driver",
        "local",
        "--opt",
        "type=tmpfs",
        "--opt",
        "device=tmpfs",
        "--opt",
        f"o={options}",
        "--label",
        f"{MANAGED_LABEL}=true",
        "--label",
        f"{RUN_LABEL}={run_id}",
        "--label",
        f"{ROLE_LABEL}=workspace",
        workspace_volume(run_id),
    ]
