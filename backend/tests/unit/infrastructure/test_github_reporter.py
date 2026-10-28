from datetime import UTC, datetime
from uuid import UUID

import pytest

from nave.application.dto.vcs import PR_COMMENT_MARKER, PrCommentBody, VcsTarget
from nave.application.errors import VcsRejectedError
from nave.domain.entities.scan import Scan
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey
from nave.infrastructure.vcs.app_identity import GithubAppIdentityProvider
from nave.infrastructure.vcs.github_http import GithubApiClient
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens
from nave.infrastructure.vcs.reporter import GithubVcsReporter
from tests.fakes.findings import REPO, make_view
from tests.fakes.github import signer
from tests.fakes.github_api import BOT, FakeGithub
from tests.fakes.memory import FakeClock

TARGET = VcsTarget(7, RepoRef("acme", "api"))
SHA = "a" * 40
T0 = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)


def make_scan(
    *,
    n: int = 1,
    verdict: Verdict = Verdict.FAIL,
    pr: int | None = None,
    failure: ReasonCode | None = None,
    ref: str = "main",
) -> Scan:
    scan, _ = Scan.queue(
        scan_id=ScanId(UUID(int=n)),
        repository_id=REPO,
        commit=CommitSha(SHA),
        ref=GitRef(ref),
        trigger=ScanTrigger.WEBHOOK,
        trigger_key=TriggerKey(f"gh-delivery:{n}"),
        at=T0,
        pull_request_number=pr,
    )
    scan, _ = scan.start_fetching(T0)
    if failure is not None:
        scan, _ = scan.fail(T0, StatusReason.from_untrusted(failure, "secret detail ghp_x"))
        return scan
    scan, _ = scan.start_scanning(T0)
    scan, _ = scan.start_processing(T0)
    summary = ScanSummary(
        new=SeverityCounts(high=2), open=SeverityCounts(high=2), verdict=verdict, truncated=False
    )
    scan, _ = scan.complete(T0, summary)
    return scan


def body(extra: str = "") -> PrCommentBody:
    return PrCommentBody(f"{PR_COMMENT_MARKER}\nresumen {extra}")


class Setup:
    def __init__(self, *, sarif_upload: bool = False) -> None:
        self.clock = FakeClock()
        self.github = FakeGithub(self.clock)
        self.transport = self.github.transport()
        api = GithubApiClient(transport=self.transport, clock=self.clock)
        jwt = signer(self.clock)
        self.tokens = GithubInstallationTokens(
            api=api, signer=jwt, clock=self.clock, sarif_upload=sarif_upload
        )
        self.reporter = GithubVcsReporter(
            api=api,
            tokens=self.tokens,
            identity=GithubAppIdentityProvider(api=api, signer=jwt),
            sarif_upload=sarif_upload,
        )


class TestCheckRun:
    def test_creates_then_updates_same_run(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan())
        s.reporter.publish_check(TARGET, make_scan())
        (run,) = s.github.check_runs
        assert run["name"] == "NAVE"
        assert run["head_sha"] == SHA
        assert run["conclusion"] == "failure"
        assert run["external_id"] == str(UUID(int=1))

    def test_conclusion_follows_verdict_and_updates_in_place(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan(verdict=Verdict.FAIL))
        s.reporter.publish_check(TARGET, make_scan(verdict=Verdict.PASS))
        (run,) = s.github.check_runs
        assert run["conclusion"] == "success"

    def test_distinct_scans_get_distinct_runs(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan(n=1))
        s.reporter.publish_check(TARGET, make_scan(n=2))
        assert len(s.github.check_runs) == 2

    def test_failed_scan_reports_code_only(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan(failure=ReasonCode.FETCH_FAILED))
        (run,) = s.github.check_runs
        assert run["conclusion"] == "failure"
        text = str(run["output"])
        assert "FETCH_FAILED" in text
        assert "ghp_" not in text

    def test_ignores_run_with_same_external_id_from_another_app(self) -> None:
        s = Setup()
        s.github.check_runs.append(
            {"id": 5, "name": "NAVE", "external_id": str(UUID(int=1)), "app": {"id": 12345}}
        )
        s.reporter.publish_check(TARGET, make_scan())
        assert len(s.github.check_runs) == 2
        assert s.github.check_runs[0].get("conclusion") is None

    def test_unauthorized_token_is_refreshed_once(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan())
        s.github.unauthorized_once = True
        s.reporter.publish_check(TARGET, make_scan())
        assert s.github.minted == 2
        assert len(s.github.check_runs) == 1

    def test_persistent_unauthorized_surfaces(self) -> None:
        s = Setup()
        s.reporter.publish_check(TARGET, make_scan())
        s.github.valid_tokens.clear()
        original = s.github._mint

        def mint_revoked(call):  # type: ignore[no-untyped-def]
            reply = original(call)
            s.github.valid_tokens.clear()
            return reply

        s.github._mint = mint_revoked  # type: ignore[method-assign]
        with pytest.raises(VcsRejectedError) as raised:
            s.reporter.publish_check(TARGET, make_scan())
        assert raised.value.status == 401


