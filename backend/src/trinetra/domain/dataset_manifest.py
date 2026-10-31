"""DatasetManifest (IMP-0130B): the deterministic semantic identity of a parsed dataset package.

Frozen fields (EIB-06): schema_version, dataset_package_asset_id, dataset_package_digest, adapter_id,
adapter_version, orientation_policy, ontology, samples, unused_members, provenance_manifest_digest?,
manifest_digest. ``manifest_digest = SHA256(JCS(manifest without manifest_digest))``; it is a derived
semantic identity and never replaces the original package digest. It carries no extraction path, mtime,
inode or temporary directory, so one semantic package parsed twice has one digest.

PROPOSED where the sources are silent (ERR-CL-020): `ManifestSample` here holds only member path, image
digest and effective size (IMP-0155's richer sample record may extend it additively); samples and
unused members are sorted by path and disjoint, paths are normalized relative POSIX paths of at most
512 characters, at most 250,000 entries in total (the dataset_zip_mvp_v1 entry bound); the adapter
allowlist is `coco_detection_v1` and `yolo_detection_v1`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, ClassVar, Final, Self

from pydantic import AfterValidator, Field, Strict, ValidationInfo, model_validator

from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.detection import ImageDimension
from trinetra.domain.digests import DigestSha256
from trinetra.domain.ontology import ClassOntology
from trinetra.domain.orientation import OrientationPolicy
from trinetra.domain.tokens import CanonicalToken
from trinetra.jcs import canonical_digest

SCHEMA_VERSION: Final = 1
MAX_MEMBER_PATH: Final = 512
MAX_MANIFEST_ENTRIES: Final = 250_000
_UNSEALED: Final = "unsealed"
_PLACEHOLDER: Final = DigestSha256(bytes(32))
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}")


class AdapterId(CanonicalToken):
    KIND = "adapter_id"
    __slots__ = ()


ADAPTER_ALLOWLIST: Final = (AdapterId("coco_detection_v1"), AdapterId("yolo_detection_v1"))


def _member_path(value: str) -> str:
    if not 1 <= len(value) <= MAX_MEMBER_PATH:
        msg = f"member path: length must be within [1, {MAX_MEMBER_PATH}]"
        raise ValueError(msg)
    if unicodedata.normalize("NFC", value) != value:
        msg = "member path: must be NFC-normalised"
        raise ValueError(msg)
    for ch in value:
        if ch == "\\" or (ch != " " and unicodedata.category(ch)[0] in "CZ"):
            msg = f"member path: forbidden character U+{ord(ch):04X}"
            raise ValueError(msg)
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."} or part != part.strip(" ") for part in parts):
        msg = "member path: must be a normalized relative POSIX path"
        raise ValueError(msg)
    return value


def _adapter_version(value: str) -> str:
    if not _VERSION.fullmatch(value):
        msg = "adapter_version: 1..32 characters of [0-9A-Za-z._+-], starting alphanumeric"
        raise ValueError(msg)
    return value


MemberPath = Annotated[str, Strict(), AfterValidator(_member_path)]
AdapterVersion = Annotated[str, Strict(), AfterValidator(_adapter_version)]


class ManifestSample(DomainModel):
    member_path: MemberPath
    image_member_digest: DigestSha256
    effective_width: ImageDimension
    effective_height: ImageDimension


class DatasetManifest(DomainModel):
    DIGEST_FIELD: ClassVar[str] = "manifest_digest"

    schema_version: Annotated[int, Strict()]
    dataset_package_asset_id: AssetId
    dataset_package_digest: DigestSha256
    adapter_id: AdapterId
    adapter_version: AdapterVersion
    orientation_policy: OrientationPolicy
    ontology: ClassOntology
    samples: tuple[ManifestSample, ...] = Field(max_length=MAX_MANIFEST_ENTRIES)
    unused_members: tuple[MemberPath, ...] = Field(max_length=MAX_MANIFEST_ENTRIES)
    provenance_manifest_digest: DigestSha256 | None
    manifest_digest: DigestSha256

    @model_validator(mode="after")
    def _allowlisted_ordered_disjoint_and_sealed(self, info: ValidationInfo) -> DatasetManifest:
        if self.schema_version != SCHEMA_VERSION:
            msg = "schema_version must be exactly 1"
            raise ValueError(msg)
        if self.adapter_id not in ADAPTER_ALLOWLIST:
            msg = "adapter_id is not on the allowlist"
            raise ValueError(msg)
        sample_paths = [s.member_path for s in self.samples]
        if sample_paths != sorted(set(sample_paths)):
            msg = "samples must have unique member paths in ascending order"
            raise ValueError(msg)
        if list(self.unused_members) != sorted(set(self.unused_members)):
            msg = "unused_members must be unique and in ascending order"
            raise ValueError(msg)
        if len(sample_paths) + len(self.unused_members) > MAX_MANIFEST_ENTRIES:
            msg = "too many manifest entries"
            raise ValueError(msg)
        if set(sample_paths) & set(self.unused_members):
            msg = "a member cannot be both a sample and unused"
            raise ValueError(msg)
        if info.context == _UNSEALED:  # only `seal` asks for this, and only with the placeholder digest
            if self.manifest_digest != _PLACEHOLDER:
                msg = "an unsealed manifest must carry the placeholder digest"
                raise ValueError(msg)
        elif self.manifest_digest != self.compute_digest(self.to_wire()):
            msg = "manifest_digest does not match the manifest content"
            raise ValueError(msg)
        return self

    @staticmethod
    def compute_digest(wire: dict[str, Any]) -> DigestSha256:
        """SHA-256 over the JCS of `wire` without its manifest_digest."""
        body = {k: v for k, v in wire.items() if k != DatasetManifest.DIGEST_FIELD}
        return DigestSha256.parse(canonical_digest(body))

    @classmethod
    def seal(cls, **fields: Any) -> Self:
        """Build a manifest from every field except manifest_digest, computing the digest."""
        if cls.DIGEST_FIELD in fields:
            msg = "seal() computes manifest_digest; do not pass it"
            raise ValueError(msg)
        unsealed = cls.model_validate({**fields, cls.DIGEST_FIELD: _PLACEHOLDER}, context=_UNSEALED)
        return cls(**{**fields, cls.DIGEST_FIELD: cls.compute_digest(unsealed.to_wire())})


__all__ = [
    "ADAPTER_ALLOWLIST",
    "MAX_MANIFEST_ENTRIES",
    "MAX_MEMBER_PATH",
    "AdapterId",
    "AdapterVersion",
    "DatasetManifest",
    "ManifestSample",
    "MemberPath",
]
