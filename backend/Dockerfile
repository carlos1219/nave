# Imagen única del backend: api, worker, scheduler y migraciones (ADR 0022). Cambia solo el
# comando. PYTHON_IMAGE se pasa fijada por digest y sin valor por defecto (CLAUDE.md §15).
ARG PYTHON_IMAGE

FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
# Solo paquetes con hash verificado; nada se resuelve en la compilación.
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
