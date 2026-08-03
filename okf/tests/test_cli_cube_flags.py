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
        "--no-git",
        *extra,
    ]


def test_no_cube_suppresses_the_pass(tmp_path, capsys):
    assert (
        cli_mod.main(
            _argv(
                tmp_path,
                "--cube-url",
                "http://cube.test",
                "--no-cube",
            )
        )
        == 0
    )

    assert _FakeRunner.last_kwargs["cube_url"] is None
    assert "cube pass skipped" in capsys.readouterr().err


def test_source_cube_suppresses_enrichment_pass(tmp_path, capsys, monkeypatch):
    # When --source cube is used, the enrichment cube pass must be skipped.
    argv = [
        "enrich",
        "--source",
        "cube",
        "--cube-url",
        "http://cube.test",
        "--out",
        str(tmp_path / "bundle"),
        "--no-web",
        "--no-docs",
        "--no-git",
    ]
    assert cli_mod.main(argv) == 0

    assert _FakeRunner.last_kwargs["cube_url"] is None
    assert "cube pass skipped" in capsys.readouterr().err


def test_cube_url_with_glue_source_enables_pass(tmp_path, capsys):
    assert (
        cli_mod.main(
            _argv(
                tmp_path,
                "--cube-url",
                "http://cube.test",
                "--cube-max-reads",
                "50",
            )
        )
        == 0
    )

    kwargs = _FakeRunner.last_kwargs
    assert kwargs["cube_url"] == "http://cube.test"
    assert kwargs["cube_max_reads"] == 50
    assert "cube pass read from http://cube.test" in capsys.readouterr().err


def test_cube_pass_skipped_when_no_cube_url_given(tmp_path, capsys):
    assert cli_mod.main(_argv(tmp_path)) == 0
    assert _FakeRunner.last_kwargs["cube_url"] is None
    assert "cube pass skipped" in capsys.readouterr().err
