"""Dataset adapter interfaces and capability descriptors (IMP-0130A).

Frozen (EIB-06): adapters are allowlisted by id, never self-selecting; the canonical MVP package input
is a ZIP under the safety profile ``dataset_zip_mvp_v1`` (8 GiB compressed, 250,000 entries, 32 GiB
actual extracted, 1 GiB per member, 512-character member paths, no automatic nested-archive
extraction). Those numbers are safety bounds, not demonstrated scalability. An adapter exposes a
capability descriptor, a `probe` that reports (never guesses) whether it recognizes a package, and a
`parse` that returns a result containing a DatasetManifest. The package, contributor-manifest and
result types belong to later packets (IMP-0131..0162), so the Protocol is generic over them.
"""

from __future__ import annotations

from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import Field, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.dataset_manifest import ADAPTER_ALLOWLIST, AdapterId, AdapterVersion
from trinetra.domain.orientation import OrientationPolicy
from trinetra.domain.text import Label

GIB: Final = 1024**3
MAX_PROBE_REASONS: Final = 16

PositiveInt = Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]


class DatasetPackageProfile(DomainModel):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    max_compressed_bytes: PositiveInt
    max_entries: PositiveInt
    max_extracted_bytes: PositiveInt
    max_member_bytes: PositiveInt
    max_member_path_chars: PositiveInt
    extract_nested_archives: bool

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> DatasetPackageProfile:
        if self.max_member_bytes > self.max_extracted_bytes:
            msg = "a single member cannot be allowed to exceed the total extraction bound"
            raise ValueError(msg)
        return self


DATASET_ZIP_MVP_V1: Final = DatasetPackageProfile(
    profile_id="dataset_zip_mvp_v1",
    max_compressed_bytes=8 * GIB,
    max_entries=250_000,
    max_extracted_bytes=32 * GIB,
    max_member_bytes=1 * GIB,
    max_member_path_chars=512,
    extract_nested_archives=False,
)


class AdapterCapabilities(DomainModel):
    """What one allowlisted adapter can do; used for selection checks, never for guessing."""

    adapter_id: AdapterId
    adapter_version: AdapterVersion
    supported_orientation_policies: tuple[OrientationPolicy, ...] = Field(min_length=1)
    supports_zero_object_images: bool

    @model_validator(mode="after")
    def _allowlisted_and_canonically_listed(self) -> AdapterCapabilities:
        if self.adapter_id not in ADAPTER_ALLOWLIST:
            msg = "adapter_id is not on the allowlist"
            raise ValueError(msg)
        policies = [p.value for p in self.supported_orientation_policies]
        if policies != sorted(set(policies)):
            msg = "supported_orientation_policies must be unique and sorted by wire form"
            raise ValueError(msg)
        return self

    def supports(self, policy: OrientationPolicy) -> bool:
        return policy in self.supported_orientation_policies


class AdapterProbe(DomainModel):
    """A probe result: a recognition verdict with bounded reasons, never a guess about a different adapter."""

    adapter_id: AdapterId
    recognized: bool
    reasons: tuple[Label, ...] = Field(max_length=MAX_PROBE_REASONS)

    @model_validator(mode="after")
    def _a_rejection_explains_itself(self) -> AdapterProbe:
        if self.adapter_id not in ADAPTER_ALLOWLIST:
            msg = "adapter_id is not on the allowlist"
            raise ValueError(msg)
        if not self.recognized and not self.reasons:
            msg = "a package that is not recognized needs at least one reason"
            raise ValueError(msg)
        return self


@runtime_checkable
class DatasetAdapter[Package, Contributor, Result](Protocol):
    """The adapter surface; selection is by declared format plus explicit probe, never heuristics."""

    @property
    def capabilities(self) -> AdapterCapabilities: ...

    def probe(self, package: Package) -> AdapterProbe: ...

    def parse(
        self,
        package: Package,
        profile: DatasetPackageProfile,
        contributor_manifest: Contributor | None,
    ) -> Result: ...


__all__ = [
    "DATASET_ZIP_MVP_V1",
    "GIB",
    "MAX_PROBE_REASONS",
    "AdapterCapabilities",
    "AdapterProbe",
    "DatasetAdapter",
    "DatasetPackageProfile",
]
