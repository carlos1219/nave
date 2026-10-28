"""Traducción de errores del driver (F7-17): una FK violada sigue siendo «no encontrado» para
el cliente, pero deja rastro con el nombre de la restricción para diagnosticar el servidor."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from nave.application.errors import ConcurrentModificationError, NotFoundError
from nave.infrastructure.persistence.errors import translate_database_error


def _error(sqlstate: str, constraint: str | None = None) -> DBAPIError:
    orig = SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint))
    return DBAPIError("SELECT secreto FROM t WHERE x = :p", {"p": "valor"}, orig)  # type: ignore[arg-type]


def test_foreign_key_violation_is_not_found_and_logs_the_constraint_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="nave.persistence"):
        translated = translate_database_error(_error("23503", "fk_scans_superseded_by_scans"))
    assert isinstance(translated, NotFoundError)
    (record,) = caplog.records
    assert record.__dict__["error"] == "fk_scans_superseded_by_scans"
    text = record.getMessage() + str(record.__dict__)
    assert "secreto" not in text
    assert "valor" not in text


def test_foreign_key_violation_without_diagnostics_still_translates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="nave.persistence"):
        assert isinstance(translate_database_error(_error("23503")), NotFoundError)
    assert caplog.records[0].__dict__["error"] == "unknown"


def test_unique_violation_stays_a_conflict_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="nave.persistence"):
        assert isinstance(translate_database_error(_error("23505")), ConcurrentModificationError)
    assert caplog.records == []
