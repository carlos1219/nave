"""RR-11: la purga la hace una función SQL; el rol de mantenimiento solo puede ejecutarla."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from nave.application.dto.maintenance import PurgeProgress
from nave.application.errors import PurgeRefusedError
from nave.application.use_cases.purge_uninstalled_organizations import PurgeUninstalledOrganizations
from nave.domain.value_objects.identifiers import OrganizationId
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.maintenance import SqlOrganizationPurgeStore
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.memory import FakeClock
from tests.integration.persistence.seed import (
    MAIN,
    ORG,
    REPO,
    at,
    new_finding,
    new_scan,
    seed_tenancy,
)

OTHER_ORG = OrganizationId(UUID(int=0x0AA))
OTHER_REPO_ID = UUID(int=0x0BB)


def count(engine: Engine, table: str, **where: object) -> int:
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    with engine.connect() as conn:
        return int(
            conn.execute(text(f"SELECT count(*) FROM {table} WHERE {clause}"), where).scalar_one()  # noqa: S608
        )


def populate(engine: Engine) -> None:
    """ORG con escaneos, hallazgos y triage; y OTHER_ORG activa con sus propios datos."""
    seed_tenancy(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(t.organizations).values(
                id=OTHER_ORG.value, github_installation_id=43, account_login="beta", status="ACTIVE"
            )
        )
        conn.execute(
            insert(t.repositories).values(
                id=OTHER_REPO_ID,
                organization_id=OTHER_ORG.value,
                github_repo_id=900,
                owner="beta",
                name="svc",
                default_branch="main",
                enabled=True,
            )
        )
    uow = SqlUnitOfWorkFactory(engine)
    first, second = new_scan(1), new_scan(2)
    with uow() as tx:
        tx.scans.add(first)
        tx.scans.add(second)
        tx.findings.add(new_finding(1, scan=first.id))
        tx.commit()
    with engine.begin() as conn:
        conn.execute(
            t.scans.update()
            .where(t.scans.c.id == first.id.value)
            .values(superseded_by=second.id.value)
        )
        conn.execute(
            insert(t.scan_logs).values(
                scan_id=first.id.value, seq=1, at=at(1), level="INFO", message="x"
            )
        )
        conn.execute(
            insert(t.triage_decisions).values(
                id=UUID(int=7),
                finding_id=new_finding(1, scan=first.id).id.value,
                state="OPEN",
                decided_at=at(1),
                decided_by=None,
                justification="Not observed in the latest scan.",
            )
        )


def uninstall(engine: Engine, org: OrganizationId, days: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE organizations SET status = 'UNINSTALLED', "
                "uninstalled_at = now() - make_interval(secs => :s) WHERE id = :id"
            ),
            {"s": days * 86400, "id": org.value},
        )


class TestFunction:
    def test_purges_everything_of_the_organization_and_only_that(self, engine: Engine) -> None:
        populate(engine)
        uninstall(engine, ORG, 31)
        store = SqlOrganizationPurgeStore(engine)
        steps: list[PurgeProgress] = []
        while not steps or not steps[-1].done:
            steps.append(store.purge_batch(ORG))
            assert len(steps) < 20
        for table in ("repositories", "scans", "findings", "memberships"):
            col = (
                "id"
                if table == "organizations"
                else "organization_id"
                if table in ("repositories", "memberships")
                else None
            )
            if col:
                assert count(engine, table, **{col: ORG.value}) == 0
        assert count(engine, "organizations", id=ORG.value) == 0
        assert count(engine, "scan_logs") == 0
        assert count(engine, "triage_decisions") == 0
        assert count(engine, "finding_occurrences") == 0
        # La otra organización, intacta; los usuarios son globales y se conservan.
        assert count(engine, "organizations", id=OTHER_ORG.value) == 1
        assert count(engine, "repositories", organization_id=OTHER_ORG.value) == 1
        assert count(engine, "users") == 2

    def test_audit_log_survives(self, engine: Engine) -> None:
        populate(engine)
        factory = SqlUnitOfWorkFactory(engine)
        uninstall(engine, ORG, 31)
        use_case = PurgeUninstalledOrganizations(
            uow_factory=factory, store=SqlOrganizationPurgeStore(engine), clock=_Now()
        )
        report = use_case.execute()
        assert (report.purged, report.refused) == (1, 0)
        with engine.connect() as conn:
            actions = [
                r.action
                for r in conn.execute(select(t.audit_log.c.action).order_by(t.audit_log.c.id))
            ]
        assert actions == ["organization.purge_requested", "organization.purged"]

    def test_small_batches_make_progress_and_resume(self, engine: Engine) -> None:
        populate(engine)
        uninstall(engine, ORG, 40)
        with engine.begin() as conn:
            first = conn.execute(
                text("SELECT public.nave_purge_organization(:o, 1)"), {"o": ORG.value}
            ).scalar_one()
        assert first["done"] is False
        assert count(engine, "organizations", id=ORG.value) == 1

    def test_unknown_organization_is_a_noop(self, engine: Engine) -> None:
        populate(engine)
        progress = SqlOrganizationPurgeStore(engine).purge_batch(OrganizationId(UUID(int=0xDEAD)))
        assert progress.done is True
        assert progress.deleted == 0


class TestTheDatabaseDecides:
    @pytest.mark.parametrize("days", [0, 1, 29.9])
    def test_refuses_inside_the_grace_period(self, engine: Engine, days: float) -> None:
        populate(engine)
        uninstall(engine, ORG, days)
        with pytest.raises(PurgeRefusedError):
            SqlOrganizationPurgeStore(engine).purge_batch(ORG)
        assert count(engine, "findings") == 1

    def test_refuses_active_and_suspended_organizations(self, engine: Engine) -> None:
        populate(engine)
        store = SqlOrganizationPurgeStore(engine)
        with pytest.raises(PurgeRefusedError):
            store.purge_batch(ORG)
        with engine.begin() as conn:
            conn.execute(text("UPDATE organizations SET status = 'SUSPENDED'"))
        with pytest.raises(PurgeRefusedError):
            store.purge_batch(ORG)
        assert count(engine, "findings") == 1

    def test_the_application_cannot_override_the_period(self, engine: Engine) -> None:
        populate(engine)
        uninstall(engine, ORG, 5)
        with pytest.raises(DBAPIError), engine.begin() as conn:
            conn.execute(text("SELECT public.nave_purge_organization(:o, 1000)"), {"o": ORG.value})
        assert count(engine, "organizations", id=ORG.value) == 1


class TestPrivileges:
    def test_maintenance_can_execute_but_has_no_delete_on_business_tables(
        self, engine: Engine
    ) -> None:
        populate(engine)
        uninstall(engine, ORG, 31)
        for table in (
            "findings",
            "triage_decisions",
            "repositories",
            "organizations",
            "memberships",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
                conn.execute(text("SET LOCAL ROLE nave_maintenance"))
                conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_maintenance"))
            conn.execute(text("SELECT public.nave_purge_organization(:o, 1)"), {"o": ORG.value})

    def test_the_app_role_cannot_execute_it(self, engine: Engine) -> None:
        populate(engine)
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_app"))
            conn.execute(text("SELECT public.nave_purge_organization(:o)"), {"o": ORG.value})

    def test_public_has_no_execute_privilege(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT has_function_privilege('public', "
                    "'nave_purge_organization(uuid, integer)', 'EXECUTE')"
                )
            ).scalar_one()
        assert row is False

    def test_the_function_pins_its_search_path(self, engine: Engine) -> None:
        with engine.connect() as conn:
            config = conn.execute(
                text("SELECT proconfig FROM pg_proc WHERE proname = 'nave_purge_organization'")
            ).scalar_one()
            secdef = conn.execute(
                text("SELECT prosecdef FROM pg_proc WHERE proname = 'nave_purge_organization'")
            ).scalar_one()
        assert secdef is True
        assert any(c.startswith("search_path=") for c in config)

    def test_candidates_list_only_organizations_past_the_period(self, engine: Engine) -> None:
        populate(engine)
        store = SqlOrganizationPurgeStore(engine)
        clock = FakeClock()
        with engine.connect() as conn:
            now = conn.execute(text("SELECT now()")).scalar_one()
        assert store.candidates(now - timedelta(days=30), 10) == []
        uninstall(engine, ORG, 31)
        uninstall(engine, OTHER_ORG, 10)
        found = store.candidates(now - timedelta(days=30), 10)
        assert [c.organization_id for c in found] == [ORG]
        del clock


class _Now:
    def now(self):  # type: ignore[no-untyped-def]
        return datetime.now(UTC)


MAIN_REF = MAIN
REPO_ID = REPO
