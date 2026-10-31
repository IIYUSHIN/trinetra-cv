"""CAS corruption detection (IMP-0052).

`verify_object` re-reads an object and classifies it; `scan_objects` walks the whole object tree and
also reports anything that should not be there. Detection only: nothing is ever repaired, moved or
deleted here, because a corrupted object is evidence and silently "fixing" it would destroy the very
thing a reviewer needs. Expected conditions are returned as results, not raised, so one bad object can
never abort a sweep. The statuses mirror what `blob_locators.verification_status` can persist
(MALFORMED, a shape violation, has no column value; mapping it is IMP-0055 work, ERR-CL-013).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.infrastructure.cas_commit import hash_file
from trinetra.infrastructure.cas_layout import digest_hex
from trinetra.infrastructure.cas_read import ObjectMissingError, ObjectShapeError, open_fd

if TYPE_CHECKING:
    from pathlib import Path

    from trinetra.infrastructure.cas_layout import CasLayout

Status = Literal["VERIFIED", "MISSING", "DIGEST_MISMATCH", "MALFORMED", "IO_ERROR"]
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_HEX2: Final = re.compile(r"^[0-9a-f]{2}$")


@dataclass(frozen=True)
class VerificationResult:
    digest: str
    status: Status
    detail: str = ""
    size: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "VERIFIED"


@dataclass
class ScanReport:
    verified: int = 0
    problems: list[VerificationResult] = field(default_factory=list)
    strays: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems and not self.strays


def verify_object(layout: CasLayout, digest: DigestSha256 | str) -> VerificationResult:
    wire = f"sha256:{digest_hex(digest)}"
    try:
        fd, info = open_fd(layout, digest)
    except ObjectMissingError:
        return VerificationResult(wire, "MISSING", "no object at the digest path")
    except ObjectShapeError as exc:
        return VerificationResult(wire, "MALFORMED", str(exc))
    except OSError as exc:
        return VerificationResult(wire, "IO_ERROR", exc.strerror or "I/O error")
    os.close(fd)
    try:
        actual, size = hash_file(layout.object_path(digest))
    except (OSError, TrinetraError) as exc:
        return VerificationResult(wire, "IO_ERROR", str(exc))
    if actual.to_wire() != wire:
        return VerificationResult(wire, "DIGEST_MISMATCH", f"content hashes to {actual.to_wire()}", size)
    if size != info.st_size:
        return VerificationResult(wire, "DIGEST_MISMATCH", "size changed while verifying", size)
    return VerificationResult(wire, "VERIFIED", "", size)


def _children(directory: Path) -> list[Path]:
    return sorted(directory.iterdir(), key=lambda p: p.name)


def scan_objects(layout: CasLayout) -> ScanReport:
    """Verify every object and report entries that do not belong in the object tree."""
    report = ScanReport()
    root = layout.objects
    for first in _children(root):
        if not _HEX2.match(first.name) or first.is_symlink() or not first.is_dir():
            report.strays.append(first.relative_to(root).as_posix())
            continue
        for second in _children(first):
            if not _HEX2.match(second.name) or second.is_symlink() or not second.is_dir():
                report.strays.append(second.relative_to(root).as_posix())
                continue
            for leaf in _children(second):
                relative = leaf.relative_to(root).as_posix()
                name = leaf.name
                if not _HEX64.match(name) or name[:2] != first.name or name[2:4] != second.name:
                    report.strays.append(relative)
                    continue
                result = verify_object(layout, f"sha256:{name}")
                if result.ok:
                    report.verified += 1
                else:
                    report.problems.append(result)
    return report
