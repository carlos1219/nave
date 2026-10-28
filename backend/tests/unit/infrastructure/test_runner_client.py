import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest

from nave.application.errors import (
    FetchUnavailableError,
    RepositoryFetchError,
    RepositoryTooLargeError,
    ScannerExecutionError,
    ScannerOutputInvalidError,
    ScannerUnavailableError,
    ScanTimeoutError,
)
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.infrastructure.sandbox.protocol import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    MAX_REQUEST_BYTES,
    RunnerKey,
    canonical_json,
    sign,
    signed_headers,
)
from nave.infrastructure.sandbox.runner_client import (
    HttpTransport,
    TransportError,
    TransportResponse,
)
from tests.fakes.memory import FakeClock
from tests.fakes.runner import KEY, RUN, client, error, ok, scan_reply
from tests.unit.application.builders import FETCH_LIMITS, SCAN_LIMITS

VECTORS = json.loads(
    (Path(__file__).parents[4] / "contracts" / "runner-protocol-vectors.json").read_text()
)
REPO = RepoRef("acme", "app")
SHA = CommitSha("a" * 40)
TOKEN = "ghs_" + "T" * 36
# Formato nuevo (ADR 0013): ~520 caracteres, con puntos; se trata como opaco.
LONG_TOKEN = "ghs_12345678_" + "eyJhbGciOiJSUzI1NiJ9." + "A" * 460 + "." + "B" * 43


class TestSigningVectors:
    @pytest.mark.parametrize("case", VECTORS["cases"])
    def test_matches_the_shared_vectors(self, case: dict[str, object]) -> None:
        key = RunnerKey.from_hex(VECTORS["key_hex"])
        body = str(case["body"]).encode()
        assert canonical_json(json.loads(body)) == body
        signature = sign(
            key,
            method="POST",
            path="/v1/runs",
            timestamp=int(str(case["timestamp"])),
            nonce=str(case["nonce"]),
            body=body,
        )
        assert signature == case["signature"]

    def test_a_changed_body_changes_the_signature(self) -> None:
        a = signed_headers(KEY, timestamp=1, nonce="0f" * 16, body=b"{}")
        b = signed_headers(KEY, timestamp=1, nonce="0f" * 16, body=b"{ }")
        assert a[HEADER_SIGNATURE] != b[HEADER_SIGNATURE]

    @pytest.mark.parametrize("nonce", ["", "abc", "0F" * 16, "0f" * 17])
    def test_malformed_nonce_is_refused(self, nonce: str) -> None:
        with pytest.raises(InvalidValueError):
            signed_headers(KEY, timestamp=1, nonce=nonce, body=b"{}")

    @pytest.mark.parametrize("value", ["", "zz" * 32, "AB" * 32, "ab" * 31])
    def test_key_format_is_strict(self, value: str) -> None:
        with pytest.raises(InvalidValueError):
            RunnerKey.from_hex(value)

    def test_key_is_not_in_repr(self) -> None:
        assert "abab" not in repr(KEY)


