from __future__ import annotations

from typing import Any

from aws_reference_agent.tools.context import get_cube_state


def list_cubes() -> dict[str, Any]:
    """List all cubes and views available in the Cube.js semantic layer.

    Returns the name, kind (cube or view), and OKF type for each concept.
    Use this once for orientation before reading individual cube metadata.

    Successful return shape:
      {"cubes": [{"id", "name", "kind", "type"}], "count"}

    Error return shape:
      {"error": "<reason>"}
    """
    state = get_cube_state()
    try:
        refs = state.source.list_concepts()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return {
        "cubes": [
            {
                "id": ref.id_str,
                "name": ref.hint["name"],
                "kind": ref.hint["kind"],
                "type": ref.type,
            }
            for ref in refs
        ],
        "count": len(refs),
    }


def read_cube_meta(name: str) -> dict[str, Any]:
    """Read the semantic metadata for one cube or view by name.

    Returns measures, dimensions, segments, title, and description for the
    named concept. This is interface-only metadata: no SQL, no table mappings,
    no join definitions.

    Each unique cube name may be read at most once per session. A second read of
    the same name is rejected with an error dict rather than an exception.

    Successful return shape:
      {"name", "meta": {...}, "read_count", "max_reads_budget"}

    Rejected return shape:
      {"error": "<reason>", "name", "read_count", "max_reads_budget"}
    """
    state = get_cube_state()

    def _reject(reason: str) -> dict[str, Any]:
        return {
            "error": reason,
            "name": name,
            "read_count": state.read_count,
            "max_reads_budget": state.max_reads,
        }

    if name in state.read:
        return _reject("already read in this session")
    if state.read_count >= state.max_reads:
        return _reject("max_reads reached")

    refs = state.source.list_concepts()
    ref = next((r for r in refs if r.hint["name"] == name), None)
    if ref is None:
        return _reject(f"cube '{name}' not found")

    try:
        meta = state.source.read_concept(ref)
    except Exception as e:  # noqa: BLE001
        return _reject(str(e))

    state.read.add(name)
    state.read_count += 1

    return {
        "name": name,
        "meta": meta,
        "read_count": state.read_count,
        "max_reads_budget": state.max_reads,
    }
