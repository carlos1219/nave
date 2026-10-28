"""Endurecimiento de Compose (CLAUDE.md §15, ADR 0022). Sin Docker: se lee el YAML."""

import re

import yaml

REQUIRED = {
    "frontend",
    "api",
    "worker",
    "postgres",
    "redis",
    "scan-runner",
    "egress-proxy",
    "scheduler",
}
JOBS = {"migrate", "cli"}  # perfil ops: sin healthcheck ni reinicio
DEV = {"keycloak"}  # perfil dev: fuera de las reglas (no es un límite de seguridad)
LONG_RUNNING = REQUIRED
IMAGE_VAR = re.compile(r"^\$\{NAVE_[A-Z_]+_IMAGE:\?[^}]*\}$")
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|PRIVATE|TOKEN|KEY)", re.IGNORECASE)

# Reparto de secretos (docs/architecture/despliegue.md §2): cada uno en el servicio que lo necesita.
SECRETS = {
    "frontend": {"tls_cert", "tls_key"},
    "api": {
        "db_app_password",
        "redis_password",
        "webhook_secret",
        "oidc_client_secret",
    },
    "worker": {
        "db_app_password",
        "db_maintenance_password",
        "redis_password",
        "github_app_private_key",
        "runner_hmac_key",
        "fingerprint_key",
    },
    "postgres": {
        "db_superuser_password",
        "db_app_password",
        "db_migrator_password",
        "db_maintenance_password",
    },
    "redis": {"redis_password"},
    "scan-runner": {"runner_hmac_key"},
    "egress-proxy": set(),
    "scheduler": {"redis_password"},
    "migrate": {"db_migrator_password"},
    "cli": {"db_app_password"},
}


def checked(services: dict) -> dict:
    return {name: svc for name, svc in services.items() if name not in DEV}


def test_servicios_requeridos(services):
    assert REQUIRED <= set(services)
    assert set(services) <= REQUIRED | JOBS | DEV


def test_todos_endurecidos(services):
    for name, svc in checked(services).items():
        assert svc.get("read_only") is True, name
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") is None, name
        assert svc.get("pid") is None and svc.get("ipc") is None, name
        for limit in ("mem_limit", "cpus", "pids_limit"):
            assert limit in svc, f"{name} sin {limit}"


def test_reinicio_y_healthcheck(services):
    for name in LONG_RUNNING:
        assert services[name].get("restart") == "unless-stopped", name
        assert "healthcheck" in services[name], name
    for name in JOBS:
        assert services[name].get("restart") == "no", name
        assert services[name].get("profiles") == ["ops"], name


def test_solo_postgres_anade_capacidades(services):
    for name, svc in services.items():
        if name == "postgres":
            assert set(svc["cap_add"]) <= {
                "CHOWN",
                "SETUID",
                "SETGID",
                "FOWNER",
                "DAC_OVERRIDE",
            }
        else:
            assert "cap_add" not in svc, name


def test_imagenes_solo_por_variable_con_digest_obligatorio(services):
    for name, svc in services.items():
        assert IMAGE_VAR.match(svc["image"]), f"{name}: {svc['image']}"


def test_usuario_no_root(services, root):
    explicit = {n for n, s in services.items() if "user" in s}
    for name in explicit:
        uid = str(services[name]["user"]).split(":")[0]
        assert uid not in {"0", "root"}, name
    # Los demás heredan USER de su Dockerfile (comprobado en test_dockerfiles).
    inherited = set(checked(services)) - explicit
    assert inherited <= {
        "api",
        "worker",
        "scheduler",
        "scan-runner",
        "migrate",
        "cli",
        "postgres",
    }
    assert "USER 65532:65532" in (root / "backend/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "USER 65532:65532" in (root / "runner/Dockerfile").read_text(
        encoding="utf-8"
    )


def test_solo_el_frontend_publica_puertos(services):
    for name, svc in services.items():
        if name == "frontend":
            assert sorted(svc["ports"]) == ["443:8443", "80:8080"]
        elif name in DEV:
            assert all(p.startswith("127.0.0.1:") for p in svc.get("ports", [])), name
        else:
            assert "ports" not in svc, name


def test_redes(compose, services):
    nets = compose["networks"]
    for name in ("edge", "internal", "runner", "egress"):
        assert nets[name]["internal"] is True, name
    assert (
        nets["egress"]["name"] == "nave-egress"
    )  # el runner conecta aquí los contenedores de fetch
    assert not nets["public"].get("internal")
    on_public = {n for n, s in checked(services).items() if "public" in _networks(s)}
    assert on_public == {"frontend", "egress-proxy"}
    for name in ("postgres", "redis", "scheduler"):
        assert _networks(services[name]) == {"internal"}, name
    assert _networks(services["scan-runner"]) == {"runner"}
    assert _networks(services["api"]) == {"edge", "internal", "egress"}
    assert _networks(services["worker"]) == {"internal", "runner", "egress"}


def _networks(svc: dict) -> set[str]:
    nets = svc.get("networks", [])
    return set(nets if isinstance(nets, list) else nets.keys())


