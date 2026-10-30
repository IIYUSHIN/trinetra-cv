"""Detect tests that write into the repository (IMP-0012).

Pure helpers used by `tests/conftest.py` and unit-tested on their own. The
snapshot is `git status` (tracked changes + untracked, .gitignore honoured)
plus a content digest of every reported file, so editing a file that was
*already* dirty before the run is still detected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from trinetra.digest import DigestError, sha256_file

MAX_TRACKED_BYTES = 64 * 1024 * 1024


def git_root(start: Path) -> Path | None:
    """Top level of the git work tree containing `start`, or None (no git / not a repo)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def _status_entries(root: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True, check=True, timeout=120,
    )
    tokens = [t for t in result.stdout.decode("utf-8", "surrogateescape").split("\0") if t]
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        code, path = tokens[i][:2], tokens[i][3:]
        entries.append((code, path))
        i += 2 if code[0] in "RC" or code[1] in "RC" else 1  # renames carry the old path next
    return entries


def snapshot(root: Path) -> dict[str, str]:
    """{path: 'XY:<sha256 or state>'} for every file git reports as changed/untracked."""
    out: dict[str, str] = {}
    for code, rel in _status_entries(root):
        target = root / rel
        if target.is_symlink():
            state = "symlink"
        elif target.is_file():
            try:
                state = sha256_file(target, max_bytes=MAX_TRACKED_BYTES)[0]
            except DigestError:
                state = "unhashable"
        else:
            state = "missing"
        out[rel] = f"{code}:{state}"
    return out


def diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Human-readable differences; empty means the run left the repository as it found it."""
    problems = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            problems.append(f"{path}: {before.get(path, 'clean')} -> {after.get(path, 'clean')}")
    return problems
