# NAVE

**Nodo de Análisis y Verificación Estática** — plataforma centralizada de escaneo estático (SAST/SCA) integrada con GitHub.

La fuente de verdad del proyecto es [`CLAUDE.md`](CLAUDE.md). Léelo antes de tocar nada.

## Estado

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Diseño: threat model, ADRs, puertos, modelo de datos, despliegue | COMPLETADA |
| 1 | Dominio + aplicación | COMPLETADA |
| 2 | Infraestructura de escaneo (sandbox, Semgrep, Trivy, SARIF) | COMPLETADA (flags de Trivy pendientes de validar en fase 6, RR-12) |
| 3 | Persistencia + Celery | COMPLETADA (persistencia, Celery, tareas periódicas) |
| 4 | API + GitHub | PENDIENTE |
| 5 | Frontend | PENDIENTE |
| 6 | Despliegue endurecido, CI/CD, SBOM, firma | PENDIENTE |
| 7 | Revisión final de seguridad | PENDIENTE |

## Mapa del repositorio

| Ruta | Contenido |
|---|---|
| `docs/adr/` | Architecture Decision Records (`NNNN-titulo.md`); índice en `docs/adr/README.md` |
| `docs/threat-model.md` | Modelo de amenazas STRIDE |
| `docs/architecture/` | Puertos y contratos, modelo de datos, despliegue |
| `docs/runbooks/` | Procedimientos operativos |
| `backend/src/nave/` | Backend hexagonal: `domain`, `application`, `infrastructure`, `delivery`, `bootstrap` |
| `backend/tests/` | `unit`, `integration`, `contract`, `security`, `e2e` |
| `runner/` | `scan-runner` (stdlib, protocolo v1, ADR 0012) |
| `deploy/scanner-sandbox/` | Imágenes de fetch, Semgrep y Trivy; path guard del fetcher |
| `contracts/` | Vectores del protocolo worker ↔ runner compartidos por ambas implementaciones |
| `frontend/src/` | Vue 3 con Feature-Sliced Design |
| `runner/` | `scan-runner`: único componente con acceso al motor de contenedores (ADR 0002) |
| `deploy/` | Nginx e imagen del sandbox de escáneres |
| `.github/workflows/` | CI |

Los ficheros que faltan respecto a §6 (`pyproject.toml`, `importlinter.ini`, `package.json`, `vite.config.ts`, `docker-compose.yml`, `nginx.conf`, Dockerfile del sandbox, workflows) se crean en la fase que les corresponde, no antes.
