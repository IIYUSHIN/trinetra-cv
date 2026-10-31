"""CAS store facade and duplicate-content idempotency (IMP-0051).

`CasStore.admit` is the single byte-admission entry point: stream to quarantine, compute identity,
publish atomically. Admitting identical content again is idempotent: the existing object is *verified*
(shape, size, and a fresh re-hash) and reported with `created=False`; the new candidate is discarded and
the existing object is never rewritten. If the object at a digest path does not match its digest the
store fails closed with a conflict and leaves the evidence untouched; it never "repairs" by overwriting
(corruption handling is IMP-0052). A filename is never an input to identity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from trinetra.errors import TrinetraError
from trinetra.infrastructure.cas_admission import ByteSource, stream_to_quarantine
from trinetra.infrastructure.cas_commit import ObjectExistsError, commit_candidate, hash_file
from trinetra.infrastructure.cas_quarantine import Quarantine
from trinetra.infrastructure.cas_read import ObjectMissingError, ObjectShapeError, check_shape

if TYPE_CHECKING:
    from pathlib import Path

    from trinetra.domain.digests import DigestSha256
    from trinetra.infrastructure.cas_layout import CasLayout

SETTLE_ATTEMPTS: Final = 100
SETTLE_SECONDS: Final = 0.01


class DuplicateConflictError(TrinetraError):
    code = "CAS_DUPLICATE_CONFLICT"
    default_message = "an object already at this digest does not match its digest"


@dataclass(frozen=True)
class AdmissionResult:
    digest: DigestSha256
    size: int
    created: bool


class CasStore:
    def __init__(self, layout: CasLayout, *, max_bytes: int | None = None) -> None:
        self._layout = layout
        layout.ensure()
        self._quarantine = (
            Quarantine(layout) if max_bytes is None else Quarantine(layout, max_bytes=max_bytes)
        )

    @property
    def layout(self) -> CasLayout:
        return self._layout

    def admit(
        self,
        source: ByteSource,
        *,
        expected_digest: DigestSha256 | None = None,
        expected_size: int | None = None,
        chunk_size: int | None = None,
    ) -> AdmissionResult:
        options = {} if chunk_size is None else {"chunk_size": chunk_size}
        candidate = stream_to_quarantine(
            self._quarantine, source, expected_digest=expected_digest, expected_size=expected_size, **options
        )
        try:
            committed = commit_candidate(self._layout, candidate)
        except ObjectExistsError:
            try:
                self._prove_existing_matches(candidate.digest, candidate.size)
            finally:
                candidate.discard()
            return AdmissionResult(candidate.digest, candidate.size, created=False)
        except BaseException:
            candidate.discard()
            raise
        return AdmissionResult(committed.digest, committed.size, created=True)

    def _prove_existing_matches(self, digest: DigestSha256, size: int) -> None:
        path = self._layout.object_path(digest)
        try:
            self._await_single_link(path)
            check_shape(path.lstat())
            actual, actual_size = hash_file(path)
        except (ObjectShapeError, ObjectMissingError, OSError, TrinetraError) as exc:
            msg = "the existing object is not a sound regular file"
            raise DuplicateConflictError(msg) from exc
        if actual != digest or actual_size != size:
            msg = "the existing object does not match its digest"
            raise DuplicateConflictError(msg)

    @staticmethod
    def _await_single_link(path: Path) -> None:
        """A concurrent admission of the same bytes briefly holds two links (publish, then unstage)."""
        for _ in range(SETTLE_ATTEMPTS):
            if path.lstat().st_nlink <= 1:
                return
            time.sleep(SETTLE_SECONDS)
