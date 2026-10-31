"""IMP-0403: synthetic scenario causes are not real-world intent attribution."""

from trinetra.domain.enums import WireEnum
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema


class ScenarioCauseClass(WireEnum):
    SEEDED_ADVERSE_MUTATION = "SEEDED_ADVERSE_MUTATION"
    SEEDED_BENIGN_CONDITION = "SEEDED_BENIGN_CONDITION"
    OPERATIONAL_SHIFT = "OPERATIONAL_SHIFT"
    DEPLOYMENT_ERROR = "DEPLOYMENT_ERROR"
    RECORD_TAMPER = "RECORD_TAMPER"
    CAPABILITY_LIMITATION = "CAPABILITY_LIMITATION"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    MIXED_CAUSE = "MIXED_CAUSE"


class _CauseSchema(PrivateSchema):
    cause_class: ScenarioCauseClass
    components: tuple[ScenarioCauseClass, ...]
    generation_record_ref: Label


class CauseTruth(PrivateRecord):
    __slots__ = ()
    SCHEMA = _CauseSchema
