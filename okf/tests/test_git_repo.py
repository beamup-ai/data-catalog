"""Tests for aws_reference_agent.git.repo.

All tests are hermetic -- no network access. A real local git repository is
built in tmp_path using a helper that shells out to the real `git` binary with
minimal config so the tests work on any machine.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aws_reference_agent.git.repo import (
    GitError,
    cleanup,
    list_files,
    open_checkout,
    read_file,
    search,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_AUTHOR_DATE = "2024-03-15T10:00:00+00:00"


def _git(*args: str, cwd: Path | None = None) -> None:
    """Run a git command in `cwd`, raising on failure."""
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _make_repo(root: Path) -> Path:
    """Initialise a bare-enough git repo at `root` and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def _commit(root: Path, message: str = "initial") -> None:
    """Stage everything and commit with a fixed author date."""
    _git("add", ".", cwd=root)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": _AUTHOR_DATE,
        "GIT_COMMITTER_DATE": _AUTHOR_DATE,
    }
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            message,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        env=env,
    )


def _write(root: Path, rel: str, text: str = "content") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# open_checkout -- local directory
# ---------------------------------------------------------------------------


def test_should_open_in_place_when_given_an_existing_repo_directory(tmp_path):
    repo = _make_repo(tmp_path / "myrepo")
    _write(repo, "hello.py", "print('hello')")
    _commit(repo)

    co = open_checkout(repo)

    assert co.root == repo.resolve()
    assert co.cloned is False
    assert co.origin == str(repo.resolve())
    assert len(co.sha) == 40
    assert co.sha.isalnum()


def test_should_resolve_to_repo_toplevel_when_given_a_subdirectory(tmp_path):
    repo = _make_repo(tmp_path / "myrepo")
    subdir = repo / "src"
    subdir.mkdir()
    _write(repo, "src/mod.py", "x = 1")
    _commit(repo)

    co = open_checkout(subdir)

    assert co.root == repo.resolve()


def test_should_raise_when_ref_given_for_local_checkout(tmp_path):
    repo = _make_repo(tmp_path / "myrepo")
    _write(repo, "f.py", "x=1")
    _commit(repo)

    with pytest.raises(GitError, match="ref"):
        open_checkout(repo, ref="main")


def test_should_raise_on_nonexistent_remote_without_hanging(tmp_path):
    dest = tmp_path / "dest"
    with pytest.raises(GitError):
        open_checkout("/nonexistent/x.git", dest=dest)


# ---------------------------------------------------------------------------
# open_checkout -- shallow clone (hermetic, local source)
# ---------------------------------------------------------------------------


def test_should_shallow_clone_a_remote_url(tmp_path):
    # A file:// URL is not a directory path, so it takes the clone branch —
    # which exercises the real `git clone` without touching the network.
    source = _make_repo(tmp_path / "source")
    _write(source, "main.py", "x = 1")
    _commit(source)
    url = source.resolve().as_uri()

    dest = tmp_path / "clone"
    co = open_checkout(url, dest=dest)

    assert co.cloned is True
    assert co.root == dest.resolve()
    assert co.origin == url
    assert len(co.sha) == 40


def test_should_ignore_dest_and_open_in_place_when_target_is_a_directory(tmp_path):
    # We never copy the operator's own working tree, so a local path wins over
    # any dest the caller supplied.
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "main.py", "x = 1")
    _commit(repo)

    co = open_checkout(repo, dest=tmp_path / "unused")

    assert co.cloned is False
    assert co.root == repo.resolve()
    assert not (tmp_path / "unused").exists()


def test_should_cleanup_cloned_repo_and_leave_local_intact(tmp_path):
    source = _make_repo(tmp_path / "source")
    _write(source, "main.py", "x = 1")
    _commit(source)

    dest = tmp_path / "clone"
    cloned = open_checkout(source.resolve().as_uri(), dest=dest)
    local = open_checkout(source)

    assert cloned.root.exists()
    cleanup(cloned)
    assert not cloned.root.exists()

    # cleanup of a non-cloned checkout is a no-op
    cleanup(local)
    assert local.root.exists()


def test_should_be_safe_to_call_cleanup_twice(tmp_path):
    source = _make_repo(tmp_path / "source")
    _write(source, "main.py", "x = 1")
    _commit(source)

    dest = tmp_path / "clone"
    co = open_checkout(source.resolve().as_uri(), dest=dest)
    cleanup(co)
    cleanup(co)  # must not raise


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _repo_with_files(tmp_path: Path) -> object:
    """Return an open_checkout of a repo with a few files."""
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "alpha.py", "def foo():\n    return 42\n")
    _write(repo, "beta.py", "def bar():\n    return foo()\n")
    _write(repo, "notes.md", "# Notes\n\nfoo is a function\n")
    _commit(repo)
    return open_checkout(repo)


