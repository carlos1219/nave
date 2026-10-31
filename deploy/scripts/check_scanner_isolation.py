"""Contrato AM-503 contra las imágenes reales de escáner (F7-04).

Ejecuta la imagen como la ejecuta el runner (sin red, FS de solo lectura, usuario sin
privilegios, workspace de solo lectura en /workspace/repo) sobre un repositorio que intenta
desactivar el escáner con su propia configuración. Falla si el hallazgo esperado no aparece:
eso significaría que el escáner obedeció al repositorio analizado.

    python deploy/scripts/check_scanner_isolation.py nave-semgrep:ci semgrep
    python deploy/scripts/check_scanner_isolation.py nave-trivy:ci trivy --trivy-db-volume nave-trivy-db
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HARDENING = [
    "--rm",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--user",
    "65532:65532",
    "--memory",
    "2g",
    "--tmpfs",
    "/tmp:rw,noexec,nosuid,nodev,size=1073741824",
    "-e",
    "HOME=/tmp",
    "-e",
    "NAVE_SCAN_TIMEOUT_SECONDS=300",
]


def semgrep_repo(repo: Path) -> str:
    (repo / "app.py").write_text("user = input()\nresult = eval(user)  # nosemgrep\n")
    # Cada uno de estos ficheros, si se respetara, ocultaría el hallazgo.
    (repo / ".semgrepignore").write_text("*\n")
    (repo / ".gitignore").write_text("*\n")
    (repo / ".semgrep.yml").write_text("rules: []\n")
    return "eval-call"


def trivy_repo(repo: Path) -> str:
    # El token se compone aquí para no dejar en el repositorio algo con forma de secreto.
    token = "ghp_" + "N4v3" * 9
    (repo / "settings.py").write_text(f'GITHUB_TOKEN = "{token}"\n')
    (repo / "trivy.yaml").write_text("scan:\n  scanners: [vuln]\nsecret:\n  config: none.yaml\n")
    (repo / ".trivyignore").write_text("github-pat\n")
    (repo / "trivy-secret.yaml").write_text("disable-rules: [github-pat]\n")
    return "github-pat"


FIXTURES = {"semgrep": semgrep_repo, "trivy": trivy_repo}


def rule_ids(sarif: str) -> set[str]:
    document = json.loads(sarif)
    return {r.get("ruleId", "") for run in document.get("runs", []) for r in run.get("results", [])}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("kind", choices=sorted(FIXTURES))
    parser.add_argument("--trivy-db-volume")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as workspace:
        repo = Path(workspace) / "repo"
        repo.mkdir()
        expected = FIXTURES[args.kind](repo)
        # El contenedor corre como 65532: el workspace de prueba debe poder leerse.
        for path in [Path(workspace), repo, *repo.iterdir()]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)  # noqa: S103 - datos de prueba
        command = ["docker", "run", *HARDENING]
        command += ["--mount", f"type=bind,src={workspace},dst=/workspace,readonly"]
        if args.kind == "trivy":
            if not args.trivy_db_volume:
                parser.error("trivy needs --trivy-db-volume")
            command += ["--mount", f"type=volume,src={args.trivy_db_volume},dst=/trivy-db,readonly"]
        command += ["-w", "/workspace", args.image, "/usr/local/bin/nave-scan"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)  # noqa: S603
    if completed.returncode != 0:
        print(f"{args.kind}: el escáner terminó con {completed.returncode}", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
        return 1
    found = rule_ids(completed.stdout)
    if not any(expected in rule for rule in found):
        print(f"{args.kind}: falta '{expected}'; la configuración del repo se respetó: {sorted(found)}", file=sys.stderr)
        return 1
    print(f"{args.kind}: ignora la configuración del repositorio ({expected} detectado)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
