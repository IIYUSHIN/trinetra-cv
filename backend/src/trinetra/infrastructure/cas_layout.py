"""CAS root layout and digest-to-path mapping (IMP-0046).

Object identity is the SHA-256 of the bytes and nothing else: never a filename, never a caller-supplied
path. The path is a pure function of the digest, so a path can neither be chosen nor smuggled in.
Layout under the state root (PROPOSED, ERR-CL-013): `cas/sha256/<h[0:2]>/<h[2:4]>/<h>` for admitted
objects and `cas/quarantine/` for staged writes (IMP-0047). The relative locator form is exactly what
migration 0001 permits in `blob_locators.locator`.
"""

from __future__ import annotations

import contextlib
import os
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.infrastructure.sqlite import DatabaseConfig, validate_database_path

if TYPE_CHECKING:
    from pathlib import Path

CAS_DIRNAME: Final = "cas"
ALGORITHM_DIRNAME: Final = "sha256"
QUARANTINE_DIRNAME: Final = "quarantine"
DIR_MODE: Final = 0o700


class CasLayoutError(TrinetraError):
    code = "CAS_LAYOUT_INVALID"
    default_message = "the CAS layout is invalid or untrustworthy"


def digest_hex(digest: DigestSha256 | str) -> str:
    """Return the 64 lowercase hex characters of a digest given as a value or its wire form."""
    if isinstance(digest, DigestSha256):
        return digest.hex
    try:
        return DigestSha256.parse(digest).hex
    except TrinetraError as exc:
        msg = "not a canonical sha256 digest"
        raise CasLayoutError(msg) from exc


def locator_for(digest: DigestSha256 | str) -> str:
    """Relative, state-root-anchored locator for a digest. Pure; performs no I/O."""
    h = digest_hex(digest)
    return f"{CAS_DIRNAME}/{ALGORITHM_DIRNAME}/{h[0:2]}/{h[2:4]}/{h}"


@dataclass(frozen=True)
class CasLayout:
    """Resolved CAS directories beneath a validated state root."""

    root: Path

    @classmethod
    def for_state_root(cls, state_root: Path) -> CasLayout:
        validate_database_path(DatabaseConfig(state_root))  # same trust rules as the database itself
        return cls(state_root / CAS_DIRNAME)

    @property
    def objects(self) -> Path:
        return self.root / ALGORITHM_DIRNAME

    @property
    def quarantine(self) -> Path:
        return self.root / QUARANTINE_DIRNAME

    def object_path(self, digest: DigestSha256 | str) -> Path:
        h = digest_hex(digest)
        return self.objects / h[0:2] / h[2:4] / h

    def ensure(self) -> None:
        """Create the fixed directories (mode 0700) and prove none is a symlink or foreign-owned."""
        for directory in (self.root, self.objects, self.quarantine):
            self._make_checked(directory)

    def check_directory(self, directory: Path) -> None:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            msg = f"{directory.name} is not a real directory"
            raise CasLayoutError(msg)
        if info.st_uid != os.geteuid():
            msg = f"{directory.name} is not owned by the running user"
            raise CasLayoutError(msg)
        if info.st_mode & 0o022:
            msg = f"{directory.name} is writable by group or others"
            raise CasLayoutError(msg)

    def shard_directory(self, digest: DigestSha256 | str) -> Path:
        """Create (if needed) and verify the two shard levels for a digest; return the leaf directory."""
        path = self.object_path(digest)
        for directory in (path.parent.parent, path.parent):
            self._make_checked(directory)
        return path.parent

    def _make_checked(self, directory: Path) -> None:
        with contextlib.suppress(FileExistsError):  # whatever exists must still prove itself below
            directory.mkdir(mode=DIR_MODE)
        self.check_directory(directory)
