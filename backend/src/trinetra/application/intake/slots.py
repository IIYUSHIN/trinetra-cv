"""Package-slot contract (IMP-0073): what each slot accepts and how many uploads it needs.

Frozen (EIB-04): commit needs exactly one DATASET and one MODEL; optional 0..1 contributor metadata,
0..N references, 0..N inference records, 0..1 assessment profile. Declared formats are claims, not
validated formats (adapters arrive in EIB-06): dataset `coco|yolo`, model `pytorch|torchscript|onnx`,
contributor metadata and assessment profile `json`; references and inference records may declare any
format or none.

PROPOSED (ERR-CL-022): a declaration is optional in every slot (absence is shown, never guessed); the
slot to AssetKind mapping below; and at most MAX_TRANSFERS_PER_INTAKE transfers (failed ones included) per
intake as an abuse bound, since "0..N" is otherwise unbounded.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import Field, model_validator

from trinetra.application.intake.enums import PackageSlot
from trinetra.domain.assets import AssetKind
from trinetra.domain.base import DomainModel
from trinetra.domain.tokens import CanonicalToken

MAX_TRANSFERS_PER_INTAKE: Final = 1_000
MAX_SLOT_UPLOADS: Final = MAX_TRANSFERS_PER_INTAKE


class DeclaredFormat(CanonicalToken):
    KIND = "declared_format"
    __slots__ = ()


class SlotContract(DomainModel):
    slot: PackageSlot
    asset_kind: AssetKind
    minimum: int = Field(strict=True, ge=0, le=MAX_SLOT_UPLOADS)
    maximum: int = Field(strict=True, ge=1, le=MAX_SLOT_UPLOADS)
    allowed_formats: tuple[DeclaredFormat, ...] | None  # None: any well-formed token may be declared

    @model_validator(mode="after")
    def _coherent(self) -> SlotContract:
        if self.minimum > self.maximum:
            msg = "minimum cannot exceed maximum"
            raise ValueError(msg)
        if self.allowed_formats is not None:
            texts = [f.text for f in self.allowed_formats]
            if not texts or texts != sorted(set(texts)):
                msg = "allowed_formats must be non-empty, unique and sorted"
                raise ValueError(msg)
        return self

    def allows(self, declared: DeclaredFormat | None) -> bool:
        if declared is None or self.allowed_formats is None:
            return True
        return declared in self.allowed_formats


def _formats(*names: str) -> tuple[DeclaredFormat, ...]:
    return tuple(DeclaredFormat(n) for n in sorted(names))


SLOT_CONTRACTS: Final[Mapping[PackageSlot, SlotContract]] = MappingProxyType({
    PackageSlot.DATASET: SlotContract(slot=PackageSlot.DATASET, asset_kind=AssetKind.DATASET,
                                      minimum=1, maximum=1, allowed_formats=_formats("coco", "yolo")),
    PackageSlot.MODEL: SlotContract(slot=PackageSlot.MODEL, asset_kind=AssetKind.MODEL,
                                    minimum=1, maximum=1,
                                    allowed_formats=_formats("pytorch", "torchscript", "onnx")),
    PackageSlot.CONTRIBUTOR_METADATA: SlotContract(
        slot=PackageSlot.CONTRIBUTOR_METADATA, asset_kind=AssetKind.CONTRIBUTOR_METADATA,
        minimum=0, maximum=1, allowed_formats=_formats("json")),
    PackageSlot.REFERENCE: SlotContract(slot=PackageSlot.REFERENCE, asset_kind=AssetKind.REFERENCE,
                                        minimum=0, maximum=MAX_SLOT_UPLOADS, allowed_formats=None),
    PackageSlot.INFERENCE_RECORDS: SlotContract(
        slot=PackageSlot.INFERENCE_RECORDS, asset_kind=AssetKind.INFERENCE_RECORD,
        minimum=0, maximum=MAX_SLOT_UPLOADS, allowed_formats=None),
    PackageSlot.ASSESSMENT_PROFILE: SlotContract(
        slot=PackageSlot.ASSESSMENT_PROFILE, asset_kind=AssetKind.ASSESSMENT_PROFILE,
        minimum=0, maximum=1, allowed_formats=_formats("json")),
})  # fmt: skip


def slot_contract(slot: PackageSlot) -> SlotContract:
    return SLOT_CONTRACTS[slot]


__all__ = [
    "MAX_SLOT_UPLOADS",
    "MAX_TRANSFERS_PER_INTAKE",
    "SLOT_CONTRACTS",
    "DeclaredFormat",
    "SlotContract",
    "slot_contract",
]
