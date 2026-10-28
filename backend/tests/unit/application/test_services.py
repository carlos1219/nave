from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from nave.application.dto.views import FindingView
from nave.application.errors import AccessDeniedError, NotFoundError
from nave.application.services.authorization import Authorizer
from nave.application.services.baseline import diff_against_baseline
from nave.application.services.pr_comment import build_pr_comment, code_span
from nave.domain.entities.finding import Finding
from nave.domain.quality_gates import PolicyVerdict, ReasonKind, Verdict, VerdictReason
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingMessage, FindingTitle
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.identifiers import FindingId, OrganizationId, ScanId, UserId
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.rule_id import RuleId
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.source_path import SourcePath
from tests.fakes.memory import FakeClock, Store
from tests.unit.application.builders import ADMIN, ORG_ID, OUTSIDER, REPO_ID, VIEWER, seed_tenancy

FP = [Fingerprint(c * 64) for c in "abcd"]
SCAN = ScanId(UUID("00000000-0000-4000-8000-0000000000e1"))


def test_baseline_diff() -> None:
    diff = diff_against_baseline([FP[0], FP[1]], frozenset({FP[1], FP[2]}))
    assert diff.new == frozenset({FP[0]})
    assert diff.persisting == frozenset({FP[1]})
    assert diff.missing == frozenset({FP[2]})


class TestAuthorizer:
    def setup_method(self) -> None:
        self.store = Store()
        seed_tenancy(self.store)
        self.authorizer = Authorizer(uow_factory=self.store.uow, clock=FakeClock())

    def _require(self, actor: UserId, required: Role) -> Role:
        with self.store.uow() as uow:
            return self.authorizer.require(
                uow,
                actor=actor,
                organization_id=ORG_ID,
                required=required,
                target_type="scan",
                target_id="s1",
            )

    def test_grants_sufficient_role(self) -> None:
        assert self._require(ADMIN, Role.TRIAGER) is Role.ADMIN
        assert self.store.state.audit == []

    def test_non_member_gets_not_found_and_is_logged_but_not_audited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # F7-16: un sondeo de un no miembro lo puede repetir cualquiera, sin límite y contra
        # organizaciones que ni existen; inundaría el audit log (ADR 0026).
        with caplog.at_level("WARNING", logger="nave.authz"), pytest.raises(NotFoundError):
            self._require(OUTSIDER, Role.VIEWER)
        assert self.store.state.audit == []
        (record,) = caplog.records
        assert record.__dict__["action"] == "access.denied"
        assert record.__dict__["user_id"] == str(OUTSIDER)
        assert record.__dict__["target_type"] == "scan"

    def test_probing_unknown_organizations_never_touches_the_audit_log(self) -> None:
        for n in range(20):
            with self.store.uow() as uow, pytest.raises(NotFoundError):
                self.authorizer.require(
                    uow,
                    actor=OUTSIDER,
                    organization_id=OrganizationId(UUID(int=0xDEAD00 + n)),
                    required=Role.VIEWER,
                    target_type="organization",
                    target_id=str(n),
                )
        assert self.store.state.audit == []

    def test_insufficient_role_is_denied_and_audited_despite_rollback(self) -> None:
        with pytest.raises(AccessDeniedError):
            self._require(VIEWER, Role.TRIAGER)
        assert self.store.state.audit[0].details == {"required_role": "TRIAGER", "member": "true"}


def view(title: str, rule: str = "rule.x", path: str = "a.py", line: int | None = 3) -> FindingView:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    finding = Finding.discover(
        finding_id=FindingId(UUID(int=1)),
        repository_id=REPO_ID,
        fingerprint=FP[0],
        scanner_kind=ScannerKind("semgrep"),
        category=FindingCategory.SAST,
        rule_id=RuleId(rule),
        severity=Severity.HIGH,
        title=FindingTitle(title),
        scan_id=SCAN,
        at=t0,
    )
    occurrence = FindingOccurrence(
        scan_id=SCAN,
        finding_id=finding.id,
        location=SourceLocation(SourcePath(path), line, line)
        if line
        else SourceLocation(SourcePath(path)),
        message=FindingMessage("m"),
        snippet=None,
        is_new=True,
    )
    return FindingView(finding, occurrence)