class TestRequests:
    def test_fetch_sends_a_signed_closed_body(self) -> None:
        runner, transport = client(ok())
        runner.fetch(run_id=RUN, repo=REPO, commit=SHA, token=TOKEN, limits=FETCH_LIMITS)
        headers, body, timeout, _ = transport.calls[0]
        assert json.loads(body) == {
            "step": "fetch",
            "run_id": RUN,
            "owner": "acme",
            "name": "app",
            "commit_sha": "a" * 40,
            "token": TOKEN,
            "max_bytes": FETCH_LIMITS.max_bytes,
            "max_files": FETCH_LIMITS.max_files,
            "timeout_seconds": 300,
        }
        assert body == canonical_json(json.loads(body))
        assert headers[HEADER_NONCE] == "0f" * 16
        assert headers[HEADER_TIMESTAMP] == str(int(FakeClock().now().timestamp()))
        assert headers[HEADER_SIGNATURE] == sign(
            KEY,
            method="POST",
            path="/v1/runs",
            timestamp=int(headers[HEADER_TIMESTAMP]),
            nonce=headers[HEADER_NONCE],
            body=body,
        )
        assert timeout > FETCH_LIMITS.max_duration

    @pytest.mark.parametrize("token", [TOKEN, LONG_TOKEN])
    def test_both_installation_token_formats_are_sent_intact(self, token: str) -> None:
        runner, transport = client(ok())
        runner.fetch(run_id=RUN, repo=REPO, commit=SHA, token=token, limits=FETCH_LIMITS)
        _, body, _, _ = transport.calls[0]
        assert json.loads(body)["token"] == token
        assert len(body) < MAX_REQUEST_BYTES

    def test_scan_and_release_bodies(self) -> None:
        runner, transport = client(ok(scan_reply()), ok())
        runner.scan(run_id=RUN, kind=ScannerKind("trivy"), limits=SCAN_LIMITS)
        runner.release(run_id=RUN)
        scan_body = json.loads(transport.calls[0][1])
        assert scan_body == {
            "step": "scan",
            "run_id": RUN,
            "scanner_kind": "trivy",
            "timeout_seconds": 900,
            "max_output_bytes": SCAN_LIMITS.max_output_bytes,
        }
        assert json.loads(transport.calls[1][1]) == {"step": "release", "run_id": RUN}

    @pytest.mark.parametrize("run_id", ["", "../x", "A" * 32, "a" * 31, RUN + "0", RUN + "\n"])
    def test_run_id_is_validated(self, run_id: str) -> None:
        runner, _ = client()
        with pytest.raises(InvalidValueError):
            runner.release(run_id=run_id)

    @pytest.mark.parametrize(
        "token", ["", "short", "ghs_" + "x" * 1030, "a b" * 10, TOKEN + "\n", LONG_TOKEN + " "]
    )
    def test_token_is_validated(self, token: str) -> None:
        runner, transport = client()
        with pytest.raises(InvalidValueError):
            runner.fetch(run_id=RUN, repo=REPO, commit=SHA, token=token, limits=FETCH_LIMITS)
        assert not transport.calls

    def test_the_token_is_never_in_an_error_message(self) -> None:
        runner, _ = client(error(500, "internal"))
        with pytest.raises(FetchUnavailableError) as raised:
            runner.fetch(run_id=RUN, repo=REPO, commit=SHA, token=TOKEN, limits=FETCH_LIMITS)
        assert TOKEN not in str(raised.value)

    def test_scan_response_limit_covers_base64(self) -> None:
        runner, transport = client(ok(scan_reply()))
        runner.scan(run_id=RUN, kind=ScannerKind("semgrep"), limits=SCAN_LIMITS)
        assert transport.calls[0][3] > SCAN_LIMITS.max_output_bytes * 4 // 3


