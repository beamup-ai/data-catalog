"""Shell out to the local `git` binary to check out, search, list, and read
repository files.

This module is pure I/O with no awareness of tool state, exactly like
`docs/reader.py`.  It delegates all credential management to whatever the
operator has already configured (SSH agent, credential.helper, GIT_ASKPASS).
We never handle a token ourselves.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CODE_SUFFIXES = frozenset(
    {
        ".sql",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".java",
        ".scala",
        ".kt",
        ".go",
        ".rb",
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".ipynb",
    }
)

_MAX_FILE_BYTES = 60 * 1024
_MAX_FILES = 300
_MAX_HITS = 50
_MAX_HIT_CHARS = 300
_GIT_TIMEOUT = 120


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Checkout:
    root: Path
    origin: str       # remote URL or local path, for provenance
    sha: str          # resolved HEAD, full 40-char
    cloned: bool      # True if we created it; caller must clean up


@dataclass(frozen=True)
class RepoHit:
    rel_path: str
    line: int
    text: str         # the matching line, truncated to a per-line char cap


@dataclass(frozen=True)
class SearchResult:
    hits: list[RepoHit]
    truncated_count: int  # hits dropped by max_hits


@dataclass(frozen=True)
class FileList:
    paths: list[str]
    truncated_count: int


@dataclass(frozen=True)
class RepoFile:
    rel_path: str
    text: str
    bytes: int        # true on-disk size even when `text` was truncated
    last_commit: str  # ISO-8601 author date of that file's last commit
    sha: str          # the checkout's HEAD sha


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = _GIT_TIMEOUT,
    ok_codes: tuple[int, ...] = (0,),
) -> tuple[int, str]:
    """Run git with *args* and return its (exit code, stdout).

    Uses a list (never shell=True) so arguments are never interpolated by
    the shell.  Sets GIT_TERMINAL_PROMPT=0 so a missing credential fails fast
    instead of blocking on an interactive password prompt; GIT_ASKPASS and the
    system config are left alone so the operator's credential helper keeps
    working.

    `ok_codes` exists for `git grep`, which signals "no matches" with exit 1.
    Any code outside `ok_codes` raises.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git timed out after {timeout}s: git {' '.join(args)}") from e
    except FileNotFoundError as e:
        raise GitError(
            "git executable not found; install git and ensure it is on PATH"
        ) from e

    if result.returncode not in ok_codes:
        stderr_tail = result.stderr.strip()[-500:]
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {stderr_tail}"
        )

    return result.returncode, result.stdout


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n\n[...truncated...]"


