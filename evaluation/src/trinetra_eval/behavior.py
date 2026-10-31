"""IMP-0402: realized effects require an explicit predeclared criterion reference."""

from trinetra.domain.digests import DigestSha256
from trinetra.domain.enums import WireEnum
from trinetra.domain.text import Label
from trinetra_eval.private import PrivateRecord, PrivateSchema


class BehaviouralTruthState(WireEnum):
    ACTIVE_EFFECT = "ACTIVE_EFFECT"
    NO_MATERIAL_EFFECT = "NO_MATERIAL_EFFECT"
    CONTROL_EXPLAINED_EFFECT = "CONTROL_EXPLAINED_EFFECT"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class _BehaviorSchema(PrivateSchema):
    behavioural_state: BehaviouralTruthState
    criterion_ref: DigestSha256
    observation_refs: tuple[DigestSha256, ...]
    limitations: tuple[Label, ...]


class BehavioralTruth(PrivateRecord):
    __slots__ = ()
    SCHEMA = _BehaviorSchema
