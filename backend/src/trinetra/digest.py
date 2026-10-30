"""SHA-256 digest utility (IMP-0008).

Standard library only. Foundation helpers, not the domain `DigestSha256` /
`ContentIdentity` value objects (those are a later packet, IMP-0027).

Wire form (PROPOSED; the Bible shows ``"sha256:..."`` in its receipt example
and lower-case hex in its own ``SHA-256:`` lines): ``sha256:`` followed by
exactly 64 lower-case hexadecimal characters. Parsing is the exact inverse of
formatting: upper-case hex, whitespace, a missing prefix, other algorithm
names and wrong lengths are all rejected, never normalised.

No new canonicalization or domain-separation scheme is introduced here; this
hashes the bytes it is given, exactly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from pathlib import Path
from typing import Final

from trinetra.errors import TrinetraError

PREFIX: Final = "sha256:"
DIGEST_SIZE: Final = 32
HEX_LENGTH: Final = 64
DEFAULT_CHUNK_SIZE: Final = 1 << 20
MAX_CHUNK_SIZE: Final = 1 << 26
_HEX_RE: Final = re.compile(r"[0-9a-f]{64}")

Buffer = bytes | bytearray | memoryview


class DigestError(TrinetraError):
    code = "DIGEST_INVALID"
    default_message = "invalid digest or digest input"


def _as_buffer(data: object, name: str = "data") -> Buffer:
    if not isinstance(data, bytes | bytearray | memoryview):
        raise DigestError(f"{name} must be bytes-like, not {type(data).__name__}", details={"name": name})
    if isinstance(data, memoryview) and not data.c_contiguous:
        raise DigestError(f"{name} must be a contiguous buffer", details={"name": name})
    return data


def sha256_bytes(data: object) -> bytes:
    """Raw 32-byte digest of `data` (bytes, bytearray or contiguous memoryview)."""
    return hashlib.sha256(_as_buffer(data)).digest()


def sha256_hex(data: object) -> str:
    """64 lower-case hex characters, no prefix."""
    return hashlib.sha256(_as_buffer(data)).hexdigest()


def format_digest(raw: object) -> str:
    """Raw 32 bytes -> ``sha256:<hex>``."""
    if not isinstance(raw, bytes) or len(raw) != DIGEST_SIZE:
        raise DigestError(f"raw digest must be exactly {DIGEST_SIZE} bytes", details={"name": "raw"})
    return PREFIX + raw.hex()


def parse_digest(text: object) -> bytes:
    """``sha256:<64 lower-case hex>`` -> raw 32 bytes; anything else is an error."""
    if not isinstance(text, str):
        raise DigestError("digest must be a string", details={"name": "digest"})
    if not text.startswith(PREFIX) or not _HEX_RE.fullmatch(text, len(PREFIX)):
        raise DigestError("digest must be 'sha256:' followed by 64 lower-case hex", details={"name": "digest"})
    return bytes.fromhex(text[len(PREFIX):])


def sha256_digest(data: object) -> str:
    """``sha256:<hex>`` of `data`."""
    return format_digest(sha256_bytes(data))


def digests_equal(a: object, b: object) -> bool:
    """Constant-time comparison of two wire-form digests; both must be valid."""
    return hmac.compare_digest(parse_digest(a), parse_digest(b))


def verify_digest(data: object, expected: object) -> bool:
    """True iff `data` hashes to the wire-form digest `expected` (constant time)."""
    return hmac.compare_digest(sha256_bytes(data), parse_digest(expected))


class Sha256Stream:
    """Incremental hashing; `finalize` may be called once, after which it is closed."""

    __slots__ = ("_closed", "_hash", "_size")

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self._size = 0
        self._closed = False

    @property
    def size(self) -> int:
        """Total bytes fed so far."""
        return self._size

    def update(self, chunk: object) -> None:
        if self._closed:
            raise DigestError("stream already finalized", details={"name": "stream"})
        buf = _as_buffer(chunk, "chunk")
        self._hash.update(buf)
        self._size += len(buf) if not isinstance(buf, memoryview) else buf.nbytes

    def finalize(self) -> str:
        """Close the stream and return ``sha256:<hex>``."""
        if self._closed:
            raise DigestError("stream already finalized", details={"name": "stream"})
        self._closed = True
        return format_digest(self._hash.digest())


def sha256_file(
    path: Path | str,
    *,
    max_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[str, int]:
    """Hash a regular file in bounded chunks; returns (``sha256:<hex>``, size).

    * `max_bytes` is mandatory: unbounded reads of untrusted files are refused.
    * Symlinks are not followed (``O_NOFOLLOW``); non-regular files (directories,
      FIFOs, devices) are rejected after ``fstat`` on the opened descriptor, so
      there is no check-then-open race.
    * A file that grows past `max_bytes` while being read is rejected.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise DigestError("max_bytes must be a non-negative int", details={"name": "max_bytes"})
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise DigestError(f"chunk_size must be in 1..{MAX_CHUNK_SIZE}", details={"name": "chunk_size"})
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DigestError(f"cannot open file: {type(exc).__name__}", details={"name": "path"}) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DigestError("not a regular file", details={"name": "path"})
        if info.st_size > max_bytes:
            raise DigestError("file larger than max_bytes", details={"name": "path"})
        stream = Sha256Stream()
        while True:
            chunk = os.read(fd, min(chunk_size, max_bytes - stream.size + 1))
            if not chunk:
                break
            stream.update(chunk)
            if stream.size > max_bytes:
                raise DigestError("file larger than max_bytes", details={"name": "path"})
        return stream.finalize(), stream.size
    except OSError as exc:
        raise DigestError(f"cannot read file: {type(exc).__name__}", details={"name": "path"}) from exc
    finally:
        os.close(fd)
