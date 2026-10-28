"""Consultas del ADR 0029 (PR, cola de triage, fallidos, escáneres y exportación) sobre
PostgreSQL real."""

from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import Engine

from nave.application.dto.export import ExportView
from nave.application.dto.insights import FindingFilters, QueueCursor
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingMessage
from nave.domain.value_objects.identifiers import FindingId, OrganizationId
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.tool_run import ToolRun
from nave.domain.value_objects.triage_state import TriageState
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import encode_summary
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.integration.persistence.seed import (
    FEATURE,
    ORG,
    OTHER_REPO,
    REPO,
    at,
    new_finding,
    new_scan,
    scan_id,
    seed_tenancy,
)

pytestmark = pytest.mark.integration
STRANGER = OrganizationId(UUID(int=0xFF))
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    seed_tenancy(engine)
    return SqlUnitOfWorkFactory(engine)


def finding_id(n: int) -> UUID:
    return UUID(int=0xF000 + n)


def finish(
    engine: Engine, n: int, *, status: str = "COMPLETED", minute: int = 1, **extra: object
) -> None:
    values: dict[str, object] = {
        "status": status,
        "started_at": at(minute - 1),
        "finished_at": at(minute),
    }
    if status in {"FAILED", "TIMED_OUT"}:
        code = "SCANNER_FAILED" if status == "FAILED" else "TIMEOUT"
        values |= {"status_reason_code": code, "status_reason_detail": "exit 2"}
    with engine.begin() as conn:
        conn.execute(
            t.scans.update().where(t.scans.c.id == scan_id(n).value).values(**values, **extra)
        )


