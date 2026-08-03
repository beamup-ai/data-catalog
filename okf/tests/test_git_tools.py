"""Tests for aws_reference_agent.tools.git_tools.

All tests are hermetic -- no network. A real git repository is built in tmp_path
using the same helper style as test_git_repo.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aws_reference_agent.git.repo import open_checkout
from aws_reference_agent.tools.context import (
    clear_git_state,
    get_git_state,
    is_augmenting_pass,
    set_git_state,
)
from aws_reference_agent.tools.git_tools import (
    list_repo_files,
    read_repo_file,
    search_repo,
)


# ---------------------------------------------------------------------------
# helpers  (duplicated from test_git_repo.py — intentional, no shared fixture)
# ---------------------------------------------------------------------------

_AUTHOR_DATE = "2024-03-15T10:00:00+00:00"


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def _commit(root: Path, message: str = "initial") -> None:
    _git("add", ".", cwd=root)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": _AUTHOR_DATE,
        "GIT_COMMITTER_DATE": _AUTHOR_DATE,
    }
    subprocess.run(
        [
            "git",
            "-c", "user.email=test@example.com",
            "-c", "user.name=Test",
            "commit",
            "-m", message,
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
# autouse fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_git_state()


# ---------------------------------------------------------------------------
# helpers that build a state from a temporary repo
# ---------------------------------------------------------------------------


def _make_checkout(tmp_path: Path, files: dict[str, str] | None = None):
    repo = _make_repo(tmp_path / "repo")
    for rel, text in (files or {"main.py": "x = 1\n"}).items():
        _write(repo, rel, text)
    _commit(repo)
    return open_checkout(repo)


# ---------------------------------------------------------------------------
# search_repo
# ---------------------------------------------------------------------------


def test_search_repo_returns_hits_with_path_line_and_text(tmp_path):
    co = _make_checkout(tmp_path, {"main.py": "needle = 42\nother = 1\n"})
    set_git_state(co)

    result = search_repo("needle")

    assert "error" not in result
    assert result["count"] == 1
    hit = result["hits"][0]
    assert hit["path"] == "main.py"
    assert hit["line"] == 1
    assert "needle" in hit["text"]


def test_search_repo_increments_search_count(tmp_path):
    co = _make_checkout(tmp_path)
    set_git_state(co)

    search_repo("x")

    assert get_git_state().search_count == 1


def test_search_repo_rejects_when_max_searches_exhausted(tmp_path):
    co = _make_checkout(tmp_path)
    set_git_state(co, max_searches=1)

    search_repo("x")           # consumes the only budget
    result = search_repo("x")  # should be rejected

    assert "error" in result
    assert "max_searches reached" in result["error"]


def test_search_repo_failed_search_still_consumes_budget(tmp_path):
    co = _make_checkout(tmp_path)
    set_git_state(co, max_searches=1)

    # Inject an invalid regex to force a GitError path.
    result = search_repo("[invalid", regex=True)

    # A failure returns an error, and the budget was consumed.
    assert "error" in result
    from aws_reference_agent.tools.context import get_git_state
    assert get_git_state().search_count == 1

    # The next call is rejected because the budget is now at the limit.
    result2 = search_repo("x")
    assert "max_searches reached" in result2["error"]


# ---------------------------------------------------------------------------
# list_repo_files
# ---------------------------------------------------------------------------


def test_list_repo_files_returns_paths_and_truncated_count(tmp_path):
    co = _make_checkout(tmp_path, {f"f{i}.py": f"x={i}" for i in range(5)})
    set_git_state(co, max_files=3)

    result = list_repo_files()

    assert "error" not in result
    assert result["count"] == 3
    assert result["truncated_count"] == 2
    assert all(p.endswith(".py") for p in result["paths"])


def test_list_repo_files_with_path_glob_filters_results(tmp_path):
    co = _make_checkout(tmp_path, {"src/a.py": "x=1", "tests/b.py": "y=2"})
    set_git_state(co)

    result = list_repo_files(path_glob="src/*")

    assert result["paths"] == ["src/a.py"]


# ---------------------------------------------------------------------------
# read_repo_file
# ---------------------------------------------------------------------------


def test_read_repo_file_returns_text_and_provenance(tmp_path):
    co = _make_checkout(tmp_path, {"mod.py": "x = 1\n"})
    set_git_state(co)

    result = read_repo_file("mod.py")

    assert "error" not in result
    assert result["text"] == "x = 1\n"
    # provenance: "<origin>@<12-char sha>:<path>"
    expected = f"{co.origin}@{co.sha[:12]}:mod.py"
    assert result["provenance"] == expected


def test_read_repo_file_last_commit_is_git_author_date(tmp_path):
    co = _make_checkout(tmp_path, {"mod.py": "x=1\n"})
    set_git_state(co)

    result = read_repo_file("mod.py")

    assert result["last_commit"].startswith("2024-03-15T")


def test_read_repo_file_rejects_second_read_of_same_path(tmp_path):
    co = _make_checkout(tmp_path, {"mod.py": "x=1\n"})
    set_git_state(co)

    read_repo_file("mod.py")
    result = read_repo_file("mod.py")

    assert "error" in result
    assert "already read" in result["error"]


def test_read_repo_file_rejects_when_max_files_reached(tmp_path):
    co = _make_checkout(tmp_path, {"a.py": "a=1\n", "b.py": "b=2\n"})
    set_git_state(co, max_files=1)

    read_repo_file("a.py")
    result = read_repo_file("b.py")

    assert "error" in result
    assert "max_files reached" in result["error"]


def test_read_repo_file_rejects_traversal_with_error_dict(tmp_path):
    co = _make_checkout(tmp_path, {"mod.py": "x=1\n"})
    set_git_state(co)

    result = read_repo_file("../escape")

    assert "error" in result
    assert isinstance(result["error"], str)


def test_read_repo_file_truncated_true_when_file_exceeds_max_bytes(tmp_path):
    content = "x" * 500
    co = _make_checkout(tmp_path, {"big.py": content})
    set_git_state(co, max_bytes=100)

    result = read_repo_file("big.py")

    assert result["truncated"] is True
    assert result["bytes"] == 500


# ---------------------------------------------------------------------------
# is_augmenting_pass
# ---------------------------------------------------------------------------


def test_is_augmenting_pass_is_true_while_git_state_is_set(tmp_path):
    co = _make_checkout(tmp_path)
    assert not is_augmenting_pass()
    set_git_state(co)
    assert is_augmenting_pass()
