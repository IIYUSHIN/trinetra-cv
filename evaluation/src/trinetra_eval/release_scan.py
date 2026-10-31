"""IMP-0419: fail-closed bounded scan of files and nested ZIP/tar/gzip release artifacts."""

from __future__ import annotations

import base64
import gzip
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Never

from trinetra_eval.canary import LeakageCanary

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_DEPTH = 8


class LeakageCanaryViolation(RuntimeError):  # noqa: N818 - frozen evaluation error name
    def __init__(self) -> None:
        super().__init__("release leakage scan failed; artifact details withheld")


def _violation() -> Never:
    raise LeakageCanaryViolation()


def assert_no_leakage(
    paths: tuple[Path, ...], canaries: tuple[LeakageCanary, ...], forbidden_terms: tuple[bytes, ...] = ()
) -> None:
    """Scan artifact contents and names. This detects known markers, not arbitrary covert channels.

    Pass private semantic vocabulary only when it is forbidden for these artifacts;
    legitimate naturally occurring asset content requires a separately reviewed scope.
    Never emit artifact names or matched bytes in exceptions or logs.
    """
    if not paths or not canaries:
        _violation()
    patterns: set[bytes] = set(forbidden_terms)
    for canary in canaries:
        token = canary.evaluator_private_token()
        patterns.update(
            (
                token,
                base64.b64encode(token),
                token.hex().encode(),
                token.decode().encode("utf-16-le"),
                token.decode().encode("utf-16-be"),
            )
        )
    remaining = [MAX_BYTES, MAX_ENTRIES]

    def check(raw: bytes) -> None:
        if any(pattern in raw for pattern in patterns):
            _violation()

    def scan(raw: bytes, depth: int) -> None:
        remaining[0] -= len(raw)
        remaining[1] -= 1
        if depth > MAX_DEPTH or min(remaining) < 0:
            _violation()
        check(raw)
        if raw.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for entry in archive.infolist():
                    remaining[1] -= 1
                    if remaining[1] < 0:
                        _violation()
                    if stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > remaining[0]:
                        _violation()
                    check(entry.filename.encode())
                    if not entry.is_dir():
                        scan(archive.read(entry), depth + 1)
        elif raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                scan(stream.read(remaining[0] + 1), depth + 1)
        elif raw[257:262] == b"ustar":
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar_archive:
                for tar_entry in tar_archive:
                    remaining[1] -= 1
                    if remaining[1] < 0:
                        _violation()
                    if not (tar_entry.isfile() or tar_entry.isdir()) or tar_entry.size > remaining[0]:
                        _violation()
                    check(tar_entry.name.encode())
                    tar_stream = tar_archive.extractfile(tar_entry) if tar_entry.isfile() else None
                    if tar_stream is not None:
                        with tar_stream:
                            scan(tar_stream.read(remaining[0] + 1), depth + 1)
        elif raw.lstrip().startswith((b"{", b"[", b'"')):
            try:
                document = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return  # not an encoded JSON artifact; its raw bytes were scanned
            pending = [document]
            while pending:
                value = pending.pop()
                if isinstance(value, str):
                    check(value.encode())
                elif isinstance(value, dict):
                    pending.extend(value.keys())
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)

    failed = False
    try:
        for root in paths:
            if root.is_symlink() or not root.exists():
                _violation()
            files = [root]
            pending_dirs = [root] if root.is_dir() else []
            while pending_dirs:
                directory = pending_dirs.pop()
                for entry in directory.iterdir():
                    files.append(entry)
                    if len(files) > MAX_ENTRIES:
                        _violation()
                    if entry.is_dir() and not entry.is_symlink():
                        pending_dirs.append(entry)
            for path in files:
                if path.is_symlink():
                    _violation()
                check(str(path).encode())
                if path.is_file():
                    if path.stat().st_size > remaining[0]:
                        _violation()
                    scan(path.read_bytes(), 0)
                elif not path.is_dir():
                    _violation()
    except Exception:  # noqa: BLE001 - fail closed without echoing truth-bearing paths or archive errors
        failed = True
    if failed:
        _violation()