class TestPullRequests:
    def test_latest_scan_of_each_pull_request(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with uow() as tx:
            for n in range(1, 5):
                tx.scans.add(new_scan(n, ref=FEATURE, minute=n))
            tx.commit()
        with engine.begin() as conn:
            # `new_scan` crea escaneos de la PR 7; el 4 se deja como escaneo de rama.
            for n, number in ((1, 7), (2, 7), (3, 8), (4, None)):
                conn.execute(
                    t.scans.update()
                    .where(t.scans.c.id == scan_id(n).value)
                    .values(pull_request_number=number)
                )
        with uow() as tx:
            found = tx.insights.pull_request_scans(ORG, limit=10)
            assert tx.insights.pull_request_scans(STRANGER, limit=10) == []
        assert [(a.scan.id, a.scan.pull_request_number) for a in found] == [
            (scan_id(3), 8),
            (scan_id(2), 7),
        ]


class TestTriageQueue:
    def seed(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        scan = new_scan(1)
        with uow() as tx:
            tx.scans.add(scan)
            tx.findings.add(new_finding(1, scan=scan.id, severity=Severity.HIGH, minute=5))
            tx.findings.add(new_finding(2, scan=scan.id, severity=Severity.CRITICAL, minute=9))
            tx.findings.add(new_finding(3, scan=scan.id, severity=Severity.HIGH, minute=1))
            tx.findings.add(
                new_finding(
                    4, scan=scan.id, severity=Severity.HIGH, repository=OTHER_REPO, minute=5
                )
            )
            tx.findings.add(new_finding(5, scan=scan.id, severity=Severity.LOW))
            tx.findings.record_occurrences(
                [
                    FindingOccurrence(
                        scan_id=scan.id,
                        finding_id=FindingId(finding_id(1)),
                        location=None,
                        message=FindingMessage("m"),
                        snippet=None,
                        is_new=True,
                    )
                ]
            )
            tx.commit()
        with engine.begin() as conn:
            conn.execute(
                t.findings.update()
                .where(t.findings.c.id == finding_id(5))
                .values(triage_state=TriageState.FIXED.value)
            )

    def test_orders_by_severity_then_age_and_pages_by_cursor(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        self.seed(uow, engine)
        empty = FindingFilters()
        with uow() as tx:
            everything = tx.insights.triage_queue(ORG, empty, after=None, limit=10)
            assert tx.insights.triage_queue(STRANGER, empty, after=None, limit=10) == []
        ids = [r.finding.id.value for r in everything]
        assert ids == [finding_id(2), finding_id(3), finding_id(1), finding_id(4)]
        assert everything[2].occurrence is not None
        paged: list[UUID] = []
        after: QueueCursor | None = None
        while True:
            with uow() as tx:
                page = tx.insights.triage_queue(ORG, empty, after=after, limit=1)
            if not page:
                break
            last = page[0].finding
            paged.append(last.id.value)
            after = QueueCursor(
                rank=last.severity.rank, first_seen_at=last.first_seen_at, finding_id=last.id
            )
        assert paged == ids

    def test_filters_and_a_foreign_repository_yields_nothing(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        self.seed(uow, engine)
        with uow() as tx:
            only_repo = tx.insights.triage_queue(
                ORG, FindingFilters(repository_id=OTHER_REPO), after=None, limit=10
            )
            critical = tx.insights.triage_queue(
                ORG, FindingFilters(severities=frozenset({Severity.CRITICAL})), after=None, limit=10
            )
            foreign = tx.insights.triage_queue(
                STRANGER, FindingFilters(repository_id=REPO), after=None, limit=10
            )
        assert [r.finding.id.value for r in only_repo] == [finding_id(4)]
        assert [r.finding.id.value for r in critical] == [finding_id(2)]
        assert foreign == []


class TestFailedAndCompleted:
    def test_failed_scans_know_whether_a_later_scan_recovered(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with uow() as tx:
            for n in range(1, 5):
                tx.scans.add(
                    new_scan(n, minute=n, ref=FEATURE) if n == 4 else new_scan(n, minute=n)
                )
            tx.commit()
        finish(engine, 1, status="FAILED", minute=2)
        finish(engine, 2, minute=3)
        finish(engine, 3, status="TIMED_OUT", minute=4)
        finish(engine, 4, minute=5)
        with uow() as tx:
            failed = tx.insights.failed_scans(ORG, limit=10)
            assert tx.insights.failed_scans(STRANGER, limit=10) == []
        assert [(f.scan.id, f.recovered) for f in failed] == [
            (scan_id(3), False),
            (scan_id(1), True),
        ]
        assert failed[0].scan.status_reason is not None

    def test_completed_scans_carry_their_tools(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with uow() as tx:
            tx.scans.add(new_scan(1))
            tx.scans.add(new_scan(2, minute=5))
            tx.commit()
        summary = ScanSummary(
            new=SeverityCounts(),
            open=SeverityCounts(),
            verdict=Verdict.PASS,
            truncated=False,
            tools=(ToolRun(ScannerKind("semgrep"), "1.180.0", DIGEST, 3),),
        )
        finish(engine, 1, minute=10, summary=encode_summary(summary))
        finish(engine, 2, minute=20, summary=encode_summary(replace(summary, tools=())))
        with uow() as tx:
            found = tx.insights.completed_scans(ORG, limit=10)
        assert [a.scan.id for a in found] == [scan_id(2), scan_id(1)]
        older = found[1].scan.summary
        assert older is not None
        assert older.tools == summary.tools


class TestExportRows:
    def test_each_view_selects_its_findings(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        scan = new_scan(1)
        with uow() as tx:
            tx.scans.add(scan)
            tx.findings.add(new_finding(1, scan=scan.id, severity=Severity.HIGH))
            tx.findings.add(
                replace(
                    new_finding(2, scan=scan.id, severity=Severity.MEDIUM),
                    category=FindingCategory.DEPENDENCY,
                )
            )
            tx.findings.add(
                new_finding(3, scan=scan.id, severity=Severity.LOW, repository=OTHER_REPO)
            )
            tx.commit()
        with engine.begin() as conn:
            conn.execute(
                t.findings.update()
                .where(t.findings.c.id == finding_id(3))
                .values(triage_state=TriageState.ACCEPTED_RISK.value, triage_expires_at=at(90))
            )
        none = FindingFilters()
        with uow() as tx:
            triage = tx.insights.export_rows(ORG, ExportView.TRIAGE, none, limit=10)
            deps = tx.insights.export_rows(ORG, ExportView.DEPENDENCIES, none, limit=10)
            risks = tx.insights.export_rows(ORG, ExportView.ACCEPTED_RISKS, none, limit=10)
            repo = tx.insights.export_rows(
                ORG, ExportView.REPOSITORY, FindingFilters(repository_id=REPO), limit=10
            )
            no_repo = tx.insights.export_rows(ORG, ExportView.REPOSITORY, none, limit=10)
            foreign = tx.insights.export_rows(
                STRANGER, ExportView.REPOSITORY, FindingFilters(repository_id=REPO), limit=10
            )
        assert [r.finding.id.value for r in triage] == [finding_id(1), finding_id(2)]
        assert [r.finding.id.value for r in deps] == [finding_id(2)]
        assert [r.finding.id.value for r in risks] == [finding_id(3)]
        assert {r.finding.id.value for r in repo} == {finding_id(1), finding_id(2)}
        assert no_repo == []
        assert foreign == []
