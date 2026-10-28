# nave-backend

Requisitos: Python 3.12.

## Preparar el entorno

Windows (PowerShell):

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
```

Linux / macOS:

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
```

## Base de datos

Las migraciones (Alembic) se aplican con el rol de migración, nunca con el de la aplicación. La URL se pasa por variable de entorno, nunca en `alembic.ini`:

```bash
NAVE_DATABASE_URL="postgresql+psycopg://nave_migrator:<contraseña>@host:5432/nave" alembic upgrade head
```

Los tests de integración necesitan un **servidor PostgreSQL de pruebas**, nunca uno con datos reales. Cada test crea y borra su propia base. Sin la variable, se saltan:

```bash
NAVE_TEST_DATABASE_URL="postgresql+psycopg://<rol de pruebas>:<contraseña>@127.0.0.1:5432/postgres" pytest
```

En Windows lo más sencillo es un contenedor desechable: `docker run --rm -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16`.

## Comprobaciones (las mismas que bloquearán el merge en CI)

```bash
ruff format --check src tests
ruff check src tests
mypy src tests
lint-imports --config importlinter.ini
pytest --cov
```

## Dependencias

De runtime (solo en `infrastructure`; `domain` y `application` usan únicamente la stdlib):

| Paquete | Para qué | Por qué este |
|---|---|---|
| `sqlalchemy` | Acceso a PostgreSQL (Core, sin ORM) | Estándar de facto, consultas siempre parametrizadas, licencia MIT |
| `alembic` | Migraciones revisables y reversibles (§7) | Del mismo proyecto que SQLAlchemy, MIT |
| `psycopg[binary]` | Driver de PostgreSQL | Driver oficial moderno, tipado, LGPL. La variante `binary` incluye libpq; en la imagen de producción (fase 6) se valorará compilar contra la libpq del sistema para recibir sus parches de seguridad con el sistema base |
| `celery` | Cola de tareas asíncronas (§10) | Estándar de facto en Python, BSD-3. Gran árbol transitivo (kombu, billiard, vine, amqp, click…), todo fijado con hashes. Se usa solo JSON, sin backend de resultados ni eventos (ADR 0010) |
| `redis` | Cliente de Redis para el transporte de Celery y la comprobación de longitud de cola | Cliente oficial, MIT |
| `cryptography` | Firma RS256 del JWT de la GitHub App (ADR 0014); Authlib (ADR 0004) también la exige | Estándar de facto, Apache-2.0/BSD, wheels precompiladas. Solo se usa para *firmar*; la verificación de JWT la hace GitHub |
| `flask` | API HTTP y webhook (CLAUDE.md §3, ADR 0015) | Mínima y estándar; sin extensiones: validación con Pydantic, autenticación propia |
| `gunicorn` | Servidor WSGI de producción de la API | Evita el servidor de desarrollo de Werkzeug; workers sync/gthread, sin estado compartido |
| `joserfc` | Validación de firma y claims del ID token OIDC (ADR 0016) | Capa JOSE mantenida por el autor de Authlib; evita analizar JWT a mano. Solo algoritmos asimétricos |
| `pydantic` | Esquemas estrictos de entrada (webhook, API) y JSON Schema para OpenAPI (CLAUDE.md §3) | Validación estricta con tipos; solo en `delivery` |

Lockfiles con hashes: `requirements-dev.lock` (desarrollo y CI) y `requirements.lock` (solo runtime, para la imagen Docker).

- Versiones exactas en `pyproject.toml` y lockfile con hashes en `requirements-dev.lock`.
- Solo se aceptan versiones publicadas hace al menos dos semanas (`--exclude-newer`), para no adoptar una versión comprometida antes de que se detecte.
- Para regenerar el lockfile, sustituye `<fecha>` por la de hace dos semanas (`AAAA-MM-DD`):

```bash
uv pip compile -p 3.12 --exclude-newer <fecha> --extra dev --generate-hashes --universal pyproject.toml -o requirements-dev.lock
```


## Configuración (variables de entorno)

Todas empiezan por `NAVE_`; una desconocida impide arrancar (un typo en un límite no debe pasar por valor por defecto). Los secretos solo se aceptan como ruta a fichero (`*_FILE`, Docker secrets): una contraseña dentro de una URL o variable se rechaza.

| Variable | Defecto | Quién la necesita |
|---|---|---|
| `NAVE_DATABASE_URL`, `NAVE_DATABASE_PASSWORD_FILE` | — | api, worker (rol `nave_app`) |
| `NAVE_MAINTENANCE_DATABASE_URL`, `NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE` | — | worker (rol `nave_maintenance`, ADR 0011) |
| `NAVE_REDIS_URL`, `NAVE_REDIS_PASSWORD_FILE` | — | api, worker, scheduler |
| `NAVE_FETCH_TIMEOUT_SECONDS` / `NAVE_SCAN_TIMEOUT_SECONDS` | 300 / 600 | worker |
| `NAVE_SCAN_TASK_SOFT_SECONDS` / `_HARD_SECONDS` | fetch + 2 escáneres + 2 min / +1 min | worker |
| `NAVE_FETCH_MAX_BYTES`, `NAVE_FETCH_MAX_FILES` | 512 MiB, 200 000 | worker |
| `NAVE_SCANNER_MAX_OUTPUT_BYTES`, `NAVE_SCANNER_MAX_RESULTS` | 256 MiB, 50 000 | worker |
| `NAVE_MAX_SARIF_FINDINGS` | 10 000 | worker |
| `NAVE_QUEUE_MAX_DEPTH` | 1000 | api, worker |
| `NAVE_TASK_MAX_RETRIES`, `NAVE_WORKER_MAX_TASKS_PER_CHILD` | 5, 25 | worker |
| `NAVE_RETENTION_WEBHOOK_DELIVERIES_DAYS`, `_SCAN_LOGS_DAYS`, `_OUTBOX_DAYS`, `_SCANS_DAYS` | 30, 30, 7, 365 | worker |
| `NAVE_SWEEP_REQUEUE_AFTER_SECONDS`, `_QUEUE_EXPIRE_AFTER_SECONDS`, `_RUNNING_STUCK_AFTER_SECONDS` | 600, 3600, 2 × límite duro | worker |
| `NAVE_LOG_LEVEL` | `INFO` | todos |

El `scheduler` solo lee `NAVE_REDIS_*` (y los límites que comparte para construir la app); no recibe credenciales de base de datos.
