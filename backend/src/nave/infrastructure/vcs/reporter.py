"""Reporter de GitHub: check run, SARIF y comentario de PR (ADR 0014; AM-802, AM-808).

- Toda operación es idempotente: el check run se identifica por `external_id = scan.id`
  y el comentario por su marcador **más** el autor (el bot de esta App).
- Los textos se componen aquí o en `services.pr_comment` solo a partir de datos del
  dominio; nunca se incluye texto libre del escáner ni el `detail` del motivo de fallo.
- Un 401 invalida el token en caché y se reintenta una vez con uno nuevo.
- La subida de SARIF solo ocurre con `sarif_upload` activado (Code Security puede no
  estar contratado) y un rechazo de GitHub no hace fallar el reporte.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from nave.application.dto.vcs import PR_COMMENT_MARKER, PrCommentBody, VcsTarget
from nave.application.dto.views import FindingView
from nave.application.errors import VcsRejectedError
from nave.domain.entities.scan import Scan
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.vcs.app_identity import AppIdentity, GithubAppIdentityProvider
from nave.infrastructure.vcs.github_http import GithubApiClient, GithubReply, GithubRequest
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens
from nave.infrastructure.vcs.sarif_writer import build_sarif_payload

_LOG: Final = logging.getLogger("nave.github")
CHECK_NAME: Final = "NAVE"
PAGE_SIZE: Final = 100
MAX_COMMENT_PAGES: Final = 10
_HTTP_OK: Final = 200
_HTTP_CREATED: Final = 201
_HTTP_ACCEPTED: Final = 202
_HTTP_UNAUTHORIZED: Final = 401
# Code Scanning no disponible (403/404), fichero demasiado grande (413) o rechazado (422).
_SARIF_SKIPPED: Final = frozenset({403, 404, 413, 422})

_CONCLUSION: Final = {
    Verdict.PASS: "success",
    Verdict.FAIL: "failure",
    Verdict.NEUTRAL: "neutral",
}
_TITLE: Final = {
    Verdict.PASS: "La política de calidad se cumple",
    Verdict.FAIL: "La política de calidad bloquea este cambio",
    Verdict.NEUTRAL: "Resultado informativo",
}


class GithubVcsReporter:
    def __init__(
        self,
        *,
        api: GithubApiClient,
        tokens: GithubInstallationTokens,
        identity: GithubAppIdentityProvider,
        sarif_upload: bool,
    ) -> None:
        self._api = api
        self._tokens = tokens
        self._identity = identity
        self._sarif_upload = sarif_upload

    # --- check run -----------------------------------------------------------------

    def publish_check(self, target: VcsTarget, scan: Scan) -> None:
        app = self._identity.get()
        payload = _check_payload(scan)
        base = _repo_path(target)

        def operation(token: str) -> None:
            existing = self._find_check_run(base, scan, app, token)
            if existing is None:
                self._send(
                    "POST",
                    f"{base}/check-runs",
                    token,
                    {"name": CHECK_NAME, "head_sha": scan.commit.value, **payload},
                    _HTTP_CREATED,
                )
            else:
                self._send("PATCH", f"{base}/check-runs/{existing}", token, payload, _HTTP_OK)

        self._with_token(target, operation)

    def _find_check_run(self, base: str, scan: Scan, app: AppIdentity, token: str) -> int | None:
        reply = self._send(
            "GET",
            f"{base}/commits/{scan.commit.value}/check-runs"
            f"?check_name={CHECK_NAME}&per_page={PAGE_SIZE}",
            token,
            None,
            _HTTP_OK,
        )
        body = reply.body
        runs = body.get("check_runs") if isinstance(body, dict) else None
        if not isinstance(runs, list):
            return None
        for run in runs:
            if not isinstance(run, dict):
                continue
            owner = run.get("app")
            run_id = run.get("id")
            if (
                run.get("external_id") == str(scan.id.value)
                and isinstance(owner, dict)
                and owner.get("id") == app.app_id
                and isinstance(run_id, int)
                and not isinstance(run_id, bool)
            ):
                return run_id
        return None

    # --- SARIF ---------------------------------------------------------------------

    def upload_sarif(self, target: VcsTarget, scan: Scan, findings: Sequence[FindingView]) -> None:
        if not self._sarif_upload:
            return
        sarif = build_sarif_payload(findings)
        if sarif is None:
            return
        body = {
            "commit_sha": scan.commit.value,
            "ref": _qualified_ref(str(scan.ref)),
            "sarif": sarif.encoded,
        }

        def operation(token: str) -> None:
            try:
                self._send(
                    "POST",
                    f"{_repo_path(target)}/code-scanning/sarifs",
                    token,
                    body,
                    _HTTP_ACCEPTED,
                )
            except VcsRejectedError as error:
                if error.status not in _SARIF_SKIPPED:
                    raise
                _LOG.warning(
                    "sarif upload skipped",
                    extra={"status": error.status, "request_id": error.request_id},
                )

        self._with_token(target, operation)
        if sarif.omitted:
            _LOG.info("sarif truncated", extra={"omitted": sarif.omitted})

    # --- comentario de PR ----------------------------------------------------------

    def upsert_pr_comment(self, target: VcsTarget, scan: Scan, body: PrCommentBody) -> None:
        number = scan.pull_request_number
        if number is None:
            return
        app = self._identity.get()
        base = _repo_path(target)

        def operation(token: str) -> None:
            existing = self._find_comment(base, number, app, token)
            payload = {"body": body.markdown}
            if existing is None:
                self._send(
                    "POST", f"{base}/issues/{number}/comments", token, payload, _HTTP_CREATED
                )
            else:
                self._send("PATCH", f"{base}/issues/comments/{existing}", token, payload, _HTTP_OK)

        self._with_token(target, operation)

    def _find_comment(self, base: str, number: int, app: AppIdentity, token: str) -> int | None:
        for page in range(1, MAX_COMMENT_PAGES + 1):
            reply = self._send(
                "GET",
                f"{base}/issues/{number}/comments?per_page={PAGE_SIZE}&page={page}",
                token,
                None,
                _HTTP_OK,
            )
            comments = reply.body
            if not isinstance(comments, list):
                return None
            for comment in comments:
                found = _own_comment_id(comment, app)
                if found is not None:
                    return found
            if len(comments) < PAGE_SIZE:
                return None
        return None

    # --- común ---------------------------------------------------------------------

    def _with_token(self, target: VcsTarget, operation: Callable[[str], None]) -> None:
        token = self._tokens.report_token(installation_id=target.installation_id, repo=target.repo)
        try:
            operation(token)
        except VcsRejectedError as error:
            if error.status != _HTTP_UNAUTHORIZED:
                raise
            self._tokens.invalidate_report_token(
                installation_id=target.installation_id, repo=target.repo
            )
            fresh = self._tokens.report_token(
                installation_id=target.installation_id, repo=target.repo
            )
            operation(fresh)

    def _send(
        self, method: str, path: str, token: str, body: Mapping[str, object] | None, expected: int
    ) -> GithubReply:
        return self._api.send(
            GithubRequest(
                method=method,
                path=path,
                bearer=token,
                json_body=body,
                expected=frozenset({expected}),
            )
        )


def _repo_path(target: VcsTarget) -> str:
    return f"/repos/{target.repo.owner}/{target.repo.name}"


def _qualified_ref(ref: str) -> str:
    return ref if ref.startswith("refs/") else f"refs/heads/{ref}"


def _own_comment_id(comment: object, app: AppIdentity) -> int | None:
    if not isinstance(comment, dict):
        return None
    author = comment.get("user")
    text, comment_id = comment.get("body"), comment.get("id")
    if (
        isinstance(author, dict)
        and author.get("type") == "Bot"
        and author.get("login") == app.bot_login
        and isinstance(text, str)
        and text.startswith(PR_COMMENT_MARKER)
        and isinstance(comment_id, int)
        and not isinstance(comment_id, bool)
    ):
        return comment_id
    return None


def _instant(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _check_payload(scan: Scan) -> dict[str, object]:
    conclusion, title, summary = _check_text(scan)
    payload: dict[str, object] = {
        "external_id": str(scan.id.value),
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title, "summary": summary},
    }
    started, finished = _instant(scan.started_at), _instant(scan.finished_at)
    if started:
        payload["started_at"] = started
    if finished:
        payload["completed_at"] = finished
    return payload


def _check_text(scan: Scan) -> tuple[str, str, str]:
    if scan.status is ScanStatus.COMPLETED and scan.summary is not None:
        summary = scan.summary
        counts = " | ".join(
            f"{s.value} {summary.new.get(s)}" for s in sorted(Severity, reverse=True)
        )
        lines = [f"Hallazgos nuevos: {counts}"]
        if summary.truncated:
            lines.append("El resultado del escáner se truncó: puede haber más hallazgos.")
        return _CONCLUSION[summary.verdict], _TITLE[summary.verdict], "\n\n".join(lines)
    reason = scan.status_reason.code.value if scan.status_reason else "UNKNOWN"
    # Solo el código estable: `detail` puede derivar de la salida de un escáner.
    text = f"El escaneo no terminó. Motivo: `{reason}`."
    if scan.status is ScanStatus.TIMED_OUT:
        return "timed_out", "El escaneo superó el tiempo máximo", text
    if scan.status is ScanStatus.CANCELLED:
        return "cancelled", "El escaneo se canceló", text
    return "failure", "El escaneo falló", text
