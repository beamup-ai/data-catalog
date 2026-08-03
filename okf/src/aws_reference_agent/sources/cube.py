"""Cube.js semantic layer source: cubes and views as OKF concepts."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from aws_reference_agent.okf_types import SEMANTIC_CUBE_TYPE, SEMANTIC_VIEW_TYPE
from aws_reference_agent.sources.base import ConceptRef, Source


class CubeError(Exception):
    pass


class CubeClient:
    # Cold /meta on a large deployment can take ~40s while the server recompiles
    # its schema, so the default is generous; warm requests return in <1s.
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def fetch_meta(self) -> dict[str, Any]:
        url = f"{self._base_url}/cubejs-api/v1/meta"
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
                body = resp.read()
        except Exception as e:
            raise CubeError(f"HTTP request failed: {e}") from e
        if status != 200:
            raise CubeError(f"Cube meta API returned HTTP {status}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise CubeError(f"Failed to parse Cube meta response as JSON: {e}") from e


class CubeSource(Source):
    name = "cube"

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 60.0,
        client=None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if client is None:
            client = CubeClient(base_url, token, timeout=timeout)
        self._client = client
        self._meta_cache: dict[str, Any] | None = None

    def _fetch_meta(self) -> dict[str, Any]:
        if self._meta_cache is None:
            self._meta_cache = self._client.fetch_meta()
        return self._meta_cache

    def list_concepts(self) -> list[ConceptRef]:
        meta = self._fetch_meta()
        cubes = sorted(meta.get("cubes", []), key=lambda c: c.get("name", ""))
        refs: list[ConceptRef] = []
        for cube in cubes:
            name = cube.get("name", "")
            kind = cube.get("type", "cube")
            if kind == "view":
                concept_type = SEMANTIC_VIEW_TYPE
                concept_id = ("views", name)
            else:
                concept_type = SEMANTIC_CUBE_TYPE
                concept_id = ("cubes", name)
            refs.append(
                ConceptRef(
                    id=concept_id,
                    type=concept_type,
                    resource=f"{self._base_url}/cubejs-api/v1/meta#{name}",
                    hint={"name": name, "kind": kind},
                )
            )
        return refs

    def read_concept(self, ref: ConceptRef) -> dict[str, Any]:
        if ref.type not in (SEMANTIC_CUBE_TYPE, SEMANTIC_VIEW_TYPE):
            raise ValueError(f"Unknown concept type: {ref.type}")
        name = ref.hint["name"]
        meta = self._fetch_meta()
        cubes = meta.get("cubes", [])
        cube: dict[str, Any] | None = None
        for c in cubes:
            if c.get("name") == name:
                cube = c
                break
        if cube is None:
            raise KeyError(f"Cube '{name}' not found in meta")

        result: dict[str, Any] = {
            "name": cube.get("name"),
            "kind": cube.get("type", "cube"),
            "title": cube.get("title"),
            "description": cube.get("description"),
            "meta": cube.get("meta"),
            "connectedComponent": cube.get("connectedComponent"),
            "hierarchies": cube.get("hierarchies"),
            "folders": cube.get("folders"),
            "measures": [_normalize_measure(m) for m in cube.get("measures", [])],
            "dimensions": [_normalize_dimension(d) for d in cube.get("dimensions", [])],
            "segments": [_normalize_segment(s) for s in cube.get("segments", [])],
        }
        return result


def _normalize_measure(m: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": m.get("name"),
        "title": m.get("title"),
        "short_title": m.get("shortTitle"),
        "type": m.get("type"),
        "description": m.get("description"),
    }
    if "aggType" in m:
        result["agg_type"] = m["aggType"]
    if "drillMembers" in m:
        result["drill_members"] = m["drillMembers"]
    return result


def _normalize_dimension(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": d.get("name"),
        "title": d.get("title"),
        "short_title": d.get("shortTitle"),
        "type": d.get("type"),
        "description": d.get("description"),
    }


def _normalize_segment(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name"),
        "title": s.get("title"),
        "short_title": s.get("shortTitle"),
        "type": s.get("type"),
        "description": s.get("description"),
    }