def test_should_find_matching_term_with_correct_metadata(tmp_path):
    co = _repo_with_files(tmp_path)

    result = search(co, "foo")

    paths = {h.rel_path for h in result.hits}
    assert "alpha.py" in paths
    assert all(isinstance(h.line, int) and h.line >= 1 for h in result.hits)
    assert all(isinstance(h.text, str) and len(h.text) > 0 for h in result.hits)


def test_should_return_empty_result_when_no_matches(tmp_path):
    co = _repo_with_files(tmp_path)

    result = search(co, "ZZZNOMATCH999")

    assert result.hits == []
    assert result.truncated_count == 0


def test_should_respect_max_hits_and_report_truncated_count(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    # 10 lines all containing the word "needle"
    lines = "\n".join(f"needle{i} = {i}" for i in range(10))
    _write(repo, "data.py", lines)
    _commit(repo)
    co = open_checkout(repo)

    result = search(co, "needle", max_hits=3)

    assert len(result.hits) == 3
    assert result.truncated_count == 7


def test_should_narrow_results_with_path_glob(tmp_path):
    co = _repo_with_files(tmp_path)

    result = search(co, "foo", path_glob="*.md")

    assert all(h.rel_path.endswith(".md") for h in result.hits)


def test_should_support_regex_search(tmp_path):
    co = _repo_with_files(tmp_path)

    result = search(co, r"def \w+", regex=True)

    assert len(result.hits) >= 2


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_should_return_only_allowed_suffixes_sorted(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "b.py", "x=1")
    _write(repo, "a.py", "y=2")
    _write(repo, "image.png", "binary")  # not in _CODE_SUFFIXES
    _write(repo, "notes.md", "# hi")
    _commit(repo)
    co = open_checkout(repo)

    fl = list_files(co)

    assert fl.paths == ["a.py", "b.py", "notes.md"]
    assert fl.truncated_count == 0


def test_should_truncate_file_list_and_report_count(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    for i in range(5):
        _write(repo, f"file{i}.py", f"x={i}")
    _commit(repo)
    co = open_checkout(repo)

    fl = list_files(co, max_files=2)

    assert len(fl.paths) == 2
    assert fl.truncated_count == 3


def test_should_apply_path_glob_to_file_list(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "src/main.py", "x=1")
    _write(repo, "tests/test_main.py", "y=2")
    _commit(repo)
    co = open_checkout(repo)

    fl = list_files(co, path_glob="src/*")

    assert fl.paths == ["src/main.py"]


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def test_should_read_file_with_correct_metadata(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    content = "x = 1\n"
    _write(repo, "mod.py", content)
    _commit(repo)
    co = open_checkout(repo)

    rf = read_file(co, "mod.py")

    assert rf.rel_path == "mod.py"
    assert rf.text == content
    assert rf.bytes == len(content.encode("utf-8"))
    assert rf.sha == co.sha


def test_should_set_last_commit_to_iso8601_author_date(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "mod.py", "x=1")
    _commit(repo)
    co = open_checkout(repo)

    rf = read_file(co, "mod.py")

    # ISO-8601 format: contains a 'T' and a timezone offset
    assert "T" in rf.last_commit
    # The commit was made with _AUTHOR_DATE
    assert rf.last_commit.startswith("2024-03-15T")


def test_should_report_true_bytes_when_text_was_truncated(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    content = "x" * 500
    _write(repo, "big.py", content)
    _commit(repo)
    co = open_checkout(repo)

    rf = read_file(co, "big.py", max_bytes=100)

    assert "[...truncated...]" in rf.text
    assert rf.bytes == 500


def test_should_reject_parent_traversal(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "mod.py", "x=1")
    _commit(repo)
    co = open_checkout(repo)

    with pytest.raises(GitError, match="outside"):
        read_file(co, "../secret")


def test_should_reject_absolute_path_outside_root(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "mod.py", "x=1")
    _commit(repo)
    co = open_checkout(repo)

    outside = str(tmp_path / "other.py")
    with pytest.raises(GitError, match="outside"):
        read_file(co, outside)


def test_should_reject_untracked_file(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "tracked.py", "x=1")
    _commit(repo)
    # write a file but do NOT commit it
    _write(repo, "untracked.py", "y=2")
    co = open_checkout(repo)

    with pytest.raises(GitError):
        read_file(co, "untracked.py")


def test_should_reject_a_tracked_symlink_pointing_outside_the_root(tmp_path):
    # The whole point of resolving before the containment check: a symlink is
    # tracked and inside the root by name, but its target is not.
    secret = tmp_path / "secret.py"
    secret.write_text("password = 1", encoding="utf-8")
    repo = _make_repo(tmp_path / "repo")
    (repo / "link.py").symlink_to(secret)
    _commit(repo)
    co = open_checkout(repo)

    with pytest.raises(GitError, match="outside"):
        read_file(co, "link.py")


def test_should_reject_disallowed_suffix(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _write(repo, "image.png", "binary")
    _commit(repo)
    co = open_checkout(repo)

    with pytest.raises(GitError, match="suffix"):
        read_file(co, "image.png")
