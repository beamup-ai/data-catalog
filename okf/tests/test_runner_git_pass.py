"""Tests for the git ingestion pass in ReferenceRunner.

Hermetic: a real local git repo is built in tmp_path, and a `file://` URI of it
exercises the clone path without touching the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aws_reference_agent import runner as runner_mod
from aws_reference_agent.runner import ReferenceRunner
from aws_reference_agent.tools.context import clear_git_state, is_git_pass


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_git_state()


def _make_repo(tmp_path: Path, subdir: str = "repo") -> Path:
    root = tmp_path / subdir
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    (root / "etl.sql").write_text("SELECT * FROM users\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-m",
            "init",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _stub_query(monkeypatch, calls: list[dict]):
    async def _fake_query(*, prompt, options):
        calls.append({"prompt": prompt, "git_pass": is_git_pass()})
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(runner_mod, "query", _fake_query)


def _runner(tmp_path: Path, **kwargs) -> ReferenceRunner:
    src = MagicMock()
    src.list_concepts.return_value = []
    return ReferenceRunner(source=src, bundle_root=tmp_path / "bundle", **kwargs)


def test_git_pass_is_skipped_when_no_git_repo(tmp_path, monkeypatch):
    calls: list[dict] = []
    _stub_query(monkeypatch, calls)
    _runner(tmp_path).run_git_pass()
    assert calls == []


def test_git_pass_runs_with_git_state_live_and_sha_in_the_prompt(
    tmp_path, monkeypatch
):
    calls: list[dict] = []
    _stub_query(monkeypatch, calls)
    repo = _make_repo(tmp_path)
    _runner(tmp_path, git_repos=[str(repo)]).run_git_pass()

    assert len(calls) == 1
    assert calls[0]["git_pass"] is True
    prompt = calls[0]["prompt"]
    assert str(repo.resolve()) in prompt
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head in prompt


def test_git_state_is_cleared_after_the_pass(tmp_path, monkeypatch):
    _stub_query(monkeypatch, [])
    _runner(tmp_path, git_repos=[str(_make_repo(tmp_path))]).run_git_pass()
    assert is_git_pass() is False


def test_git_state_is_cleared_when_the_pass_raises(tmp_path, monkeypatch):
    async def _boom(*, prompt, options):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    monkeypatch.setattr(runner_mod, "query", _boom)
    with pytest.raises(RuntimeError):
        _runner(tmp_path, git_repos=[str(_make_repo(tmp_path))]).run_git_pass()
    assert is_git_pass() is False


def test_a_local_checkout_survives_the_pass(tmp_path, monkeypatch):
    _stub_query(monkeypatch, [])
    repo = _make_repo(tmp_path)
    _runner(tmp_path, git_repos=[str(repo)]).run_git_pass()
    # cleanup() must be a no-op for a tree we did not create.
    assert (repo / "etl.sql").exists()


def test_temp_clone_is_removed_when_the_pass_raises(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    roots: list[Path] = []

    async def _capture_then_boom(*, prompt, options):
        from aws_reference_agent.tools.context import get_git_state

        roots.append(get_git_state().checkout.root)
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    monkeypatch.setattr(runner_mod, "query", _capture_then_boom)
    with pytest.raises(RuntimeError):
        _runner(tmp_path, git_repos=[repo.resolve().as_uri()]).run_git_pass()

    assert len(roots) == 1
    assert not roots[0].exists()


def test_two_repos_yield_two_passes(tmp_path, monkeypatch):
    repo_a = _make_repo(tmp_path, "repo_a")
    repo_b = _make_repo(tmp_path, "repo_b")

    calls: list[dict] = []
    _stub_query(monkeypatch, calls)

    _runner(tmp_path, git_repos=[str(repo_a), str(repo_b)]).run_git_pass()

    assert len(calls) == 2
    assert all(c["git_pass"] is True for c in calls)
    # Each repo's path appears in exactly one prompt.
    assert sum(str(repo_a.resolve()) in c["prompt"] for c in calls) == 1
    assert sum(str(repo_b.resolve()) in c["prompt"] for c in calls) == 1


def test_git_pass_runs_after_the_web_pass_and_before_the_docs_pass(
    tmp_path, monkeypatch
):
    from aws_reference_agent.tools.context import is_docs_pass

    order: list[str] = []

    async def _fake_query(*, prompt, options):
        if is_git_pass():
            order.append("git")
        elif is_docs_pass():
            order.append("docs")
        else:
            order.append("web")
        return
        yield  # pragma: no cover

    monkeypatch.setattr(runner_mod, "query", _fake_query)
    monkeypatch.setattr(
        runner_mod, "regenerate_indexes", lambda *a, **k: order.append("indexes")
    )

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "d.md").write_text("# d\n", encoding="utf-8")

    r = _runner(
        tmp_path,
        web_seeds=["https://example.com/docs"],
        git_repos=[str(_make_repo(tmp_path))],
        docs_roots=[docs],
    )
    r.enrich_all()

    assert order == ["web", "git", "docs", "indexes"]
