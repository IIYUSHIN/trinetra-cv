"""CAS streaming SHA-256 admission (IMP-0048).

Bytes are streamed from an untrusted source into quarantine while a SHA-256 is computed over exactly
the bytes written. Memory is bounded by the chunk size, never the object size. The digest is computed
by Core from the bytes; a caller-declared digest or size is only a claim to be checked, and a mismatch
fails closed and discards the staged file. Nothing is admitted to the CAS here: the result is a staged
candidate whose identity is proven, to be committed durably by IMP-0049 or discarded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from trinetra.infrastructure.cas_quarantine import Quarantine, StagedWrite

DEFAULT_CHUNK: Final = 1024 * 1024
MAX_CHUNK: Final = 64 * 1024 * 1024


class AdmissionError(TrinetraError):
    code = "CAS_ADMISSION_REJECTED"
    default_message = "the byte stream was rejected during CAS admission"


class ByteSource(Protocol):
    def read(self, size: int, /) -> object: ...


@dataclass
class StagedCandidate:
    """A fully written, fsynced quarantine file with a Core-computed identity. Not yet admitted."""

    staged: StagedWrite
    digest: DigestSha256
    size: int

    def discard(self) -> None:
        self.staged.discard()


def stream_to_quarantine(
    quarantine: Quarantine,
    source: ByteSource,
    *,
    expected_digest: DigestSha256 | None = None,
    expected_size: int | None = None,
    chunk_size: int = DEFAULT_CHUNK,
) -> StagedCandidate:
    if not 1 <= chunk_size <= MAX_CHUNK:
        msg = "chunk_size is outside the permitted range"
        raise AdmissionError(msg)
    if expected_size is not None and expected_size < 0:
        msg = "expected_size must not be negative"
        raise AdmissionError(msg)
    hasher = hashlib.sha256()
    staged = quarantine.begin()
    try:
        total = 0
        while True:
            chunk = source.read(chunk_size)
            if not isinstance(chunk, bytes):
                msg = "the byte source returned a non-bytes value"
                raise AdmissionError(msg)  # noqa: TRY301 - single cleanup path below
            if not chunk:
                break
            if len(chunk) > chunk_size:
                msg = "the byte source returned more than requested"
                raise AdmissionError(msg)  # noqa: TRY301 - single cleanup path below
            total += len(chunk)
            if expected_size is not None and total > expected_size:
                msg = "the stream is longer than the declared size"
                raise AdmissionError(msg)  # noqa: TRY301 - single cleanup path below
            hasher.update(chunk)
            staged.write(chunk)
        if expected_size is not None and total != expected_size:
            msg = "the stream is shorter than the declared size"
            raise AdmissionError(msg)  # noqa: TRY301 - single cleanup path below
        digest = DigestSha256(hasher.digest())
        if expected_digest is not None and digest != expected_digest:
            msg = "the stream does not match the declared digest"
            raise AdmissionError(msg)  # noqa: TRY301 - single cleanup path below
        staged.sync_and_close()
    except BaseException:
        staged.discard()
        raise
    return StagedCandidate(staged, digest, total)
