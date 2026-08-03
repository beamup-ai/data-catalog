from __future__ import annotations

from typing import Any

from aws_reference_agent.git.repo import Checkout, GitError, list_files, read_file, search
from aws_reference_agent.tools.context import get_git_state


def _provenance(checkout: Checkout, rel_path: str) -> str:
    return f"{checkout.origin}@{checkout.sha[:12]}:{rel_path}"


def search_repo(
    pattern: str,
    path_glob: str | None = None,
    regex: bool = False,
) -> dict[str, Any]:
    """Search tracked files in the repo for a pattern using git grep.

    This is git grep over files tracked by the repository. The intended use
    is to find the catalog's own table names, distinctive column names, or any
    identifier that would appear in source code or configuration files.

    By default, the search is a fixed-string match. Set `regex=True` to use a
    Perl-compatible regular expression instead. Use `path_glob` to narrow the
    search to files whose path matches a shell glob (e.g., "*.sql", "src/**").

    When `truncated_count` is greater than zero, the result was cut off at the
    budget limit. Do not retry the same pattern identically — narrow it with a
    more specific string or restrict the scope with `path_glob`.

    Each call consumes one unit of the search budget even if it returns no hits
    or raises an error.

    Successful return shape:
      {"pattern", "path_glob", "hits": [{"path", "line", "text"}], "count",
       "truncated_count", "search_count", "max_searches_budget"}

    Rejected return shape:
      {"error": "<reason>", "search_count", "max_searches_budget"}
    """
    state = get_git_state()

    def _reject(reason: str) -> dict[str, Any]:
        return {
            "error": reason,
            "search_count": state.search_count,
            "max_searches_budget": state.max_searches,
        }

    if state.search_count >= state.max_searches:
        return _reject("max_searches reached")

    # Consume budget before the call so a pathological pattern cannot be retried
    # indefinitely by exploiting error paths that might look free.
    state.search_count += 1

    try:
        result = search(
            state.checkout,
            pattern,
            path_glob=path_glob,
            max_hits=state.max_hits,
            regex=regex,
        )
    except GitError as e:
        return _reject(f"search failed: {e}")

    return {
        "pattern": pattern,
        "path_glob": path_glob,
        "hits": [
            {"path": h.rel_path, "line": h.line, "text": h.text}
            for h in result.hits
        ],
        "count": len(result.hits),
        "truncated_count": result.truncated_count,
        "search_count": state.search_count,
        "max_searches_budget": state.max_searches,
    }


def list_repo_files(path_glob: str | None = None) -> dict[str, Any]:
    """List tracked files in the repository filtered to code and text suffixes.

    Use this for orientation — to understand repo layout, locate configuration
    files, or identify which directories contain source code. This listing is NOT
    exhaustive of the repository and is NOT the set of readable paths: it covers
    only tracked files whose suffix is one this pass can read, so images,
    binaries, and untracked or gitignored files never appear here.

    Unlike `list_local_docs`, there is no pre-vetted manifest; prefer
    `search_repo` to find files relevant to a specific concept, and reserve this
    tool for understanding the overall structure.

    Successful return shape:
      {"path_glob", "paths", "count", "truncated_count"}

    Rejected return shape:
      {"error": "<reason>"}
    """
    state = get_git_state()

    try:
        result = list_files(
            state.checkout,
            path_glob=path_glob,
            max_files=state.max_files,
        )
    except GitError as e:
        return {"error": f"list failed: {e}"}

    return {
        "path_glob": path_glob,
        "paths": result.paths,
        "count": len(result.paths),
        "truncated_count": result.truncated_count,
    }


def read_repo_file(path: str) -> dict[str, Any]:
    """Read one tracked file from the repository and return its content.

    Always cite the `provenance` field verbatim when referencing information from
    this file. Never cite a bare filename. The provenance string encodes the
    remote origin, the resolved HEAD SHA (12 chars), and the file path within the
    repo — enough for a reader to locate the exact commit.

    `last_commit` is the git author date (ISO-8601) of the last commit that
    touched this file. It is the ONLY freshness signal available. Filesystem
    modification times are meaningless in a fresh clone and must not be used.

    When `truncated` is true, the `text` field contains only a partial file.
    Do not assert anything about what the remainder of the file contains.

    Each unique path may be read at most once per session. A second read of the
    same path is rejected with an error dict rather than an exception.

    Successful return shape:
      {"path", "text", "bytes", "truncated", "last_commit", "provenance",
       "read_count", "max_files_budget"}

    Rejected return shape:
      {"error": "<reason>", "path", "read_count", "max_files_budget"}
    """
    state = get_git_state()

    # `doc_tools.read_local_doc` confines reads by requiring manifest
    # membership. There is no manifest here — a code repo is searched, not
    # enumerated — so confinement is `git/repo.py:read_file`'s
    # resolve-then-contain check plus its tracked-file requirement. The
    # difference is deliberate, not an omission.
    def _reject(reason: str) -> dict[str, Any]:
        return {
            "error": reason,
            "path": path,
            "read_count": state.read_count,
            "max_files_budget": state.max_files,
        }

    if path in state.read:
        return _reject("already read in this session")
    if state.read_count >= state.max_files:
        return _reject("max_files reached")

    try:
        doc = read_file(state.checkout, path, max_bytes=state.max_bytes)
    except GitError as e:
        return _reject(f"read failed: {e}")

    state.read.add(path)
    state.read_count += 1

    return {
        "path": doc.rel_path,
        "text": doc.text,
        "bytes": doc.bytes,
        "truncated": doc.bytes > state.max_bytes,
        "last_commit": doc.last_commit,
        "provenance": _provenance(state.checkout, doc.rel_path),
        "read_count": state.read_count,
        "max_files_budget": state.max_files,
    }
