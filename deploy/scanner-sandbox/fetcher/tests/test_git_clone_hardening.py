"""AM-504: el git endurecido se prueba con el git real, contra repositorios locales."""

import io
import os
import subprocess
from pathlib import Path

import pytest

import nave_fetch
from nave_fetch import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TOO_LARGE,
    EXIT_UNAVAILABLE,
    HARDENING,
    classify_failure,
    clone,
    git,
    git_environment,
    parse_arguments,
    read_token,
)

TOKEN = "ghs_" + "Z" * 36
SHA = "a" * 40
GOOD = [
    "--owner=acme",
    "--name=app",
    f"--sha={SHA}",
    "--max-bytes=1000",
    "--max-files=100",
    "--timeout=60",
]


def run(command: list[str], cwd: Path, **env: str) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(cwd),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            **env,
        },
    )
    return result.stdout


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Repositorio local con hook, symlink, submódulo declarado y fichero con nombre NTFS."""
    repo = tmp_path / "source"
    repo.mkdir()
    run(["git", "init", "-q", "-b", "main"], repo)
    run(["git", "config", "user.email", "t@t"], repo)
    run(["git", "config", "user.name", "t"], repo)
    (repo / "a.py").write_text("print(1)\n")
    (repo / "link").symlink_to("/etc/passwd")
    (repo / ".gitmodules").write_text('[submodule "x"]\n path = x\n url = https://evil.example/x\n')
    run(["git", "add", "-A"], repo)
    run(["git", "commit", "-q", "-m", "init"], repo)
    return repo


class TestArguments:
    def test_valid(self) -> None:
        assert parse_arguments(GOOD) is not None

    @pytest.mark.parametrize(
        "bad",
        [
            [*GOOD, "--extra=1"],
            GOOD[:-1],
            [*GOOD, GOOD[0]],
            ["--owner", "acme", *GOOD[1:]],
            [a if not a.startswith("--owner") else "--owner=-bad" for a in GOOD],
            [a if not a.startswith("--sha") else "--sha=main" for a in GOOD],
            [a if not a.startswith("--name") else "--name=a/b" for a in GOOD],
            [a if not a.startswith("--max-bytes") else "--max-bytes=-1" for a in GOOD],
            ["owner=acme", *GOOD[1:]],
            [],
        ],
    )
    def test_invalid(self, bad: list[str]) -> None:
        assert parse_arguments(bad) is None

    def test_a_name_with_leading_dashes_stays_a_value(self) -> None:
        parsed = parse_arguments([a if not a.startswith("--name") else "--name=--x" for a in GOOD])
        assert parsed is not None
        assert parsed["name"] == "--x"


class TestToken:
    def test_valid_token(self) -> None:
        assert read_token(io.StringIO(TOKEN + "\n")) == TOKEN

    @pytest.mark.parametrize("text", ["", "\n", "short\n", "a b" * 10 + "\n", "x" * 1200])
    def test_invalid_token(self, text: str) -> None:
        assert read_token(io.StringIO(text)) is None

    def test_git_receives_the_token_only_through_its_environment(self) -> None:
        env = git_environment(TOKEN)
        assert TOKEN not in " ".join(HARDENING)
        assert env["GIT_CONFIG_KEY_0"].startswith("http.https://github.com/")
        assert "Basic" in env["GIT_CONFIG_VALUE_0"]
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_TERMINAL_PROMPT"] == "0"


class TestHardeningWithRealGit:
    def test_every_setting_is_applied(self, tmp_path: Path) -> None:
        run(["git", "init", "-q"], tmp_path)
        command = ["git"]
        for setting in HARDENING:
            command += ["-c", setting]
        listing = run([*command, "config", "--list"], tmp_path).lower()
        for expected in (
            "core.hookspath=/dev/null",
            "core.symlinks=false",
            "protocol.allow=never",
            "protocol.https.allow=always",
            "transfer.fsckobjects=true",
            "core.protectntfs=true",
            "http.followredirects=false",
        ):
            assert expected in listing

    def test_hooks_do_not_run_on_checkout(self, source: Path, tmp_path: Path) -> None:
        marker = tmp_path / "hook-ran"
        hook = source / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        work = tmp_path / "work"
        run(["git", "clone", "-q", "--no-checkout", str(source), str(work)], tmp_path)
        shutil_hook = work / ".git" / "hooks" / "post-checkout"
        shutil_hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        shutil_hook.chmod(0o755)
        code, _ = git(
            ["checkout", "--quiet", "--force", "main"],
            {
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            work,
            30,
        )
        assert code == 0
        assert not marker.exists()

    def test_symlinks_are_materialised_as_plain_files(self, source: Path, tmp_path: Path) -> None:
        work = tmp_path / "work"
        run(["git", "clone", "-q", "--no-checkout", str(source), str(work)], tmp_path)
        code, _ = git(
            ["checkout", "--quiet", "--force", "main"],
            {
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            work,
            30,
        )
        assert code == 0
        link = work / "link"
        assert link.exists()
        assert not link.is_symlink()
        assert link.read_text() == "/etc/passwd"

    def test_other_protocols_are_blocked(self, source: Path, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        assert git(["init", "-q"], env, work, 30)[0] == 0
        assert git(["remote", "add", "origin", f"file://{source}"], env, work, 30)[0] == 0
        code, stderr = git(["fetch", "--depth=1", "origin", "main"], env, work, 30)
        assert code != 0
        assert "protocol" in stderr or "not allowed" in stderr


class TestClassification:
    def test_no_space_is_too_large(self, tmp_path: Path) -> None:
        assert (
            classify_failure("fatal: write error: no space left on device", tmp_path)
            == EXIT_TOO_LARGE
        )

    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: unable to access: could not resolve host: github.com",
            "rpc failed; curl 56",
            "error: the requested url returned error: 503",
        ],
    )
    def test_network_problems_are_transient(self, stderr: str, tmp_path: Path) -> None:
        assert classify_failure(stderr, tmp_path) == EXIT_UNAVAILABLE

    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: repository not found",
            "error: the requested url returned error: 403",
            "",
        ],
    )
    def test_everything_else_is_permanent(self, stderr: str, tmp_path: Path) -> None:
        assert classify_failure(stderr, tmp_path) == EXIT_FAILED


class TestClone:
    def args(self) -> dict[str, str]:
        parsed = parse_arguments(GOOD)
        assert parsed is not None
        return parsed

    def fake_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        outcomes: list[tuple[int, str]],
        calls: list[list[str]],
    ) -> None:
        def fake(
            arguments: list[str], env: dict[str, str], cwd: Path, timeout: int
        ) -> tuple[int, str]:
            calls.append(arguments)
            if arguments[0] == "checkout":
                (cwd / ".git").mkdir(exist_ok=True)
                (cwd / "a.py").write_text("x")
            return outcomes.pop(0) if outcomes else (0, "")

        monkeypatch.setattr(nave_fetch, "git", fake)

    def test_success_removes_dot_git_and_keeps_the_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        self.fake_git(monkeypatch, [], calls)
        assert clone(self.args(), TOKEN, tmp_path) == EXIT_OK
        assert (tmp_path / "repo" / "a.py").exists()
        assert not (tmp_path / "repo" / ".git").exists()
        assert [c[0] for c in calls] == ["init", "remote", "fetch", "checkout"]
        assert "https://github.com/acme/app.git" in calls[1]
        assert SHA in calls[2]
        assert all(TOKEN not in " ".join(c) for c in calls)

    def test_fetches_exactly_one_commit_without_submodules_or_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        self.fake_git(monkeypatch, [], calls)
        clone(self.args(), TOKEN, tmp_path)
        fetch = calls[2]
        assert {"--depth=1", "--no-tags", "--no-recurse-submodules"} <= set(fetch)

    def test_git_failure_is_classified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake_git(monkeypatch, [(0, ""), (0, ""), (128, "fatal: repository not found")], [])
        assert clone(self.args(), TOKEN, tmp_path) == EXIT_FAILED

    def test_timeout_is_transient(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def slow(*_: object) -> tuple[int, str]:
            raise subprocess.TimeoutExpired("git", 1)

        monkeypatch.setattr(nave_fetch, "git", slow)
        assert clone(self.args(), TOKEN, tmp_path) == EXIT_UNAVAILABLE

    def test_too_many_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake_git(monkeypatch, [], [])
        args = {**self.args(), "max-files": "0"}
        assert clone(args, TOKEN, tmp_path) == EXIT_TOO_LARGE

    def test_main_rejects_bad_input_without_running_git(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_: object) -> None:
            raise AssertionError("git must not run")

        monkeypatch.setattr(nave_fetch, "git", fail)
        monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
        assert nave_fetch.main(["--owner=acme"]) == EXIT_FAILED
        monkeypatch.setattr("sys.stdin", io.StringIO("bad\n"))
        assert nave_fetch.main(GOOD) == EXIT_FAILED
