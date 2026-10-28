"""Traducción de errores de la UnitOfWork en los caminos difíciles de provocar con un
PostgreSQL real: fallo al abrir, al hacer commit y al deshacer con la conexión rota."""

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from nave.application.errors import ConcurrentModificationError, PersistenceUnavailableError
from nave.infrastructure.persistence.errors import translated_errors
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWork


class _OrigError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("driver error")
        self.sqlstate = sqlstate


def _error(kind: type[Any], sqlstate: str | None = None) -> Any:
    return kind("SELECT secret", {"p": "secret"}, _OrigError(sqlstate))


class _Tx:
    def __init__(self, *, commit_error: Exception | None, rollback_error: Exception | None) -> None:
        self.is_active = True
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    def commit(self) -> None:
        if self._commit_error is not None:
            raise self._commit_error
        self.is_active = False

    def rollback(self) -> None:
        if self._rollback_error is not None:
            raise self._rollback_error
        self.is_active = False


class _Conn:
    def __init__(self, tx: _Tx, begin_error: Exception | None) -> None:
        self._tx = tx
        self._begin_error = begin_error
        self.closed = False

    def begin(self) -> _Tx:
        if self._begin_error is not None:
            raise self._begin_error
        return self._tx

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(
        self,
        *,
        begin_error: Exception | None = None,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.conn = _Conn(
            _Tx(commit_error=commit_error, rollback_error=rollback_error), begin_error
        )

    def connect(self) -> _Conn:
        return self.conn


def _uow(engine: _Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)  # type: ignore[arg-type]


def test_unexpected_error_on_open_is_not_masked() -> None:
    engine = _Engine(begin_error=_error(ProgrammingError, "42601"))
    with pytest.raises(ProgrammingError), _uow(engine):
        pass
    assert engine.conn.closed


def test_conflict_on_commit_is_translated() -> None:
    engine = _Engine(commit_error=_error(IntegrityError, "23505"))
    with pytest.raises(ConcurrentModificationError) as excinfo, _uow(engine) as uow:
        uow.commit()
    assert "secret" not in str(excinfo.value)


def test_unexpected_error_on_commit_is_not_masked() -> None:
    engine = _Engine(commit_error=_error(ProgrammingError, "42601"))
    with pytest.raises(ProgrammingError), _uow(engine) as uow:
        uow.commit()


def test_broken_rollback_does_not_hide_the_original_error() -> None:
    engine = _Engine(rollback_error=_error(OperationalError))
    with pytest.raises(PersistenceUnavailableError), _uow(engine):
        raise _error(OperationalError)
    assert engine.conn.closed


def test_non_database_errors_pass_through() -> None:
    with pytest.raises(KeyError), _uow(_Engine()):
        raise KeyError("x")


class TestTranslatedErrors:
    def test_known_driver_errors_become_application_errors_without_leaking_sql(self) -> None:
        with pytest.raises(PersistenceUnavailableError) as raised, translated_errors():
            raise _error(OperationalError)
        assert "secret" not in str(raised.value)
        with pytest.raises(ConcurrentModificationError), translated_errors():
            raise _error(IntegrityError, "23505")

    def test_programming_errors_and_foreign_exceptions_propagate_unchanged(self) -> None:
        with pytest.raises(ProgrammingError), translated_errors():
            raise _error(ProgrammingError, "42P01")
        with pytest.raises(ZeroDivisionError), translated_errors():
            raise ZeroDivisionError