def test_reparto_de_secretos(compose, services):
    declared = set(compose["secrets"])
    for name, expected in SECRETS.items():
        assert set(services[name].get("secrets", [])) == expected, name
    used = set().union(*(set(s.get("secrets", [])) for s in services.values()))
    assert used <= declared


def test_ningun_secreto_en_el_entorno(services):
    for name, svc in services.items():
        for key, value in svc.get("environment", {}).items():
            if SECRET_NAME.search(key):
                assert key.endswith("_FILE"), f"{name}.{key}"
                assert str(value).startswith("/run/secrets/"), f"{name}.{key}"
            assert "@" not in str(value).split("://", 1)[-1].split("/")[0].replace(
                "@postgres", ""
            ).replace("@egress-proxy", "") or "://" not in str(value), (
                f"{name}.{key}: credenciales en una URL"
            )


def test_montajes(services):
    for name, svc in services.items():
        for volume in svc.get("volumes", []):
            text = str(volume)
            if name == "scan-runner" and text.endswith(":/var/run/engine.sock"):
                continue
            host_bind = text.startswith((".", "/", "$"))
            if host_bind:
                assert text.endswith(":ro"), f"{name}: {text}"
            assert "docker.sock" not in text or name == "scan-runner", name


def test_variables_coinciden_con_la_configuracion_del_backend(root, services):
    text = (root / "backend/src/nave/bootstrap/config.py").read_text(encoding="utf-8")

    def names(block: str) -> set[str]:
        body = text.split(f"{block}: Final = frozenset(", 1)[1].split("\n)\n", 1)[0]
        return set(re.findall(r'"(NAVE_[A-Z_]+)"', body))

    known, api_only, worker_only = (
        names("KNOWN_VARIABLES"),
        names("API_ONLY_VARIABLES"),
        names("WORKER_ONLY_VARIABLES"),
    )
    api_text = (root / "backend/src/nave/bootstrap/api_config.py").read_text(
        encoding="utf-8"
    )
    api_extra = set(re.findall(r'"(NAVE_[A-Z_]+)"', api_text))
    for service in ("api", "worker", "scheduler", "cli"):
        env = set(services[service].get("environment", {}))
        assert env <= known | api_extra, f"{service}: {sorted(env - known - api_extra)}"
    api_env = set(services["api"]["environment"])
    worker_env = set(services["worker"]["environment"])
    assert not (api_env & worker_only)
    assert not (worker_env & api_only)
    assert not (
        set(services["scheduler"].get("environment", {})) & (api_only | worker_only)
    )


def test_entorno_del_runner(root, services):
    text = (root / "runner/src/nave_runner/config.py").read_text(encoding="utf-8")
    known = set(
        re.findall(
            r'"([A-Z_]+)",?\n',
            text.split("KNOWN: Final = frozenset(", 1)[1].split("}", 1)[0],
        )
    )
    scanners = ["SEMGREP", "TRIVY"]
    allowed = known | {f"{p}_{s}" for p in ("IMAGE", "VERSION") for s in scanners}
    env = {
        k.removeprefix("NAVE_RUNNER_")
        for k in services["scan-runner"]["environment"]
        if k.startswith("NAVE_RUNNER_")
    }
    assert env <= allowed, sorted(env - allowed)
    assert (
        services["scan-runner"]["environment"]["NAVE_RUNNER_SCANNERS"]
        == "semgrep,trivy"
    )


def test_dependencias_de_arranque(services):
    assert (
        services["worker"]["depends_on"]["scan-runner"]["condition"]
        == "service_healthy"
    )
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_dev_override_no_cambia_el_endurecimiento(root):
    dev = yaml.safe_load((root / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    assert set(dev["services"]) == {"api"}
    for name, service in dev["services"].items():
        for key in ("read_only", "cap_drop", "ports", "privileged", "user", "security_opt"):
            assert key not in service, (name, key)


def test_proxy_de_salida_con_sonda_propia_y_config(services, root):
    proxy = services["egress-proxy"]
    assert proxy["healthcheck"]["test"] == ["CMD", "/healthcheck", "4750"]
    assert "--config-file=/etc/smokescreen/config.yaml" in proxy["command"]
    assert not any(c.startswith("--egress-acl-file") for c in proxy["command"])
    config = yaml.safe_load(
        (root / "deploy/egress-proxy/config.yaml").read_text(encoding="utf-8")
    )
    assert config["allow_missing_role"] is True
    assert config["acl_file"] == "/etc/smokescreen/acl.yaml"


def test_registro_de_gunicorn_sin_consulta(services):
    command = services["api"]["command"]
    fmt = next(c for c in command if c.startswith("--access-logformat="))
    assert "%(U)s" in fmt
    for leaked in ("%(r)s", "%(q)s", "%(f)s", "%(a)s"):
        assert leaked not in fmt
    assert (
        "--logger-class=nave.infrastructure.observability.gunicorn_logger.AccessLogger" in command
    )