class TestErrorMapping:
    def fetch(self, response: TransportResponse | TransportError) -> None:
        runner, _ = client(response)
        runner.fetch(run_id=RUN, repo=REPO, commit=SHA, token=TOKEN, limits=FETCH_LIMITS)

    def scan(self, response: TransportResponse | TransportError) -> None:
        runner, _ = client(response)
        runner.scan(run_id=RUN, kind=ScannerKind("semgrep"), limits=SCAN_LIMITS)

    def test_busy_is_transient_and_honours_retry_after(self) -> None:
        with pytest.raises(FetchUnavailableError) as raised:
            self.fetch(error(429, "busy", "17"))
        assert raised.value.retry_after == timedelta(seconds=17)

    def test_retry_after_is_capped(self) -> None:
        with pytest.raises(ScannerUnavailableError) as raised:
            self.scan(error(429, "busy", "999999"))
        assert raised.value.retry_after == timedelta(minutes=5)

    @pytest.mark.parametrize("raw", ["-1", "abc", "1.5", "", "٣", "1" * 7])
    def test_garbage_retry_after_is_ignored(self, raw: str) -> None:
        with pytest.raises(FetchUnavailableError) as raised:
            self.fetch(error(429, "busy", raw))
        assert raised.value.retry_after is None

    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (error(413, "too_large"), RepositoryTooLargeError),
            (error(422, "fetch_failed"), RepositoryFetchError),
            (error(400, "bad_request"), RepositoryFetchError),
            (error(503, "fetch_unavailable"), FetchUnavailableError),
            (error(504, "timeout"), FetchUnavailableError),
            (error(401, "unauthorized"), FetchUnavailableError),
            (error(403, "whatever"), FetchUnavailableError),
            (error(500, "internal"), FetchUnavailableError),
            (error(418, "never-heard-of-it"), FetchUnavailableError),
            (TransportResponse(502, b"<html>bad gateway</html>"), FetchUnavailableError),
            (TransportError("ConnectionRefusedError"), FetchUnavailableError),
        ],
    )
    def test_fetch_errors(
        self, response: TransportResponse | TransportError, expected: type[Exception]
    ) -> None:
        with pytest.raises(expected):
            self.fetch(response)

    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (error(504, "timeout"), ScanTimeoutError),
            (error(422, "scanner_failed"), ScannerExecutionError),
            (error(413, "output_too_large"), ScannerOutputInvalidError),
            (error(400, "bad_request"), ScannerExecutionError),
            (error(404, "unknown_workspace"), ScannerUnavailableError),
            (error(401, "unauthorized"), ScannerUnavailableError),
            (error(500, "internal"), ScannerUnavailableError),
            (TransportError("TimeoutError"), ScannerUnavailableError),
        ],
    )
    def test_scan_errors(
        self, response: TransportResponse | TransportError, expected: type[Exception]
    ) -> None:
        with pytest.raises(expected):
            self.scan(response)

    @pytest.mark.parametrize("body", [{"status": "no"}, {"status": "ok", "x": 1}, []])
    def test_fetch_requires_the_exact_ok_reply(self, body: object) -> None:
        with pytest.raises(RepositoryFetchError):
            self.fetch(ok(body))

    def test_fetch_rejects_non_json(self) -> None:
        with pytest.raises(RepositoryFetchError):
            self.fetch(TransportResponse(200, b"not json"))


class TestScanReply:
    def parse(self, payload: object) -> object:
        runner, _ = client(TransportResponse(200, json.dumps(payload).encode()))
        return runner.scan(run_id=RUN, kind=ScannerKind("semgrep"), limits=SCAN_LIMITS)

    def test_valid_reply(self) -> None:
        reply = self.parse(scan_reply())
        assert reply.stdout == b"{}"  # type: ignore[attr-defined]
        assert reply.scanner_version == "1.2.3"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            "text",
            {},
            {**scan_reply(), "extra": 1},
            {k: v for k, v in scan_reply().items() if k != "exit_code"},
            scan_reply(exit_code=True),
            scan_reply(exit_code="0"),
            scan_reply(scanner_version=3),
            scan_reply(image_digest=None),
            scan_reply(stdout_b64="not base64!!"),
            scan_reply(stdout_b64=5),
            scan_reply(
                stdout_b64=base64.b64encode(b"x" * (SCAN_LIMITS.max_output_bytes + 1)).decode()
            ),
        ],
    )
    def test_invalid_replies_are_rejected(self, payload: object) -> None:
        with pytest.raises(ScannerOutputInvalidError):
            self.parse(payload)

    def test_deeply_nested_json_is_rejected_without_crashing(self) -> None:
        runner, _ = client(TransportResponse(200, b"[" * 100_000))
        with pytest.raises(ScannerOutputInvalidError):
            runner.scan(run_id=RUN, kind=ScannerKind("semgrep"), limits=SCAN_LIMITS)


class TestHttpTransportUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://runner",
            "runner:8080",
            "http://",
            "http://u:p@runner",
            "http://runner/x",
            "http://runner?q=1",
            "http://runner#f",
            "",
        ],
    )
    def test_unsafe_urls_are_rejected(self, url: str) -> None:
        with pytest.raises(InvalidValueError):
            HttpTransport(url)

    @pytest.mark.parametrize(
        "url", ["http://scan-runner:8080", "https://runner.internal", "http://[::1]:9/"]
    )
    def test_valid_urls(self, url: str) -> None:
        HttpTransport(url)
