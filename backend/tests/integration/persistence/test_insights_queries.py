"""Consultas agregadas por organización sobre PostgreSQL real."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert

from nave.application.dto.audit import AuditEntry
from nave.domain.quality_gates import QualityGatePolicy, Verdict
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.identifiers import OrganizationId
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import encode_policy, encode_summary
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.integration.persistence.seed import (
    ADMIN,
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


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    seed_tenancy(engine)
    return SqlUnitOfWorkFactory(engine)


def finding_id(n: int) -> UUID:
    return UUID(int=0xF000 + n)


def seed_findings(uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
    scan = new_scan(1)
    with uow() as tx:
        tx.scans.add(scan)
        tx.findings.add(new_finding(1, scan=scan.id, severity=Severity.HIGH))
        tx.findings.add(new_finding(2, scan=scan.id, severity=Severity.CRITICAL))
        tx.findings.add(new_finding(3, scan=scan.id, severity=Severity.HIGH, repository=OTHER_REPO))
        tx.findings.add(new_finding(4, scan=scan.id, severity=Severity.LOW))
        dependency = replace(
            new_finding(5, scan=scan.id, severity=Severity.MEDIUM),
            category=FindingCategory.DEPENDENCY,
        )
        tx.findings.add(dependency)
        tx.findings.add(new_finding(6, scan=scan.id, severity=Severity.HIGH, minute=0))
        tx.commit()
    with engine.begin() as conn:
        conn.execute(
            t.findings.update()
            .where(t.findings.c.id == finding_id(4))
            .values(triage_state=TriageState.FIXED.value, last_seen_at=at(0) + timedelta(days=2))
        )
        conn.execute(
            t.findings.update()
            .where(t.findings.c.id == finding_id(6))
            .values(triage_state=TriageState.ACCEPTED_RISK.value, triage_expires_at=at(60))
        )


class TestAggregates:
    def test_open_buckets_by_repository_scanner_and_severity(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        seed_findings(uow, engine)
        with uow() as tx:
            buckets = tx.insights.open_buckets(ORG)
            assert tx.insights.open_buckets(STRANGER) == []
        totals = {(b.repository.name, b.severity): b.count for b in buckets}
        assert sum(totals.values()) == 4  # 1, 2, 3 y la dependencia; FIXED y ACCEPTED no
        assert totals[("lib", Severity.HIGH)] == 1

    def test_rule_stats_fix_times_and_overdue(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        seed_findings(uow, engine)
        with uow() as tx:
            rules = tx.insights.rule_stats(ORG, limit=3)
            fixes = tx.insights.fix_times(ORG)
            overdue = tx.insights.overdue_counts(
                ORG, {Severity.HIGH: at(1), Severity.CRITICAL: at(-1)}
            )
            assert tx.insights.overdue_counts(ORG, {}) == {}
        assert len(rules) == 3
        assert rules[0].open == 1
        assert rules[0].max_severity is Severity.CRITICAL
        (fix,) = fixes
        assert (fix.severity, fix.fixed) == (Severity.LOW, 1)
        assert fix.mean_seconds == pytest.approx(2 * 86400)
        assert overdue == {Severity.HIGH: 2}

    def test_dependencies_and_accepted_risks(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        seed_findings(uow, engine)
        with uow() as tx:
            (dependency,) = tx.insights.dependency_findings(ORG, limit=10)
            (risk,) = tx.insights.accepted_risks(ORG, limit=10)
            assert tx.insights.accepted_risks(STRANGER, limit=10) == []
        assert dependency.finding.category is FindingCategory.DEPENDENCY
        assert dependency.repository.name == "app"
        assert risk.finding.id.value == finding_id(6)
        assert risk.justification is None


class TestScansAndSummaries:
    def test_recent_scans_put_active_ones_first(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            for n in range(1, 4):
                tx.scans.add(new_scan(n, minute=n))
            tx.commit()
        with uow() as tx:
            found = tx.insights.recent_scans(ORG, limit=2)
            assert tx.insights.recent_scans(STRANGER, limit=2) == []
        assert [a.scan.id for a in found] == [scan_id(3), scan_id(2)]
        assert found[0].repository.owner == "acme"

    def test_open_summaries_only_from_the_default_branch(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with uow() as tx:
            tx.scans.add(new_scan(1))
            tx.scans.add(new_scan(2, minute=10))
            tx.scans.add(new_scan(3, ref=FEATURE, minute=20))
            tx.scans.add(new_scan(4, repository=OTHER_REPO, minute=30))
            tx.commit()
        summary = ScanSummary(
            new=SeverityCounts(), open=SeverityCounts(high=2), verdict=Verdict.PASS, truncated=False
        )
        with engine.begin() as conn:
            for n, minute in ((1, 1), (2, 100), (3, 101), (4, 102)):
                conn.execute(
                    t.scans.update()
                    .where(t.scans.c.id == scan_id(n).value)
                    .values(
                        status="COMPLETED",
                        started_at=at(minute - 1),
                        finished_at=at(minute),
                        summary=encode_summary(summary),
                    )
                )
        with uow() as tx:
            points = tx.insights.open_summaries(ORG, since=at(50))
        # Antes de la ventana solo cuenta el último de cada repositorio; la rama feature, nunca.
        assert [p.finished_at for p in points] == [at(1), at(100), at(102)]
        assert {p.repository_id for p in points} == {REPO, OTHER_REPO}


class TestPolicyAndAudit:
    def test_policy_versions_newest_first(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        policy = QualityGatePolicy(version=1, gates=(), sla_days={}, fail_on_sla_breach=False)
        with engine.begin() as conn:
            for version in (1, 2):
                conn.execute(
                    insert(t.quality_gate_policies).values(
                        id=UUID(int=0xB0 + version),
                        organization_id=ORG.value,
                        version=version,
                        document=encode_policy(policy),
                        active=version == 2,
                        created_at=at(version),
                    )
                )
        with uow() as tx:
            versions = tx.insights.policy_versions(ORG)
            assert tx.insights.policy_versions(STRANGER) == []
        assert [(v.version, v.active) for v in versions] == [(2, True), (1, False)]

    def test_audit_entries_of_the_organization_newest_first(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        with uow() as tx:
            for n in range(3):
                tx.audit.append(
                    AuditEntry(
                        occurred_at=at(n),
                        action="finding.triaged",
                        actor=ADMIN,
                        organization_id=ORG if n < 2 else STRANGER,
                        target_type="finding",
                        target_id=f"f{n}",
                        details={"state": "OPEN"},
                    )
                )
            tx.commit()
        with uow() as tx:
            entries = tx.insights.audit_entries(ORG, limit=10, before=None)
            older = tx.insights.audit_entries(ORG, limit=10, before=entries[0].id)
        assert [e.target_id for e in entries] == ["f1", "f0"]
        assert entries[0].details == {"state": "OPEN"}
        assert entries[0].actor == ADMIN
        assert [e.target_id for e in older] == ["f0"]
