"""Garantías de seguridad que viven en la base de datos (AM-103, AM-305, AM-306)."""

import threading
from datetime import timedelta
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import URL, Engine, create_engine, insert, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

from nave.application.dto.audit import AuditEntry
from nave.application.dto.views import LogLevel
from nave.application.errors import PersistenceUnavailableError
from nave.domain.errors import InvalidFindingTextError
from nave.domain.events import ScanQueued
from nave.domain.value_objects.trigger import ScanTrigger
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.audit import GENESIS_HASH, verify_audit_chain
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.integration.persistence.seed import (
    MAIN,
    ORG,
    REPO,
    TRIAGER,
    at,
    new_finding,
    new_scan,
    seed_tenancy,
)

pytestmark = pytest.mark.integration


def _entry(n: int) -> AuditEntry:
    return AuditEntry(
        occurred_at=at(n),
        action="finding.triaged",
        actor=TRIAGER,
        organization_id=ORG,
        target_type="finding",
        target_id=f"f-{n}",
        details={"state": "FALSE_POSITIVE"},
    )


def test_schema_matches_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), t.metadata)
    assert diff == []


class TestAppendOnly:
    def test_audit_log_cannot_be_modified_even_by_owner(self, engine: Engine) -> None:
        uow = SqlUnitOfWorkFactory(engine)
        with uow() as tx:
            tx.audit.append(_entry(1))
            tx.commit()
        for statement in (
            "UPDATE audit_log SET action = 'x.y'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
                conn.execute(text(statement))

    def test_triage_decisions_cannot_be_rewritten(self, engine: Engine) -> None:
        seed_tenancy(engine)
        scan = new_scan()
        finding = new_finding(1, scan=scan.id)
        with engine.begin() as conn:
            conn.execute(
                insert(t.scans).values(
                    id=scan.id.value,
                    repository_id=REPO.value,
                    commit_sha=scan.commit.value,
                    ref=MAIN.value,
                    trigger="MANUAL",
                    trigger_key="manual:1",
                    status="QUEUED",
                    queued_at=at(0),
                    requested_by=TRIAGER.value,
                    version=0,
                )
            )
        uow = SqlUnitOfWorkFactory(engine)
        with uow() as tx:
            tx.findings.add(finding)
            tx.commit()
        with engine.begin() as conn:
            conn.execute(
                insert(t.triage_decisions).values(
                    id=UUID(int=1),
                    finding_id=finding.id.value,
                    state="OPEN",
                    decided_at=at(1),
                    decided_by=None,
                    justification="Not observed in the latest scan.",
                )
            )
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(update(t.triage_decisions).values(state="FIXED"))


class TestRuntimeRoleGrants:
    @pytest.mark.parametrize(
        ("role", "statement"),
        [
            ("nave_app", "UPDATE audit_log SET action = 'x.y'"),
            ("nave_app", "DELETE FROM triage_decisions"),
            ("nave_app", "DELETE FROM scans"),
            ("nave_app", "CREATE TABLE evil (id int)"),
            ("nave_maintenance", "SELECT * FROM audit_log"),
            ("nave_maintenance", "INSERT INTO scan_logs SELECT * FROM scan_logs"),
        ],
    )
    def test_least_privilege(self, engine: Engine, role: str, statement: str) -> None:
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            conn.execute(text(statement))

    def test_app_role_can_do_its_job(self, engine: Engine) -> None:
        seed_tenancy(engine)
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_app"))
            conn.execute(text("SELECT count(*) FROM scans"))
            conn.execute(
                insert(t.audit_log).values(
                    occurred_at=at(0),
                    action="a.b",
                    target_type="x",
                    target_id="y",
                    details={},
                    prev_hash=GENESIS_HASH,
                    hash="f" * 64,
                )
            )
            conn.execute(text("DELETE FROM memberships"))


class TestAuditChain:
    def test_chain_is_verifiable_and_detects_tampering(self, engine: Engine) -> None:
        uow = SqlUnitOfWorkFactory(engine)
        for n in range(3):
            with uow() as tx:
                tx.audit.append(_entry(n))
                tx.commit()
        with engine.connect() as conn:
            assert verify_audit_chain(conn) is None
            second = conn.execute(select(t.audit_log.c.id).order_by(t.audit_log.c.id)).all()[1].id
        with engine.begin() as conn:
            # Un atacante con privilegios de superusuario desactiva el trigger y reescribe.
            conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            conn.execute(
                text("UPDATE audit_log SET target_id = 'forged' WHERE id = :id"), {"id": second}
            )
            conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
        with engine.connect() as conn:
            assert verify_audit_chain(conn) == second

    def test_concurrent_appends_never_fork_the_chain(self, engine: Engine) -> None:
        uow = SqlUnitOfWorkFactory(engine)
        errors: list[BaseException] = []

        def writer(base: int) -> None:
            try:
                for n in range(5):
                    with uow() as tx:
                        tx.audit.append(_entry(base + n))
                        tx.commit()
            except BaseException as error:  # noqa: BLE001 - se reporta en el hilo principal
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        with engine.connect() as conn:
            assert conn.execute(select(t.audit_log.c.id)).all().__len__() == 20
            assert verify_audit_chain(conn) is None


def test_concurrent_log_appends_get_unique_sequences(engine: Engine) -> None:
    seed_tenancy(engine)
    uow = SqlUnitOfWorkFactory(engine)
    scan = new_scan()
    with uow() as tx:
        tx.scans.add(scan)
        tx.commit()

    def writer(base: int) -> None:
        for n in range(5):
            with uow() as tx:
                tx.scan_logs.append(scan.id, LogLevel.INFO, f"line {base + n}", at(n))
                tx.commit()

    threads = [threading.Thread(target=writer, args=(i * 10,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with uow() as tx:
        entries = tx.scan_logs.list(scan.id, after_seq=0, limit=100)
    assert [e.seq for e in entries] == list(range(1, 21))


def test_corrupt_row_fails_on_load(engine: Engine) -> None:
    seed_tenancy(engine)
    uow = SqlUnitOfWorkFactory(engine)
    scan = new_scan()
    finding = new_finding(1, scan=scan.id)
    with uow() as tx:
        tx.scans.add(scan)
        tx.findings.add(finding)
        tx.commit()
    with engine.begin() as conn:
        conn.execute(update(t.findings).values(title="evil\N{RIGHT-TO-LEFT OVERRIDE}title"))
    with pytest.raises(InvalidFindingTextError), uow() as tx:
        tx.findings.get(finding.id)


class TestOutbox:
    def test_events_exist_only_if_the_transaction_commits(self, engine: Engine) -> None:
        seed_tenancy(engine)
        uow = SqlUnitOfWorkFactory(engine)
        scan = new_scan()
        event = ScanQueued(
            occurred_at=at(0),
            scan_id=scan.id,
            repository_id=REPO,
            commit=scan.commit,
            ref=MAIN,
            trigger=ScanTrigger.MANUAL,
        )
        with uow() as tx:
            tx.events.publish([event])
        with uow() as tx:
            tx.events.publish([])
            tx.events.publish([event])
            tx.commit()
        with engine.connect() as conn:
            rows = conn.execute(select(t.outbox_events)).all()
        assert len(rows) == 1
        assert rows[0].type == "ScanQueued"
        assert rows[0].payload == {
            "occurred_at": at(0).isoformat(),
            "scan_id": str(scan.id.value),
            "repository_id": str(REPO.value),
            "commit": scan.commit.value,
            "ref": MAIN.value,
            "trigger": "MANUAL",
        }
        assert rows[0].published_at is None


class TestErrorTranslation:
    def test_statement_timeout_is_transient(self, database_url: URL) -> None:
        engine = create_database_engine(database_url, statement_timeout=timedelta(milliseconds=50))
        try:
            uow = SqlUnitOfWorkFactory(engine)
            with pytest.raises(PersistenceUnavailableError), uow() as tx:
                tx.scans._conn.execute(text("SELECT pg_sleep(1)"))
        finally:
            engine.dispose()

    def test_unreachable_database_is_transient(self, database_url: URL) -> None:
        engine = create_database_engine(database_url.set(port=1))
        try:
            with pytest.raises(PersistenceUnavailableError), SqlUnitOfWorkFactory(engine)():
                pass
        finally:
            engine.dispose()

    def test_programming_errors_are_not_masked(self, engine: Engine) -> None:
        with pytest.raises(ProgrammingError), SqlUnitOfWorkFactory(engine)() as tx:
            tx.scans._conn.execute(text("SELECT * FROM no_such_table"))

    def test_engine_rejects_other_drivers(self, database_url: URL) -> None:
        with pytest.raises(ValueError, match="only"):
            create_database_engine(database_url.set(drivername="postgresql+psycopg2"))
        with pytest.raises(ValueError, match="positive"):
            create_database_engine(database_url, statement_timeout=timedelta(0))
        create_engine(database_url).dispose()
