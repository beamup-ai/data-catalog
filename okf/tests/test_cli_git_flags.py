from __future__ import annotations

from pathlib import Path

import pytest

from aws_reference_agent import cli as cli_mod


class _FakeRunner:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def enrich_all(self, only=None):
        return 1


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(cli_mod, "ReferenceRunner", _FakeRunner)
    monkeypatch.setattr(cli_mod, "_build_source", lambda name, args: object())
    _FakeRunner.last_kwargs = {}


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "enrich",
        "--source",
        "glue",
        "--database",
        "db",
        "--out",
        str(tmp_path / "bundle"),
        "--no-web",
        "--no-docs",
        *extra,
    ]


def test_git_flags_are_threaded_into_the_runner(tmp_path, capsys):
    assert cli_mod.main(
        _argv(
            tmp_path,
            "--git-repo",
            "git@example.com:acme/warehouse.git",
            "--git-ref",
            "release",
            "--git-max-files",
            "7",
            "--git-max-bytes",
            "1024",
            "--git-max-searches",
            "9",
            "--git-max-hits",
            "3",
        )
    ) == 0

    kwargs = _FakeRunner.last_kwargs
    assert kwargs["git_repo"] == "git@example.com:acme/warehouse.git"
    assert kwargs["git_ref"] == "release"
    assert kwargs["git_max_files"] == 7
    assert kwargs["git_max_bytes"] == 1024
    assert kwargs["git_max_searches"] == 9
    assert kwargs["git_max_hits"] == 3
    assert "git pass searched git@example.com:acme/warehouse.git" in (
        capsys.readouterr().err
    )


def test_no_git_suppresses_the_pass(tmp_path, capsys):
    assert cli_mod.main(
        _argv(tmp_path, "--git-repo", "https://example.com/a.git", "--no-git")
    ) == 0

    assert _FakeRunner.last_kwargs["git_repo"] is None
    assert "git pass skipped" in capsys.readouterr().err


def test_git_pass_skipped_when_no_repo_given(tmp_path, capsys):
    assert cli_mod.main(_argv(tmp_path)) == 0
    assert _FakeRunner.last_kwargs["git_repo"] is None
    assert "git pass skipped" in capsys.readouterr().err


def test_a_url_git_repo_is_accepted_without_a_directory_precheck(tmp_path):
    # The docs pass rejects a non-directory root; --git-repo must not, because a
    # remote URL is the primary use case.
    assert cli_mod.main(
        _argv(tmp_path, "--git-repo", "git@example.com:acme/warehouse.git")
    ) == 0
    assert (
        _FakeRunner.last_kwargs["git_repo"] == "git@example.com:acme/warehouse.git"
    )
