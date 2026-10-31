"""CAS atomic durable commit (IMP-0049).

Publishes a verified staged candidate at its digest path. Order (REV-0123): bytes already fsynced in
quarantine -> mode made read-only -> `link()` to the final name (atomic, and it can never overwrite an
existing object) -> staged name removed -> shard directories fsynced -> object independently re-read and
re-hashed. After a crash at any point either no object exists at the digest path, or a complete one
does; a leftover quarantine file is only an orphan to sweep. Objects are never modified after this.
An already-present object is reported, not touched (duplicate handling is IMP-0051).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from pathlib import Path

    from trinetra.infrastructure.cas_admission import StagedCandidate
    from trinetra.infrastructure.cas_layout import CasLayout

OBJECT_MODE: Final = 0o444
READ_CHUNK: Final = 1024 * 1024


class CommitError(TrinetraError):
    code = "CAS_COMMIT_FAILED"
    default_message = "the CAS object could not be committed"


class ObjectExistsError(TrinetraError):
    """An object already occupies the digest path; the staged candidate was left untouched."""

    code = "CAS_OBJECT_EXISTS"
    default_message = "an object already exists at this digest"


@dataclass(frozen=True)
class CommittedObject:
    digest: DigestSha256
    size: int
    path: Path


def hash_file(path: Path) -> tuple[DigestSha256, int]:
    """Stream a file through SHA-256 without following a final symlink. Returns (digest, size)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            msg = "not a regular file"
            raise CommitError(msg)
        hasher = hashlib.sha256()
        size = 0
        while True:
            block = os.read(fd, READ_CHUNK)
            if not block:
                break
            hasher.update(block)
            size += len(block)
    finally:
        os.close(fd)
    return DigestSha256(hasher.digest()), size


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def commit_candidate(layout: CasLayout, candidate: StagedCandidate) -> CommittedObject:
    """Atomically publish `candidate` at its digest path, then re-verify it from disk."""
    staged = candidate.staged
    if staged.is_open:
        msg = "the staged candidate is still open for writing"
        raise CommitError(msg)
    target = layout.object_path(candidate.digest)
    shard = layout.shard_directory(candidate.digest)
    os.chmod(staged.path, OBJECT_MODE)
    try:
        os.link(staged.path, target)
    except FileExistsError as exc:
        msg = "an object already exists at this digest"
        raise ObjectExistsError(msg) from exc
    try:
        staged.path.unlink()
        _fsync_directory(shard)
        _fsync_directory(shard.parent)
        _fsync_directory(layout.objects)
        _fsync_directory(layout.quarantine)
        actual, size = hash_file(target)
        if actual != candidate.digest or size != candidate.size:
            msg = "the committed object does not match its digest"
            raise CommitError(msg)  # noqa: TRY301 - single cleanup path below
    except BaseException:
        # Only the object this call created is removed; an unverified object must never stay addressable.
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise
    return CommittedObject(candidate.digest, size, target)
