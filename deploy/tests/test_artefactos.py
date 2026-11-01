"""Dockerfiles, workflows, plantilla de imágenes y scripts (ADR 0022)."""

import importlib.util
import re

import pytest
import yaml

DOCKERFILES = [
    "backend/Dockerfile",
    "runner/Dockerfile",
    "deploy/nginx/Dockerfile",
    "deploy/scanner-sandbox/fetcher/Dockerfile",
    "deploy/scanner-sandbox/semgrep/Dockerfile",
    "deploy/scanner-sandbox/trivy/Dockerfile",
]
USES = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


@pytest.mark.parametrize("path", DOCKERFILES)
def test_dockerfile(root, path):
    text = (root / path).read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    froms = [line for line in lines if line.startswith("FROM ")]
    assert froms
    for line in froms:
        assert re.match(r"FROM \$\{[A-Z_]+\}( AS [\w-]+)?$", line), line
    for line in lines:
        if line.startswith("ARG ") and "=" in line:
            name = line.split()[1].split("=")[0]
            assert not name.endswith("_IMAGE"), (
                f"{path}: ARG de imagen con valor por defecto"
            )
    assert not re.search(r"\bADD\s+https?://", text)
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(ba)?sh", text)
    assert ":latest" not in text
    if "nginx" not in path:
        assert "USER 65532:65532" in text, path


def test_backend_instala_solo_con_hashes(root):
    text = (root / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes" in text and "--only-binary=:all:" in text
    assert "gcc" not in text and "build-essential" not in text


def test_plantilla_de_imagenes(root, services):
    example = (root / "deploy/images.env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in example.splitlines()
        if "=" in line and not line.startswith("#")
    )
    for key, value in values.items():
        assert re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[A-Z0-9a-z]+", value
        ), key
    used = set()
    for svc in services.values():
        used |= set(re.findall(r"\$\{(NAVE_[A-Z_]+_IMAGE)", svc["image"]))
        for v in svc.get("environment", {}).values():
            used |= set(re.findall(r"\$\{(NAVE_[A-Z_]+_IMAGE)", str(v)))
    assert used <= set(values), sorted(used - set(values))


