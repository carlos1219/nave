from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import Engine

from nave.application.dto.views import FindingQuery, LogLevel, PageRequest
from nave.application.errors import ConcurrentModificationError, NotFoundError
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import (
    Gate,
    GateScope,
    PolicyVerdict,
    QualityGatePolicy,
    ReasonKind,
    Verdict,
    VerdictReason,
)
from nave.domain.value_objects.finding_text import FindingMessage, Justification, Snippet
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import TriageDecisionId, UserId
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.source_path import SourcePath
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import TriggerKey
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import encode_policy
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWork, SqlUnitOfWorkFactory
from tests.integration.persistence.seed import (
    ADMIN,
    FEATURE,
    MAIN,
    ORG,
    OTHER_REPO,
    REPO,
    TRIAGER,
    at,
    fingerprint,
    new_finding,
    new_scan,
    scan_id,
    seed_tenancy,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    seed_tenancy(engine)
    return SqlUnitOfWorkFactory(engine)


def _complete(n: int = 1, *, ref: GitRef = MAIN, minute: int = 0) -> Scan:
    scan = new_scan(n, ref=ref, minute=minute)
    scan, _ = scan.start_fetching(at(minute + 1))
    scan, _ = scan.start_scanning(at(minute + 1))
    scan, _ = scan.start_processing(at(minute + 1))
    summary = ScanSummary(
        new=SeverityCounts(high=1),
        open=SeverityCounts(high=2),
        verdict=Verdict.FAIL,
        truncated=False,
    )
    scan, _ = scan.complete(at(minute + 2), summary)
    return scan


class TestScans:
    def test_round_trip_preserves_every_field(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = _complete()
        with uow() as tx:
            tx.scans.add(scan)
            tx.commit()
        with uow() as tx:
            assert tx.scans.get(scan.id) == scan

    def test_failure_reason_round_trip(self, uow: SqlUnitOfWorkFactory) -> None:
        scan, _ = new_scan().start_fetching(at(1))
        failed, _ = scan.fail(at(2), StatusReason(ReasonCode.FETCH_FAILED, "clone failed"))
        with uow() as tx:
            tx.scans.add(failed)
            tx.commit()
        with uow() as tx:
            assert tx.scans.get(failed.id).status_reason == failed.status_reason

    def test_save_uses_optimistic_locking(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = new_scan()
        with uow() as tx:
            tx.scans.add(scan)
            tx.commit()
        fetching, _ = scan.start_fetching(at(1))
        with uow() as tx:
            saved = tx.scans.save(fetching)
            tx.commit()
        assert saved.version == 1
        stale = replace(fetching, version=0)
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.scans.save(stale)

    def test_save_of_unknown_scan(self, uow: SqlUnitOfWorkFactory) -> None:
        with pytest.raises(NotFoundError), uow() as tx:
            tx.scans.save(new_scan(9))

    def test_get_unknown(self, uow: SqlUnitOfWorkFactory) -> None:
        with pytest.raises(NotFoundError), uow() as tx:
            tx.scans.get(scan_id(99))

    def test_trigger_key_is_unique(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            tx.scans.add(new_scan(1))
            tx.commit()
        clash = replace(new_scan(2), trigger_key=TriggerKey("manual:1"))
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.scans.add(clash)
            tx.commit()
        with uow() as tx:
            found = tx.scans.find_by_trigger_key(TriggerKey("manual:1"))
            assert found is not None
            assert found.id == scan_id(1)
            assert tx.scans.find_by_trigger_key(TriggerKey("manual:404")) is None

    def test_active_scans_for_ref(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            tx.scans.add(new_scan(1))
            tx.scans.add(new_scan(2, ref=FEATURE))
            tx.scans.add(_complete(3))
            tx.commit()
        with uow() as tx:
            active = tx.scans.find_active_for_ref(REPO, MAIN)
        assert [s.id for s in active] == [scan_id(1)]


def _occurrence(scan: Scan, finding: Finding, *, is_new: bool = True) -> FindingOccurrence:
    return FindingOccurrence(
        scan_id=scan.id,
        finding_id=finding.id,
        location=SourceLocation(SourcePath("app/run.py"), 3, 4),
        message=FindingMessage("message"),
        snippet=Snippet("x = 1\n\ty = 2"),
        is_new=is_new,
    )


class TestFindings:
    def _scan(self, uow: SqlUnitOfWorkFactory, n: int = 1) -> Scan:
        scan = new_scan(n)
        with uow() as tx:
            tx.scans.add(scan)
            tx.commit()
        return scan

    def test_round_trip_and_unique_fingerprint(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = self._scan(uow)
        finding = new_finding(1, scan=scan.id)
        with uow() as tx:
            tx.findings.add(finding)
            tx.commit()
        with uow() as tx:
            assert tx.findings.get(finding.id) == finding
            assert tx.findings.find_by_fingerprints(REPO, [fingerprint(1)]) == {
                fingerprint(1): finding
            }
            assert tx.findings.find_by_fingerprints(REPO, []) == {}
        duplicate = replace(new_finding(2, scan=scan.id), fingerprint=fingerprint(1))
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.findings.add(duplicate)
            tx.commit()

    def test_accepted_risk_and_optimistic_save(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = self._scan(uow)
        finding = new_finding(1, scan=scan.id)
        with uow() as tx:
            tx.findings.add(finding)
            tx.commit()
        decision = TriageDecision.by_user(
            decision_id=TriageDecisionId(scan.id.value),
            finding_id=finding.id,
            state=TriageState.ACCEPTED_RISK,
            user=TRIAGER,
            at=at(5),
            justification=Justification("Vendor fix scheduled for next quarter."),
            expires_at=at(5) + timedelta(days=30),
        )
        accepted, _ = finding.apply_triage(decision)
        with uow() as tx:
            saved = tx.findings.save(accepted)
            tx.findings.add_decision(decision)
            tx.commit()
        assert saved.version == 1
        with uow() as tx:
            loaded = tx.findings.get(finding.id)
        assert loaded.triage_state is TriageState.ACCEPTED_RISK
        assert loaded.triage_expires_at == decision.expires_at
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.findings.save(accepted)

    def test_unknown_finding(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = self._scan(uow)
        with pytest.raises(NotFoundError), uow() as tx:
            tx.findings.get(new_finding(1, scan=scan.id).id)
        with pytest.raises(NotFoundError), uow() as tx:
            tx.findings.save(new_finding(1, scan=scan.id))

    def test_occurrences_are_idempotent(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = self._scan(uow)
        finding = new_finding(1, scan=scan.id)
        with uow() as tx:
            tx.findings.add(finding)
            tx.findings.record_occurrences([_occurrence(scan, finding)])
            tx.findings.record_occurrences([_occurrence(scan, finding, is_new=False)])
            tx.findings.record_occurrences([])
            tx.commit()
        with uow() as tx:
            (view,) = tx.findings.views_for_scan(scan.id, only_new=False, limit=10)
        assert view.occurrence == _occurrence(scan, finding)

    def test_list_filters_and_keyset_pagination(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = self._scan(uow)
        severities = [Severity.LOW, Severity.HIGH, Severity.CRITICAL, Severity.HIGH, Severity.INFO]
        findings = [new_finding(n, scan=scan.id, severity=s) for n, s in enumerate(severities, 1)]
        with uow() as tx:
            for finding in findings:
                tx.findings.add(finding)
            tx.findings.record_occurrences(
                [_occurrence(scan, f, is_new=f.severity is Severity.HIGH) for f in findings[:4]]
            )
            tx.commit()
        seen: list[object] = []
        cursor = None
        with uow() as tx:
            while True:
                page = tx.findings.list(
                    FindingQuery(repository_id=REPO), PageRequest(limit=2, cursor=cursor)
                )
                seen += [v.finding.id for v in page.items]
                assert all(v.occurrence is None for v in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert seen == sorted((f.id for f in findings), key=lambda i: i.value)
            high = tx.findings.list(
                FindingQuery(repository_id=REPO, severities=frozenset({Severity.HIGH})),
                PageRequest(),
            )
            assert {v.finding.id for v in high.items} == {findings[1].id, findings[3].id}
            new_in_scan = tx.findings.list(
                FindingQuery(repository_id=REPO, seen_in_scan=scan.id, only_new=True),
                PageRequest(),
            )
            assert {v.finding.id for v in new_in_scan.items} == {findings[1].id, findings[3].id}
            assert all(v.occurrence is not None for v in new_in_scan.items)
            in_scan = tx.findings.list(
                FindingQuery(
                    repository_id=REPO,
                    seen_in_scan=scan.id,
                    states=frozenset({TriageState.OPEN}),
                    scanner_kinds=frozenset({findings[0].scanner_kind}),
                ),
                PageRequest(),
            )
            assert {v.finding.id for v in in_scan.items} == {findings[0].id, findings[2].id}
            ranked = tx.findings.views_for_scan(scan.id, only_new=False, limit=3)
            assert [v.finding.severity for v in ranked] == [
                Severity.CRITICAL,
                Severity.HIGH,
                Severity.HIGH,
            ]
            assert len(tx.findings.views_for_scan(scan.id, only_new=True, limit=10)) == 2

    @pytest.mark.parametrize("cursor", ["not-hex", "A" * 32, "0" * 31])
    def test_tampered_cursor_is_rejected(self, uow: SqlUnitOfWorkFactory, cursor: str) -> None:
        with pytest.raises(InvalidValueError), uow() as tx:
            tx.findings.list(FindingQuery(repository_id=REPO), PageRequest(cursor=cursor))

    def test_baseline_is_latest_completed_default_branch_scan(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        old, latest, pr = (
            _complete(1, minute=0),
            _complete(2, minute=10),
            _complete(3, ref=FEATURE, minute=20),
        )
        with uow() as tx:
            for scan in (old, latest, pr):
                tx.scans.add(scan)
            f1, f2, f3 = (new_finding(n, scan=old.id) for n in (1, 2, 3))
            for finding in (f1, f2, f3):
                tx.findings.add(finding)
            tx.findings.record_occurrences(
                [_occurrence(old, f1), _occurrence(latest, f2), _occurrence(pr, f3)]
            )
            tx.commit()
        with uow() as tx:
            assert tx.findings.baseline_fingerprints(REPO) == frozenset({fingerprint(2)})
            assert tx.findings.baseline_fingerprints(OTHER_REPO) == frozenset()
            not_seen = tx.findings.list_not_seen_in(REPO, latest.id, after=None, limit=100)
            assert {f.id for f in not_seen} == {f1.id, f2.id, f3.id}
            pages = []
            after = None
            while page := tx.findings.list_not_seen_in(REPO, latest.id, after=after, limit=2):
                pages.append([f.id for f in page])
                after = page[-1].id
            assert [len(p) for p in pages] == [2, 1]
            assert [i for p in pages for i in p] == sorted(
                (f.id for f in not_seen), key=lambda i: i.value
            )

    def test_baseline_of_unknown_repository(self, uow: SqlUnitOfWorkFactory) -> None:
        with pytest.raises(NotFoundError), uow() as tx:
            tx.findings.baseline_fingerprints(replace(REPO, value=scan_id(1).value))


class TestMembershipsAndTenants:
    def test_role_lifecycle(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            assert tx.memberships.role_of(TRIAGER, ORG) is None
            tx.memberships.set_role(ADMIN, ORG, Role.ADMIN, granted_by=ADMIN, at=at(0))
            tx.memberships.set_role(TRIAGER, ORG, Role.VIEWER, granted_by=ADMIN, at=at(0))
            tx.memberships.set_role(TRIAGER, ORG, Role.TRIAGER, granted_by=ADMIN, at=at(1))
            tx.commit()
        with uow() as tx:
            assert tx.memberships.role_of(TRIAGER, ORG) is Role.TRIAGER
            assert tx.memberships.count_admins(ORG) == 1
            tx.memberships.remove(TRIAGER, ORG)
            tx.memberships.remove(TRIAGER, ORG)
            tx.commit()
        with uow() as tx:
            assert tx.memberships.role_of(TRIAGER, ORG) is None

    def test_unknown_user_is_not_found(self, uow: SqlUnitOfWorkFactory) -> None:
        ghost = UserId(scan_id(77).value)
        with pytest.raises(NotFoundError), uow() as tx:
            tx.memberships.set_role(ghost, ORG, Role.VIEWER, granted_by=ADMIN, at=at(0))

    def test_tenant_catalog(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            repository = tx.tenants.get_repository(REPO)
            assert repository.ref.full_name == "acme/app"
            assert tx.tenants.find_repository_by_github_id(100) == repository
            assert tx.tenants.find_repository_by_github_id(999) is None
            assert tx.tenants.get_organization(ORG).github_installation_id == 42
        with pytest.raises(NotFoundError), uow() as tx:
            tx.tenants.get_repository(replace(REPO, value=scan_id(1).value))
        with pytest.raises(NotFoundError), uow() as tx:
            tx.tenants.get_organization(replace(ORG, value=scan_id(1).value))


class TestLogsDeliveriesPolicies:
    def test_scan_log_sequence(self, uow: SqlUnitOfWorkFactory) -> None:
        scan = new_scan()
        with uow() as tx:
            tx.scans.add(scan)
            for n in range(3):
                tx.scan_logs.append(scan.id, LogLevel.INFO, f"step {n}", at(n))
            tx.commit()
        with uow() as tx:
            entries = tx.scan_logs.list(scan.id, after_seq=1, limit=10)
        assert [(e.seq, e.message) for e in entries] == [(2, "step 1"), (3, "step 2")]

    def test_unsafe_log_message_is_rejected_before_writing(self, uow: SqlUnitOfWorkFactory) -> None:
        with pytest.raises(InvalidValueError), uow() as tx:
            tx.scan_logs.append(scan_id(1), LogLevel.INFO, "a\x1b[31mb", at(0))

    def test_webhook_deliveries_are_unique(self, uow: SqlUnitOfWorkFactory) -> None:
        delivery = "72d3162e-cc78-41e3-81ab-4c9367dc0958"
        with uow() as tx:
            assert not tx.webhook_deliveries.exists(delivery)
            tx.webhook_deliveries.add(delivery, "push", at(0), "ENQUEUED")
            tx.commit()
        with uow() as tx:
            assert tx.webhook_deliveries.exists(delivery)
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.webhook_deliveries.add(delivery, "push", at(1), "ENQUEUED")
            tx.commit()

    def test_policy_and_evaluations(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        policy = QualityGatePolicy(
            version=1,
            gates=(Gate("no-new-high", GateScope.NEW_FINDINGS, {Severity.HIGH: 0}),),
            sla_days={Severity.CRITICAL: 7},
            fail_on_sla_breach=True,
        )
        policy_id = scan_id(500).value
        with engine.begin() as conn:
            conn.execute(
                t.quality_gate_policies.insert().values(
                    id=policy_id,
                    organization_id=ORG.value,
                    version=1,
                    document=encode_policy(policy),
                    active=True,
                    created_at=at(0),
                )
            )
        scan = new_scan()
        verdict = PolicyVerdict(
            Verdict.FAIL,
            (
                VerdictReason(
                    kind=ReasonKind.GATE,
                    rule="no-new-high",
                    blocking=True,
                    severity=Severity.HIGH,
                    observed=2,
                    limit=0,
                ),
            ),
        )
        with uow() as tx:
            record = tx.policies.active_policy(ORG)
            assert record is not None
            assert record.policy == policy
            assert record.policy_id == str(policy_id)
            tx.scans.add(scan)
            assert tx.policies.get_evaluation(scan.id) is None
            tx.policies.record_evaluation(scan.id, None, PolicyVerdict(Verdict.NEUTRAL))
            tx.policies.record_evaluation(scan.id, record.policy_id, verdict)
            tx.commit()
        with uow() as tx:
            assert tx.policies.get_evaluation(scan.id) == verdict
            assert tx.policies.active_policy(replace(ORG, value=scan.id.value)) is None


def test_unit_of_work_cannot_be_reused(engine: Engine) -> None:
    unit = SqlUnitOfWork(engine)
    with pytest.raises(RuntimeError):
        unit.commit()
    with unit:
        with pytest.raises(RuntimeError):
            unit.__enter__()
        unit.commit()
        with pytest.raises(RuntimeError):
            unit.commit()
