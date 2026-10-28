"""AM-503 contra el Semgrep real.

Ejecuta exactamente los flags de `deploy/scanner-sandbox/semgrep/nave-scan` (se leen del
propio envoltorio, para no duplicarlos) sobre repositorios que intentan ocultar
hallazgos o inyectar reglas. Requiere `NAVE_TEST_SEMGREP_BIN` apuntando a un Semgrep de
la versión fijada; sin él, el test se omite (la CI lo aporta en la fase 6).
"""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from nave.infrastructure.scanners.sarif import parse_sarif
from nave.infrastructure.scanners.semgrep import map_result

_BIN = os.environ.get("NAVE_TEST_SEMGREP_BIN")
WRAPPER = Path(__file__).parents[3] / "deploy" / "scanner-sandbox" / "semgrep" / "nave-scan"
PLATFORM_RULES = """
rules:
  - id: nave.test.shell-true
    languages: [python]
    severity: ERROR
    message: shell=True
    pattern: subprocess.run(..., shell=True)
"""
VULNERABLE = "import subprocess\n\ndef run(cmd):\n    subprocess.run(cmd, shell=True)\n"

pytestmark = pytest.mark.skipif(
    not _BIN or not Path(_BIN).exists(), reason="NAVE_TEST_SEMGREP_BIN is not set"
)


def wrapper_arguments(rules: Path) -> list[str]:
    text = WRAPPER.read_text().replace("\\\n", " ")
    line = next(line for line in text.splitlines() if "semgrep scan" in line)
    words = shlex.split(line.split("semgrep scan", maxsplit=1)[1])
    words[words.index("--config") + 1] = str(rules)
    return words


def scan(workspace: Path, rules: Path) -> list[dict[str, object]]:
    assert _BIN is not None
    completed = subprocess.run(  # noqa: S603
        [_BIN, "scan", *wrapper_arguments(rules)],
        cwd=workspace,
        capture_output=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(workspace.parent),
            "SEMGREP_SEND_METRICS": "off",
        },
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()[-500:]
    return json.loads(completed.stdout)["runs"][0]["results"]  # type: ignore[no-any-return]


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "r.yaml").write_text(PLATFORM_RULES)
    workspace = tmp_path / "workspace"
    (workspace / "repo" / "src").mkdir(parents=True)
    (workspace / "repo" / "src" / "app.py").write_text(VULNERABLE)
    return workspace, rules


def test_baseline_finds_the_vulnerability(world: tuple[Path, Path]) -> None:
    results = scan(*world)
    assert [r["ruleId"] for r in results] == ["nave.test.shell-true"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        (".semgrepignore", "src/\n"),
        (".semgrepignore", "*\n"),
        (".gitignore", "src/app.py\n"),
        (".gitignore", "*\n"),
    ],
)
def test_repo_ignore_files_cannot_hide_findings(
    world: tuple[Path, Path], filename: str, content: str
) -> None:
    workspace, rules = world
    (workspace / "repo" / filename).write_text(content)
    assert len(scan(workspace, rules)) == 1


def test_inline_nosemgrep_comments_do_not_remove_findings(world: tuple[Path, Path]) -> None:
    """Semgrep marca el resultado como `suppressions: inSource` en SARIF en vez de omitirlo.
    NAVE no honra esa marca (la decide quien controla el código): el hallazgo se conserva."""
    workspace, rules = world
    source = workspace / "repo" / "src" / "app.py"
    source.write_text(VULNERABLE.replace("shell=True)", "shell=True)  # nosemgrep"))
    raw = scan(workspace, rules)
    assert len(raw) == 1
    assert raw[0].get("suppressions") == [{"kind": "inSource"}]
    parsed = parse_sarif(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [{"tool": {"driver": {"name": "Semgrep OSS"}}, "results": raw}],
            }
        ).encode()
    )
    assert [map_result(r) is not None for r in parsed.results] == [True]


def test_repo_rule_files_are_never_loaded(world: tuple[Path, Path]) -> None:
    workspace, rules = world
    repo = workspace / "repo"
    evil = (
        "rules:\n  - id: evil.injected\n    languages: [python]\n    severity: ERROR\n"
        "    message: x\n    pattern: subprocess.run(...)\n"
    )
    for name in (".semgrep.yml", "semgrep.yml", ".semgrep.yaml"):
        (repo / name).write_text(evil)
    (repo / ".semgrep").mkdir()
    (repo / ".semgrep" / "rules.yaml").write_text(evil)
    assert [r["ruleId"] for r in scan(workspace, rules)] == ["nave.test.shell-true"]


def test_symlinks_to_outside_files_are_not_followed(
    world: tuple[Path, Path], tmp_path: Path
) -> None:
    workspace, rules = world
    outside = tmp_path / "outside.py"
    outside.write_text(VULNERABLE)
    (workspace / "repo" / "src" / "link.py").symlink_to(outside)
    uris = [
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]  # type: ignore[index]
        for r in scan(workspace, rules)
    ]
    assert all(str(u).startswith("repo/") for u in uris)
    assert not any("outside" in str(u) for u in uris)


def test_paths_come_out_relative_to_the_workspace_and_map_to_repository_paths(
    world: tuple[Path, Path],
) -> None:
    workspace, rules = world
    assert _BIN is not None
    completed = subprocess.run(  # noqa: S603
        [_BIN, "scan", *wrapper_arguments(rules)],
        cwd=workspace,
        capture_output=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(workspace.parent),
            "SEMGREP_SEND_METRICS": "off",
        },
        timeout=120,
        check=True,
    )
    parsed = parse_sarif(completed.stdout)
    mapped = [map_result(r) for r in parsed.results]
    assert [m.path for m in mapped if m] == ["src/app.py"]
    assert parsed.dropped == 0
