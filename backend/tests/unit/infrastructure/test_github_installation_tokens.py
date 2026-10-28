import logging
from datetime import timedelta

import pytest

from nave.application.errors import VcsRejectedError, VcsUnavailableError
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.vcs.github_http import GithubApiClient, TransportError
from nave.infrastructure.vcs.installation_tokens import (
    MAX_CACHE_ENTRIES,
    GithubInstallationTokens,
    InstallationToken,
)
from tests.fakes.github import (
    LONG_TOKEN,
    TOKEN,
    Call,
    FakeGithubTransport,
    Reply,
    response,
    signer,
)
from tests.fakes.memory import FakeClock

REPO = RepoRef("acme", "app")
FETCH_GRANT = {"contents": "read", "metadata": "read"}
REPORT_GRANT = {"checks": "write", "pull_requests": "write", "metadata": "read"}


def minted(  # noqa: PLR0913
    clock: FakeClock,
    *,
    token: str = TOKEN,
    permissions: dict[str, str] | None = None,
    selection: str = "selected",
    repositories: object = "default",
    lifetime: timedelta = timedelta(hours=1),
) -> Reply:
    body: dict[str, object] = {
        "token": token,
        "expires_at": (clock.now() + lifetime).isoformat().replace("+00:00", "Z"),
        "permissions": FETCH_GRANT if permissions is None else permissions,
        "repository_selection": selection,
    }
    if repositories == "default":
        body["repositories"] = [{"name": "app", "id": 1}]
    elif repositories is not None:
        body["repositories"] = repositories
    return response(201, body)


def make(
    *replies: Reply, sarif: bool = False
) -> tuple[GithubInstallationTokens, FakeGithubTransport, FakeClock]:
    clock = FakeClock()
    transport = FakeGithubTransport(*replies)
    api = GithubApiClient(transport=transport, clock=clock)
    tokens = GithubInstallationTokens(
        api=api, signer=signer(clock), clock=clock, sarif_upload=sarif
    )
    return tokens, transport, clock


def by_kind(calls: list[Call], method: str) -> list[Call]:
    return [c for c in calls if c.method == method]


class TestFetchToken:
    def test_mints_a_read_only_token_for_one_repository(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO) as token:
            assert token == TOKEN
        mint = transport.calls[0]
        assert (mint.method, mint.path) == ("POST", "/app/installations/7/access_tokens")
        assert mint.json == {"repositories": ["app"], "permissions": {"contents": "read"}}
        assert mint.bearer.count(".") == 2  # JWT de la App, no un token de instalación

    def test_revokes_the_token_as_soon_as_the_block_ends(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO):
            assert len(transport.calls) == 1
        revoke = transport.calls[1]
        assert (revoke.method, revoke.path, revoke.bearer) == (
            "DELETE",
            "/installation/token",
            TOKEN,
        )

    def test_revokes_when_the_block_raises(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock), response(204))
        with pytest.raises(RuntimeError), tokens.fetch_token(installation_id=7, repo=REPO):
            raise RuntimeError("boom")
        assert transport.calls[1].method == "DELETE"

    def test_an_already_expired_token_is_a_normal_revocation_outcome(self) -> None:
        clock = FakeClock()
        tokens, _, _ = make(minted(clock), response(401))
        with tokens.fetch_token(installation_id=7, repo=REPO):
            pass

    @pytest.mark.parametrize("failure", [response(500), TransportError("down"), response(403)])
    def test_a_failed_revocation_is_logged_without_the_token_and_not_raised(
        self, failure: Reply, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = FakeClock()
        tokens, _, _ = make(minted(clock), failure)
        with (
            caplog.at_level(logging.WARNING, logger="nave.github"),
            tokens.fetch_token(installation_id=7, repo=REPO),
        ):
            pass
        assert "revoke failed" in caplog.text
        assert TOKEN not in caplog.text

    def test_never_caches_fetch_tokens(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock), response(204), minted(clock), response(204))
        for _ in range(2):
            with tokens.fetch_token(installation_id=7, repo=REPO):
                pass
        assert len(by_kind(transport.calls, "POST")) == 2

    def test_accepts_the_long_opaque_token_format(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock, token=LONG_TOKEN), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO) as token:
            assert token == LONG_TOKEN
        assert transport.calls[1].bearer == LONG_TOKEN

    @pytest.mark.parametrize("status", [404, 422, 403, 401])
    def test_a_refusal_to_mint_is_permanent(self, status: int) -> None:
        tokens, _, _ = make(response(status, {"message": "no"}))
        with pytest.raises(VcsRejectedError), tokens.fetch_token(installation_id=7, repo=REPO):
            pass

    def test_github_being_down_is_transient(self) -> None:
        tokens, _, _ = make(response(503))
        with pytest.raises(VcsUnavailableError), tokens.fetch_token(installation_id=7, repo=REPO):
            pass


