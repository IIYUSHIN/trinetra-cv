"""The capability catalog and what this build stage honestly declares for it (IMP-0093, IMP-0094).

Source (EIB-04 / Wave GR): the Preflight view lists eight capabilities. Only asset identity exists in
this build stage; the other seven are declared UNAVAILABLE with the reason code
`not_implemented_in_current_build_stage`. Nothing here may claim more than what runs: when a capability is
built, its declaration moves from this table to the module that owns it.

PROPOSED (ERR-CL-022): reason texts are engineering wording, not frozen copy.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import CapabilityAssessment, CapabilityReasonCode

ASSET_IDENTITY: Final = CapabilityName("asset_identity")
DATASET_SEMANTIC_ADAPTER: Final = CapabilityName("dataset_semantic_adapter")
MODEL_RUNTIME: Final = CapabilityName("model_runtime")
DATA_INTEGRITY: Final = CapabilityName("data_integrity")
MODEL_INTEGRITY: Final = CapabilityName("model_integrity")
DRIFT_ASSESSMENT: Final = CapabilityName("drift_assessment")
TIIR_VERIFICATION: Final = CapabilityName("tiir_verification")
ASSURANCE_REASONING: Final = CapabilityName("assurance_reasoning")

CAPABILITY_CATALOG: Final = (
    ASSET_IDENTITY, DATASET_SEMANTIC_ADAPTER, MODEL_RUNTIME, DATA_INTEGRITY, MODEL_INTEGRITY,
    DRIFT_ASSESSMENT, TIIR_VERIFICATION, ASSURANCE_REASONING,
)

AVAILABLE_IN_BUILD: Final = CapabilityReasonCode("available_in_current_build_stage")
NOT_IMPLEMENTED: Final = CapabilityReasonCode("not_implemented_in_current_build_stage")

_NOT_IMPLEMENTED_TEXT: Final = MappingProxyType({
    DATASET_SEMANTIC_ADAPTER: "Dataset semantic adapters are not implemented in this build stage.",
    MODEL_RUNTIME: "No model runtime is implemented in this build stage, so no model can be run.",
    DATA_INTEGRITY: "Data integrity checks are not implemented in this build stage.",
    MODEL_INTEGRITY: "Model integrity checks are not implemented in this build stage.",
    DRIFT_ASSESSMENT: "Drift assessment is not implemented in this build stage.",
    TIIR_VERIFICATION: "Inference-record verification is not implemented in this build stage.",
    ASSURANCE_REASONING: "Assurance reasoning is not implemented in this build stage.",
})


def build_stage_declarations() -> tuple[CapabilityAssessment, ...]:
    """What is declared for the current build stage, in catalog order."""
    items = [CapabilityAssessment(
        capability=ASSET_IDENTITY, capability_state=CapabilityState.AVAILABLE, reason_code=AVAILABLE_IN_BUILD,
        reason_text="Asset identity (byte digest and size) is established by Core when an asset is admitted.",
        required_access=(), available_access=(), required_reference_purposes=(),
    )]
    items.extend(
        CapabilityAssessment(
            capability=name, capability_state=CapabilityState.UNAVAILABLE, reason_code=NOT_IMPLEMENTED,
            reason_text=_NOT_IMPLEMENTED_TEXT[name],
            required_access=(), available_access=(), required_reference_purposes=(),
        )
        for name in CAPABILITY_CATALOG[1:]
    )
    return tuple(items)


class BuildStageCapabilitySource:
    """The CapabilitySourcePort for the current build stage."""

    def declared(self) -> tuple[CapabilityAssessment, ...]:
        return build_stage_declarations()


__all__ = [
    "ASSET_IDENTITY",
    "ASSURANCE_REASONING",
    "AVAILABLE_IN_BUILD",
    "CAPABILITY_CATALOG",
    "DATASET_SEMANTIC_ADAPTER",
    "DATA_INTEGRITY",
    "DRIFT_ASSESSMENT",
    "MODEL_INTEGRITY",
    "MODEL_RUNTIME",
    "NOT_IMPLEMENTED",
    "TIIR_VERIFICATION",
    "BuildStageCapabilitySource",
    "build_stage_declarations",
]
