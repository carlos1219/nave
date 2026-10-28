"""Entorno de Alembic. Solo modo online: las migraciones se aplican contra la base real
con un rol de migración (`nave_migrator`), nunca con el rol de la aplicación."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from nave.infrastructure.persistence.schema import metadata

config = context.config


def _database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get(
        "NAVE_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("set NAVE_DATABASE_URL or pass -x url=...")
    return url


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