class TestScopeIsVerified:
    """No se confía en que GitHub haya aplicado el límite pedido (AM-506)."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {"selection": "all"},
            {"repositories": [{"name": "app"}, {"name": "other"}]},
            {"repositories": [{"name": "other"}]},
            {"repositories": []},
            {"repositories": "nonsense"},
            {"permissions": {"contents": "write", "metadata": "read"}},
            {"permissions": {"contents": "read", "administration": "read"}},
            {"permissions": {"contents": "admin"}},
            {"permissions": {"metadata": "write"}},
        ],
    )
    def test_a_wider_token_is_revoked_and_refused(self, overrides: dict[str, object]) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock, **overrides), response(204))  # type: ignore[arg-type]
        with (
            pytest.raises(VcsRejectedError, match="scope"),
            tokens.fetch_token(installation_id=7, repo=REPO),
        ):
            pytest.fail("the token must never reach the caller")
        assert transport.calls[1].method == "DELETE"
        assert transport.calls[1].bearer == TOKEN

    def test_accepts_the_exact_scope_with_implicit_metadata(self) -> None:
        clock = FakeClock()
        tokens, _, _ = make(minted(clock), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO):
            pass

    def test_repository_names_compare_case_insensitively(self) -> None:
        clock = FakeClock()
        tokens, _, _ = make(minted(clock, repositories=[{"name": "APP"}]), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO):
            pass

    def test_a_response_without_the_repository_list_is_tolerated_when_selected(self) -> None:
        clock = FakeClock()
        tokens, _, _ = make(minted(clock, repositories=None), response(204))
        with tokens.fetch_token(installation_id=7, repo=REPO):
            pass


class TestResponseValidation:
    @pytest.mark.parametrize(
        "token", ["", "short", "a b" * 10, "x\n" * 12, "é" * 24, 12345, None, "x" * 1025]
    )
    def test_the_token_must_look_like_an_opaque_token(self, token: object) -> None:
        clock = FakeClock()
        body = {
            "token": token,
            "expires_at": "2026-10-11T09:00:00Z",
            "permissions": FETCH_GRANT,
            "repository_selection": "selected",
        }
        tokens, _, _ = make(response(201, body))
        with pytest.raises(VcsRejectedError), tokens.fetch_token(installation_id=7, repo=REPO):
            pass
        assert clock is not None

    @pytest.mark.parametrize(
        "expires",
        [
            None,
            5,
            "",
            "garbage",
            "2026-10-11T07:00:00Z",
            "2026-10-11T09:00:00",
            "2026-10-12T08:00:00Z",
        ],
    )
    def test_the_expiry_must_be_a_future_instant_with_zone_within_two_hours(
        self, expires: object
    ) -> None:
        body = {
            "token": TOKEN,
            "expires_at": expires,
            "permissions": FETCH_GRANT,
            "repository_selection": "selected",
        }
        tokens, _, _ = make(response(201, body))
        with pytest.raises(VcsRejectedError), tokens.fetch_token(installation_id=7, repo=REPO):
            pass

    @pytest.mark.parametrize("body", [None, [], "x", 1])
    def test_the_body_must_be_an_object(self, body: object) -> None:
        tokens, _, _ = make(response(201, body))
        with pytest.raises(VcsRejectedError), tokens.fetch_token(installation_id=7, repo=REPO):
            pass


class TestReportToken:
    def test_asks_for_checks_and_comments_only_by_default(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock, permissions=REPORT_GRANT))
        assert tokens.report_token(installation_id=7, repo=REPO) == TOKEN
        assert transport.calls[0].json == {
            "repositories": ["app"],
            "permissions": {"checks": "write", "pull_requests": "write"},
        }

    def test_asks_for_security_events_only_when_sarif_upload_is_on(self) -> None:
        clock = FakeClock()
        grant = {**REPORT_GRANT, "security_events": "write"}
        tokens, transport, _ = make(minted(clock, permissions=grant), sarif=True)
        tokens.report_token(installation_id=7, repo=REPO)
        assert transport.calls[0].json == {
            "repositories": ["app"],
            "permissions": {
                "checks": "write",
                "pull_requests": "write",
                "security_events": "write",
            },
        }

    def test_caches_the_token_and_does_not_revoke_it(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(minted(clock, permissions=REPORT_GRANT))
        for _ in range(3):
            assert tokens.report_token(installation_id=7, repo=REPO) == TOKEN
        assert len(transport.calls) == 1

    def test_a_token_still_valid_beyond_the_margin_is_reused(self) -> None:
        tokens, transport, clock = make(minted(FakeClock(), permissions=REPORT_GRANT))
        tokens.report_token(installation_id=7, repo=REPO)
        clock.advance(minutes=54)  # quedan 6 min, más que el margen de 5
        assert tokens.report_token(installation_id=7, repo=REPO) == TOKEN
        assert len(transport.calls) == 1

    def test_a_token_within_the_margin_of_expiry_is_replaced(self) -> None:
        second = "ghs_" + "N" * 36
        tokens, transport, clock = make(
            minted(FakeClock(), permissions=REPORT_GRANT),
            minted(
                FakeClock(), token=second, lifetime=timedelta(hours=2), permissions=REPORT_GRANT
            ),
        )
        tokens.report_token(installation_id=7, repo=REPO)
        clock.advance(minutes=56)  # quedan 4 min
        assert tokens.report_token(installation_id=7, repo=REPO) == second
        assert len(transport.calls) == 2

    def test_tokens_are_per_installation_and_per_repository(self) -> None:
        clock = FakeClock()
        grant = REPORT_GRANT
        tokens, transport, _ = make(
            minted(clock, permissions=grant),
            minted(clock, permissions=grant, repositories=[{"name": "other"}]),
            minted(clock, permissions=grant),
        )
        tokens.report_token(installation_id=7, repo=REPO)
        tokens.report_token(installation_id=7, repo=RepoRef("acme", "other"))
        tokens.report_token(installation_id=8, repo=REPO)
        assert [c.path for c in transport.calls] == [
            "/app/installations/7/access_tokens",
            "/app/installations/7/access_tokens",
            "/app/installations/8/access_tokens",
        ]

    def test_invalidating_forces_a_new_token(self) -> None:
        clock = FakeClock()
        tokens, transport, _ = make(
            minted(clock, permissions=REPORT_GRANT), minted(clock, permissions=REPORT_GRANT)
        )
        tokens.report_token(installation_id=7, repo=REPO)
        tokens.invalidate_report_token(installation_id=7, repo=REPO)
        tokens.report_token(installation_id=7, repo=REPO)
        assert len(transport.calls) == 2

    def test_a_wider_report_token_is_refused_and_not_cached(self) -> None:
        clock = FakeClock()
        wide = {**REPORT_GRANT, "contents": "write"}
        tokens, transport, _ = make(
            minted(clock, permissions=wide),
            response(204),
            minted(clock, permissions=REPORT_GRANT),
        )
        with pytest.raises(VcsRejectedError):
            tokens.report_token(installation_id=7, repo=REPO)
        assert tokens.report_token(installation_id=7, repo=REPO) == TOKEN
        assert [c.method for c in transport.calls] == ["POST", "DELETE", "POST"]

    def test_the_cache_is_bounded(self) -> None:
        clock = FakeClock()
        total = MAX_CACHE_ENTRIES + 20

        def handler(call: Call) -> Reply:
            name = str(call.json["repositories"][0])  # type: ignore[index]
            return minted(clock, permissions=REPORT_GRANT, repositories=[{"name": name}])

        transport = FakeGithubTransport(handler=handler)
        api = GithubApiClient(transport=transport, clock=clock)
        tokens = GithubInstallationTokens(
            api=api, signer=signer(clock), clock=clock, sarif_upload=False
        )
        for n in range(total):
            tokens.report_token(installation_id=7, repo=RepoRef("acme", f"r{n}"))
        assert len(tokens._cache) <= MAX_CACHE_ENTRIES

    def test_the_token_is_not_in_the_repr(self) -> None:
        token = InstallationToken(TOKEN, FakeClock().now())
        assert TOKEN not in repr(token)


def test_a_response_without_permissions_is_treated_as_a_scope_mismatch() -> None:
    clock = FakeClock()
    body = {
        "token": TOKEN,
        "expires_at": "2026-10-11T09:00:00Z",
        "repository_selection": "selected",
    }
    tokens, transport, _ = make(response(201, body), response(204))
    with pytest.raises(VcsRejectedError, match="scope"):
        tokens.report_token(installation_id=7, repo=REPO)
    assert transport.calls[1].method == "DELETE"
    assert clock is not None


def test_expired_entries_are_evicted_before_live_ones() -> None:
    clock = FakeClock()

    def handler(call: Call) -> Reply:
        name = str(call.json["repositories"][0])  # type: ignore[index]
        life = timedelta(minutes=10) if name.startswith("old") else timedelta(hours=1)
        return minted(clock, permissions=REPORT_GRANT, repositories=[{"name": name}], lifetime=life)

    transport = FakeGithubTransport(handler=handler)
    tokens = GithubInstallationTokens(
        api=GithubApiClient(transport=transport, clock=clock),
        signer=signer(clock),
        clock=clock,
        sarif_upload=False,
    )
    for n in range(MAX_CACHE_ENTRIES):
        tokens.report_token(installation_id=7, repo=RepoRef("acme", f"old{n}"))
    clock.advance(minutes=30)
    tokens.report_token(installation_id=7, repo=RepoRef("acme", "fresh"))
    assert list(tokens._cache) == [(7, "fresh")]