def _confine(root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` under `root`, refusing anything that escapes it.

    Resolving before the containment check is what makes this safe against both
    `../` traversal and symlinks whose target lies outside the root.
    """
    resolved = (root / rel_path).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise GitError(f"path resolves outside the repo root: {rel_path}")
    return resolved


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def open_checkout(
    target: str | Path,
    *,
    ref: str | None = None,
    dest: Path | None = None,
) -> Checkout:
    """Open or clone a git repository and return a Checkout descriptor.

    If `target` is an existing directory it is opened in place (cloned=False)
    and `dest` is ignored — we never copy the operator's own working tree.
    Passing `ref` for a local checkout raises GitError, because honouring it
    would mean checking out a different ref in a tree we do not own.

    Anything else is treated as a remote and shallow-cloned into `dest`. The
    caller owns `dest`'s lifetime; pass `tempfile.mkdtemp(...)`, or let this
    function create one and call `cleanup` when the pass ends.
    """
    if Path(target).is_dir():
        resolved = Path(target).resolve()

        if ref is not None:
            raise GitError(
                "passing ref= for a local checkout is not supported; "
                "we do not mutate the operator's working tree"
            )

        # Use the git toplevel so a subdirectory argument still yields a valid
        # repo root even when the caller passed a subdirectory.
        _, toplevel_str = _run_git(["rev-parse", "--show-toplevel"], cwd=resolved)
        root = Path(toplevel_str.strip()).resolve()

        _, head = _run_git(["rev-parse", "HEAD"], cwd=root)
        sha = head.strip()
        return Checkout(root=root, origin=str(resolved), sha=sha, cloned=False)

    # Remote (or any non-directory) path: shallow clone.
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="okf-repo-"))

    clone_args = ["clone", "--depth", "1", "--single-branch"]
    if ref is not None:
        clone_args += ["--branch", ref]
    clone_args += [str(target), str(dest)]

    _run_git(clone_args)

    root = Path(dest).resolve()
    _, head = _run_git(["rev-parse", "HEAD"], cwd=root)
    return Checkout(root=root, origin=str(target), sha=head.strip(), cloned=True)


def search(
    checkout: Checkout,
    pattern: str,
    *,
    path_glob: str | None = None,
    max_hits: int = _MAX_HITS,
    regex: bool = False,
) -> SearchResult:
    """Search tracked files in `checkout` for `pattern` using git grep.

    Returns an empty SearchResult when there are no matches (git grep exit 1
    with no output is not an error).  Any other non-zero exit code raises
    GitError.
    """
    args = ["grep", "-n", "-I", "--no-color"]
    if not regex:
        args.append("--fixed-strings")
    else:
        # Use Perl-compatible regex (PCRE) so that common patterns like \w+
        # work consistently across platforms.
        args.append("-P")
    args += ["-e", pattern]
    if path_glob is not None:
        args += ["--", path_glob]

    # Exit 1 is git grep's "no matches", not a failure, so it is accepted here
    # and yields an empty result. Anything else still raises.
    _, stdout = _run_git(args, cwd=checkout.root, ok_codes=(0, 1))

    hits: list[RepoHit] = []
    for raw_line in stdout.splitlines():
        # Format: path:line_number:text  -- split on the first two colons only.
        parts = raw_line.split(":", 2)
        if len(parts) < 3:
            continue
        rel_path, line_str, text = parts
        try:
            line = int(line_str)
        except ValueError:
            continue
        hits.append(
            RepoHit(
                rel_path=rel_path,
                line=line,
                text=text[:_MAX_HIT_CHARS],
            )
        )

    kept = hits[:max_hits]
    return SearchResult(hits=kept, truncated_count=len(hits) - len(kept))


def list_files(
    checkout: Checkout,
    *,
    path_glob: str | None = None,
    max_files: int = _MAX_FILES,
) -> FileList:
    """List tracked files in `checkout` filtered to allowed code suffixes.

    `git ls-files` returns only tracked files, so .git/ and gitignored files
    are excluded for free.  Results are sorted and truncated to `max_files`.
    """
    _, stdout = _run_git(["ls-files"], cwd=checkout.root)

    found: list[str] = []
    for rel in stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        if Path(rel).suffix.lower() not in _CODE_SUFFIXES:
            continue
        if path_glob is not None and not fnmatch.fnmatch(rel, path_glob):
            continue
        found.append(rel)

    found.sort()
    kept = found[:max_files]
    return FileList(paths=kept, truncated_count=len(found) - len(kept))


def read_file(
    checkout: Checkout,
    rel_path: str,
    *,
    max_bytes: int = _MAX_FILE_BYTES,
) -> RepoFile:
    """Read one tracked file from `checkout` and return its content plus metadata.

    `bytes` is the true on-disk size even when `text` was truncated, so a
    consumer can tell it is holding a partial file.

    Confinement: we resolve the full path first, then require it to be inside
    checkout.root.  This is the same guard as `docs/reader.py:_confine` and
    defends against both `../` traversal and escaping symlinks.

    Additionally we require the file to be tracked by git.  An untracked or
    gitignored file that merely exists on disk is refused.
    """
    # --- confinement guard ----------------------------------------------------
    # Resolving before the containment check defends against both `../`
    # traversal and escaping symlinks.
    path = _confine(checkout.root, rel_path)

    # --- suffix guard ---------------------------------------------------------
    if Path(rel_path).suffix.lower() not in _CODE_SUFFIXES:
        raise GitError(
            f"file suffix not in allowed code suffixes: {Path(rel_path).suffix!r}"
        )

    # --- tracking guard -------------------------------------------------------
    # git ls-files --error-unmatch exits non-zero when the file is untracked.
    _run_git(["ls-files", "--error-unmatch", "--", rel_path], cwd=checkout.root)

    if not path.is_file():
        raise GitError(f"not a regular file: {rel_path}")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GitError(str(exc)) from exc

    text = raw.decode("utf-8", errors="replace")

    # --- last-commit date -----------------------------------------------------
    _, log_out = _run_git(
        ["log", "-1", "--format=%aI", "--", rel_path], cwd=checkout.root
    )
    last_commit = log_out.strip()
    if not last_commit:
        raise GitError(f"could not determine last commit for: {rel_path}")

    return RepoFile(
        rel_path=str(Path(rel_path).as_posix()),
        text=_truncate(text, max_bytes),
        bytes=len(raw),
        last_commit=last_commit,
        sha=checkout.sha,
    )


def cleanup(checkout: Checkout) -> None:
    """Remove the checkout directory if we created it; a no-op otherwise.

    Safe to call more than once.
    """
    if checkout.cloned:
        shutil.rmtree(checkout.root, ignore_errors=True)
