"""Intake enumerations (IMP-0071..0073).

Three separate closed dimensions, never merged or compared: `PackageSlot` (which part of the package an
upload is for, lowercase wire form like AssetKind), `IntakeState` and `TransferState` (workflow states,
uppercase wire form). There is no persisted COMMITTING state (an exclusive lock protects commit) and no
PARSED/VALIDATED transfer state until real operations exist (EIB-04).
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class PackageSlot(WireEnum):
    DATASET = "dataset"
    MODEL = "model"
    CONTRIBUTOR_METADATA = "contributor_metadata"
    REFERENCE = "reference"
    INFERENCE_RECORDS = "inference_records"
    ASSESSMENT_PROFILE = "assessment_profile"


class IntakeState(WireEnum):
    OPEN = "OPEN"
    COMMITTED = "COMMITTED"
    ABANDONED = "ABANDONED"


class TransferState(WireEnum):
    RECEIVED = "RECEIVED"
    ADMITTED = "ADMITTED"
    FAILED = "FAILED"


__all__ = ["IntakeState", "PackageSlot", "TransferState"]
