from __future__ import annotations

import pytest

from aws_reference_agent.okf_types import SEMANTIC_CUBE_TYPE, SEMANTIC_VIEW_TYPE
from aws_reference_agent.sources.base import ConceptRef
from aws_reference_agent.sources.cube import CubeSource


class StubCubeClient:
    def __init__(self, meta):
        self._meta = meta
        self.fetch_count = 0

    def fetch_meta(self):
        self.fetch_count += 1
        return self._meta


_MINIMAL_META = {
    "cubes": [
        {
            "name": "orders",
            "type": "cube",
            "title": "Orders",
            "description": "Order data",
            "meta": {"owner": "team-x"},
            "connectedComponent": 1,
            "hierarchies": [],
            "folders": [],
            "measures": [
                {
                    "name": "orders.count",
                    "title": "Orders Count",
                    "shortTitle": "Count",
                    "type": "number",
                    "aggType": "count",
                    "description": "Total orders",
                    "drillMembers": ["orders.id"],
                }
            ],
            "dimensions": [
                {
                    "name": "orders.status",
                    "title": "Orders Status",
                    "shortTitle": "Status",
                    "type": "string",
                    "description": "Order status",
                }
            ],
            "segments": [],
        },
        {
            "name": "active_orders",
            "type": "view",
            "title": "Active Orders",
            "measures": [],
            "dimensions": [],
            "segments": [],
        },
    ]
}


def make_source(meta=None):
    client = StubCubeClient(meta or _MINIMAL_META)
    return CubeSource(base_url="http://cube.example.com", client=client), client


def test_should_sort_cubes_by_name_when_listing_concepts():
    src, _ = make_source()
    refs = src.list_concepts()
    names = [r.hint["name"] for r in refs]
    assert names == sorted(names)


def test_should_assign_cube_id_and_type_for_cube_entries():
    src, _ = make_source()
    refs = src.list_concepts()
    cube_ref = next(r for r in refs if r.hint["name"] == "orders")
    assert cube_ref.id == ("cubes", "orders")
    assert cube_ref.type == SEMANTIC_CUBE_TYPE


def test_should_assign_view_id_and_type_for_view_entries():
    src, _ = make_source()
    refs = src.list_concepts()
    view_ref = next(r for r in refs if r.hint["name"] == "active_orders")
    assert view_ref.id == ("views", "active_orders")
    assert view_ref.type == SEMANTIC_VIEW_TYPE


def test_should_set_resource_url_on_concept_ref():
    src, _ = make_source()
    refs = src.list_concepts()
    cube_ref = next(r for r in refs if r.hint["name"] == "orders")
    assert cube_ref.resource == "http://cube.example.com/cubejs-api/v1/meta#orders"


def test_should_set_hint_name_and_kind_on_concept_ref():
    src, _ = make_source()
    refs = src.list_concepts()
    cube_ref = next(r for r in refs if r.hint["name"] == "orders")
    assert cube_ref.hint == {"name": "orders", "kind": "cube"}


def test_should_normalize_measure_with_agg_type_and_drill_members():
    src, _ = make_source()
    ref = src.find(("cubes", "orders"))
    result = src.read_concept(ref)
    measure = result["measures"][0]
    assert measure["name"] == "orders.count"
    assert measure["title"] == "Orders Count"
    assert measure["short_title"] == "Count"
    assert measure["type"] == "number"
    assert measure["agg_type"] == "count"
    assert measure["description"] == "Total orders"
    assert measure["drill_members"] == ["orders.id"]


def test_should_normalize_dimension_without_agg_type_or_drill_members():
    src, _ = make_source()
    ref = src.find(("cubes", "orders"))
    result = src.read_concept(ref)
    dimension = result["dimensions"][0]
    assert dimension["name"] == "orders.status"
    assert dimension["short_title"] == "Status"
    assert dimension["type"] == "string"
    assert "agg_type" not in dimension
    assert "drill_members" not in dimension


def test_should_return_none_for_missing_optional_fields_without_error():
    meta = {
        "cubes": [
            {
                "name": "sparse",
                "type": "cube",
                "measures": [{"name": "sparse.count"}],
                "dimensions": [],
                "segments": [],
            }
        ]
    }
    src, _ = make_source(meta)
    ref = src.find(("cubes", "sparse"))
    result = src.read_concept(ref)
    assert result["title"] is None
    assert result["description"] is None
    assert result["meta"] is None
    measure = result["measures"][0]
    assert measure["title"] is None
    assert "agg_type" not in measure
    assert "drill_members" not in measure


def test_should_raise_value_error_for_unknown_concept_type():
    src, _ = make_source()
    ref = ConceptRef(id=("foo", "bar"), type="Something Else", hint={"name": "bar"})
    with pytest.raises(ValueError, match="Unknown concept type"):
        src.read_concept(ref)


def test_should_raise_key_error_when_cube_name_not_in_meta():
    src, _ = make_source()
    ref = ConceptRef(
        id=("cubes", "missing"),
        type=SEMANTIC_CUBE_TYPE,
        hint={"name": "missing", "kind": "cube"},
    )
    with pytest.raises(KeyError, match="missing"):
        src.read_concept(ref)


def test_should_fetch_meta_only_once_across_list_and_read():
    src, client = make_source()
    src.list_concepts()
    ref = src.find(("cubes", "orders"))
    src.read_concept(ref)
    src.list_concepts()
    assert client.fetch_count == 1


def test_should_return_none_from_sample_rows():
    src, _ = make_source()
    ref = src.find(("cubes", "orders"))
    assert src.sample_rows(ref) is None


def test_should_return_unsupported_string_from_validate_query():
    src, _ = make_source()
    result = src.validate_query("SELECT 1")
    assert result is not None
    assert isinstance(result, str)


def test_should_forward_timeout_to_client_when_no_client_injected():
    from aws_reference_agent.sources.cube import CubeSource

    src = CubeSource(base_url="http://cube.example.com", timeout=42.0)
    assert src._client._timeout == 42.0


def test_should_default_timeout_to_generous_value_for_cold_meta():
    from aws_reference_agent.sources.cube import CubeSource

    src = CubeSource(base_url="http://cube.example.com")
    assert src._client._timeout == 60.0
