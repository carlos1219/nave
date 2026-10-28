"""Infraestructura de tests de integración contra PostgreSQL real.

`NAVE_TEST_DATABASE_URL` apunta a un servidor de pruebas con un rol que puede crear
bases y roles (nunca a una base con datos reales). La sesión migra una base plantilla
una vez y cada test recibe un clon propio (`CREATE DATABASE ... TEMPLATE`), de modo que
los tests no comparten estado y el audit log empieza vacío en cada uno.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.pool import NullPool

from nave.infrastructure.persistence.engine import create_database_engine

_ENV = "NAVE_TEST_DATABASE_URL"
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RUNTIME_ROLES = ("nave_app", "nave_maintenance")


def _admin_url() -> URL:
    raw = os.environ.get(_ENV)
    if not raw:
        pytest.skip(f"{_ENV} is not set: integration tests need a PostgreSQL test server")
    return make_url(raw)


def _admin_engine(url: URL) -> Engine:
    # NullPool: cada conexión se cierra al soltarla; ninguna queda viva entre tests.
    return create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )


@pytest.fixture(scope="session")
def template_database() -> Iterator[URL]:
    url = _admin_url()
    admin = _admin_engine(url)
    name = f"nave_it_template_{uuid.uuid4().hex[:8]}"
    try:
        with admin.connect() as conn:
            for role in _RUNTIME_ROLES:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                )
                if exists.first() is None:
                    conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        template_url = url.set(database=name)
        _migrate(template_url)
        yield template_url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _migrate(url: URL) -> None:
    config = Config(os.path.join(_BACKEND, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(_BACKEND, "alembic"))
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


@pytest.fixture
def database_url(template_database: URL) -> Iterator[URL]:
    admin = _admin_engine(template_database)
    name = f"nave_it_{uuid.uuid4().hex[:12]}"
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_database.database}"'))
        yield template_database.set(database=name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(database_url: URL) -> Iterator[Engine]:
    engine = create_database_engine(database_url)
    yield engine
    engine.dispose()
