"""GitHub simulado con estado: tokens, check runs, comentarios y subidas de SARIF."""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from nave.infrastructure.vcs.github_http import RawResponse
from tests.fakes.github import Call, FakeGithubTransport, Reply, response
from tests.fakes.memory import FakeClock

APP_ID = 99
APP_SLUG = "nave-app"
BOT = f"{APP_SLUG}[bot]"
_REPO = r"/repos/(?P<o>[^/]+)/(?P<r>[^/?]+)"


@dataclass
class FakeGithub:
    clock: FakeClock
    check_runs: list[dict[str, object]] = field(default_factory=list)
    comments: list[dict[str, object]] = field(default_factory=list)
    sarifs: list[dict[str, object]] = field(default_factory=list)
    valid_tokens: set[str] = field(default_factory=set)
    minted: int = 0
    sarif_status: int = 202
    unauthorized_once: bool = False
    next_id: int = 1000
    installations: dict[int, dict[str, object]] = field(default_factory=dict)
    installation_repos: dict[int, list[dict[str, object]]] = field(default_factory=dict)
    mint_extra_permission: str | None = None
    token_installation: dict[str, int] = field(default_factory=dict)

    def transport(self) -> FakeGithubTransport:
        return FakeGithubTransport(handler=self.handle)

    def add_comment(self, body: str, *, login: str, kind: str = "User") -> int:
        self.next_id += 1
        self.comments.append(
            {"id": self.next_id, "body": body, "user": {"login": login, "type": kind}}
        )
        return self.next_id

    # --- enrutado ------------------------------------------------------------------

    def handle(self, call: Call) -> Reply:  # noqa: PLR0911, PLR0912 - un retorno por ruta
        url = urlsplit(call.path)
        path, query = url.path, parse_qs(url.query)
        if call.method == "POST" and re.fullmatch(r"/app/installations/\d+/access_tokens", path):
            return self._mint(call)
        if call.method == "GET" and (m := re.fullmatch(r"/app/installations/(\d+)", path)):
            found = self.installations.get(int(m.group(1)))
            return response(200, found) if found else response(404, {"message": "Not Found"})
        if call.method == "GET" and path == "/app/installations":
            return self._page(list(self.installations.values()), query)
        if call.method == "GET" and path == "/installation/repositories":
            return self._list_installation_repos(call, query)
        if path == "/app":
            return response(200, {"id": APP_ID, "slug": APP_SLUG})
        if path == "/installation/token":
            self.valid_tokens.discard(call.bearer)
            return response(204)
        if call.bearer not in self.valid_tokens or self._take_unauthorized():
            return response(401, {"message": "Bad credentials"})
        if re.fullmatch(_REPO + r"/commits/[0-9a-f]{40}/check-runs", path):
            return self._list_check_runs(query)
        if call.method == "POST" and re.fullmatch(_REPO + "/check-runs", path):
            return self._create_check_run(call)
        if call.method == "PATCH" and (m := re.fullmatch(_REPO + r"/check-runs/(\d+)", path)):
            return self._patch(self.check_runs, int(m.group(3)), call)
        if call.method == "GET" and re.fullmatch(_REPO + r"/issues/\d+/comments", path):
            return self._list_comments(query)
        if call.method == "POST" and re.fullmatch(_REPO + r"/issues/\d+/comments", path):
            return self._create_comment(call)
        if call.method == "PATCH" and (m := re.fullmatch(_REPO + r"/issues/comments/(\d+)", path)):
            return self._patch(self.comments, int(m.group(3)), call)
        if call.method == "POST" and re.fullmatch(_REPO + "/code-scanning/sarifs", path):
            return self._upload_sarif(call)
        return response(404, {"message": "Not Found"})

    # --- rutas ---------------------------------------------------------------------

    def _take_unauthorized(self) -> bool:
        taken, self.unauthorized_once = self.unauthorized_once, False
        return taken

    @staticmethod
    def _page(items: list[dict[str, object]], query: dict[str, list[str]]) -> RawResponse:
        page = int(query.get("page", ["1"])[0])
        size = int(query.get("per_page", ["30"])[0])
        return response(200, items[(page - 1) * size : page * size])

    def _list_installation_repos(self, call: Call, query: dict[str, list[str]]) -> RawResponse:
        if call.bearer not in self.valid_tokens:
            return response(401, {"message": "Bad credentials"})
        repos = self.installation_repos.get(self.token_installation[call.bearer], [])
        page = int(query.get("page", ["1"])[0])
        size = int(query.get("per_page", ["30"])[0])
        return response(
            200, {"total_count": len(repos), "repositories": repos[(page - 1) * size : page * size]}
        )

    def _mint(self, call: Call) -> RawResponse:
        body = call.json
        assert isinstance(body, dict)
        self.minted += 1
        token = f"ghs_{self.minted:036d}"
        self.valid_tokens.add(token)
        self.token_installation[token] = int(call.path.split("/")[3])
        expires = (self.clock.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        permissions = dict(body["permissions"])
        if self.mint_extra_permission:
            permissions[self.mint_extra_permission] = "write"
        payload: dict[str, object] = {
            "token": token,
            "expires_at": expires,
            "repository_selection": "selected" if "repositories" in body else "all",
            "permissions": permissions,
        }
        if "repositories" in body:
            payload["repositories"] = [{"name": n} for n in body["repositories"]]
        return response(201, payload)

    def _list_check_runs(self, query: dict[str, list[str]]) -> RawResponse:
        name = query.get("check_name", [None])[0]
        runs = [r for r in self.check_runs if name is None or r["name"] == name]
        return response(200, {"total_count": len(runs), "check_runs": runs})

    def _create_check_run(self, call: Call) -> RawResponse:
        body = call.json
        assert isinstance(body, dict)
        self.next_id += 1
        run = {**body, "id": self.next_id, "app": {"id": APP_ID}}
        self.check_runs.append(run)
        return response(201, run)

    def _list_comments(self, query: dict[str, list[str]]) -> RawResponse:
        page = int(query.get("page", ["1"])[0])
        size = int(query.get("per_page", ["30"])[0])
        return response(200, self.comments[(page - 1) * size : page * size])

    def _create_comment(self, call: Call) -> RawResponse:
        body = call.json
        assert isinstance(body, dict)
        self.next_id += 1
        comment = {"id": self.next_id, "body": body["body"], "user": {"login": BOT, "type": "Bot"}}
        self.comments.append(comment)
        return response(201, comment)

    @staticmethod
    def _patch(items: list[dict[str, object]], item_id: int, call: Call) -> RawResponse:
        body = call.json
        assert isinstance(body, dict)
        for item in items:
            if item["id"] == item_id:
                item.update(body)
                return response(200, item)
        return response(404, {"message": "Not Found"})

    def _upload_sarif(self, call: Call) -> RawResponse:
        if self.sarif_status != 202:
            return response(self.sarif_status, {"message": "Code scanning is not enabled"})
        body = call.json
        assert isinstance(body, dict)
        self.sarifs.append(body)
        return response(202, {"id": "abc"})
