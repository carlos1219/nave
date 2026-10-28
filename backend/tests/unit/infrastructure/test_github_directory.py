import pytest

from nave.application.errors import VcsRejectedError
from nave.domain.errors import DomainError
from nave.infrastructure.vcs.app_identity import GithubAppIdentityProvider  # noqa: F401
from nave.infrastructure.vcs.directory import PAGE_SIZE, GithubInstallationDirectory
from nave.infrastructure.vcs.github_http import GithubApiClient
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens
from tests.fakes.github import signer
from tests.fakes.github_api import FakeGithub
from tests.fakes.memory import FakeClock


def installation(
    installation_id: int = 10, login: str = "acme", **extra: object
) -> dict[str, object]:
    return {"id": installation_id, "account": {"id": 500, "login": login}, **extra}


def repo(
    repo_id: int, name: str = "api", branch: str = "main", owner: str = "acme"
) -> dict[str, object]:
    return {"id": repo_id, "name": name, "default_branch": branch, "owner": {"login": owner}}


class Setup:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.github = FakeGithub(self.clock)
        self.transport = self.github.transport()
        api = GithubApiClient(transport=self.transport, clock=self.clock)
        jwt = signer(self.clock)
        tokens = GithubInstallationTokens(api=api, signer=jwt, clock=self.clock, sarif_upload=True)
        self.directory = GithubInstallationDirectory(api=api, signer=jwt, tokens=tokens)


def test_reads_installation_from_github() -> None:
    s = Setup()
    s.github.installations[10] = installation(suspended_at=None)
    info = s.directory.get_installation(10)
    assert info is not None
    assert (info.installation_id, info.account_id, info.account_login, info.suspended) == (
        10,
        500,
        "acme",
        False,
    )
    assert s.directory.get_installation(11) is None


def test_suspended_installation() -> None:
    s = Setup()
    s.github.installations[10] = installation(suspended_at="2026-10-01T00:00:00Z")
    info = s.directory.get_installation(10)
    assert info is not None
    assert info.suspended is True


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 10},
        {"id": "10", "account": {"id": 1, "login": "a"}},
        {"id": 10, "account": {"id": 1, "login": "-bad-"}},
        {"id": True, "account": {"id": 1, "login": "a"}},
    ],
)
def test_malformed_installation_is_rejected(payload: dict[str, object]) -> None:
    s = Setup()
    s.github.installations[10] = payload
    with pytest.raises((VcsRejectedError, DomainError)):
        s.directory.get_installation(10)


def test_lists_repositories_with_a_scoped_token_that_is_revoked() -> None:
    s = Setup()
    s.github.installations[10] = installation()
    s.github.installation_repos[10] = [repo(1), repo(2, "web", "trunk")]
    found = s.directory.list_repositories(10)
    assert [(r.github_repo_id, r.ref.name, r.default_branch.value) for r in found] == [
        (1, "api", "main"),
        (2, "web", "trunk"),
    ]
    mint = next(c for c in s.transport.calls if c.path.endswith("/access_tokens"))
    assert mint.json == {"permissions": {"metadata": "read"}}
    assert s.github.valid_tokens == set()


def test_pagination() -> None:
    s = Setup()
    s.github.installation_repos[10] = [repo(i, f"r{i}") for i in range(1, PAGE_SIZE + 31)]
    assert len(s.directory.list_repositories(10)) == PAGE_SIZE + 30


def test_unsupported_repositories_are_skipped_not_fatal() -> None:
    s = Setup()
    s.github.installation_repos[10] = [
        repo(1),
        repo(2, name="bad name"),
        repo(3, branch="--upload-pack=x"),
        {"id": 4},
        "junk",
        repo(5, "ok"),
    ]
    assert [r.github_repo_id for r in s.directory.list_repositories(10)] == [1, 5]


def test_token_with_more_permissions_than_requested_is_refused_and_revoked() -> None:
    s = Setup()
    s.github.mint_extra_permission = "contents"
    s.github.installation_repos[10] = [repo(1)]
    with pytest.raises(VcsRejectedError):
        s.directory.list_repositories(10)
    assert s.github.valid_tokens == set()


def test_lists_installation_ids_across_pages() -> None:
    s = Setup()
    for i in range(1, PAGE_SIZE + 6):
        s.github.installations[i] = installation(i)
    assert len(s.directory.list_installation_ids()) == PAGE_SIZE + 5
