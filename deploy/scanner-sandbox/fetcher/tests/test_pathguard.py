import os
from pathlib import Path

import pytest

from pathguard import PathEscapeError, TreeTooLargeError, resolve_inside, sanitize_tree


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "repo" / "src").mkdir(parents=True)
    (tmp_path / "repo" / "src" / "a.py").write_text("print(1)")
    return tmp_path / "repo"


class TestResolveInside:
    def test_accepts_a_file_inside(self, root: Path) -> None:
        assert resolve_inside(root, "src/a.py") == (root / "src" / "a.py").resolve()

    @pytest.mark.parametrize(
        "candidate",
        [
            "../x",
            "src/../../x",
            "/etc/passwd",
            "src/\x00a.py",
            "..",
            "a/../..",
            "missing.py",
        ],
    )
    def test_rejects_escapes_and_nonexistent_paths(self, root: Path, candidate: str) -> None:
        with pytest.raises(PathEscapeError):
            resolve_inside(root, candidate)

    def test_rejects_a_symlink_that_leaves_the_root(self, root: Path, tmp_path: Path) -> None:
        outside = tmp_path / "secret.txt"
        outside.write_text("s")
        (root / "link").symlink_to(outside)
        with pytest.raises(PathEscapeError):
            resolve_inside(root, "link")

    def test_rejects_a_directory_symlink_used_as_a_prefix(self, root: Path, tmp_path: Path) -> None:
        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "f").write_text("x")
        (root / "d").symlink_to(tmp_path / "other")
        with pytest.raises(PathEscapeError):
            resolve_inside(root, "d/f")

    def test_a_symlink_that_stays_inside_is_resolved_to_its_target(self, root: Path) -> None:
        (root / "alias").symlink_to(root / "src" / "a.py")
        assert resolve_inside(root, "alias") == (root / "src" / "a.py").resolve()

    def test_a_symlink_loop_is_rejected(self, root: Path) -> None:
        (root / "a").symlink_to(root / "b")
        (root / "b").symlink_to(root / "a")
        with pytest.raises(PathEscapeError):
            resolve_inside(root, "a")

    def test_a_sibling_directory_with_the_same_prefix_is_outside(self, tmp_path: Path) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "repo-evil").mkdir()
        (tmp_path / "repo-evil" / "f").write_text("x")
        (tmp_path / "repo" / "l").symlink_to(tmp_path / "repo-evil" / "f")
        with pytest.raises(PathEscapeError):
            resolve_inside(tmp_path / "repo", "l")


class TestSanitizeTree:
    def test_counts_regular_files(self, root: Path) -> None:
        (root / "b.txt").write_text("12345")
        stats = sanitize_tree(root, max_files=10, max_bytes=1000)
        assert (
            stats.files,
            stats.bytes,
            stats.removed_links,
            stats.removed_special,
        ) == (2, 13, 0, 0)

    def test_removes_symlinks_without_following_them(self, root: Path, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("do not read")
        (root / "to-secret").symlink_to(secret)
        (root / "to-dir").symlink_to(tmp_path, target_is_directory=True)
        (root / "dangling").symlink_to(tmp_path / "missing")
        stats = sanitize_tree(root, max_files=10, max_bytes=1000)
        assert stats.removed_links == 3
        assert not any(p.is_symlink() for p in root.rglob("*"))
        assert secret.read_text() == "do not read"
        assert tmp_path.exists()

    def test_removes_fifos(self, root: Path) -> None:
        os.mkfifo(root / "pipe")
        stats = sanitize_tree(root, max_files=10, max_bytes=1000)
        assert stats.removed_special == 1
        assert not (root / "pipe").exists()

    def test_file_count_limit(self, root: Path) -> None:
        for i in range(5):
            (root / f"f{i}").write_text("x")
        with pytest.raises(TreeTooLargeError):
            sanitize_tree(root, max_files=3, max_bytes=1000)

    def test_byte_limit(self, root: Path) -> None:
        (root / "big").write_bytes(b"x" * 500)
        with pytest.raises(TreeTooLargeError):
            sanitize_tree(root, max_files=10, max_bytes=100)

    def test_limits_are_inclusive(self, root: Path) -> None:
        stats = sanitize_tree(root, max_files=1, max_bytes=8)
        assert stats.files == 1

    def test_deep_trees_are_handled(self, root: Path) -> None:
        deep = root
        for _ in range(60):
            deep /= "d"
        deep.mkdir(parents=True)
        (deep / "f").write_text("x")
        assert sanitize_tree(root, max_files=10, max_bytes=1000).files == 2
