"""Asset admission profile and media metadata checks (IMP-0054).

A pure policy: given a claimed AssetKind, a claimed media type, a measured size and the first bytes of
the content, decide whether the asset may be admitted. It performs no I/O and decides nothing about
scientific validity. The media type is a *claim* by the uploader; the only independent evidence here is
a small magic-byte sniff for common container/image formats. A claim that contradicts what the bytes
plainly are is refused; `application/octet-stream` is the honest "unknown" claim and is accepted where a
kind allows it. Content that sniffs as nothing known is not thereby valid or invalid: that is left to the
later format-specific validators.

PROPOSED defaults (ERR-CL-013, not frozen, no measured basis): the per-kind media-type allow-lists and
size ceilings below are conservative engineering placeholders meant to be overridden by configuration;
they are not scientific thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from trinetra.domain.assets import AssetKind
from trinetra.errors import TrinetraError

OCTET: Final = "application/octet-stream"
SNIFF_BYTES: Final = 16
MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB


class AdmissionProfileError(TrinetraError):
    code = "ASSET_PROFILE_REJECTED"
    default_message = "the asset does not satisfy the admission profile"


def sniff_media_type(head: bytes) -> str | None:
    """Identify a few common formats from their leading bytes; None means 'not recognised'."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"BM") and len(head) >= SNIFF_BYTES and head[6:10] == b"\x00\x00\x00\x00":
        return "image/bmp"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "application/zip"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    return None


@dataclass(frozen=True)
class KindRule:
    media_types: frozenset[str]
    max_bytes: int

    def __post_init__(self) -> None:
        if not self.media_types or self.max_bytes < 0:
            msg = "a kind rule needs at least one media type and a non-negative size ceiling"
            raise AdmissionProfileError(msg)


_IMAGES = frozenset({"image/png", "image/jpeg", "image/bmp", "image/webp", "image/tiff"})
_TEXT_LIKE = frozenset({"application/json", "text/plain", "text/csv"})
_ARCHIVES = frozenset({"application/zip", "application/gzip", "application/x-tar"})


@dataclass(frozen=True)
class AdmissionProfile:
    rules: tuple[tuple[AssetKind, KindRule], ...]

    def __post_init__(self) -> None:
        kinds = [kind for kind, _ in self.rules]
        if len(set(kinds)) != len(kinds) or set(kinds) != set(AssetKind):
            msg = "a profile must define exactly one rule for every AssetKind"
            raise AdmissionProfileError(msg)

    def rule_for(self, kind: AssetKind) -> KindRule:
        for candidate, rule in self.rules:
            if candidate == kind:
                return rule
        msg = "no rule for this kind"  # unreachable: __post_init__ guarantees completeness
        raise AdmissionProfileError(msg)

    def check(self, kind: AssetKind, media_type: str, size_bytes: int, head: bytes) -> None:
        """Raise AdmissionProfileError unless the asset may be admitted under this profile."""
        rule = self.rule_for(kind)
        if media_type not in rule.media_types:
            msg = f"media type is not allowed for {kind.value}"
            raise AdmissionProfileError(msg, details={"kind": kind.value, "media_type": media_type})
        if not 0 <= size_bytes <= rule.max_bytes:
            msg = f"size is outside the ceiling for {kind.value}"
            raise AdmissionProfileError(msg, details={"kind": kind.value, "size_bytes": size_bytes})
        seen = sniff_media_type(head[:SNIFF_BYTES])
        if seen is not None and media_type not in (OCTET, seen):
            msg = "the claimed media type contradicts the content"
            raise AdmissionProfileError(msg, details={"claimed": media_type, "detected": seen})
        if seen is not None and media_type == OCTET and seen not in rule.media_types:
            msg = f"the content is a {seen}, which is not allowed for {kind.value}"
            raise AdmissionProfileError(msg, details={"detected": seen})


def _rule(types: frozenset[str], ceiling: int) -> KindRule:
    return KindRule(types | {OCTET}, ceiling)


DEFAULT_PROFILE: Final = AdmissionProfile(
    (
        (AssetKind.DATASET, _rule(_IMAGES | _ARCHIVES | _TEXT_LIKE, 8 * GIB)),
        (AssetKind.MODEL, _rule(frozenset({"application/zip"}), 8 * GIB)),
        (AssetKind.CONTRIBUTOR_METADATA, _rule(_TEXT_LIKE, 64 * MIB)),
        (AssetKind.REFERENCE, _rule(_IMAGES | _ARCHIVES | _TEXT_LIKE | {"application/pdf"}, 1 * GIB)),
        (AssetKind.INFERENCE_RECORD, _rule(frozenset({"application/json"}), 64 * MIB)),
        (AssetKind.ASSESSMENT_PROFILE, _rule(frozenset({"application/json"}), 64 * MIB)),
        (AssetKind.INFERENCE_OUTPUT, _rule(_TEXT_LIKE | _ARCHIVES, 1 * GIB)),
        (AssetKind.INFERENCE_RECEIPT, _rule(frozenset({"application/json"}), 64 * MIB)),
    )
)

__all__ = [
    "DEFAULT_PROFILE",
    "AdmissionProfile",
    "AdmissionProfileError",
    "KindRule",
    "sniff_media_type",
]
