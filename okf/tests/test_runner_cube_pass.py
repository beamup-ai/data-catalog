"""Tests for the Cube semantic layer pass in ReferenceRunner.

Hermetic: a fake CubeSource is injected via monkeypatching CubeSource
construction in the runner so no network calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aws_reference_agent import runner as runner_mod
from aws_reference_agent.runner import ReferenceRunner
from aws_reference_agent.tools.context import clear_cube_state, is_cube_pass


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_cube_state()


def _stub_query(monkeypatch, calls: list[dict]):
    async def _fake_query(*, prompt, options):
        calls.append({"prompt": prompt, "cube_pass": is_cube_pass()})
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(runner_mod, "query", _fake_query)


def _fake_cube_source():
    src = MagicMock()
    src.list_concepts.return_value = []
    return src


def _runner(tmp_path, **kwargs) -> ReferenceRunner:
    src = MagicMock()
    src.list_concepts.return_value = []
    return ReferenceRunner(source=src, bundle_root=tmp_path / "bundle", **kwargs)


def test_cube_pass_is_skipped_when_no_cube_url(tmp_path, monkeypatch):
    calls: list[dict] = []
    _stub_query(monkeypatch, calls)
    _runner(tmp_path).run_cube_pass()
    assert calls == []


def test_cube_pass_runs_with_cube_state_live_and_url_in_prompt(
    tmp_path, monkeypatch
):
    calls: list[dict] = []
    _stub_query(monkeypatch, calls)

    fake_source = _fake_cube_source()
    with patch(
        "aws_reference_agent.runner.CubeSource", return_value=fake_source
    ):
        _runner(tmp_path, cube_url="http://cube.test").run_cube_pass()

    assert len(calls) == 1
    assert calls[0]["cube_pass"] is True
    assert "http://cube.test" in calls[0]["prompt"]


def test_cube_state_is_cleared_after_the_pass(tmp_path, monkeypatch):
    _stub_query(monkeypatch, [])
    fake_source = _fake_cube_source()
    with patch(
        "aws_reference_agent.runner.CubeSource", return_value=fake_source
    ):
        _runner(tmp_path, cube_url="http://cube.test").run_cube_pass()
    assert is_cube_pass() is False


def test_cube_state_is_cleared_when_the_pass_raises(tmp_path, monkeypatch):
    async def _boom(*, prompt, options):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    monkeypatch.setattr(runner_mod, "query", _boom)
    fake_source = _fake_cube_source()
    with patch(
        "aws_reference_agent.runner.CubeSource", return_value=fake_source
    ):
        with pytest.raises(RuntimeError):
            _runner(tmp_path, cube_url="http://cube.test").run_cube_pass()
    assert is_cube_pass() is False


def test_cube_pass_runs_after_git_and_before_docs(tmp_path, monkeypatch):
    from aws_reference_agent.tools.context import is_docs_pass, is_git_pass

    import subprocess
    from pathlib import Path

    # Build a minimal local git repo for the git pass.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "x.sql").write_text("SELECT 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    order: list[str] = []

    async def _fake_query(*, prompt, options):
        if is_cube_pass():
            order.append("cube")
        elif is_git_pass():
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

    fake_source = _fake_cube_source()
    with patch(
        "aws_reference_agent.runner.CubeSource", return_value=fake_source
    ):
        r = _runner(
            tmp_path,
            web_seeds=["https://example.com/docs"],
            git_repos=[str(repo)],
            cube_url="http://cube.test",
            docs_roots=[docs],
        )
        r.enrich_all()

    assert order == ["web", "git", "cube", "docs", "indexes"]
