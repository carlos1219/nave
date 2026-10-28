import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from nave.application.errors import (
    FetchUnavailableError,
    RepositoryFetchError,
    RepositoryTooLargeError,
    VcsRejectedError,
)
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.sandbox.runner_fetcher import RunnerRepositoryFetcher
from tests.fakes.runner import RUN, FakeTransport, client, error, ok
from tests.unit.application.builders import FETCH_LIMITS

REPO = RepoRef("acme", "app")
SHA = CommitSha("a" * 40)
TOKEN = "ghs_" + "S" * 36


class Tokens:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.requests: list[tuple[int, RepoRef]] = []
        self.open = False
        self.revoked = 0

    @contextmanager
    def fetch_token(self, *, installation_id: int, repo: RepoRef) -> Iterator[str]:
        self.requests.append((installation_id, repo))
        if self.fail:
            raise self.fail
        self.open = True
        try:
            yield TOKEN
        finally:
            self.open = False
            self.revoked += 1


def steps(transport: FakeTransport) -> list[str]:
    return [json.loads(call[1])["step"] for call in transport.calls]


def make(
    *responses: object, tokens: Tokens | None = None
) -> tuple[RunnerRepositoryFetcher, FakeTransport, Tokens]:
    runner, transport = client(*responses)  # type: ignore[arg-type]
    provider = tokens or Tokens()
    return (
        RunnerRepositoryFetcher(client=runner, tokens=provider, new_run_id=lambda: RUN),
        transport,
        provider,
    )


def checkout(fetcher: RunnerRepositoryFetcher):
    return fetcher.checkout(installation_id=7, repo=REPO, commit=SHA, limits=FETCH_LIMITS)


def test_yields_the_run_id_as_handle_and_releases_afterwards() -> None:
    fetcher, transport, tokens = make(ok(), ok())
    with checkout(fetcher) as handle:
        assert handle.value == RUN
        assert steps(transport) == ["fetch"]
    assert steps(transport) == ["fetch", "release"]
    assert tokens.requests == [(7, REPO)]


def test_releases_when_the_body_raises() -> None:
    fetcher, transport, _ = make(ok(), ok())
    with pytest.raises(RuntimeError), checkout(fetcher):
        raise RuntimeError("boom")
    assert steps(transport) == ["fetch", "release"]


def test_releases_after_a_failed_fetch_and_keeps_the_original_error() -> None:
    fetcher, transport, _ = make(error(413, "too_large"), ok())
    with pytest.raises(RepositoryTooLargeError), checkout(fetcher):
        pass
    assert steps(transport) == ["fetch", "release"]


def test_a_failing_release_does_not_mask_the_original_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fetcher, _, _ = make(error(413, "too_large"), error(500, "internal"))
    with (
        caplog.at_level(logging.WARNING, logger="nave.sandbox"),
        pytest.raises(RepositoryTooLargeError),
        checkout(fetcher),
    ):
        pass
    assert "workspace release failed" in caplog.text
    assert TOKEN not in caplog.text


def test_a_failing_release_after_success_is_logged_not_raised() -> None:
    fetcher, _, _ = make(ok(), error(500, "internal"))
    with checkout(fetcher):
        pass


def test_a_token_provider_failure_is_propagated_and_nothing_is_fetched() -> None:
    boom = FetchUnavailableError("github down")
    fetcher, transport, _ = make(ok(), tokens=Tokens(boom))
    with pytest.raises(FetchUnavailableError), checkout(fetcher):
        pass
    assert steps(transport) == ["release"]


def test_every_checkout_gets_a_fresh_run_id() -> None:
    runner, _ = client()
    fetcher = RunnerRepositoryFetcher(client=runner, tokens=Tokens())
    ids = set()
    for _ in range(5):
        ids.add(fetcher._new_run_id())
    assert len(ids) == 5


def test_the_token_is_revoked_before_the_scan_starts() -> None:
    fetcher, _, tokens = make(ok(), ok())
    with checkout(fetcher):
        assert tokens.revoked == 1
        assert not tokens.open


def test_the_token_is_revoked_when_the_fetch_fails() -> None:
    fetcher, _, tokens = make(error(413, "too_large"), ok())
    with pytest.raises(RepositoryTooLargeError), checkout(fetcher):
        pass
    assert tokens.revoked == 1


def test_a_rejected_installation_is_a_permanent_fetch_failure() -> None:
    fetcher, transport, _ = make(ok(), tokens=Tokens(VcsRejectedError("nope", status=404)))
    with pytest.raises(RepositoryFetchError), checkout(fetcher):
        pass
    assert steps(transport) == ["release"]