SUMMARY = ScanSummary(
    new=SeverityCounts(high=25), open=SeverityCounts(high=30), verdict=Verdict.FAIL, truncated=True
)
VERDICT = PolicyVerdict(
    Verdict.FAIL,
    (
        VerdictReason(
            kind=ReasonKind.GATE,
            rule="no-new-high",
            blocking=True,
            severity=Severity.HIGH,
            observed=25,
            limit=0,
        ),
    ),
)


class TestPrComment:
    @pytest.mark.parametrize(
        "payload",
        [
            "@everyone please merge",
            "![x](https://evil.example/track.png)",
            "<img src=x onerror=alert(1)>",
            "[click](javascript:alert(1))",
            "`` break out `` @admin",
            "line1\n\n# heading injection",
            "-->\n<!-- nave:pr-summary -->",
        ],
    )
    def test_attacker_text_is_confined_to_code_spans(self, payload: str) -> None:
        body = build_pr_comment(
            summary=SUMMARY, verdict=VERDICT, new_findings=[view("t", path="a.py")]
        )
        assert body.markdown.startswith("<!-- nave:pr-summary -->")
        span = code_span(payload)
        fence = span.split(" ", 1)[0]
        assert set(fence) == {"`"}
        inner = span[len(fence) + 1 : -len(fence) - 1]
        assert fence not in inner
        assert "\n" not in span

    def test_lists_at_most_twenty_and_reports_remainder(self) -> None:
        views: Sequence[FindingView] = [view(f"title {i}") for i in range(30)]
        body = build_pr_comment(summary=SUMMARY, verdict=VERDICT, new_findings=views)
        assert body.markdown.count("- **HIGH**") == 20
        assert "y 5 más" in body.markdown
        assert "resultados incompletos" in body.markdown
        assert "`` no-new-high ``" not in body.markdown
        assert "` no-new-high `" in body.markdown

    def test_location_without_line(self) -> None:
        body = build_pr_comment(
            summary=SUMMARY, verdict=VERDICT, new_findings=[view("t", line=None)]
        )
        assert "` a.py `" in body.markdown

    def test_empty_text_becomes_placeholder(self) -> None:
        assert code_span("   ") == "` - `"

    def test_long_text_is_truncated(self) -> None:
        assert len(code_span("x" * 1000)) < 210


class TestPrCommentBranches:
    def test_minimal_comment_without_reasons_or_findings(self) -> None:
        summary = ScanSummary(
            new=SeverityCounts(), open=SeverityCounts(), verdict=Verdict.PASS, truncated=False
        )
        body = build_pr_comment(
            summary=summary, verdict=PolicyVerdict(Verdict.PASS), new_findings=[]
        )
        assert "Motivos" not in body.markdown
        assert "#### Hallazgos nuevos" not in body.markdown
        assert "incompletos" not in body.markdown

    def test_all_findings_listed_has_no_remainder(self) -> None:
        summary = ScanSummary(
            new=SeverityCounts(high=1),
            open=SeverityCounts(high=1),
            verdict=Verdict.FAIL,
            truncated=False,
        )
        reason = VerdictReason(kind=ReasonKind.TRUNCATED, rule="results-truncated", blocking=False)
        body = build_pr_comment(
            summary=summary,
            verdict=PolicyVerdict(Verdict.FAIL, (reason,)),
            new_findings=[view("t")],
        )
        assert "más." not in body.markdown
        assert "informativo" in body.markdown

    def test_location_missing_entirely(self) -> None:
        base = view("t")
        assert base.occurrence is not None
        no_location = FindingView(base.finding, replace(base.occurrence, location=None))
        no_occurrence = FindingView(base.finding, None)
        body = build_pr_comment(
            summary=SUMMARY, verdict=VERDICT, new_findings=[no_location, no_occurrence]
        )
        assert body.markdown.count("en ` - `") == 2

    def test_oversized_comment_is_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import nave.application.services.pr_comment as module  # noqa: PLC0415

        monkeypatch.setattr(module, "MAX_COMMENT_LENGTH", 300)
        body = build_pr_comment(
            summary=SUMMARY, verdict=VERDICT, new_findings=[view(f"t{i}") for i in range(20)]
        )
        assert len(body.markdown) <= 300
        assert body.markdown.endswith("(recortado)")
