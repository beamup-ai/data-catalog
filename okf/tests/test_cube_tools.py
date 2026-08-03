"""Tests for aws_reference_agent.tools.cube_tools.

All tests are hermetic -- no network. A fake CubeSource is built directly from
the CubeSource constructor with a stubbed client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aws_reference_agent.sources.cube import CubeSource
from aws_reference_agent.tools.context import (
    clear_cube_state,
    get_cube_state,
    is_augmenting_pass,
    set_cube_state,
)
from aws_reference_agent.tools.cube_tools import list_cubes, read_cube_meta


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_FAKE_META = {
    "cubes": [
        {
            "name": "Orders",
            "type": "cube",
            "title": "Orders",
            "description": "Order transactions",
            "measures": [],
            "dimensions": [],
            "segments": [],
        },
        {
            "name": "ActiveUsers",
            "type": "view",
            "title": "Active Users",
            "description": "Users with at least one session in 30 days",
            "measures": [],
            "dimensions": [],
            "segments": [],
        },
    ]
}


def _make_source() -> CubeSource:
    client = MagicMock()
    client.fetch_meta.return_value = _FAKE_META
    return CubeSource(base_url="http://cube.test", client=client)


# ---------------------------------------------------------------------------
# autouse fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_cube_state()


# ---------------------------------------------------------------------------
# list_cubes
# ---------------------------------------------------------------------------


def test_list_cubes_returns_all_cubes():
    set_cube_state(_make_source())

    result = list_cubes()

    assert "error" not in result
    assert result["count"] == 2
    names = {c["name"] for c in result["cubes"]}
    assert names == {"Orders", "ActiveUsers"}


def test_list_cubes_includes_kind_and_type():
    set_cube_state(_make_source())

    result = list_cubes()

    by_name = {c["name"]: c for c in result["cubes"]}
    assert by_name["Orders"]["kind"] == "cube"
    assert by_name["ActiveUsers"]["kind"] == "view"
    assert by_name["Orders"]["type"] != ""


def test_list_cubes_returns_error_on_source_failure():
    client = MagicMock()
    client.fetch_meta.side_effect = Exception("network error")
    source = CubeSource(base_url="http://cube.test", client=client)
    set_cube_state(source)

    result = list_cubes()

    assert "error" in result


# ---------------------------------------------------------------------------
# read_cube_meta
# ---------------------------------------------------------------------------


def test_read_cube_meta_returns_meta_and_increments_read_count():
    set_cube_state(_make_source())

    result = read_cube_meta("Orders")

    assert "error" not in result
    assert result["name"] == "Orders"
    assert "meta" in result
    assert result["read_count"] == 1
    assert result["max_reads_budget"] >= 1


def test_read_cube_meta_second_read_of_same_name_is_rejected():
    set_cube_state(_make_source())

    read_cube_meta("Orders")
    result = read_cube_meta("Orders")

    assert "error" in result
    assert "already read" in result["error"]


def test_read_cube_meta_rejects_when_max_reads_reached():
    set_cube_state(_make_source(), max_reads=1)

    read_cube_meta("Orders")
    result = read_cube_meta("ActiveUsers")

    assert "error" in result
    assert "max_reads reached" in result["error"]


def test_read_cube_meta_rejects_unknown_name():
    set_cube_state(_make_source())

    result = read_cube_meta("NoSuchCube")

    assert "error" in result
    assert "not found" in result["error"]


def test_read_cube_meta_returns_error_on_read_concept_failure():
    client = MagicMock()
    client.fetch_meta.return_value = _FAKE_META
    source = CubeSource(base_url="http://cube.test", client=client)
    # Patch read_concept to blow up after list_concepts succeeds.
    source.read_concept = MagicMock(side_effect=Exception("boom"))
    set_cube_state(source)

    result = read_cube_meta("Orders")

    assert "error" in result
    assert result["read_count"] == 0  # budget not consumed on error


# ---------------------------------------------------------------------------
# get_cube_state raises when unset
# ---------------------------------------------------------------------------


def test_get_cube_state_raises_when_unset():
    # _cleanup fixture already called clear_cube_state before this test.
    with pytest.raises(RuntimeError, match="Cube state not set"):
        get_cube_state()


# ---------------------------------------------------------------------------
# is_augmenting_pass
# ---------------------------------------------------------------------------


def test_is_augmenting_pass_is_true_while_cube_state_is_set():
    assert not is_augmenting_pass()
    set_cube_state(_make_source())
    assert is_augmenting_pass()