class TestPrComment:
    def test_creates_then_edits_own_comment(self) -> None:
        s = Setup()
        scan = make_scan(pr=5)
        s.reporter.upsert_pr_comment(TARGET, scan, body("uno"))
        s.reporter.upsert_pr_comment(TARGET, scan, body("dos"))
        (comment,) = s.github.comments
        assert str(comment["body"]).endswith("dos")

    def test_no_pull_request_is_a_noop(self) -> None:
        s = Setup()
        s.reporter.upsert_pr_comment(TARGET, make_scan(pr=None), body())
        assert s.github.comments == []
        assert s.transport.calls == []

    def test_spoofed_marker_from_another_user_is_not_edited(self) -> None:
        s = Setup()
        spoof = s.github.add_comment(f"{PR_COMMENT_MARKER}\nfake", login="mallory")
        s.reporter.upsert_pr_comment(TARGET, make_scan(pr=5), body("real"))
        assert len(s.github.comments) == 2
        assert s.github.comments[0]["id"] == spoof
        assert s.github.comments[0]["body"].endswith("fake")  # type: ignore[attr-defined]

    def test_spoofed_marker_from_user_named_like_bot_is_not_edited(self) -> None:
        s = Setup()
        s.github.add_comment(f"{PR_COMMENT_MARKER}\nfake", login=BOT, kind="User")
        s.reporter.upsert_pr_comment(TARGET, make_scan(pr=5), body("real"))
        assert len(s.github.comments) == 2

    def test_own_comment_without_marker_is_left_alone(self) -> None:
        s = Setup()
        s.github.add_comment("otra cosa", login=BOT, kind="Bot")
        s.reporter.upsert_pr_comment(TARGET, make_scan(pr=5), body())
        assert len(s.github.comments) == 2

    def test_finds_comment_on_later_page(self) -> None:
        s = Setup()
        for i in range(130):
            s.github.add_comment(f"c{i}", login="someone")
        mine = s.github.add_comment(f"{PR_COMMENT_MARKER}\nold", login=BOT, kind="Bot")
        s.reporter.upsert_pr_comment(TARGET, make_scan(pr=5), body("new"))
        assert len(s.github.comments) == 131
        assert s.github.comments[-1]["id"] == mine
        assert str(s.github.comments[-1]["body"]).endswith("new")


class TestSarif:
    def test_off_by_default_sends_nothing(self) -> None:
        s = Setup()
        s.reporter.upload_sarif(TARGET, make_scan(), [make_view()])
        assert s.transport.calls == []

    def test_uploads_with_qualified_ref(self) -> None:
        s = Setup(sarif_upload=True)
        s.reporter.upload_sarif(TARGET, make_scan(), [make_view()])
        (upload,) = s.github.sarifs
        assert upload["commit_sha"] == SHA
        assert upload["ref"] == "refs/heads/main"
        assert isinstance(upload["sarif"], str)

    def test_pull_request_ref_is_kept(self) -> None:
        s = Setup(sarif_upload=True)
        s.reporter.upload_sarif(TARGET, make_scan(ref="refs/pull/5/head", pr=5), [make_view()])
        assert s.github.sarifs[0]["ref"] == "refs/pull/5/head"

    def test_requests_security_events_scope_only_when_enabled(self) -> None:
        on, off = Setup(sarif_upload=True), Setup(sarif_upload=False)
        for setup in (on, off):
            setup.reporter.publish_check(TARGET, make_scan())

        def requested(setup: Setup) -> object:
            mint = next(c for c in setup.transport.calls if c.path.endswith("/access_tokens"))
            assert isinstance(mint.json, dict)
            return mint.json["permissions"]

        assert "security_events" in requested(on)  # type: ignore[operator]
        assert "security_events" not in requested(off)  # type: ignore[operator]

    @pytest.mark.parametrize("status", [403, 404, 413, 422])
    def test_unavailable_code_scanning_is_skipped(self, status: int) -> None:
        s = Setup(sarif_upload=True)
        s.github.sarif_status = status
        s.reporter.upload_sarif(TARGET, make_scan(), [make_view()])

    def test_no_usable_findings_sends_nothing(self) -> None:
        s = Setup(sarif_upload=True)
        s.reporter.upload_sarif(TARGET, make_scan(), [make_view(path=None)])
        assert s.transport.calls == []
