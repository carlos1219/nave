"""Path guard del contenedor de fetch (CLAUDE.md §8, AM-504, AM-505).

Se ejecuta dentro del contenedor, sobre el árbol recién clonado y antes de que ningún
escáner lo toque. Resuelve cada ruta con `Path.resolve(strict=True)` y comprueba que
queda dentro de la raíz; elimina enlaces simbólicos y ficheros especiales; cuenta
ficheros y bytes frente a los límites (anti zip bomb).
"""

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class PathEscapeError(Exception):
    """Una ruta resuelve fuera de la raíz permitida."""


class TreeTooLargeError(Exception):
    """El árbol supera el número de ficheros o de bytes permitido."""


@dataclass(frozen=True, slots=True)
class TreeStats:
    files: int
    bytes: int
    removed_links: int
    removed_special: int


def resolve_inside(root: Path, candidate: str | Path) -> Path:
    """Ruta absoluta y real de `candidate` bajo `root`, o `PathEscapeError`.

    Rechaza bytes nulos, rutas absolutas, componentes `..` y cualquier resolución (por
    enlaces simbólicos incluidos) que acabe fuera de la raíz."""
    text = os.fspath(candidate)
    if "\x00" in text:
        raise PathEscapeError("null byte in path")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise PathEscapeError("path is absolute or contains '..'")
    real_root = root.resolve(strict=True)
    try:
        resolved = (real_root / path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PathEscapeError("path cannot be resolved") from error
    if not resolved.is_relative_to(real_root):
        raise PathEscapeError("path escapes the workspace")
    return resolved


def _entries(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    for directory, subdirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*subdirs, *names]:
            path = base / name
            yield path, path.lstat()


def sanitize_tree(root: Path, *, max_files: int, max_bytes: int) -> TreeStats:
    """Deja en `root` solo directorios y ficheros regulares.

    `os.walk(followlinks=False)` y `lstat` no atraviesan enlaces. Un enlace simbólico o un
    fichero especial (dispositivo, FIFO, socket) se borra; no se intenta "arreglar"."""
    real_root = root.resolve(strict=True)
    files = total = links = special = 0
    for path, info in list(_entries(real_root)):
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            path.unlink()
            links += 1
        elif stat.S_ISREG(mode):
            resolve_inside(real_root, path.relative_to(real_root))
            files += 1
            total += info.st_size
        elif not stat.S_ISDIR(mode):
            path.unlink()
            special += 1
        if files > max_files or total > max_bytes:
            raise TreeTooLargeError
    return TreeStats(files, total, links, special)