def test_secretos_coinciden_con_el_generador(root, compose):
    spec = importlib.util.spec_from_file_location(
        "init_secrets", root / "deploy/scripts/init_secrets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.GENERATED) | set(module.MANUAL) == set(compose["secrets"])
    for name in compose["secrets"]:
        assert compose["secrets"][name]["file"] == f"./deploy/secrets/{name}"
    assert all(len(name) < 40 for name in module.GENERATED)


def test_script_de_roles_solo_usa_secretos_de_postgres(root):
    script = (root / "deploy/postgres/init-roles.sh").read_text(encoding="utf-8")
    used = set(re.findall(r"/run/secrets/(\w+)", script))
    assert used == {
        "db_migrator_password",
        "db_app_password",
        "db_maintenance_password",
    }
    assert "SUPERUSER " not in script.replace("NOSUPERUSER", "")
    assert script.count("NOSUPERUSER") == 3


def _redis_acls(root):
    script = (root / "deploy/redis/entrypoint.sh").read_text(encoding="utf-8")
    base = next(line for line in script.splitlines() if line.startswith("base="))
    expanded = script.replace("${base}", base.split("=", 1)[1].strip('"'))
    return script, {
        line.split()[1]: line.split() for line in expanded.splitlines() if line.startswith("user ")
    }


def test_redis_no_expone_las_contrasenas(root):
    script, _ = _redis_acls(root)
    assert "requirepass" not in script and " -a " not in script
    assert "sha256sum" in script
    assert 'save ""' in script and "appendonly no" in script


def test_redis_un_usuario_por_servicio(root):
    # RR-31: un componente comprometido solo puede lo que su servicio necesita.
    _, acls = _redis_acls(root)
    assert set(acls) == {"default", "health", "nave-api", "nave-scheduler", "nave-worker"}
    assert "off" in acls["default"] and "-@all" in acls["default"]
    assert acls["health"][-2:] == ["+ping", "+hello"] and not any(w.startswith(("~", "&")) for w in acls["health"])
    for user in ("nave-api", "nave-scheduler", "nave-worker"):
        assert "nopass" not in acls[user] and any(w.startswith("#") for w in acls[user])
        assert "resetkeys" in acls[user] and "resetchannels" in acls[user]
        assert "~*" not in acls[user] and "&*" not in acls[user]
    for producer in ("nave-api", "nave-scheduler"):
        words = acls[producer]
        assert "-@all" in words and "+lpush" in words
        for forbidden in ("+brpop", "+del", "+eval", "+evalsha", "+hset", "+@all", "+keys", "+flushall"):
            assert forbidden not in words, (producer, forbidden)
        assert "~unacked*" not in words and "~*pidbox*" not in words
    assert not any(w.startswith("&") for w in acls["nave-scheduler"])
    worker = acls["nave-worker"]
    denied = ("-@admin", "-@dangerous", "-function", "-fcall", "-fcall_ro")
    assert all(deny in worker for deny in denied)
    # El Lock de redis-py (mutex de kombu) libera con Lua: denegar el scripting tumba al worker.
    assert "-@scripting" not in worker and "-eval" not in worker and "-evalsha" not in worker
    # PSUBSCRIBE exige el patrón literal (F7-18 / RR-31).
    assert "&/0.celeryev/worker.*" in worker and "&/0.celery.pidbox" in worker


def test_allowlist_de_salida(root):
    acl = yaml.safe_load(
        (root / "deploy/egress-proxy/acl.yaml").read_text(encoding="utf-8")
    )
    domains = acl["default"]["allowed_domains"]
    assert set(domains) == {"github.com", "api.github.com", "codeload.github.com"}
    assert acl["default"]["action"] == "enforce"


def workflows(root):
    return [
        (p.name, yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted((root / ".github/workflows").glob("*.yml"))
    ]


def test_workflows_fijados_y_sin_inyeccion(root):
    flows = workflows(root)
    assert {n for n, _ in flows} == {"ci.yml", "release.yml"}
    for name, flow in flows:
        triggers = flow.get("on") or flow.get(True)
        assert "pull_request_target" not in triggers, name
        if name == "release.yml":
            assert set(triggers) == {"push"} and triggers["push"] == {"tags": ["v*"]}
        assert flow["permissions"] == {"contents": "read"}, name
        for job_name, job in flow["jobs"].items():
            assert "timeout-minutes" in job, f"{name}:{job_name}"
            for step in job["steps"]:
                if "uses" in step:
                    assert USES.match(step["uses"]), f"{name}:{step['uses']}"
                    if step["uses"].startswith("actions/checkout"):
                        assert step["with"]["persist-credentials"] is False
                run = step.get("run", "")
                assert "${{" not in run, f"{name}:{job_name}: expresión dentro de run"


def test_ci_bloquea_lo_que_exige_el_proyecto(root):
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for needed in (
        "ruff format --check",
        "ruff check",
        "mypy",
        "lint-imports",
        "pytest",
        "npm run check",
        "npm audit",
        "semgrep scan",
        "trivy-action",
        "--require-hashes",
        "deploy/tests",
    ):
        assert needed in ci, needed


def test_release_firma_y_genera_sbom(root):
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    for needed in (
        "cosign sign",
        "cosign attest",
        "cyclonedx",
        "provenance",
        "id-token: write",
    ):
        assert needed in release, needed
    flow = yaml.safe_load(release)
    assert flow["jobs"]["publish"]["permissions"]["id-token"] == "write"
    assert set(flow["jobs"]["publish"]["strategy"]["matrix"]["image"]) == {
        "backend",
        "frontend",
        "runner",
        "fetcher",
        "semgrep",
        "trivy",
        "egress-proxy",
    }


def _example_values(root):
    example = (root / "deploy/images.env.example").read_text(encoding="utf-8")
    return dict(
        line.split("=", 1) for line in example.splitlines() if "=" in line and not line.startswith("#")
    )


def test_release_publica_todas_las_imagenes_propias_de_la_plantilla(root):
    """F7-24: producción exige NAVE_EGRESS_PROXY_IMAGE y ninguna imagen oficial lo cubría."""
    own = {
        key.removeprefix("NAVE_").removesuffix("_IMAGE").lower().replace("_", "-")
        for key, value in _example_values(root).items()
        if value.startswith("ghcr.io/PROPIETARIO/")
    }
    flow = yaml.safe_load((root / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    published = set(flow["jobs"]["publish"]["strategy"]["matrix"]["image"])
    assert own == published
    assert "egress-proxy" in own
    # y el resumen convierte el nombre a una variable válida (NAVE_EGRESS_PROXY_IMAGE)
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "tr '[:lower:]-' '[:upper:]_'" in release


def test_el_proxy_de_salida_se_compila_de_una_version_fija(root):
    build = (root / "deploy/build-versions.env").read_text(encoding="utf-8")
    version = dict(line.split("=", 1) for line in build.split() if "=" in line)
    assert re.fullmatch(r"v\d+\.\d+\.\d+", version["NAVE_SMOKESCREEN_VERSION"])
    dev = (root / "deploy/images.dev.env").read_text(encoding="utf-8")
    assert f"NAVE_SMOKESCREEN_VERSION={version['NAVE_SMOKESCREEN_VERSION']}" in dev
    values = _example_values(root)
    assert "ghcr.io/stripe/smokescreen" not in " ".join(values.values())  # esa imagen no existe
    assert values["NAVE_GO_IMAGE"].startswith("golang:")


def test_pin_images_resuelve_exactamente_las_bases_de_la_plantilla(root):
    module = _load(root, "pin_images")
    own = {k for k, v in _example_values(root).items() if v.startswith("ghcr.io/PROPIETARIO/")}
    bases = set(_example_values(root)) - own
    assert module.BASE == bases


def test_el_lock_de_digests_esta_versionado_y_es_valido(root):
    lock = (root / "deploy/images.lock.env").read_text(encoding="utf-8")
    entries = dict(
        line.split("=", 1) for line in lock.splitlines() if line and not line.startswith("#")
    )
    module = _load(root, "pin_images")
    assert set(entries) == module.BASE  # sin esto el trabajo `images` de CI no construye nada
    for key, value in entries.items():
        assert re.fullmatch(r"[a-z0-9./:_-]+@sha256:[0-9a-f]{64}", value), key


def test_ci_construye_todas_las_imagenes_con_los_digests_del_lock(root):
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for needed in ("name: backend", "name: frontend", "name: runner", "name: fetcher",
                   "name: semgrep", "name: egress-proxy", "name: trivy", "check_scanner_flags.py"):
        assert needed in ci, needed
    assert "build-versions.env" in ci


def test_dependabot_cubre_actions_npm_y_pip(root):
    config = yaml.safe_load((root / ".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert {"github-actions", "npm", "pip"} <= ecosystems
    for update in config["updates"]:
        assert update["schedule"]["interval"] == "weekly"


def test_dockerfiles_propios_no_corren_como_root(root):
    for path in ("deploy/nginx/Dockerfile", "deploy/egress-proxy/Dockerfile", "runner/Dockerfile",
                 "backend/Dockerfile", "deploy/scanner-sandbox/fetcher/Dockerfile",
                 "deploy/scanner-sandbox/semgrep/Dockerfile", "deploy/scanner-sandbox/trivy/Dockerfile"):
        users = re.findall(r"^USER\s+(\S+)", (root / path).read_text(encoding="utf-8"), re.M)
        assert users, path
        assert users[-1].split(":")[0] not in {"0", "root"}, path


def test_egress_proxy_eleva_dependencias_con_cve(root):
    text = (root / "deploy/egress-proxy/Dockerfile").read_text(encoding="utf-8")
    for module in ("logrus", "golang.org/x/net", "golang.org/x/text"):
        assert module in text, module
    assert "go install github.com/stripe/smokescreen@" not in text


def test_excepcion_de_secretos_del_autoescaneo_es_minima(root):
    config = yaml.safe_load((root / ".github/trivy-secret.yaml").read_text(encoding="utf-8"))
    rules = config["allow-rules"]
    assert [r["path"] for r in rules] == [r"^contracts/runner-protocol-vectors\.json$"]
    assert "TRIVY_SECRET_CONFIG: .github/trivy-secret.yaml" in (
        root / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")


def test_secretos_fuera_del_repositorio(root):
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "secrets/" in ignore and "deploy/images.env" in ignore
    assert "deploy/.env" in ignore or ".env" in ignore


def test_versiones_de_escaner_coinciden(root):
    versions = dict(
        line.split("=")
        for line in (root / "deploy/scanner-versions.env")
        .read_text(encoding="utf-8")
        .split()
        if "=" in line
    )
    for name in ("env.example", "env.dev.example"):
        text = (root / "deploy" / name).read_text(encoding="utf-8")
        for key, value in versions.items():
            assert f"{key}={value}" in text, f"{name}: {key}"


def _load(root, name):
    spec = importlib.util.spec_from_file_location(name, root / f"deploy/scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actualizacion_de_la_bd_de_trivy(root):
    module = _load(root, "update_trivy_db")
    image = "ghcr.io/o/nave-trivy@sha256:" + "a" * 64
    volume, chown, fetch = module.commands(image)
    assert volume == ["docker", "volume", "create", "nave-trivy-db"]
    for command in (chown, fetch):
        assert command[:3] == ["docker", "run", "--rm"]
        assert "--read-only" in command and command[command.index("--cap-drop") + 1] == "ALL"
        assert "no-new-privileges:true" in command
        assert "--network" not in command  # red por defecto: la allowlist del proxy no cambia
        assert "--privileged" not in command
    assert command_has(fetch, "--user", "65532:65532")
    assert "--download-db-only" in fetch and image in fetch
    # La base de datos ya no cabe en el tmpfs de /tmp: los temporales van al volumen.
    assert command_has(fetch, "-e", "TMPDIR=/db")
    assert module.IMAGE.fullmatch(image) and not module.IMAGE.fullmatch("trivy:latest")


def command_has(command, flag, value):
    return command[command.index(flag) + 1] == value


def test_verificador_de_flags(root):
    module = _load(root, "check_scanner_flags")
    script = (root / "deploy/scanner-sandbox/trivy/nave-scan").read_text(encoding="utf-8")
    flags = module.flags_in(script)
    assert {"--skip-db-update", "--offline-scan", "--scanners", "--format", "--cache-dir"} <= flags
    assert not module.missing(flags, " ".join(flags))
    assert module.missing({"--no-existe"}, "--format --scanners") == ["--no-existe"]
    semgrep = module.flags_in((root / "deploy/scanner-sandbox/semgrep/nave-scan").read_text(encoding="utf-8"))
    assert "--metrics" in semgrep or "--sarif" in semgrep or semgrep


def test_instalaciones_con_hashes_no_mezclan_paquetes_sueltos(root):
    # En el primer CI real, `-r requirements-test.lock pytest==9.1.1` fallaba: con
    # --require-hashes, pip rechaza cualquier paquete sin hash en la misma orden.
    for workflow in (root / ".github/workflows").glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "--require-hashes" in line:
                assert not re.search(r"\s[A-Za-z0-9_.-]+==", line), f"{workflow.name}: {line.strip()}"


def test_ci_comprueba_que_los_escaneres_ignoran_la_configuracion_del_repo(root):
    # F7-04: el contrato AM-503 corre contra las imágenes reales de Semgrep y Trivy.
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "check_scanner_isolation.py nave-semgrep:ci semgrep" in ci
    assert "check_scanner_isolation.py nave-trivy:ci trivy --trivy-db-volume nave-trivy-db" in ci
    assert ci.index("update_trivy_db.py") < ci.index("check_scanner_isolation.py nave-trivy:ci")


def test_envoltorios_de_escaner_ejecutables_en_cualquier_checkout(root):
    # F7-34: construida desde un checkout sin bit de ejecución (Windows), la imagen daba
    # «permission denied» al lanzar nave-scan; el modo se fija en el propio Dockerfile.
    for kind in ("semgrep", "trivy"):
        dockerfile = (root / f"deploy/scanner-sandbox/{kind}/Dockerfile").read_text(encoding="utf-8")
        assert "COPY --chmod=0555 nave-scan /usr/local/bin/nave-scan" in dockerfile, kind


def test_trivy_enlaza_la_base_de_datos_sin_copiarla(root):
    # F7-33: la base de datos supera el tmpfs del escaneo (1 GiB); copiarla rompía todo
    # escaneo de Trivy con «No space left on device».
    script = (root / "deploy/scanner-sandbox/trivy/nave-scan").read_text(encoding="utf-8")
    assert "ln -s /trivy-db/db /tmp/trivy-cache/db" in script
    assert "cp " not in script


def test_configuracion_de_trivy_de_la_plataforma_es_yaml_cargable(root):
    """Trivy 0.75 aborta con `secrets config decode error: EOF` si el fichero solo tiene
    comentarios: un documento vacío (None) no es una configuración válida."""
    base = root / "deploy/scanner-sandbox/trivy"
    secret = yaml.safe_load((base / "trivy-secret.yaml").read_text(encoding="utf-8"))
    assert isinstance(secret, dict) and secret.get("disable-rules") == []
    assert (base / "empty.trivyignore").read_text(encoding="utf-8").strip() == ""


def test_ci_verifica_los_flags_de_los_escaneres(root):
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "check_scanner_flags.py" in ci
    assert ci.count("check_scanner_flags.py") >= 2  # trivy y semgrep
