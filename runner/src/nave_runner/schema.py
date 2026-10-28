"""Esquema cerrado de las peticiones (AM-402).

Nada de lo que llega en el cuerpo elige imagen, flags, montajes ni red: solo identifica
el trabajo (ids validados con expresiones regulares) y acota límites que nunca superan
los máximos del propio runner.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Final

from nave_runner.config import RunnerConfig

_RUN_ID: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_OWNER: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", re.ASCII)
_NAME: Final = re.compile(r"[A-Za-z0-9._-]{1,100}", re.ASCII)
_SHA: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)
_TOKEN: Final = re.compile(r"[A-Za-z0-9._-]{20,1024}", re.ASCII)
_KIND: Final = re.compile(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", re.ASCII)

_FETCH_KEYS: Final = frozenset(
    {
        "step",
        "run_id",
        "owner",
        "name",
        "commit_sha",
        "token",
        "max_bytes",
        "max_files",
        "timeout_seconds",
    }
)
_SCAN_KEYS: Final = frozenset(
    {"step", "run_id", "scanner_kind", "timeout_seconds", "max_output_bytes"}
)
_RELEASE_KEYS: Final = frozenset({"step", "run_id"})


class BadRequestError(Exception):
    """Cuerpo inválido. El mensaje es interno: al cliente solo se le dice `bad_request`."""


@dataclass(frozen=True, slots=True)
class FetchRequest:
    run_id: str
    owner: str
    name: str
    commit_sha: str
    token: str = field(repr=False)
    max_bytes: int
    max_files: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ScanRequest:
    run_id: str
    scanner_kind: str
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    run_id: str


Request = FetchRequest | ScanRequest | ReleaseRequest


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BadRequestError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise BadRequestError("non-finite number")


def parse_request(body: bytes, config: RunnerConfig) -> Request:
    try:
        document = json.loads(
            body.decode("ascii"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as error:
        raise BadRequestError("not json") from error
    if not isinstance(document, dict):
        raise BadRequestError("not an object")
    step = document.get("step")
    if step == "fetch":
        return _fetch(document, config)
    if step == "scan":
        return _scan(document, config)
    if step == "release":
        _exact(document, _RELEASE_KEYS)
        return ReleaseRequest(_matching(document, "run_id", _RUN_ID))
    raise BadRequestError("unknown step")


def _exact(document: dict[str, object], keys: frozenset[str]) -> None:
    if set(document) != keys:
        raise BadRequestError("unexpected or missing keys")


def _matching(document: dict[str, object], key: str, pattern: re.Pattern[str]) -> str:
    value = document[key]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise BadRequestError(f"invalid {key}")
    return value


def _integer(document: dict[str, object], key: str, cap: int) -> int:
    value = document[key]
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= cap:
        raise BadRequestError(f"invalid {key}")
    return value


def _fetch(document: dict[str, object], config: RunnerConfig) -> FetchRequest:
    _exact(document, _FETCH_KEYS)
    name = _matching(document, "name", _NAME)
    if name in {".", ".."} or name.endswith(".git"):
        raise BadRequestError("invalid name")
    return FetchRequest(
        run_id=_matching(document, "run_id", _RUN_ID),
        owner=_matching(document, "owner", _OWNER),
        name=name,
        commit_sha=_matching(document, "commit_sha", _SHA),
        token=_matching(document, "token", _TOKEN),
        max_bytes=_integer(document, "max_bytes", config.max_fetch_bytes),
        max_files=_integer(document, "max_files", config.max_fetch_files),
        timeout_seconds=_integer(document, "timeout_seconds", config.max_fetch_seconds),
    )


def _scan(document: dict[str, object], config: RunnerConfig) -> ScanRequest:
    _exact(document, _SCAN_KEYS)
    kind = _matching(document, "scanner_kind", _KIND)
    if kind not in config.scanners:
        raise BadRequestError("scanner not enabled")
    return ScanRequest(
        run_id=_matching(document, "run_id", _RUN_ID),
        scanner_kind=kind,
        timeout_seconds=_integer(document, "timeout_seconds", config.max_scan_seconds),
        max_output_bytes=_integer(document, "max_output_bytes", config.max_output_bytes),
    )
